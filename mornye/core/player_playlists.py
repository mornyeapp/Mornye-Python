"""Persistent playlists for the Player.

A track is either a file in the local library (`path` is the file) or a
song that is not downloaded yet (`path` is a ``remote:`` key, see
:func:`remote_key`). The second kind comes from importing a playlist found
through a provider: every song is kept, and :func:`link_tracks` points an
entry at its file once the song has been downloaded.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from . import db

logger = logging.getLogger(__name__)

MAX_NAME_LENGTH = 120
MAX_DESCRIPTION_LENGTH = 2000
MAX_TRACKS = 20_000
MAX_IMPORT_BYTES = 5_000_000
#: Marks a playlist entry that has no file in the library yet.
REMOTE_PREFIX = "remote:"

_TRACK_FIELDS = ("path", "title", "artist", "album", "duration_s", "isrc", "source_url")
_TRACK_COLUMNS = ", ".join(_TRACK_FIELDS)


#: ``fn(owner, playlist_ids, deleted)`` after every change, whoever made it.
ChangeListener = Callable[[str, list[str], bool], None]
_listeners: list[ChangeListener] = []
_listeners_lock = threading.Lock()


def add_change_listener(fn: ChangeListener) -> Callable[[], None]:
    """Hear every playlist change (Mornye Link tells the phones). Returns
    the function that stops listening."""
    with _listeners_lock:
        _listeners.append(fn)

    def remove() -> None:
        with _listeners_lock:
            if fn in _listeners:
                _listeners.remove(fn)

    return remove


def _changed(owner: str, playlist_ids: Iterable[str], deleted: bool = False) -> None:
    ids = sorted(set(playlist_ids))
    if not ids:
        return
    with _listeners_lock:
        listeners = list(_listeners)
    for fn in listeners:
        try:
            fn(owner, ids, deleted)
        except Exception:
            logger.debug("A playlist listener failed", exc_info=True)


def is_remote(path: str) -> bool:
    return str(path).startswith(REMOTE_PREFIX)


def remote_key(track: Mapping[str, Any]) -> str:
    """The stable key of a song that is not downloaded.

    The ISRC when there is one (the same recording, whatever service it
    came from), else the song's link, else its title, artist and album.
    """
    isrc = str(track.get("isrc") or "").strip().upper()
    if isrc:
        return f"{REMOTE_PREFIX}isrc:{isrc}"
    url = str(track.get("source_url") or "").strip()
    if url:
        return f"{REMOTE_PREFIX}url:{url}"
    fingerprint = "\x1f".join(
        str(track.get(key) or "").strip().casefold()
        for key in ("title", "artist", "album")
    )
    digest = hashlib.sha1(fingerprint.encode()).hexdigest()[:20]
    return f"{REMOTE_PREFIX}meta:{digest}"


def _text(value: Any, limit: int, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text.")
    value = value.strip()
    if len(value) > limit:
        raise ValueError(f"{label} must be at most {limit} characters.")
    return value


def _playlist(conn, playlist_id: str, owner: str):
    return conn.execute(
        "SELECT id, name, description, cover, created_at, updated_at, "
        "source_url, source_keys, synced_at "
        "FROM player_playlists WHERE id = ? AND owner = ?",
        (playlist_id, owner),
    ).fetchone()


def _track_out(row) -> dict[str, Any]:
    return {
        "path": row["path"],
        "title": row["title"],
        "artist": row["artist"],
        "album": row["album"],
        "duration_s": float(row["duration_s"] or 0),
        "isrc": row["isrc"] or "",
        "source_url": row["source_url"] or "",
        "downloaded": not is_remote(row["path"]),
    }


def _tracks_of(conn, playlist_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"SELECT {_TRACK_COLUMNS} FROM player_playlist_tracks "
        "WHERE playlist_id = ? ORDER BY position",
        (playlist_id,),
    ).fetchall()
    return [_track_out(row) for row in rows]


def list_playlists(owner: str = "") -> list[dict[str, Any]]:
    conn = db.connection()
    rows = conn.execute(
        "SELECT id, name, description, cover, created_at, updated_at, "
        "source_url, synced_at "
        "FROM player_playlists WHERE owner = ? "
        "ORDER BY updated_at DESC, name COLLATE NOCASE",
        (owner,),
    ).fetchall()
    tracks: dict[str, list[dict[str, Any]]] = {row["id"]: [] for row in rows}
    # One query for every playlist's tracks rather than one per playlist.
    columns = ", ".join(f"t.{name}" for name in _TRACK_FIELDS)
    for track in conn.execute(
        f"SELECT t.playlist_id, {columns} FROM player_playlist_tracks t "
        "JOIN player_playlists p ON p.id = t.playlist_id "
        "WHERE p.owner = ? ORDER BY t.playlist_id, t.position",
        (owner,),
    ).fetchall():
        tracks[track["playlist_id"]].append(_track_out(track))
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "cover": row["cover"],
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
            "source_url": row["source_url"] or "",
            "synced_at": float(row["synced_at"] or 0),
            "tracks": tracks[row["id"]],
        }
        for row in rows
    ]


def get_playlist(playlist_id: str, owner: str) -> dict[str, Any] | None:
    return next((p for p in list_playlists(owner) if p["id"] == playlist_id), None)


def _insert_track(
    conn, playlist_id: str, position: int, track: Mapping[str, Any]
) -> None:
    conn.execute(
        "INSERT INTO player_playlist_tracks "
        f"(playlist_id, position, {_TRACK_COLUMNS}) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            playlist_id,
            position,
            str(track["path"]),
            str(track.get("title") or ""),
            str(track.get("artist") or ""),
            str(track.get("album") or ""),
            float(track.get("duration_s") or 0),
            str(track.get("isrc") or ""),
            str(track.get("source_url") or ""),
        ),
    )


def _replace_tracks(
    conn, playlist_id: str, tracks: Iterable[Mapping[str, Any]]
) -> None:
    conn.execute(
        "DELETE FROM player_playlist_tracks WHERE playlist_id = ?", (playlist_id,)
    )
    seen: set[str] = set()
    for track in tracks:
        path = str(track["path"])
        if path in seen:
            continue
        seen.add(path)
        _insert_track(conn, playlist_id, len(seen) - 1, track)


def _touch(conn, playlist_id: str) -> None:
    conn.execute(
        "UPDATE player_playlists SET updated_at = ? WHERE id = ?",
        (time.time(), playlist_id),
    )


def create_playlist(
    name: str,
    description: str = "",
    cover: str = "",
    tracks: Iterable[Mapping[str, Any]] = (),
    owner: str = "",
    *,
    source_url: str = "",
    source_keys: Iterable[str] = (),
) -> dict[str, Any]:
    """`source_url` is the provider playlist this one follows (see
    :func:`record_source_sync`), with `source_keys` the songs it listed."""
    title = _text(name, MAX_NAME_LENGTH, "Playlist name")
    if not title:
        raise ValueError("Playlist name cannot be empty.")
    summary = _text(description, MAX_DESCRIPTION_LENGTH, "Description")
    if not isinstance(cover, str):
        raise ValueError("Cover must be an image.")
    entries = list(tracks)
    if len(entries) > MAX_TRACKS:
        raise ValueError(f"A playlist can contain at most {MAX_TRACKS} tracks.")
    playlist_id = uuid.uuid4().hex
    now = time.time()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO player_playlists "
            "(id, owner, name, description, cover, created_at, updated_at, "
            "source_url, source_keys, synced_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                playlist_id,
                owner,
                title,
                summary,
                cover,
                now,
                now,
                str(source_url or ""),
                json.dumps(sorted(set(source_keys))),
                now if source_url else 0.0,
            ),
        )
        _replace_tracks(conn, playlist_id, entries)
    playlist = get_playlist(playlist_id, owner)
    if playlist is None:  # pragma: no cover - written in the same call
        raise ValueError("The playlist could not be read back.")
    _changed(owner, [playlist_id])
    return playlist


def update_playlist(
    playlist_id: str,
    owner: str,
    *,
    name: str,
    description: str,
    cover: str,
) -> dict[str, Any] | None:
    title = _text(name, MAX_NAME_LENGTH, "Playlist name")
    if not title:
        raise ValueError("Playlist name cannot be empty.")
    summary = _text(description, MAX_DESCRIPTION_LENGTH, "Description")
    if not isinstance(cover, str):
        raise ValueError("Cover must be an image.")
    with db.transaction() as conn:
        cursor = conn.execute(
            "UPDATE player_playlists SET name = ?, description = ?, cover = ?, "
            "updated_at = ? WHERE id = ? AND owner = ?",
            (title, summary, cover, time.time(), playlist_id, owner),
        )
        if not cursor.rowcount:
            return None
    _changed(owner, [playlist_id])
    return get_playlist(playlist_id, owner)


def delete_playlist(playlist_id: str, owner: str) -> bool:
    with db.transaction() as conn:
        cursor = conn.execute(
            "DELETE FROM player_playlists WHERE id = ? AND owner = ?",
            (playlist_id, owner),
        )
    if cursor.rowcount:
        _changed(owner, [playlist_id], deleted=True)
    return bool(cursor.rowcount)


def add_tracks(
    playlist_id: str,
    owner: str,
    tracks: Iterable[Mapping[str, Any]],
) -> dict[str, Any] | None:
    entries = list(tracks)
    if not entries:
        return get_playlist(playlist_id, owner)
    with db.transaction() as conn:
        if _playlist(conn, playlist_id, owner) is None:
            return None
        paths = {
            row["path"]
            for row in conn.execute(
                "SELECT path FROM player_playlist_tracks WHERE playlist_id = ?",
                (playlist_id,),
            ).fetchall()
        }
        new_paths = {str(track["path"]) for track in entries} - paths
        if len(paths) + len(new_paths) > MAX_TRACKS:
            raise ValueError(f"A playlist can contain at most {MAX_TRACKS} tracks.")
        position = len(paths)
        for track in entries:
            path = str(track["path"])
            if path in paths:
                continue
            _insert_track(conn, playlist_id, position, track)
            paths.add(path)
            position += 1
        _touch(conn, playlist_id)
    _changed(owner, [playlist_id])
    return get_playlist(playlist_id, owner)


def remove_track(playlist_id: str, owner: str, path: str) -> dict[str, Any] | None:
    with db.transaction() as conn:
        if _playlist(conn, playlist_id, owner) is None:
            return None
        remaining = [t for t in _tracks_of(conn, playlist_id) if t["path"] != path]
        _replace_tracks(conn, playlist_id, remaining)
        _touch(conn, playlist_id)
    _changed(owner, [playlist_id])
    return get_playlist(playlist_id, owner)


def reorder_tracks(
    playlist_id: str, owner: str, paths: Iterable[str]
) -> dict[str, Any] | None:
    ordered = list(paths)
    if len(ordered) > MAX_TRACKS or len(set(ordered)) != len(ordered):
        raise ValueError("The playlist order is invalid.")
    with db.transaction() as conn:
        if _playlist(conn, playlist_id, owner) is None:
            return None
        current = {t["path"]: t for t in _tracks_of(conn, playlist_id)}
        if set(ordered) != set(current):
            raise ValueError("The playlist changed; reload it and try again.")
        _replace_tracks(conn, playlist_id, [current[path] for path in ordered])
        _touch(conn, playlist_id)
    _changed(owner, [playlist_id])
    return get_playlist(playlist_id, owner)


class PlaylistConflict(ValueError):
    """The playlist changed since the version the edit was made on."""


def updated_ms(playlist: Mapping[str, Any]) -> int:
    """A playlist's version as another device holds it: `updated_at` in ms."""
    return int(round(float(playlist.get("updated_at") or 0) * 1000))


