"""Health checks: each extension's own `serviceHealth` endpoints, and the
direct lyrics provider servers."""

from __future__ import annotations

import asyncio
import time
from typing import Any, NamedTuple

import httpx

_UA = "Mornye-HealthCheck/5.0"
_TIMEOUT = httpx.Timeout(connect=2.0, read=3.0, write=2.0, pool=2.0)
_SERVERS = {
    "apple": "https://api.zarz.moe/apple-music/lyrics?id=6781024437&format=elrc",
    "lyricsplus": "https://lyricsplus.prjktla.my.id/v1/lyrics/get?title=Bohemian%20Rhapsody&artist=Queen&isrc=GBUM71029604",
    "lrcred": "https://lrc.red/s/GBUM71029604.ttml",
    "lrclib": "https://lrclib.net/api/get?artist_name=Queen&track_name=Bohemian%20Rhapsody",
    "spotify": "https://spclient.wg.spotify.com/color-lyrics/v2/track/0UHB9METy4VCXNgkcGqHqS?format=json&market=from_token",
    "binilyrics": "https://lyrics-api.binimum.org/?track=Bohemian%20Rhapsody&artist=Queen&duration=355",
    # Unison answers 404 for anything it has no lyrics for, and it has few:
    # this is a track it does have.
    "unison": "https://unison.boidu.dev/lyrics?song=Someone%20Like%20You&artist=Adele&duration=285",
    # "Tum Hi Ho" (id aRZbUYD7): one of the has_lyrics:true results, so this
    # actually exercises the lyrics.getLyrics call, not just search.
    "jiosaavn": "https://www.jiosaavn.com/api.php?__call=lyrics.getLyrics&_format=json&_marker=0&api_version=4&ctx=web6dot0&lyrics_id=aRZbUYD7",
    # core/lyrics_direct.py: each provider's first (search) step.
    "kugou": "https://mobilecdn.kugou.com/api/v3/search/song?format=json&keyword=Queen%20Bohemian%20Rhapsody&page=1&pagesize=1",
    "qq": "https://c.y.qq.com/soso/fcgi-bin/client_search_cp?w=Bohemian%20Rhapsody%20Queen&format=json&p=1&n=1&new_json=1",
    "genius": "https://genius.com/api/search/song?q=Bohemian%20Rhapsody%20Queen&per_page=1",
}


class HealthResult(NamedTuple):
    provider: str
    url: str
    method: str
    ok: bool
    latency: float
    detail: str


async def _probe(client: httpx.AsyncClient, name: str, url: str) -> HealthResult:
    started = time.perf_counter()
    try:
        response = await client.get(
            url,
            headers={"User-Agent": _UA, "Accept": "application/json,text/plain"},
            follow_redirects=True,
        )
        latency = (time.perf_counter() - started) * 1000
        if 200 <= response.status_code < 300:
            return HealthResult(
                name, url, "GET", True, latency, f"HTTP {response.status_code}"
            )
        return HealthResult(
            name, url, "GET", False, latency, f"HTTP {response.status_code}"
        )
    except httpx.TimeoutException:
        return HealthResult(name, url, "GET", False, -1, "timeout")
    except httpx.RequestError as exc:
        return HealthResult(name, url, "GET", False, -1, str(exc)[:40])
    except Exception as exc:
        return HealthResult(name, url, "GET", False, -1, str(exc)[:40])


async def run_health_check(
    services: list[str] | None = None,
    *,
    include_all_endpoints: bool = True,
) -> list[HealthResult]:
    """Check direct lyrics server reachability for configured services."""
    del include_all_endpoints

    from . import get_amazon_endpoint

    servers = dict(_SERVERS)
    amazon_url = get_amazon_endpoint("spotbye1")
    if amazon_url:
        servers["amazon"] = f"{amazon_url.rstrip('/')}/lyrics/QM5FT1600115"

    if services:
        servers = {name: url for name, url in servers.items() if name in services}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        results = await asyncio.gather(
            *(_probe(client, name, url) for name, url in servers.items())
        )
    return list(results)


# ---------------------------------------------------------------------------
# Extension health: each installed extension's own `serviceHealth` checks
# ---------------------------------------------------------------------------
#
# A Mornye-Extension manifest lists the endpoints it depends on:
#
#   "serviceHealth": [{"id": "zarz-api", "label": "Zarz API",
#                      "url": "https://api.zarz.moe/v1/health",
#                      "method": "GET", "serviceKey": "deezer",
#                      "timeoutMs": 4000, "required": true}]
#
# Without `serviceKey` the check passes on any 2xx answer. With it, the
# answer is a shared status page ({"services": {"deezer": {"ok": true}}})
# and the check is that service's own `ok` — the gateway can be up while
# the one service behind it is down. Several extensions share one URL, so
# each URL is fetched once per run.

_MAX_CHECK_TIMEOUT_S = 15.0


def _extension_checks(ext: Any) -> list[dict]:
    raw = (getattr(ext, "manifest", None) or {}).get("serviceHealth") or []
    if not isinstance(raw, list):
        return []
    checks = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        checks.append(entry)
    return checks


