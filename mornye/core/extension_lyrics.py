"""Lyrics from extensions that declare ``lyrics_provider``.

A Mornye-Extension of type ``lyrics_provider`` exports
``fetchLyrics(trackName, artistName, albumName, durationSec)`` and answers in
the mobile runtime's shape::

    {"lines": [{"startTimeMs", "endTimeMs", "words"}, ...],
     "syncType": "LINE_SYNCED" | "UNSYNCED", "plainLyrics": "...",
     "instrumental": false, "provider": "Jellyfin"}

Nothing called it: the providers in core/lyrics.py were all built in. Now
each such extension is a provider of its own, named ``ext:<name>`` — the
same prefix the download path uses — so it can be ranked among the others
in every interface's provider list.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from .extension_enrichment import _has_javascript, _installed_extensions, _provider

logger = logging.getLogger(__name__)

PREFIX = "ext:"

_FETCH_LYRICS_RE = re.compile(r"\bfetchLyrics\b")
_implements_cache: dict[str, tuple[float, bool]] = {}


def _implements_fetch_lyrics(ext: Any) -> bool:
    """Read from the source, like enrichTrack: asking by calling would start
    a Node process per extension just to hear "No method"."""
    try:
        path = Path(ext.index_js)
        mtime = path.stat().st_mtime
    except (OSError, AttributeError):
        return False
    key = str(path)
    cached = _implements_cache.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        found = bool(
            _FETCH_LYRICS_RE.search(path.read_text(encoding="utf-8", errors="replace"))
        )
    except OSError:
        return False
    _implements_cache[key] = (mtime, found)
    return found


def is_lyrics_extension(ext: Any) -> bool:
    return (
        _has_javascript(ext)
        and "lyrics_provider" in (getattr(ext, "types", None) or [])
        and _implements_fetch_lyrics(ext)
    )


def lyrics_extensions(manager: Any = None) -> list[dict[str, str]]:
    """``[{"id": "ext:jellyfin", "name": "jellyfin", "label": "Jellyfin"}]``."""
    rows = [
        {
            "id": PREFIX + ext.name,
            "name": ext.name,
            "label": getattr(ext, "display_name", "") or ext.name,
        }
        for ext in _installed_extensions(manager)
        if is_lyrics_extension(ext)
    ]
    rows.sort(key=lambda row: row["label"].lower())
    return rows


def _lrc_stamp(ms: Any) -> str:
    try:
        total = max(0, int(ms))
    except (TypeError, ValueError):
        total = 0
    minutes, rest = divmod(total, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"[{minutes:02d}:{seconds:02d}.{millis // 10:02d}]"


def result_to_lrc(result: Any) -> str:
    """The extension's answer as LRC text (or plain text when unsynced)."""
    if isinstance(result, str):
        return result.strip()
    if not isinstance(result, dict) or result.get("instrumental") is True:
        return ""
    lines = result.get("lines")
    if isinstance(lines, list) and lines and result.get("syncType") != "UNSYNCED":
        out = []
        for line in lines:
            if not isinstance(line, dict):
                continue
            words = str(line.get("words") or "").strip()
            out.append(f"{_lrc_stamp(line.get('startTimeMs'))}{words}")
        text = "\n".join(out).strip()
        if text:
            return text
    plain = result.get("plainLyrics")
    if isinstance(plain, str) and plain.strip():
        return plain.strip()
    if isinstance(lines, list):
        return "\n".join(
            str(line.get("words") or "") for line in lines if isinstance(line, dict)
        ).strip()
    return ""


async def fetch_extension_lyrics(
    provider_id: str,
    track_name: str,
    artist_name: str,
    album_name: str = "",
    duration_s: int = 0,
    *,
    provider_factory: Any = None,
) -> str:
    """Ask one extension; ``""`` when it has nothing or fails."""
    name = provider_id[len(PREFIX) :] if provider_id.startswith(PREFIX) else ""
    if not name:
        return ""

    def call() -> Any:
        return _provider(name, provider_factory)._call(
            "fetchLyrics", track_name, artist_name, album_name, int(duration_s or 0)
        )

    try:
        result = await asyncio.to_thread(call)
    except Exception as exc:
        logger.debug("[lyrics/%s] fetchLyrics failed: %s", provider_id, exc)
        return ""
    return result_to_lrc(result)