def set_tracks(
    playlist_id: str,
    owner: str,
    tracks: Iterable[Mapping[str, Any]],
    *,
    base_updated_ms: int | None = None,
) -> dict[str, Any] | None:
    """Replace the whole track list (a path listed twice is kept once).

    `base_updated_ms` is the version the new list was made from (see
    :func:`updated_ms`): when the playlist has changed since, nothing is
    written and :class:`PlaylistConflict` is raised, so an edit made on
    another device is never lost silently. None when there is no such
    playlist.
    """
    entries = list(tracks)
    if len(entries) > MAX_TRACKS:
        raise ValueError(f"A playlist can contain at most {MAX_TRACKS} tracks.")
    with db.transaction() as conn:
        row = _playlist(conn, playlist_id, owner)
        if row is None:
            return None
        current = updated_ms({"updated_at": row["updated_at"]})
        if base_updated_ms is not None and base_updated_ms != current:
            raise PlaylistConflict("The playlist changed; reload it and try again.")
        _replace_tracks(conn, playlist_id, entries)
        _touch(conn, playlist_id)
    _changed(owner, [playlist_id])
    return get_playlist(playlist_id, owner)


def link_tracks(owner: str, links: Mapping[str, Mapping[str, Any]]) -> int:
    """Point not-downloaded entries at the files that now hold them.

    `links` maps a ``remote:`` key to a library track (a dict with `path`
    and its tags). Every playlist of `owner` listing the key gets the file
    in that place, under the file's own title, artist and album. When the
    file is already in that playlist, the remote entry is dropped rather
    than listing the song twice. Returns how many entries changed.
    """
    wanted = {key: track for key, track in links.items() if is_remote(key)}
    if not wanted:
        return 0
    changed = 0
    placeholders = ", ".join("?" * len(wanted))
    with db.transaction() as conn:
        playlist_ids = [
            row["playlist_id"]
            for row in conn.execute(
                "SELECT DISTINCT t.playlist_id FROM player_playlist_tracks t "
                "JOIN player_playlists p ON p.id = t.playlist_id "
                f"WHERE p.owner = ? AND t.path IN ({placeholders})",
                (owner, *wanted),
            ).fetchall()
        ]
        for playlist_id in playlist_ids:
            updated: list[dict[str, Any]] = []
            for track in _tracks_of(conn, playlist_id):
                local = wanted.get(track["path"])
                if local is None:
                    updated.append(track)
                    continue
                changed += 1
                updated.append(
                    {
                        "path": str(local["path"]),
                        "title": local.get("title") or track["title"],
                        "artist": local.get("artist") or track["artist"],
                        "album": local.get("album") or track["album"],
                        "duration_s": local.get("duration_s") or track["duration_s"],
                        "isrc": local.get("isrc") or track["isrc"],
                        "source_url": track["source_url"],
                    }
                )
            # _replace_tracks keeps the first occurrence of a path, so a
            # song that was already there as a file is not listed twice.
            _replace_tracks(conn, playlist_id, updated)
            _touch(conn, playlist_id)
    _changed(owner, playlist_ids)
    return changed