async def _fetch_status(
    client: httpx.AsyncClient, url: str, method: str, timeout_s: float
) -> tuple[bool, int | None, Any, float, str]:
    """(http_ok, status_code, parsed_json_or_None, latency_ms, error)."""
    started = time.perf_counter()
    try:
        response = await client.request(
            method,
            url,
            headers={"User-Agent": _UA, "Accept": "application/json,text/plain,*/*"},
            follow_redirects=True,
            timeout=timeout_s,
        )
    except httpx.TimeoutException:
        return False, None, None, -1, "timeout"
    except Exception as exc:
        return False, None, None, -1, str(exc)[:80]
    latency = (time.perf_counter() - started) * 1000
    payload = None
    try:
        payload = response.json()
    except Exception:
        payload = None
    ok = 200 <= response.status_code < 300
    return ok, response.status_code, payload, latency, ""


def _keyed_result(payload: Any, key: str) -> tuple[bool, str] | None:
    """A service's own verdict from a shared status page, if it has one."""
    if not isinstance(payload, dict):
        return None
    services = payload.get("services")
    if not isinstance(services, dict) or key not in services:
        return None
    entry = services[key]
    if not isinstance(entry, dict):
        return bool(entry), ""
    ok = bool(entry.get("ok"))
    detail = entry.get("error") or entry.get("detail") or ""
    status = entry.get("status")
    if not detail and status:
        detail = f"HTTP {status}"
    return ok, str(detail or "")


async def run_extension_health_check(
    names: list[str] | None = None,
    *,
    manager: Any = None,
    probe: bool = True,
) -> list[dict]:
    """Run the `serviceHealth` checks of the installed extensions.

    Returns one row per extension that declares checks, in display-name
    order: ``{"name", "display_name", "service", "ok", "checks": [{"id",
    "label", "url", "ok", "required", "latency", "detail"}]}``. An extension
    is ok when every required check passes (any check, if none is required).
    With ``probe=False`` nothing is fetched: the rows list what would be
    checked, with ``ok`` set to None.
    """
    if manager is None:
        from ..extensions.manager import ExtensionManager

        manager = ExtensionManager(auto_install_downloads=False)
    try:
        installed = manager.list_installed()
    except Exception:
        return []

    from ..extensions.catalog import canonical_service_name

    targets = []
    for ext in installed:
        if names and ext.name not in names:
            continue
        checks = _extension_checks(ext)
        if checks:
            targets.append((ext, checks))
    if not targets:
        return []

    if not probe:
        rows = [
            {
                "name": ext.name,
                "display_name": getattr(ext, "display_name", "") or ext.name,
                "service": canonical_service_name(ext.name) or ext.name,
                "ok": None,
                "checks": [
                    {
                        "id": str(c.get("id") or ""),
                        "label": str(c.get("label") or c.get("id") or c["url"]),
                        "url": str(c["url"]),
                        "ok": None,
                        "required": c.get("required", True) is not False,
                        "latency": -1,
                        "detail": "",
                    }
                    for c in checks
                ],
            }
            for ext, checks in targets
        ]
        rows.sort(key=lambda row: row["display_name"].lower())
        return rows

    # One request per (method, url), however many extensions ask for it.
    wanted: dict[tuple[str, str], float] = {}
    for _ext, checks in targets:
        for check in checks:
            key = (str(check.get("method") or "GET").upper(), str(check["url"]))
            try:
                timeout = float(check.get("timeoutMs") or 5000) / 1000
            except (TypeError, ValueError):
                timeout = 5.0
            wanted[key] = min(
                max(wanted.get(key, 0.0), timeout, 1.0), _MAX_CHECK_TIMEOUT_S
            )

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        keys = list(wanted)
        fetched = await asyncio.gather(
            *(
                _fetch_status(client, url, method, wanted[(method, url)])
                for method, url in keys
            )
        )
    responses = dict(zip(keys, fetched))

    rows = []
    for ext, checks in targets:
        results = []
        for check in checks:
            method = str(check.get("method") or "GET").upper()
            http_ok, status, payload, latency, error = responses[
                (method, str(check["url"]))
            ]
            ok, detail = http_ok, error or (f"HTTP {status}" if status else "")
            service_key = str(check.get("serviceKey") or "").strip()
            if service_key and status is not None:
                keyed = _keyed_result(payload, service_key)
                if keyed is not None:
                    ok, keyed_detail = keyed
                    detail = keyed_detail or ("OK" if ok else detail)
            results.append(
                {
                    "id": str(check.get("id") or ""),
                    "label": str(check.get("label") or check.get("id") or check["url"]),
                    "url": str(check["url"]),
                    "ok": ok,
                    "required": check.get("required", True) is not False,
                    "latency": round(latency) if latency >= 0 else -1,
                    "detail": detail,
                }
            )
        required = [r for r in results if r["required"]] or results
        rows.append(
            {
                "name": ext.name,
                "display_name": getattr(ext, "display_name", "") or ext.name,
                "service": canonical_service_name(ext.name) or ext.name,
                "ok": all(r["ok"] for r in required),
                "checks": results,
            }
        )
    rows.sort(key=lambda row: row["display_name"].lower())
    return rows


def print_extension_health_report(rows: list[dict]) -> None:
    """Print the extension checks, one line per check."""
    for row in rows:
        state = "OK" if row["ok"] else "FAIL"
        print(f"{row['display_name']} ({row['name']}): {state}")
        for check in row["checks"]:
            mark = "ok" if check["ok"] else "fail"
            print(f"    {check['label']}: {mark} ({check['detail']}) {check['url']}")


def print_health_report(
    results: list[HealthResult],
    *,
    show_urls: bool = True,
) -> None:
    """Print a compact health report for direct servers."""
    for result in results:
        suffix = f" {result.url}" if show_urls else ""
        state = "OK" if result.ok else "FAIL"
        print(f"{result.provider}: {state} ({result.detail}){suffix}")
