"""track_quality.py — What quality Qobuz and TIDAL have for one track.

Asked on demand, from the track list. The answer is the ``quality`` object
the qobuz-web and tidal-web extensions return from ``checkAvailability()``;
an extension that returns none reads as "unknown", never as low quality.
Amazon Music is not asked: it only says what a stream is once the stream
has been requested, which spends API quota.

A check costs nothing of that kind. The extension runs without a signed
session. A Spotify link is first resolved to the target service's native
link, then the extension checks that service's public page: no download
ticket, no verification window. The price is that Qobuz can miss a track a
download would find, which reads as "unknown".

Known answers are cached on disk, so checking a track again costs no
request. "Unknown", "not available" and errors are not cached: each can be
different the next time (an updated extension, a service back up).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from .atomic_io import write_json_atomic
from .extension_enrichment import _has_javascript, _installed_extensions, _provider
from .models import TrackMetadata
from .paths import cache_path

logger = logging.getLogger(__name__)

#: Services whose extensions report a quality, in the order shown.
SERVICES = {"qobuz": "Qobuz", "tidal": "TIDAL"}

CACHE_FILE_NAME = "track_quality.json"
#: A catalogue does gain Hi-Res masters, so an answer is not kept for ever.
CACHE_TTL_S = 7 * 24 * 3600
CACHE_MAX_ENTRIES = 5000

_cache_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def _load_cache() -> dict[str, dict]:
    try:
        data = json.loads(cache_path(CACHE_FILE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _cached(key: str, now: float) -> dict | None:
    with _cache_lock:
        entry = _load_cache().get(key)
    if not isinstance(entry, dict) or not isinstance(entry.get("result"), dict):
        return None
    try:
        age = now - float(entry.get("at", 0))
    except (TypeError, ValueError):
        return None
    return entry["result"] if 0 <= age < CACHE_TTL_S else None


def _store(key: str, result: dict, now: float) -> None:
    with _cache_lock:
        data = {
            k: v
            for k, v in _load_cache().items()
            if isinstance(v, dict) and now - float(v.get("at", 0) or 0) < CACHE_TTL_S
        }
        data[key] = {"at": now, "result": result}
        if len(data) > CACHE_MAX_ENTRIES:
            newest = sorted(data, key=lambda k: data[k]["at"], reverse=True)
            data = {k: data[k] for k in newest[:CACHE_MAX_ENTRIES]}
        try:
            write_json_atomic(cache_path(CACHE_FILE_NAME), data)
        except OSError:
            logger.debug("[track-quality] could not write the cache", exc_info=True)


def _track_key(metadata: TrackMetadata) -> str:
    """The same for the same row, before anything has been looked up."""
    if metadata.isrc:
        return f"isrc:{metadata.isrc.upper()}"
    if metadata.id:
        return f"id:{metadata.id}"
    return f"meta:{metadata.title}|{metadata.artists}|{metadata.duration_ms}".casefold()


# ---------------------------------------------------------------------------
# Reading an answer
# ---------------------------------------------------------------------------


def _number(value: Any) -> float:
    try:
        return max(float(value or 0), 0.0)
    except (TypeError, ValueError):
        return 0.0


def _extension_quality(service: str, track: Any) -> dict | None:
    """Translate a service extension's native ``audio_quality`` label."""
    if not isinstance(track, dict):
        return None
    label = str(track.get("audio_quality") or "").strip()
    if service == "qobuz":
        match = re.fullmatch(
            r"(\d+)\s*bit(?:\s*/\s*(\d+(?:\.\d+)?)\s*kHz)?",
            label,
            re.IGNORECASE,
        )
        if not match:
            return None
        depth = int(match.group(1))
        rate = float(match.group(2) or 0)
        return {
            "hires": depth > 16,
            "max_bit_depth": depth,
            "max_sampling_rate": rate,
        }

    normalized = re.sub(r"\s+", "", label).upper()
    if normalized == "24BIT":
        return {"hires": True, "max_bit_depth": 24}
    if normalized == "MQA":
        return {"hires": True, "max_quality": "MQA"}
    if normalized in {"HI_RES", "HI_RES_LOSSLESS"}:
        return {"hires": True, "max_quality": "HI_RES_LOSSLESS"}
    if normalized == "16BIT/44.1KHZ":
        return {"hires": False, "max_quality": "LOSSLESS"}
    if normalized == "320KBPS":
        return {"hires": False, "max_quality": "HIGH"}
    if normalized == "96KBPS":
        return {"hires": False, "max_quality": "LOW"}
    return None


