"""core/failed_tracks.py — the tracks that still have to be downloaded.

A run's failures used to live in two places, and neither outlived it: the
downloader's own list (printed in the end-of-run summary, then dropped) and
the frontend's queue (emptied by a page reload, and never filled at all for a
download the browser did not start). A playlist of 1,800 tracks that ended
with 40 failures therefore left no record of which 40 — the only way to find
them again was to run the whole playlist.

This is that record: one row per track per account, written by a
post-download hook the moment a track fails and removed the moment it
downloads — in the same run or any later one, including when a later run
finds the file already on disk. What is left in the table is exactly what is
still missing, which is what the queue panel's "Retry" re-submits.

Each row keeps the track's full metadata, not only a link: a retry has to be
able to run after a restart, when the tracklist the failure came from is no
longer in memory.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from typing import Any

from . import db

logger = logging.getLogger(__name__)

#: A provider error can run to a traceback; the panel shows one line of it.
_MAX_ERROR_CHARS = 500


def track_key(metadata: Any) -> str:
    """What identifies one track across runs.

    The source's own id first (the same track fetched from the same playlist
    twice has the same id), then the ISRC, then title and artists — a CSV row
    can carry nothing more. Empty when there is nothing to go on at all.
    """
    for attr in ("id", "isrc"):
        value = str(getattr(metadata, attr, "") or "").strip()
        if value:
            return f"{attr}:{value}"
    title = str(getattr(metadata, "title", "") or "").strip().lower()
    if not title:
        return ""
    artists = str(getattr(metadata, "artists", "") or "").strip().lower()
    return f"name:{title}|{artists}"


def _serialise(metadata: Any) -> str:
    dump = getattr(metadata, "model_dump", None)
    if dump is None:
        return ""
    try:
        return db.dumps(dump(mode="json"))
    except Exception:
        logger.debug("[failed_tracks] could not serialise a track", exc_info=True)
        return ""


def record_failure(
    owner: str,
    metadata: Any,
    error: str = "",
    source_url: str = "",
    playlist_name: str = "",
    *,
    only_existing: bool = False,
) -> None:
    """Adds a failed track, or counts one more attempt at it. Never raises.

    The same contract as download_log.record(): this runs from a
    post-download hook, and bookkeeping must never turn into a failure of the
    download it is keeping books on.

    `only_existing` is a retry's mode: it counts the attempt on a row that is
    still listed and never adds one back. A retry still running when the
    list was cleared used to write every track it failed back into it, so
    the cleared tracks came back and were retried again (issue #284).
    """
    key = track_key(metadata)
    track = _serialise(metadata)
    if not key or not track:
        return
    now = time.time()
    try:
        with db.transaction() as conn:
            if only_existing:
                name = (playlist_name or "").strip()
                conn.execute(
                    """
                    UPDATE failed_tracks SET
                        playlist_name  = CASE WHEN ? <> '' THEN ?
                                         ELSE playlist_name END,
                        error          = ?,
                        attempts       = attempts + 1,
                        last_failed_at = ?
                    WHERE owner = ? AND source_url = ? AND track_key = ?
                    """,
                    (
                        name,
                        name,
                        (error or "")[:_MAX_ERROR_CHARS],
                        now,
                        owner or "",
                        source_url or "",
                        key,
                    ),
                )
                return
            conn.execute(
                """
                INSERT INTO failed_tracks (
                    owner, track_key, track, source_url, playlist_name,
                    error, attempts, first_failed_at, last_failed_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(owner, source_url, track_key) DO UPDATE SET
                    track          = excluded.track,
                    source_url     = excluded.source_url,
                    -- A run that does not know the playlist (a retry of a
                    -- row saved before #277) must not erase a name already
                    -- known, or the next retry lands loose again.
                    playlist_name  = CASE
                        WHEN excluded.playlist_name <> ''
                        THEN excluded.playlist_name
                        ELSE failed_tracks.playlist_name
                    END,
                    error          = excluded.error,
                    attempts       = failed_tracks.attempts + 1,
                    last_failed_at = excluded.last_failed_at
                """,
                (
                    owner or "",
                    key,
                    track,
                    source_url or "",
                    (playlist_name or "").strip(),
                    (error or "")[:_MAX_ERROR_CHARS],
                    now,
                    now,
                ),
            )
    except Exception:
        logger.debug("[failed_tracks] could not record %s", key, exc_info=True)


def is_playlist_url(url: str) -> bool:
    """Whether `url` names a playlist, which downloads into a folder of its
    own. The same test the tracklist fetch uses to decide that (app.py)."""
    lower = (url or "").lower()
    return "/playlist/" in lower or ("list=" in lower and "olak5uy_" not in lower)


def fill_playlist_name(owner: str, source_url: str, playlist_name: str) -> int:
    """Gives a playlist's failures with no folder name that name. Never raises.

    Rows recorded before failures kept their playlist (issue #277) have an
    empty name, so retrying them downloaded into the bare download folder
    and left them off the M3U. Only empty names are filled; returns how many.
    """
    name = (playlist_name or "").strip()
    if not name or not source_url:
        return 0
    try:
        with db.transaction() as conn:
            return conn.execute(
                "UPDATE failed_tracks SET playlist_name = ?"
                " WHERE owner = ? AND source_url = ?"
                " AND TRIM(playlist_name) = ''",
                (name, owner or "", source_url),
            ).rowcount
    except Exception:
        logger.debug("[failed_tracks] could not name %s", source_url, exc_info=True)
        return 0


def failure_key(key: str, source_url: str = "") -> str:
    """One failure's key in the list: the track *in one collection*.

    A song missing from two playlists is two failures (issue #277), each
    retried into its own folder. A bare track key still names the track in
    every collection, so a caller holding only that keeps working.
    """
    return f"{key}|{source_url}" if source_url else key


def resolve(owner: str, metadata: Any, source_url: str | None = None) -> None:
    """Forgets a track that has now downloaded. Never raises.

    Only its failure in `source_url`'s collection when that is given: the same
    track still missing from another playlist still belongs in that one's
    folder and M3U. Every one of its failures when it is not.
    """
    key = track_key(metadata)
    if not key:
        return
    try:
        with db.transaction() as conn:
            if source_url is None:
                conn.execute(
                    "DELETE FROM failed_tracks WHERE owner = ? AND track_key = ?",
                    (owner or "", key),
                )
            else:
                conn.execute(
                    "DELETE FROM failed_tracks"
                    " WHERE owner = ? AND track_key = ? AND source_url = ?",
                    (owner or "", key, source_url or ""),
                )
    except Exception:
        logger.debug("[failed_tracks] could not resolve %s", key, exc_info=True)


def hook(
    owner: str = "",
    source_url: str = "",
    playlist_name: str = "",
    *,
    only_existing: bool = False,
) -> Callable[[Any, Any], None]:
    """A post-download hook that keeps the table in step with each run.

    A success resolves the track, and so does a skip — a skipped track is one
    that is already on disk, which is the outcome a retry is after. Anything
    else is recorded, with `source_url` kept so the retry can rebuild the
    track's link (see core.tracklist.track_url), and `playlist_name` so it
    lands back in that playlist's folder and M3U (issue #277).
    """

    def _on_track(result: Any, metadata: Any) -> None:
        if getattr(result, "success", False):
            resolve(owner, metadata, source_url)
        else:
            record_failure(
                owner,
                metadata,
                getattr(result, "error", "") or "",
                source_url,
                playlist_name,
                only_existing=only_existing,
            )

    _on_track.__qualname__ = "failed_tracks.hook.on_track"
    return _on_track


def _rows(owner: str, keys: Iterable[str] | None) -> list[Any]:
    rows = (
        db.connection()
        .execute(
            "SELECT * FROM failed_tracks WHERE owner = ? ORDER BY last_failed_at DESC",
            (owner or "",),
        )
        .fetchall()
    )
    if keys is None:
        return rows
    # Filtered here rather than with an IN (...) built for the occasion: the
    # list is one account's failures, small by construction.
    wanted = {str(k) for k in keys}
    return [
        row
        for row in rows
        if row["track_key"] in wanted
        or failure_key(row["track_key"], row["source_url"]) in wanted
    ]


def list_for(owner: str = "") -> list[dict]:
    """What the queue panel shows, newest failure first. Never raises."""
    try:
        rows = _rows(owner, None)
    except Exception:
        logger.debug("[failed_tracks] could not read the list", exc_info=True)
        return []
    items: list[dict] = []
    for row in rows:
        track = db.loads(row["track"], {}) or {}
        items.append(
            {
                "key": failure_key(row["track_key"], row["source_url"]),
                "title": track.get("title", ""),
                "artists": track.get("artists", ""),
                "album": track.get("album", ""),
                "error": row["error"],
                "attempts": int(row["attempts"]),
                "first_failed_at": float(row["first_failed_at"]),
                "last_failed_at": float(row["last_failed_at"]),
            }
        )
    return items


def retry_groups(
    owner: str = "", keys: Iterable[str] | None = None
) -> list[tuple[str, str, list[dict]]]:
    """The failures to re-submit as ``(source_url, playlist_name, tracks)``.

    Grouped by the collection they came from, because a bare track id only
    turns into a link next to the service that issued it, and one download
    job carries one source URL and one playlist folder. Never raises.
    """
    return [(url, name, tracks) for url, name, tracks, _ in retry_batches(owner, keys)]


def retry_batches(
    owner: str = "", keys: Iterable[str] | None = None
) -> list[tuple[str, str, list[dict], list[str]]]:
    """retry_groups(), with each track's failure key beside it.

    ``(source_url, playlist_name, tracks, keys)``, ``keys[i]`` naming
    ``tracks[i]``: what lets a retry tell which tracks another retry already
    has in hand, and a queued retry which of its tracks are still listed.
    Never raises.
    """
    try:
        rows = _rows(owner, keys)
    except Exception:
        logger.debug("[failed_tracks] could not read the list", exc_info=True)
        return []
    groups: dict[tuple[str, str], tuple[list[dict], list[str]]] = {}
    for row in rows:
        track = db.loads(row["track"], None)
        if isinstance(track, dict):
            group = (row["source_url"] or "", row["playlist_name"] or "")
            tracks, group_keys = groups.setdefault(group, ([], []))
            tracks.append(track)
            group_keys.append(failure_key(row["track_key"], row["source_url"]))
    return [
        (url, name, tracks, group_keys)
        for (url, name), (tracks, group_keys) in groups.items()
    ]


def listed_keys(owner: str = "") -> set[str]:
    """The failure keys still on the list. Never raises."""
    try:
        rows = _rows(owner, None)
    except Exception:
        logger.debug("[failed_tracks] could not read the list", exc_info=True)
        return set()
    return {failure_key(row["track_key"], row["source_url"]) for row in rows}


def clear(owner: str = "", keys: Iterable[str] | None = None) -> int:
    """Drops failures from the list without retrying them.

    All of the account's, or only `keys`. Returns how many rows went. Never
    raises.
    """
    removed = 0
    try:
        with db.transaction() as conn:
            if keys is None:
                removed = conn.execute(
                    "DELETE FROM failed_tracks WHERE owner = ?", (owner or "",)
                ).rowcount
            else:
                rows = conn.execute(
                    "SELECT track_key, source_url FROM failed_tracks WHERE owner = ?",
                    (owner or "",),
                ).fetchall()
                wanted = {str(k) for k in keys}
                for row in rows:
                    if (
                        row["track_key"] in wanted
                        or failure_key(row["track_key"], row["source_url"]) in wanted
                    ):
                        removed += conn.execute(
                            "DELETE FROM failed_tracks WHERE owner = ?"
                            " AND track_key = ? AND source_url = ?",
                            (owner or "", row["track_key"], row["source_url"]),
                        ).rowcount
    except Exception:
        logger.debug("[failed_tracks] could not clear the list", exc_info=True)
        return 0
    return removed
