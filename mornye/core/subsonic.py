"""core/subsonic.py — the library as a Subsonic / OpenSubsonic server sees it.

Apps such as Symfonium, Feishin, DSub or Substreamer speak the Subsonic API.
With it, the music Mornye keeps can be played from them, on a phone or
another computer, instead of only from the Player page. The HTTP side is
webapi/subsonic.py; this module is everything underneath it:

- **The index.** Built from the Player's stored library
  (core/player_library.py): songs, albums and artists with stable ids.
  A song's id is derived from its path, an album's from its first album
  artist and title, an artist's from the name, so the ids a client has
  saved still mean the same thing after a rescan. Artists are split the way
  the Player splits them ("Doja Cat;SZA" is two artists), and an album shared
  by two artists belongs to both.
- **Credentials.** Off unless MORNYE_SUBSONIC_PASSWORD is set. Subsonic's
  token scheme needs the password itself (md5(password + salt)), so it is a
  password of its own, never a web account's (those are stored hashed).
  MORNYE_SUBSONIC_API_KEY enables OpenSubsonic's apiKey instead or as well.
- **What a client keeps on the server.** Stars, play counts, playlists and
  the saved play queue (schema v12).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import db, player_library
from .env import env_value

logger = logging.getLogger(__name__)

API_VERSION = "1.16.1"
SERVER_TYPE = "mornye"
USER_ENV = "MORNYE_SUBSONIC_USER"
PASSWORD_ENV = "MORNYE_SUBSONIC_PASSWORD"
API_KEY_ENV = "MORNYE_SUBSONIC_API_KEY"
DEFAULT_USER = "mornye"
MUSIC_FOLDER_ID = 1


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def username() -> str:
    return (env_value(USER_ENV, "") or "").strip() or DEFAULT_USER


def _password() -> str:
    return env_value(PASSWORD_ENV, "") or ""


def _api_key() -> str:
    return env_value(API_KEY_ENV, "") or ""


def enabled() -> bool:
    """Whether the Subsonic API answers at all."""
    return bool(_password() or _api_key())


class AuthError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def authenticate(params: dict[str, str]) -> str:
    """The user a request authenticates as, or AuthError.

    Accepts the three forms clients use: `apiKey` (OpenSubsonic), `t` + `s`
    (token: md5(password + salt)), and `p` (the password in clear, or as
    `enc:` + hex). Comparisons are constant-time.
    """
    key = params.get("apiKey", "")
    if key:
        expected = _api_key()
        if expected and hmac.compare_digest(key, expected):
            return username()
        raise AuthError(44, "Invalid API key")
    user = params.get("u", "")
    if not user:
        raise AuthError(10, "Required parameter is missing: u")
    password = _password()
    if not password or not hmac.compare_digest(user, username()):
        raise AuthError(40, "Wrong username or password")
    token, salt = params.get("t", ""), params.get("s", "")
    if token and salt:
        digest = hashlib.md5((password + salt).encode("utf-8")).hexdigest()
        if hmac.compare_digest(token.lower(), digest):
            return user
        raise AuthError(40, "Wrong username or password")
    clear = params.get("p", "")
    if clear.startswith("enc:"):
        try:
            clear = bytes.fromhex(clear[4:]).decode("utf-8")
        except ValueError:
            raise AuthError(40, "Wrong username or password") from None
    if clear and hmac.compare_digest(clear, password):
        return user
    raise AuthError(10 if not clear else 40, "Wrong username or password")


# ---------------------------------------------------------------------------
# The index
# ---------------------------------------------------------------------------


def artist_names(value: str) -> list[str]:
    """The artists one tag credits, as the Player splits them.

    ";" and NUL always separate; "/" only inside a credit with spaces, so
    "AC/DC" stays one name. A comma never does: "Tyler, The Creator".
    """
    out: list[str] = []
    for part in re.split(r"\s*[;\x00]\s*", value or ""):
        pieces = re.split(r"\s*/\s*", part) if re.search(r"\s", part) else [part]
        for piece in pieces:
            piece = piece.strip()
            if piece and piece.lower() not in {p.lower() for p in out}:
                out.append(piece)
    return out


def _id(prefix: str, *parts: str) -> str:
    raw = "\x1f".join(p.lower() for p in parts)
    return prefix + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


_CONTENT_TYPES = {
    "flac": "audio/flac",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "mp4": "audio/mp4",
    "aac": "audio/aac",
    "ogg": "audio/ogg",
    "opus": "audio/ogg",
    "wav": "audio/wav",
    "aiff": "audio/aiff",
    "aif": "audio/aiff",
    "wv": "audio/x-wavpack",
    "tta": "audio/x-tta",
    "wma": "audio/x-ms-wma",
    "ape": "audio/x-ape",
}


def content_type(suffix: str) -> str:
    return _CONTENT_TYPES.get(suffix.lower().lstrip("."), "application/octet-stream")


@dataclass
class Song:
    id: str
    path: str
    size: int
    title: str
    artists: list[str]
    album: str
    album_id: str
    album_artists: list[str]
    track: int
    disc: int
    year: int
    genre: str
    duration: int
    suffix: str

    @property
    def artist(self) -> str:
        return ", ".join(self.artists)


@dataclass
class Album:
    id: str
    name: str
    artists: list[str]
    songs: list[Song] = field(default_factory=list)

    @property
    def artist(self) -> str:
        return ", ".join(self.artists)

    @property
    def year(self) -> int:
        return next((s.year for s in self.songs if s.year), 0)

    @property
    def genre(self) -> str:
        return next((s.genre for s in self.songs if s.genre), "")

    @property
    def duration(self) -> int:
        return sum(s.duration for s in self.songs)


@dataclass
class Artist:
    id: str
    name: str
    albums: list[Album] = field(default_factory=list)


@dataclass
class Index:
    songs: dict[str, Song]
    albums: dict[str, Album]
    artists: dict[str, Artist]
    created: float


def artist_id(name: str) -> str:
    return _id("ar-", name)


def _int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def build_index(rows: list[tuple[str, int, dict[str, Any]]]) -> Index:
    """The Subsonic view of the stored library rows."""
    songs: dict[str, Song] = {}
    albums: dict[str, Album] = {}
    artists: dict[str, Artist] = {}

    for path, size, row in rows:
        track_artists = artist_names(str(row.get("artist", ""))) or ["Unknown artist"]
        album_artists = artist_names(str(row.get("album_artist", ""))) or track_artists
        album_name = str(row.get("album", "")).strip() or "Unknown album"
        album_id = _id("al-", album_artists[0], album_name)
        song = Song(
            id=_id("so-", path),
            path=path,
            size=size,
            title=str(row.get("title", "")) or Path(path).stem,
            artists=track_artists,
            album=album_name,
            album_id=album_id,
            album_artists=album_artists,
            track=_int(row.get("track")),
            disc=_int(row.get("disc")),
            year=_int(str(row.get("year", ""))[:4]),
            genre=str(row.get("genre", "")).split(";")[0].strip(),
            duration=_int(row.get("duration_s")),
            suffix=Path(path).suffix.lstrip(".").lower(),
        )
        songs[song.id] = song
        album = albums.get(album_id)
        if album is None:
            album = albums[album_id] = Album(album_id, album_name, album_artists)
        album.songs.append(song)

    for album in albums.values():
        album.songs.sort(key=lambda s: (s.disc, s.track, s.title.lower()))
        credited: list[str] = list(album.artists)
        for song in album.songs:
            seen = {c.lower() for c in credited}
            credited += [n for n in song.artists if n.lower() not in seen]
        for name in credited:
            aid = artist_id(name)
            if aid not in artists:
                artists[aid] = Artist(aid, name)
            artists[aid].albums.append(album)
    for a in artists.values():
        a.albums.sort(key=lambda al: (al.year or 9999, al.name.lower()))
    return Index(songs, albums, artists, time.time())


_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"version": None, "index": None}


def index() -> Index:
    """The current index, rebuilt only when the stored library changed."""
    current = player_library.version()
    with _cache_lock:
        if _cache["index"] is None or _cache["version"] != current:
            _cache["index"] = build_index(player_library.all_rows())
            _cache["version"] = current
        return _cache["index"]


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

_scan_lock = threading.Lock()
_scan_state: dict[str, Any] = {"scanning": False}


def scan_status() -> dict[str, Any]:
    return {"scanning": _scan_state["scanning"], "count": len(index().songs)}


def start_scan(roots: list[str]) -> None:
    """Syncs the stored library with `roots` in the background."""

    def run() -> None:
        try:
            result = player_library.sync(roots)
            logger.info("[subsonic] library synced: %s", result)
        except Exception:
            logger.warning("[subsonic] library scan failed", exc_info=True)
        finally:
            _scan_state["scanning"] = False
            db.close_thread_connection()

    with _scan_lock:
        if _scan_state["scanning"]:
            return
        _scan_state["scanning"] = True
    threading.Thread(target=run, name="subsonic-scan", daemon=True).start()


# ---------------------------------------------------------------------------
# Lyrics
# ---------------------------------------------------------------------------

_LRC_LINE = re.compile(r"\[(\d+):(\d+(?:[.:]\d+)?)\]")
_LRC_WORD = re.compile(r"<\d+:\d+(?:[.:]\d+)?>")


def lyrics_for(song: Song) -> tuple[bool, list[tuple[int, str]]] | None:
    """(synced, [(start ms, text)]) from a .lrc beside the file or the
    embedded lyrics; None when there are none."""
    text = ""
    sidecar = Path(song.path).with_suffix(".lrc")
    try:
        if sidecar.is_file():
            text = sidecar.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    if not text:
        try:
            from .tagger import read_embedded_tags

            text = read_embedded_tags(song.path, include_cover=False).lyrics or ""
        except Exception:
            text = ""
    if not text.strip():
        return None
    timed: list[tuple[int, str]] = []
    plain: list[str] = []
    for raw in text.splitlines():
        stamps = list(_LRC_LINE.finditer(raw))
        words = _LRC_WORD.sub("", _LRC_LINE.sub("", raw)).strip()
        if stamps:
            for m in stamps:
                seconds = float(m.group(2).replace(":", "."))
                timed.append((int((int(m.group(1)) * 60 + seconds) * 1000), words))
        elif raw.strip() and not re.match(r"^\[[a-z]+:", raw.strip(), re.I):
            plain.append(raw.strip())
    if timed:
        return True, sorted(timed)
    return False, [(0, line) for line in plain]


# ---------------------------------------------------------------------------
# What a client keeps on the server
# ---------------------------------------------------------------------------


def starred(user: str) -> dict[str, float]:
    rows = (
        db.connection()
        .execute(
            "SELECT item_id, starred_at FROM subsonic_starred WHERE username = ?",
            (user,),
        )
        .fetchall()
    )
    return {r["item_id"]: float(r["starred_at"]) for r in rows}


def set_starred(user: str, ids: list[str], on: bool) -> None:
    now = time.time()
    with db.transaction() as conn:
        for item in ids:
            if on:
                conn.execute(
                    "INSERT OR IGNORE INTO subsonic_starred VALUES (?, ?, ?)",
                    (user, item, now),
                )
            else:
                conn.execute(
                    "DELETE FROM subsonic_starred WHERE username = ? AND item_id = ?",
                    (user, item),
                )


def plays(user: str) -> dict[str, tuple[int, float]]:
    rows = (
        db.connection()
        .execute(
            "SELECT song_id, plays, last_played FROM subsonic_plays WHERE username = ?",
            (user,),
        )
        .fetchall()
    )
    return {r["song_id"]: (int(r["plays"]), float(r["last_played"])) for r in rows}


def scrobble(user: str, song_ids: list[str], when: float | None = None) -> None:
    now = when or time.time()
    with db.transaction() as conn:
        for sid in song_ids:
            conn.execute(
                "INSERT INTO subsonic_plays (username, song_id, plays, last_played)"
                " VALUES (?, ?, 1, ?) ON CONFLICT(username, song_id) DO UPDATE SET"
                " plays = plays + 1, last_played = excluded.last_played",
                (user, sid, now),
            )


def _playlist(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "owner": row["username"],
        "name": row["name"],
        "comment": row["comment"],
        "public": bool(row["public"]),
        "songs": [s for s in db.loads(row["songs"], []) if isinstance(s, str)],
        "created": float(row["created"]),
        "changed": float(row["changed"]),
    }


def playlists(user: str) -> list[dict[str, Any]]:
    rows = (
        db.connection()
        .execute(
            "SELECT * FROM subsonic_playlists WHERE username = ? OR public = 1"
            " ORDER BY name COLLATE NOCASE",
            (user,),
        )
        .fetchall()
    )
    return [_playlist(r) for r in rows]


def playlist(playlist_id: str) -> dict[str, Any] | None:
    row = (
        db.connection()
        .execute("SELECT * FROM subsonic_playlists WHERE id = ?", (playlist_id,))
        .fetchone()
    )
    return _playlist(row) if row else None


def save_playlist(
    user: str,
    *,
    playlist_id: str | None = None,
    name: str | None = None,
    comment: str | None = None,
    public: bool | None = None,
    songs: list[str] | None = None,
) -> str:
    now = time.time()
    current = playlist(playlist_id) if playlist_id else None
    if current is None:
        new_id = "pl-" + secrets.token_hex(8)
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO subsonic_playlists"
                " (id, username, name, comment, public, songs, created, changed)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id,
                    user,
                    name or "New playlist",
                    comment or "",
                    1 if public else 0,
                    db.dumps(songs or []),
                    now,
                    now,
                ),
            )
        return new_id
    with db.transaction() as conn:
        conn.execute(
            "UPDATE subsonic_playlists SET name = ?, comment = ?, public = ?,"
            " songs = ?, changed = ? WHERE id = ?",
            (
                current["name"] if name is None else name,
                current["comment"] if comment is None else comment,
                (1 if current["public"] else 0) if public is None else int(public),
                db.dumps(current["songs"] if songs is None else songs),
                now,
                current["id"],
            ),
        )
    return current["id"]


def delete_playlist(playlist_id: str) -> None:
    with db.transaction() as conn:
        conn.execute("DELETE FROM subsonic_playlists WHERE id = ?", (playlist_id,))


def play_queue(user: str) -> dict[str, Any] | None:
    row = (
        db.connection()
        .execute("SELECT * FROM subsonic_play_queue WHERE username = ?", (user,))
        .fetchone()
    )
    if not row:
        return None
    return {
        "current": row["current"],
        "position": int(row["position"]),
        "songs": [s for s in db.loads(row["songs"], []) if isinstance(s, str)],
        "changed": float(row["changed"]),
        "changed_by": row["changed_by"],
    }


def save_play_queue(
    user: str, songs: list[str], current: str, position: int, client: str
) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO subsonic_play_queue"
            " (username, current, position, songs, changed, changed_by)"
            " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(username) DO UPDATE SET"
            " current = excluded.current, position = excluded.position,"
            " songs = excluded.songs, changed = excluded.changed,"
            " changed_by = excluded.changed_by",
            (user, current, int(position), db.dumps(songs), time.time(), client),
        )
