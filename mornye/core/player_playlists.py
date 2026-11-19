"""Persistent playlists for the Player.

A track is either a file in the local library (`path` is the file) or a
song that is not downloaded yet (`path` is a ``remote:`` key, see
:func:`remote_key`). The second kind comes from importing a playlist found
through a provider: every song is kept, and :func:`link_tracks` points an
entry at its file once the song has been downloaded.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from . import db

MAX_NAME_LENGTH = 120
MAX_DESCRIPTION_LENGTH = 2000
MAX_TRACKS = 20_000
MAX_IMPORT_BYTES = 5_000_000
#: Marks a playlist entry that has no file in the library yet.
REMOTE_PREFIX = "remote:"

_TRACK_FIELDS = ("path", "title", "artist", "album", "duration_s", "isrc", "source_url")
_TRACK_COLUMNS = ", ".join(_TRACK_FIELDS)


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
        "SELECT id, name, description, cover, created_at, updated_at "
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
        "SELECT id, name, description, cover, created_at, updated_at "
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
) -> dict[str, Any]:
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
            "(id, owner, name, description, cover, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (playlist_id, owner, title, summary, cover, now, now),
        )
        _replace_tracks(conn, playlist_id, entries)
    playlist = get_playlist(playlist_id, owner)
    if playlist is None:  # pragma: no cover - written in the same call
        raise ValueError("The playlist could not be read back.")
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
    return get_playlist(playlist_id, owner)


def delete_playlist(playlist_id: str, owner: str) -> bool:
    with db.transaction() as conn:
        cursor = conn.execute(
            "DELETE FROM player_playlists WHERE id = ? AND owner = ?",
            (playlist_id, owner),
        )
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
    return get_playlist(playlist_id, owner)


def remove_track(playlist_id: str, owner: str, path: str) -> dict[str, Any] | None:
    with db.transaction() as conn:
        if _playlist(conn, playlist_id, owner) is None:
            return None
        remaining = [t for t in _tracks_of(conn, playlist_id) if t["path"] != path]
        _replace_tracks(conn, playlist_id, remaining)
        _touch(conn, playlist_id)
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
    return changed
