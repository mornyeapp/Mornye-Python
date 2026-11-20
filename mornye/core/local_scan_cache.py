"""core/local_scan_cache.py — Fix Local Files results, kept between scans.

Scanning a folder in Fix Local Files reads every file's tags and looks each
one up on Spotify (and fingerprints the ones its tags cannot settle): on a
network share that is a long wait, and it used to be the same wait every
time. This keeps the result the list shows for each file (the payload of
LocalTaggingMixin._serialize_scan_entry) with the size and modification time
the file had when it was worked out. The next scan of the folder shows the
stored results at once and redoes only the files that are new, changed on
disk (a file fixed with Apply is one: writing tags changes it) or whose
result is older than MAX_AGE_S, since what Spotify answers can change.

Rows are keyed by path alone: they describe files. Every function swallows
database errors: without the cache Fix Local Files still works, it only
scans everything as it used to.
"""

from __future__ import annotations

import base64
import binascii
import io
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import db

logger = logging.getLogger(__name__)

#: A stored result older than this is worked out again (30 days).
MAX_AGE_S = 30 * 24 * 3600

#: Longest side of the cover the list shows, in pixels (it is drawn at 40).
COVER_PX = 96


@dataclass(frozen=True)
class Stored:
    mtime_ns: int
    size: int
    entry: dict[str, Any]
    scanned_at: float

    def matches(self, mtime_ns: int, size: int, now: float | None = None) -> bool:
        """Whether this result still holds for the file as it is on disk."""
        age = (time.time() if now is None else now) - self.scanned_at
        return self.mtime_ns == mtime_ns and self.size == size and age < MAX_AGE_S


def _under(path: str, root: Path) -> bool:
    candidate = Path(path)
    return candidate == root or candidate.is_relative_to(root)


def stored(root: str) -> dict[str, Stored]:
    """The stored results of the files under `root` (or of `root` itself
    when it is a file), by path. {} on error."""
    base = Path(root)
    try:
        rows = (
            db.connection()
            .execute(
                "SELECT path, mtime_ns, size, entry, scanned_at FROM local_scan_cache"
            )
            .fetchall()
        )
    except Exception:
        logger.debug("[local_scan_cache] could not read the cache", exc_info=True)
        return {}
    out: dict[str, Stored] = {}
    for row in rows:
        if not _under(row["path"], base):
            continue
        entry = db.loads(row["entry"], None)
        if isinstance(entry, dict):
            out[row["path"]] = Stored(
                int(row["mtime_ns"]),
                int(row["size"]),
                entry,
                float(row["scanned_at"]),
            )
    return out


def store(entries: Iterable[tuple[str, int, int, dict[str, Any]]]) -> None:
    """Saves `(path, mtime_ns, size, entry)` for each file just worked out."""
    now = time.time()
    values = [
        (path, int(mtime_ns), int(size), db.dumps(entry), now)
        for path, mtime_ns, size, entry in entries
    ]
    if not values:
        return
    try:
        with db.transaction() as conn:
            conn.executemany(
                "INSERT INTO local_scan_cache (path, mtime_ns, size, entry, scanned_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(path) DO UPDATE SET mtime_ns = excluded.mtime_ns,"
                " size = excluded.size, entry = excluded.entry,"
                " scanned_at = excluded.scanned_at",
                values,
            )
    except Exception:
        logger.debug(
            "[local_scan_cache] could not store %d result(s)",
            len(values),
            exc_info=True,
        )


def forget(paths: Iterable[str]) -> None:
    """Drops the results of files that are no longer on disk."""
    values = [(p,) for p in paths]
    if not values:
        return
    try:
        with db.transaction() as conn:
            conn.executemany("DELETE FROM local_scan_cache WHERE path = ?", values)
    except Exception:
        logger.debug(
            "[local_scan_cache] could not forget %d result(s)",
            len(values),
            exc_info=True,
        )


def cover_thumbnail(data_uri: str, size: int = COVER_PX) -> str:
    """A file's embedded cover, as the list shows it: a small JPEG data URI.

    The scanner reads covers at full size — often hundreds of KB each — and
    the list draws them at 40 px. Sent and stored at full size, a folder of a
    few thousand files was hundreds of MB in one push. "" when there is no
    cover or it cannot be decoded (the list then shows its placeholder).
    """
    if not data_uri or "," not in data_uri:
        return ""
    try:
        raw = base64.b64decode(data_uri.split(",", 1)[1], validate=False)
    except (binascii.Error, ValueError):
        return ""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as source:
            img = source.convert("RGB")
            img.thumbnail((size, size))
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=82)
    except Exception:
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode("ascii")
