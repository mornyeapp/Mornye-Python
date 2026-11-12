"""Manual tag editing for a single local audio file.

The automatic path (`local_processor.retag_local_file_async`) replaces a file's
tags with a matched track's metadata. This is the other half: change exactly
the fields the user typed, keep every other tag, the cover and the lyrics as
they are, and put the original back if the write fails.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ErrorKind, MornyeError
from .tagger import (
    SUPPORTED_SUFFIXES,
    _write_tags_async,
    fit_cover,
    read_embedded_tags,
)

logger = logging.getLogger(__name__)

#: The fields the editor shows, in display order. Anything else a file carries
#: (ReplayGain, MusicBrainz ids, label…) is preserved untouched.
EDITABLE_FIELDS: tuple[str, ...] = (
    "TITLE",
    "ARTIST",
    "ALBUM",
    "ALBUMARTIST",
    "DATE",
    "GENRE",
    "TRACKNUMBER",
    "DISCNUMBER",
    "COMPOSER",
    "ISRC",
    "COPYRIGHT",
)


def _as_jpeg(data: bytes) -> bytes:
    """The writers label every embedded cover image/jpeg, so anything else
    (a PNG or WebP the user picked) is converted first."""
    if data[:2] == b"\xff\xd8":
        return data
    import io

    from PIL import Image

    with Image.open(io.BytesIO(data)) as img:
        out = io.BytesIO()
        img.convert("RGB").save(out, format="JPEG", quality=92)
    return out.getvalue()


def _clear_flac_pictures(path: Path) -> None:
    """mutagen's FLAC.delete() drops the comments but leaves the pictures, so
    a replaced or removed cover would otherwise survive next to the new one."""
    from mutagen.flac import FLAC

    audio = FLAC(str(path))
    audio.clear_pictures()
    audio.save()


@dataclass
class FileTags:
    path: str
    tags: dict[str, str] = field(default_factory=dict)
    lyrics: str = ""
    cover: bytes | None = None
    cover_mime: str = "image/jpeg"


def read_file_tags(path: str | Path) -> FileTags:
    """The tags, lyrics and cover of `path` as the editor will show them."""
    p = Path(path)
    if not p.is_file():
        raise MornyeError(ErrorKind.FILE_IO, f"File not found: {p}")
    if p.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise MornyeError(ErrorKind.FILE_IO, f"Unsupported file type: {p.suffix}")
    emb = read_embedded_tags(p, include_cover=True)
    return FileTags(
        path=str(p),
        tags=dict(emb.tags),
        lyrics=emb.lyrics or "",
        cover=emb.cover_data,
        cover_mime=emb.cover_mime or "image/jpeg",
    )


async def save_file_tags_async(
    path: str | Path,
    edits: dict[str, str],
    *,
    lyrics: str | None = None,
    cover: bytes | None = None,
    remove_cover: bool = False,
    backup: bool = True,
) -> None:
    """Writes `edits` over the tags `path` already has.

    An empty value removes that tag. `lyrics=None` keeps the embedded lyrics,
    `""` removes them. `cover` replaces the artwork; `remove_cover` drops it.
    """
    p = Path(path)
    current = await asyncio.to_thread(read_file_tags, p)

    tags = dict(current.tags)
    for key, value in (edits or {}).items():
        key = str(key).strip().upper()
        if not key:
            continue
        value = str(value).strip()
        if value:
            tags[key] = value
        else:
            tags.pop(key, None)

    new_lyrics = current.lyrics if lyrics is None else lyrics
    cover_data = current.cover
    if remove_cover:
        cover_data = None
    elif cover:
        try:
            cover = await asyncio.to_thread(_as_jpeg, cover)
        except Exception as exc:
            raise MornyeError(
                ErrorKind.FILE_IO, f"The cover image could not be read: {exc}"
            ) from exc
        fitted = await asyncio.to_thread(fit_cover, cover)
        if fitted is None:
            raise MornyeError(ErrorKind.FILE_IO, "The cover image is too large to embed")
        cover_data = fitted

    backup_path: Path | None = None
    if backup:
        backup_path = p.with_suffix(p.suffix + ".bak")
        n = 1
        while backup_path.exists():
            backup_path = p.with_suffix(f"{p.suffix}.bak.{n}")
            n += 1
        await asyncio.to_thread(shutil.copy2, p, backup_path)

    try:
        if p.suffix.lower() == ".flac":
            await asyncio.to_thread(_clear_flac_pictures, p)
        await _write_tags_async(
            p,
            tags,
            cover_data,
            new_lyrics or None,
            "",
            True,
            p.suffix.lower(),
            None,
        )
    except Exception as exc:
        logger.warning("[tag_editor] write failed for %s: %s", p.name, exc)
        if backup_path and backup_path.exists():
            await asyncio.to_thread(shutil.copy2, backup_path, p)
            await asyncio.to_thread(backup_path.unlink)
        if isinstance(exc, MornyeError):
            raise
        raise MornyeError(
            ErrorKind.FILE_IO, f"Failed to write tags to {p.name}: {exc}", cause=exc
        ) from exc

    if backup_path and backup_path.exists():
        try:
            await asyncio.to_thread(backup_path.unlink)
        except OSError:  # a stray .bak is harmless
            logger.debug("[tag_editor] could not remove %s", backup_path)
