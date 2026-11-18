"""core/player_library.py — the Player's library, kept between visits.

The Player used to read the tags of every file in its folders each time it
opened. This keeps the row it shows for each file (library_scan.read_track_row)
with the size and modification time the file had when it was read. Opening
the Player then shows the stored rows at once, and the scan that follows
re-reads only the files that are new or changed, and drops the ones that are
gone (see LibraryToolsMixin._scan_music_library_thread).

Rows are keyed by path alone, shared by every account: they describe files,
and which files an account may see is decided before anything is read here.
Every function below swallows database errors: without the cache the Player
still works, it only scans everything as it used to.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import db

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Stored:
    mtime_ns: int
    size: int
    row: dict[str, Any]

    def matches(self, mtime_ns: int, size: int) -> bool:
        """Whether the file is still the one this row was read from."""
        return self.mtime_ns == mtime_ns and self.size == size


def _under(path: str, roots: list[Path]) -> bool:
    candidate = Path(path)
    return any(candidate == root or candidate.is_relative_to(root) for root in roots)


def stored_rows(roots: Iterable[str]) -> dict[str, Stored]:
    """The stored rows of the files under `roots`, by path. {} on error."""
    bases = [Path(r) for r in roots]
    try:
        rows = (
            db.connection()
            .execute("SELECT path, mtime_ns, size, row FROM player_library")
            .fetchall()
        )
    except Exception:
        logger.debug("[player_library] could not read the library", exc_info=True)
        return {}
    out: dict[str, Stored] = {}
    for row in rows:
        if not _under(row["path"], bases):
            continue
        data = db.loads(row["row"], None)
        if isinstance(data, dict):
            out[row["path"]] = Stored(int(row["mtime_ns"]), int(row["size"]), data)
    return out


def store(entries: Iterable[tuple[str, int, int, dict[str, Any]]]) -> None:
    """Saves `(path, mtime_ns, size, row)` for each file just read."""
    now = time.time()
    values = [
        (path, int(mtime_ns), int(size), db.dumps(row), now)
        for path, mtime_ns, size, row in entries
    ]
    if not values:
        return
    try:
        with db.transaction() as conn:
            conn.executemany(
                "INSERT INTO player_library (path, mtime_ns, size, row, scanned_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(path) DO UPDATE SET mtime_ns = excluded.mtime_ns,"
                " size = excluded.size, row = excluded.row,"
                " scanned_at = excluded.scanned_at",
                values,
            )
    except Exception:
        logger.debug(
            "[player_library] could not store %d row(s)", len(values), exc_info=True
        )


def forget(paths: Iterable[str]) -> None:
    """Drops the rows of files that are no longer on disk."""
    values = [(p,) for p in paths]
    if not values:
        return
    try:
        with db.transaction() as conn:
            conn.executemany("DELETE FROM player_library WHERE path = ?", values)
    except Exception:
        logger.debug(
            "[player_library] could not forget %d row(s)", len(values), exc_info=True
        )
