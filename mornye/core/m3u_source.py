"""core/m3u_source.py — an M3U/M3U8 playlist as an input source.

The mobile app imports M3U playlists; here they go through exactly the same
path as a CSV (core/csv_source.py): each entry becomes a `CsvRow`, and
`resolve_rows()` keeps the links, searches the rest, and reports what it
could not match instead of guessing. So `mornye --csv playlist.m3u8 DEST`,
the GUI's CSV import and the REST endpoint all accept one.

What an entry can carry, best first:

- a link (a streaming-service URL or `spotify:` URI) — kept as is;
- a local file that still exists — its own tags (title, artist, album,
  ISRC, duration), read with mutagen;
- the `#EXTINF:<seconds>,<Artist> - <Title>` line before it, plus the
  extended `#EXTART:` / `#EXTALB:` directives;
- failing all that, the file name: "Artist - Title.flac", with a leading
  track number ("03 - Title") dropped.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path, PureWindowsPath

from .csv_source import CsvDocument, CsvRow, normalize_link
from .errors import ErrorKind, MornyeError
from .isrc_utils import normalize_isrc

logger = logging.getLogger(__name__)

_EXTINF_RE = re.compile(r"^#EXTINF:\s*(-?\d+(?:\.\d+)?)?[^,]*,(.*)$", re.IGNORECASE)
_TRACK_NUMBER_RE = re.compile(r"^\s*\d{1,3}\s*[-._)\s]\s*")
_M3U_SUFFIXES = (".m3u", ".m3u8")


def looks_like_m3u(text: str, name: str = "") -> bool:
    if (name or "").lower().endswith(_M3U_SUFFIXES):
        return True
    head = (text or "").lstrip("﻿").lstrip()
    return head.upper().startswith("#EXTM3U") or "\n#EXTINF:" in (text or "")[:4096]


def _split_display(display: str) -> tuple[str, str]:
    """(artist, title) from "Artist - Title"; ("", text) without a dash."""
    text = (display or "").strip()
    if " - " in text:
        artist, title = text.split(" - ", 1)
        return artist.strip(), title.strip()
    return "", text


def _from_filename(location: str) -> tuple[str, str]:
    path = PureWindowsPath(location) if "\\" in location else Path(location)
    stem = path.stem
    stem = _TRACK_NUMBER_RE.sub("", stem, count=1) or stem
    return _split_display(stem)


def _first(tags, key: str) -> str:
    try:
        value = tags.get(key)
    except Exception:
        return ""
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "").strip()


def _read_local_tags(path: Path) -> dict[str, object]:
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(str(path), easy=True)
    except Exception as exc:
        logger.debug("[m3u] could not read tags of %s: %s", path, exc)
        return {}
    if audio is None:
        return {}
    tags = audio.tags or {}
    info = getattr(audio, "info", None)
    length = getattr(info, "length", 0) or 0
    return {
        "title": _first(tags, "title"),
        "artist": _first(tags, "artist"),
        "album": _first(tags, "album"),
        "isrc": _first(tags, "isrc"),
        "duration_ms": int(length * 1000),
    }


def _local_path(location: str, base_dir: Path | None) -> Path | None:
    if location.lower().startswith("file://"):
        from urllib.parse import unquote, urlsplit

        location = unquote(urlsplit(location).path)
    path = Path(location).expanduser()
    if not path.is_absolute():
        if base_dir is None:
            return None
        path = base_dir / path
    try:
        return path if path.is_file() else None
    except OSError:
        return None


def read_m3u_text(
    text: str,
    *,
    name: str = "",
    base_dir: str | Path | None = None,
) -> CsvDocument:
    """Parses an M3U/M3U8 playlist into the same document a CSV gives.

    `base_dir` resolves relative entries; when omitted it is the folder of
    `name`, if `name` is a real path. Contents posted by the GUI have no
    folder, so their relative entries fall back to #EXTINF and file names.
    """
    label = name or "(m3u)"
    if base_dir is None and name:
        parent = Path(name).expanduser().parent
        base_dir = parent if str(parent) not in ("", ".") and parent.is_dir() else None
    base = Path(base_dir) if base_dir else None

    rows: list[CsvRow] = []
    ignored: list[int] = []
    pending_inf: tuple[int, str, str] | None = None  # (duration_ms, artist, title)
    ext_artist = ext_album = ""

    for number, raw in enumerate((text or "").lstrip("﻿").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            upper = line.upper()
            m = _EXTINF_RE.match(line)
            if m:
                seconds = float(m.group(1)) if m.group(1) else 0.0
                artist, title = _split_display(m.group(2))
                pending_inf = (int(seconds * 1000) if seconds > 0 else 0, artist, title)
            elif upper.startswith("#EXTART:"):
                ext_artist = line.split(":", 1)[1].strip()
            elif upper.startswith("#EXTALB:"):
                ext_album = line.split(":", 1)[1].strip()
            continue

        url = ""
        if line.lower().startswith(("http://", "https://", "spotify:")):
            url = normalize_link(line)
        title = artist = album = isrc = ""
        duration_ms = 0

        if not url:
            local = _local_path(line, base)
            if local is not None:
                tags = _read_local_tags(local)
                title = str(tags.get("title") or "")
                artist = str(tags.get("artist") or "")
                album = str(tags.get("album") or "")
                isrc = normalize_isrc(str(tags.get("isrc") or ""))
                duration_ms = int(tags.get("duration_ms") or 0)

        if pending_inf is not None:
            inf_ms, inf_artist, inf_title = pending_inf
            duration_ms = duration_ms or inf_ms
            artist = artist or inf_artist
            title = title or inf_title
        if not url and not title:
            artist_guess, title_guess = _from_filename(line)
            artist = artist or artist_guess
            title = title_guess
        artist = artist or ext_artist
        album = album or ext_album
        pending_inf = None

        row = CsvRow(
            line=number,
            url=url,
            isrc=isrc,
            title=title,
            artist=artist,
            album=album,
            duration_ms=duration_ms,
        )
        if row.url or row.isrc or row.title:
            rows.append(row)
        else:
            ignored.append(number)

    if not rows:
        raise MornyeError(
            ErrorKind.PARSE_ERROR,
            f"{label}: no track found in the playlist.",
        )
    return CsvDocument(
        path=name,
        delimiter="",
        has_header=False,
        columns={},
        rows=tuple(rows),
        ignored_lines=tuple(ignored),
    )
