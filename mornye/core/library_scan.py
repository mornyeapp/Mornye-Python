"""A light scan of a music folder, for the Player.

`scan_local` (Fix Local Files) identifies every file against Spotify, which is
what makes it slow. The Player only needs what is already in the files: title,
artist, album, track number, year, duration and format. No cover is read here —
thumbnails are fetched on demand for the rows that are actually on screen.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .tagger import SUPPORTED_SUFFIXES, read_embedded_tags

logger = logging.getLogger(__name__)

THUMB_PX = 96


def iter_audio_files(root: str | Path) -> Iterator[Path]:
    """Audio files under `root` (or `root` itself if it is a file), sorted, with
    hidden folders and backup files left out."""
    base = Path(root)
    if base.is_file():
        if base.suffix.lower() in SUPPORTED_SUFFIXES:
            yield base
        return
    if not base.is_dir():
        return
    for path in sorted(base.rglob("*"), key=lambda p: str(p).lower()):
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        if any(part.startswith(".") for part in path.relative_to(base).parts):
            continue
        if path.is_file():
            yield path


def _duration_s(path: Path) -> float:
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(str(path))
        return round(float(getattr(getattr(audio, "info", None), "length", 0) or 0), 1)
    except Exception:
        return 0.0


def _num(value: str) -> int:
    head = (value or "").split("/")[0].strip()
    return int(head) if head.isdigit() else 0


def read_track_row(path: Path) -> dict[str, Any]:
    """The row the Player shows for `path`. Never raises: an unreadable file
    still gets a row, named after the file."""
    tags: dict[str, str] = {}
    try:
        tags = read_embedded_tags(path, include_cover=False).tags
    except Exception as exc:  # a broken tag block must not drop the file
        logger.debug("[library] tags unreadable for %s: %s", path.name, exc)
    return {
        "path": str(path),
        "title": tags.get("TITLE") or path.stem,
        "artist": tags.get("ARTIST", ""),
        "album": tags.get("ALBUM", ""),
        "album_artist": tags.get("ALBUMARTIST", ""),
        "track": _num(tags.get("TRACKNUMBER", "")),
        "disc": _num(tags.get("DISCNUMBER", "")),
        "year": (tags.get("DATE") or tags.get("YEAR") or "")[:4],
        "duration_s": _duration_s(path),
        "format": path.suffix.lstrip(".").upper(),
    }


def thumbnail_jpeg(path: str | Path, size: int = THUMB_PX) -> bytes | None:
    """The embedded cover of `path` as a small JPEG, or None."""
    emb = read_embedded_tags(Path(path), include_cover=True)
    if not emb.cover_data:
        return None
    from PIL import Image

    with Image.open(io.BytesIO(emb.cover_data)) as source:
        img = source.convert("RGB")
        img.thumbnail((size, size))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=82)
    return out.getvalue()