def _result(service: str, state: str, text: str, detail: str = "") -> dict:
    return {
        "service": service,
        "state": state,
        "label": f"{SERVICES[service]} {text}",
        "detail": detail,
    }


#: How the extensions say they looked and found nothing.
_NO_MATCH = re.compile(r"no verified .* match|no longer matched", re.IGNORECASE)
#: What ``session.signedFetch`` answers without a signed session. Qobuz passes
#: it on when its public sources came back empty or failed.
_NO_SESSION = "no session_handler configured"


def describe(service: str, answer: Any) -> dict:
    """``{"service", "state", "label", "detail"}`` for one checkAvailability()
    answer. `state` is hires, standard, unknown, unavailable or error."""
    answer = answer if isinstance(answer, dict) else {}
    quality = answer.get("quality")
    if not isinstance(quality, dict):
        if not answer.get("available"):
            reason = str(answer.get("reason") or "")
            if _NO_MATCH.search(reason):
                return _result(service, "unavailable", "not found", reason)
            if _NO_SESSION in reason:
                return _result(
                    service,
                    "unknown",
                    "unknown",
                    "Not found on the public pages. A download may still find it.",
                )
            return _result(service, "error", "check failed", reason)
        return _result(
            service,
            "unknown",
            "unknown",
            "The extension reports no quality. Check for an update.",
        )

    hires = quality.get("hires")
    state = "hires" if hires is True else "standard" if hires is False else "unknown"

    if service in {"qobuz", "tidal"}:
        depth = int(_number(quality.get("max_bit_depth")))
        rate = _number(quality.get("max_sampling_rate"))
        if rate >= 1000:  # Hz rather than kHz
            rate /= 1000
        if depth:
            state = "hires" if depth > 16 else "standard"
            text = f"{depth}-bit"
            if rate:
                text += f"/{rate:g} kHz"
            return _result(service, state, text)

    if state == "hires":
        if (
            service == "tidal"
            and str(quality.get("max_quality") or "").upper() == "MQA"
        ):
            return _result(service, state, "MQA")
        return _result(service, state, "Hi-Res")
    if state == "standard":
        # TIDAL names the tier; a name it does not use is not guessed at.
        tier = str(quality.get("max_quality") or "").upper()
        names = {"LOSSLESS": "Lossless", "HIGH": "High", "LOW": "Low"}
        return _result(service, state, names.get(tier, "no Hi-Res"))
    return _result(service, state, "unknown")


# ---------------------------------------------------------------------------
# Asking
# ---------------------------------------------------------------------------


def _service_of(ext: Any) -> str:
    return re.sub(r"-(?:web|py)$", "", str(ext.name).lower())


def quality_extensions(manager: Any = None) -> list[tuple[str, Any]]:
    """``[(service, extension)]``, one per service that has an extension."""
    found: dict[str, Any] = {}
    for ext in _installed_extensions(manager):
        service = _service_of(ext)
        if service in SERVICES and _has_javascript(ext):
            found.setdefault(service, ext)
    return [(service, found[service]) for service in SERVICES if service in found]


async def _with_isrc(metadata: TrackMetadata) -> TrackMetadata:
    """A Spotify track list often has no ISRC; the download looks it up
    before matching, so the check does too."""
    if metadata.isrc or not re.fullmatch(r"[A-Za-z0-9]{22}", metadata.id):
        return metadata
    try:
        from .http import AsyncHttpClient
        from .isrc_helper import IsrcHelper

        isrc = await IsrcHelper(AsyncHttpClient("isrc")).get_isrc_async(metadata.id)
    except Exception as exc:
        logger.debug("[track-quality] no ISRC for %s: %s", metadata.id, exc)
        return metadata
    return metadata.model_copy(update={"isrc": isrc}) if isrc else metadata