def playlist_source(playlist_id: str, owner: str) -> tuple[str, set[str]] | None:
    """The provider playlist `playlist_id` follows and the songs it has
    listed so far, or None when there is no such playlist. The URL is
    empty for a playlist that follows nothing."""
    row = _playlist(db.connection(), playlist_id, owner)
    if row is None:
        return None
    try:
        keys = json.loads(row["source_keys"] or "[]")
    except ValueError:
        keys = []
    return row["source_url"] or "", {str(key) for key in keys if key}


def set_playlist_source(
    playlist_id: str, owner: str, url: str, source_keys: Iterable[str] = ()
) -> bool:
    """Make a playlist follow `url` (or nothing, when it is empty).

    `source_keys` are the songs counted as already seen: for a playlist
    imported before following existed, the ones it lists now. False when
    there is no such playlist.
    """
    with db.transaction() as conn:
        cursor = conn.execute(
            "UPDATE player_playlists SET source_url = ?, source_keys = ?, "
            "synced_at = 0 WHERE id = ? AND owner = ?",
            (
                str(url or ""),
                json.dumps(sorted(set(source_keys)) if url else []),
                playlist_id,
                owner,
            ),
        )
    if cursor.rowcount:
        _changed(owner, [playlist_id])
    return bool(cursor.rowcount)


