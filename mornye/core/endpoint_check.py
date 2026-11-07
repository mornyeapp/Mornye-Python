"""Check that a self-hosted download endpoint answers like it should.

Backs the "Test" buttons next to the custom endpoints (GUI and web):
tidal-web expects a hifi-api compatible server, qobuz-web a qobuz-rest-api
compatible one. Each check makes two GET requests: the server's status
route (``/``), then one that needs a working upstream account (``/info/``
for Tidal, ``/me`` for Qobuz) so a server that is up but signed out is not
reported as working.

The requests go only to the URL the user typed, with a short timeout and no
redirects, and only the fields shown to the user are read from the answer.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

#: A track hifi-api's own README uses as its example.
TIDAL_PROBE_TRACK_ID = "144371283"
TIMEOUT_S = 8.0
SERVICES = ("tidal", "qobuz")


def _result(ok: bool, message: str, **details: Any) -> dict:
    return {"ok": ok, "message": message, "details": details}


def _json(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def normalize_base_url(url: str) -> str | None:
    """The base URL without query, fragment or trailing slash, or None if it
    is not an http(s) URL with a host and no credentials."""
    text = str(url or "").strip()
    try:
        parsed = urlparse(text)
        host = parsed.hostname
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not host:
        return None
    if parsed.username or parsed.password:
        return None
    return parsed._replace(query="", fragment="").geturl().rstrip("/")


def check_custom_endpoint(
    service: str,
    url: str,
    api_key: str = "",
    client: httpx.Client | None = None,
) -> dict:
    """Probe ``url`` as the custom endpoint of ``service`` ("tidal"/"qobuz").

    Returns ``{"ok": bool, "message": str, "details": {...}}``; never raises.
    """
    service = str(service or "").strip().lower()
    if service not in SERVICES:
        return _result(False, f"Unknown service '{service}'.")
    base = normalize_base_url(url)
    if base is None:
        return _result(False, "Enter an http:// or https:// URL first.")

    own_client = client is None
    http = client or httpx.Client(timeout=TIMEOUT_S, follow_redirects=False)
    try:
        if service == "tidal":
            return _check_tidal(http, base)
        return _check_qobuz(http, base, str(api_key or "").strip())
    except httpx.TimeoutException:
        return _result(False, f"No answer from {base} within {TIMEOUT_S:.0f} s.")
    except httpx.HTTPError as e:
        return _result(False, f"Could not reach {base}: {e}")
    finally:
        if own_client:
            http.close()


def _check_tidal(http: httpx.Client, base: str) -> dict:
    root = http.get(base + "/", headers={"Accept": "application/json"})
    info = _json(root)
    if root.status_code != 200 or "version" not in info:
        return _result(
            False,
            f"{base} answered HTTP {root.status_code}, not like a hifi-api server.",
        )
    version = str(info.get("version") or "?")
    track = http.get(
        base + "/info/",
        params={"id": TIDAL_PROBE_TRACK_ID},
        headers={"Accept": "application/json"},
    )
    if track.status_code != 200 or not isinstance(_json(track).get("data"), dict):
        return _result(
            False,
            f"hifi-api {version} is reachable, but a track lookup failed "
            f"(HTTP {track.status_code}). Check the server's Tidal login.",
            version=version,
        )
    return _result(True, f"hifi-api {version} works.", version=version)


def _check_qobuz(http: httpx.Client, base: str, api_key: str) -> dict:
    root = http.get(base + "/", headers={"Accept": "application/json"})
    info = _json(root)
    if root.status_code != 200 or "status" not in info:
        return _result(
            False,
            f"{base} answered HTTP {root.status_code}, not like a qobuz-rest-api server.",
        )
    version = str(info.get("version") or "?")
    headers = {"Accept": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    me = http.get(base + "/me", headers=headers)
    if me.status_code in (401, 403):
        needed = "is missing" if not api_key else "was refused"
        return _result(
            False,
            f"qobuz-rest-api {version} is reachable, but the API key {needed}.",
            version=version,
        )
    account = _json(me)
    if me.status_code != 200 or not account:
        return _result(
            False,
            f"qobuz-rest-api {version} is reachable, but /me answered HTTP "
            f"{me.status_code}. Check the server's Qobuz token.",
            version=version,
        )
    plan = str(account.get("plan") or "").strip()
    who = str(account.get("display") or account.get("login") or "").strip()
    signed_in = ""
    if who:
        signed_in = f" Signed in as {who}" + (f" ({plan})" if plan else "") + "."
    return _result(
        True, f"qobuz-rest-api {version} works.{signed_in}", version=version, plan=plan
    )