async def _ask(
    service: str, ext: Any, metadata: TrackMetadata, provider_factory: Any
) -> dict:
    service_name = SERVICES[service]
    try:
        provider = await asyncio.to_thread(
            _provider, ext.name, provider_factory, signed_requests=False
        )
        # Let the service extension resolve the Spotify metadata against its
        # own catalogue; external cross-catalogue resolvers often lack recent
        # releases and cannot report the service's audio quality.
        options = await provider._provider_match_options(metadata, resolve_links=False)
        resolved_track: Any = {}
        try:
            resolved_track = await asyncio.to_thread(
                provider.enrich_track,
                {
                    "name": metadata.title,
                    "artists": metadata.artists,
                    "isrc": metadata.isrc,
                    "album_name": metadata.album,
                    "duration_ms": metadata.duration_ms,
                },
                {},
            )
        except Exception as exc:
            logger.warning(
                "[track-quality] %s extension resolve failed: %s",
                service_name,
                exc,
            )

        native_id = (
            str((resolved_track or {}).get(f"{service}_id") or "").strip()
            if isinstance(resolved_track, dict)
            else ""
        )
        if native_id and re.fullmatch(r"\d+", native_id):
            options[f"{service}_id"] = native_id
            native_url = str((resolved_track or {}).get("external_urls") or "").strip()
            if native_url:
                options[f"{service}_url"] = native_url

        # A row that is already this service's track needs no search.
        link = metadata.external_url
        host = (urlsplit(link).hostname or "").lower()
        if host == f"{service}.com" or host.endswith(f".{service}.com"):
            source_id = provider._provider_track_id(service, link)
            if source_id:
                options[f"{service}_id"] = source_id
        answer = await asyncio.to_thread(
            provider.check_availability,
            isrc=metadata.isrc,
            track_name=metadata.title,
            artist_name=metadata.artists,
            options=options,
        )
        native_quality = _extension_quality(service, resolved_track)
        if (
            isinstance(answer, dict)
            and answer.get("available")
            and not isinstance(answer.get("quality"), dict)
            and native_quality
        ):
            answer = {**answer, "quality": native_quality}
    except Exception as exc:
        logger.warning("[track-quality] %s check failed: %s", service_name, exc)
        return _result(service, "error", "check failed", str(exc))
    return describe(service, answer)


async def check_track_quality_async(
    track: dict, *, manager: Any = None, provider_factory: Any = None
) -> dict:
    """``{"status": "ok", "results": [...]}`` for a row of the track list,
    or ``{"status": "error", "error"}`` when there is nothing to ask."""
    title = str(track.get("title") or track.get("name") or "").strip()
    artists = str(track.get("artists") or track.get("artist") or "").strip()
    if not title:
        logger.warning("[track-quality] check requested for a track without a title")
        return {"status": "error", "error": "The track has no title."}
    metadata = TrackMetadata(
        id=str(track.get("id") or ""),
        title=title,
        artists=artists,
        album=str(track.get("album") or ""),
        album_artist=str(track.get("album_artist") or artists),
        isrc=str(track.get("isrc") or "").strip(),
        duration_ms=int(_number(track.get("duration_ms"))),
        external_url=str(track.get("external_url") or ""),
    )
    extensions = quality_extensions(manager)
    if not extensions:
        logger.warning("[track-quality] no Qobuz or TIDAL extension is available")
        return {
            "status": "error",
            "error": "Install the Qobuz or TIDAL extension to check qualities.",
        }

    now = time.time()
    track_key = _track_key(metadata)
    # An updated extension may read the catalogue differently.
    keys = {
        service: f"{ext.name}@{ext.version}|{track_key}" for service, ext in extensions
    }
    results = {service: _cached(keys[service], now) for service, _ in extensions}
    missing = [(service, ext) for service, ext in extensions if not results[service]]
    if missing:
        metadata = await _with_isrc(metadata)
        answers = await asyncio.gather(
            *(
                _ask(service, ext, metadata, provider_factory)
                for service, ext in missing
            )
        )
        for (service, _), result in zip(missing, answers, strict=True):
            results[service] = result
            if result["state"] in ("hires", "standard"):
                _store(keys[service], result, now)
    return {"status": "ok", "results": [results[s] for s, _ in extensions]}