def record_source_sync(
    playlist_id: str,
    owner: str,
    source_keys: Iterable[str],
    tracks: Iterable[Mapping[str, Any]] = (),
    drop: Iterable[str] = (),
) -> dict[str, Any] | None:
    """Append the songs the source added and remember what it lists.

    `tracks` are appended (a path already in the playlist is skipped) and
    `source_keys` joins the songs seen before, so a song the user removed
    here is not brought back by the next sync. The entries whose path is in
    `drop` are removed first. Returns the playlist.
    """
    entries = list(tracks)
    dropped = {str(path) for path in drop}
    with db.transaction() as conn:
        row = _playlist(conn, playlist_id, owner)
        if row is None:
            return None
        try:
            known = set(json.loads(row["source_keys"] or "[]"))
        except ValueError:
            known = set()
        if dropped:
            _replace_tracks(
                conn,
                playlist_id,
                [t for t in _tracks_of(conn, playlist_id) if t["path"] not in dropped],
            )
        paths = {
            r["path"]
            for r in conn.execute(
                "SELECT path FROM player_playlist_tracks WHERE playlist_id = ?",
                (playlist_id,),
            ).fetchall()
        }
        fresh = [t for t in entries if str(t["path"]) not in paths]
        if len(paths) + len(fresh) > MAX_TRACKS:
            raise ValueError(f"A playlist can contain at most {MAX_TRACKS} tracks.")
        position = len(paths)
        for track in fresh:
            path = str(track["path"])
            if path in paths:
                continue
            _insert_track(conn, playlist_id, position, track)
            paths.add(path)
            position += 1
        now = time.time()
        conn.execute(
            "UPDATE player_playlists SET source_keys = ?, synced_at = ?"
            + (", updated_at = ?" if fresh or dropped else "")
            + " WHERE id = ?",
            (
                json.dumps(sorted(known | set(source_keys))),
                now,
                *((now,) if fresh or dropped else ()),
                playlist_id,
            ),
        )
    _changed(owner, [playlist_id])
    return get_playlist(playlist_id, owner)
