"""webapi/subsonic.py — the Subsonic / OpenSubsonic API under /rest.

Lets apps such as Symfonium, Feishin, DSub or Substreamer browse, search and
stream the library of a --web instance. The data and the credentials live in
core/subsonic.py; this is the protocol: one route per method, reachable as
`/rest/name` and `/rest/name.view`, by GET or by form POST, answering in XML
by default and in JSON with `f=json`.

Errors follow the protocol, not HTTP: status 200 with
`status="failed"` and the Subsonic error code, which is what clients parse.
"""

from __future__ import annotations

import io
import logging
import random
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl
from xml.etree import ElementTree as ET

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from ..core import subsonic as sub

logger = logging.getLogger(__name__)

_XMLNS = "http://subsonic.org/restapi"
_COVER_NAMES = ("cover.jpg", "folder.jpg", "front.jpg", "cover.png", "folder.png")
_ARTICLES = "The El La Los Las Le Les"


class _Fail(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Request and response plumbing
# ---------------------------------------------------------------------------


async def _params(request: Request) -> dict[str, list[str]]:
    """Query parameters, plus a form POST body (OpenSubsonic's formPost).

    The body is decoded here rather than with `request.form()`: that needs
    python-multipart, which Mornye does not depend on, and without it every
    parameter of a POST was lost (the Windows CI runner had no copy of it).
    formPost is always application/x-www-form-urlencoded.
    """
    out: dict[str, list[str]] = {}
    for key, value in request.query_params.multi_items():
        out.setdefault(key, []).append(value)
    content_type = request.headers.get("content-type", "").lower()
    if request.method == "POST" and content_type.startswith(
        "application/x-www-form-urlencoded"
    ):
        body = (await request.body()).decode("utf-8", errors="replace")
        for key, field in parse_qsl(body, keep_blank_values=True):
            out.setdefault(key, []).append(field)
    return out


def _one(params: dict[str, list[str]], name: str, default: str = "") -> str:
    values = params.get(name)
    return values[0] if values else default


def _int(params: dict[str, list[str]], name: str, default: int, top: int = 500) -> int:
    try:
        return max(0, min(top, int(_one(params, name, str(default)))))
    except ValueError:
        return default


def _required(params: dict[str, list[str]], name: str) -> str:
    value = _one(params, name)
    if not value:
        raise _Fail(10, f"Required parameter is missing: {name}")
    return value


def _envelope(status: str, body: dict[str, Any]) -> dict[str, Any]:
    from .. import __version__

    return {
        "status": status,
        "version": sub.API_VERSION,
        "type": sub.SERVER_TYPE,
        "serverVersion": str(__version__),
        "openSubsonic": True,
        **body,
    }


def _xml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _to_xml(parent: ET.Element, key: str, value: Any) -> None:
    if isinstance(value, list):
        for item in value:
            _to_xml(parent, key, item)
        return
    el = ET.SubElement(parent, key)
    if isinstance(value, dict):
        for k, v in value.items():
            if v is None:
                continue
            if k == "value" and not isinstance(v, (dict, list)):
                el.text = _xml_value(v)
            elif isinstance(v, (dict, list)):
                _to_xml(el, k, v)
            else:
                el.set(k, _xml_value(v))
    else:
        el.text = _xml_value(value)


def _strip_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_strip_none(v) for v in value]
    return value


def _respond(
    params: dict[str, list[str]], body: dict[str, Any], status: str = "ok"
) -> Response:
    data = _envelope(status, body)
    if _one(params, "f").lower() in ("json", "jsonp"):
        return JSONResponse({"subsonic-response": _strip_none(data)})
    root = ET.Element("subsonic-response", {"xmlns": _XMLNS})
    for k, v in data.items():
        if isinstance(v, (dict, list)):
            _to_xml(root, k, v)
        elif v is not None:
            root.set(k, _xml_value(v))
    buf = io.BytesIO()
    ET.ElementTree(root).write(buf, encoding="utf-8", xml_declaration=True)
    return Response(buf.getvalue(), media_type="text/xml; charset=utf-8")


def _error(params: dict[str, list[str]], code: int, message: str) -> Response:
    return _respond(params, {"error": {"code": code, "message": message}}, "failed")


def _iso(ts: float | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _sort_name(name: str) -> str:
    lowered = name.lower()
    for article in _ARTICLES.lower().split():
        if lowered.startswith(article + " "):
            return lowered[len(article) + 1 :]
    return lowered


def _matches(query: str, *fields: str) -> bool:
    return all(any(word in f.lower() for f in fields) for word in query.lower().split())


def _plays_of(view: _View, album: sub.Album) -> int:
    return sum(view.plays.get(s.id, (0, 0.0))[0] for s in album.songs)


def _last_played(view: _View, album: sub.Album) -> float:
    return max((view.plays.get(s.id, (0, 0.0))[1] for s in album.songs), default=0)


# ---------------------------------------------------------------------------
# Library objects as the protocol shows them
# ---------------------------------------------------------------------------


class _View:
    """One request's view of the library: the index plus this user's stars
    and play counts, read once."""

    def __init__(self, user: str) -> None:
        self.user = user
        self.idx = sub.index()
        self.stars = sub.starred(user)
        self.plays = sub.plays(user)

    def song(self, s: sub.Song) -> dict[str, Any]:
        count, last = self.plays.get(s.id, (0, 0.0))
        bitrate = int(s.size * 8 / s.duration / 1000) if s.duration else 0
        first_artist = s.artists[0] if s.artists else ""
        return {
            "id": s.id,
            "parent": s.album_id,
            "isDir": False,
            "title": s.title,
            "album": s.album,
            "artist": s.artist,
            "track": s.track or None,
            "discNumber": s.disc or None,
            "year": s.year or None,
            "genre": s.genre_tag or s.genre or None,
            "coverArt": s.album_id,
            "size": s.size,
            "contentType": sub.content_type(s.suffix),
            "suffix": s.suffix,
            "duration": s.duration,
            "bitRate": bitrate,
            "path": f"{first_artist}/{s.album}/{Path(s.path).name}",
            "albumId": s.album_id,
            "artistId": sub.artist_id(first_artist) if first_artist else None,
            "type": "music",
            "mediaType": "song",
            "isVideo": False,
            "playCount": count,
            "played": _iso(last),
            "starred": _iso(self.stars.get(s.id)),
            "displayArtist": s.artist,
            "displayAlbumArtist": ", ".join(s.album_artists),
            "artists": [{"id": sub.artist_id(n), "name": n} for n in s.artists],
            "albumArtists": [
                {"id": sub.artist_id(n), "name": n} for n in s.album_artists
            ],
            "genres": [{"name": s.genre}] if s.genre else [],
            # OpenSubsonic's song ISRCs; a Link phone compares its saved
            # copies with them (Update metadata).
            "isrc": [s.isrc] if s.isrc else [],
        }

    def album(self, a: sub.Album, *, folder: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": a.id,
            "name": a.name,
            "artist": a.artist,
            "artistId": sub.artist_id(a.artists[0]) if a.artists else None,
            "coverArt": a.id,
            "songCount": len(a.songs),
            "duration": a.duration,
            "playCount": _plays_of(self, a),
            "year": a.year or None,
            "genre": a.genre or None,
            "starred": _iso(self.stars.get(a.id)),
            "displayArtist": a.artist,
            "artists": [{"id": sub.artist_id(n), "name": n} for n in a.artists],
            "genres": [{"name": a.genre}] if a.genre else [],
            "isCompilation": False,
        }
        if folder:
            out.update(
                {
                    "title": a.name,
                    "album": a.name,
                    "isDir": True,
                    "parent": out["artistId"],
                }
            )
        return out

    def artist(self, a: sub.Artist) -> dict[str, Any]:
        return {
            "id": a.id,
            "name": a.name,
            "albumCount": len(a.albums),
            "coverArt": a.albums[0].id if a.albums else None,
            "starred": _iso(self.stars.get(a.id)),
        }

    def get_song(self, song_id: str) -> sub.Song:
        song = self.idx.songs.get(song_id)
        if song is None:
            raise _Fail(70, "Song not found")
        return song

    def get_album(self, album_id: str) -> sub.Album:
        album = self.idx.albums.get(album_id)
        if album is None:
            raise _Fail(70, "Album not found")
        return album

    def get_artist(self, artist_id: str) -> sub.Artist:
        artist = self.idx.artists.get(artist_id)
        if artist is None:
            raise _Fail(70, "Artist not found")
        return artist

    def indexed_artists(self) -> list[dict[str, Any]]:
        groups: dict[str, list[sub.Artist]] = {}
        for a in sorted(self.idx.artists.values(), key=lambda x: _sort_name(x.name)):
            first = _sort_name(a.name)[:1].upper()
            groups.setdefault(first if first.isalpha() else "#", []).append(a)
        return [
            {"name": letter, "artist": [self.artist(a) for a in items]}
            for letter, items in sorted(groups.items())
        ]


def _cover_bytes(path: str, size: int) -> bytes | None:
    from ..core.library_scan import thumbnail_jpeg

    try:
        data = thumbnail_jpeg(path, size or 600)
    except Exception:
        data = None
    if data:
        return data
    folder = Path(path).parent
    for name in _COVER_NAMES:
        candidate = folder / name
        if not candidate.is_file():
            continue
        try:
            from PIL import Image

            with Image.open(candidate) as source:
                img = source.convert("RGB")
                if size:
                    img.thumbnail((size, size))
                out = io.BytesIO()
                img.save(out, format="JPEG", quality=85)
            return out.getvalue()
        except Exception:
            return candidate.read_bytes()
    return None


def _playlist_head(view: _View, p: dict[str, Any]) -> dict[str, Any]:
    songs = [view.idx.songs[i] for i in p["songs"] if i in view.idx.songs]
    return {
        "id": p["id"],
        "name": p["name"],
        "comment": p["comment"] or None,
        "owner": p["owner"],
        "public": p["public"],
        "songCount": len(songs),
        "duration": sum(s.duration for s in songs),
        "created": _iso(p["created"]),
        "changed": _iso(p["changed"]),
        "coverArt": songs[0].album_id if songs else None,
    }


def _playlist_full(view: _View, p: dict[str, Any] | None) -> dict[str, Any]:
    if not p:
        return {}
    songs = [view.idx.songs[i] for i in p["songs"] if i in view.idx.songs]
    return {**_playlist_head(view, p), "entry": [view.song(s) for s in songs]}


def _own_playlist(playlist_id: str, user: str) -> dict[str, Any]:
    found = sub.playlist(playlist_id)
    if not found:
        raise _Fail(70, "Playlist not found")
    if found["owner"] != user:
        raise _Fail(50, "Not your playlist")
    return found


# ---------------------------------------------------------------------------
# The methods
# ---------------------------------------------------------------------------


def _system(name: str, params: dict[str, list[str]], user: str, roots) -> Any:
    if name == "ping":
        return {}
    if name == "getLicense":
        return {"license": {"valid": True}}
    if name == "getOpenSubsonicExtensions":
        return {
            "openSubsonicExtensions": [
                {"name": "formPost", "versions": [1]},
                {"name": "songLyrics", "versions": [1, 2]},
                {"name": "apiKeyAuthentication", "versions": [1]},
            ]
        }
    if name == "getMusicFolders":
        return {
            "musicFolders": {
                "musicFolder": [{"id": sub.MUSIC_FOLDER_ID, "name": "Music"}]
            }
        }
    if name == "getUser":
        roles = dict.fromkeys(
            ("streamRole", "downloadRole", "playlistRole", "coverArtRole"), True
        )
        roles |= dict.fromkeys(
            (
                "adminRole",
                "settingsRole",
                "uploadRole",
                "commentRole",
                "podcastRole",
                "jukeboxRole",
                "shareRole",
                "videoConversionRole",
            ),
            False,
        )
        return {
            "user": {
                "username": user,
                "scrobblingEnabled": True,
                **roles,
                "folder": [sub.MUSIC_FOLDER_ID],
            }
        }
    if name == "getScanStatus":
        return {"scanStatus": sub.scan_status()}
    if name == "startScan":
        sub.start_scan(roots())
        return {"scanStatus": {**sub.scan_status(), "scanning": True}}
    return None


def _browse(name: str, params: dict[str, list[str]], view: _View) -> Any:
    idx = view.idx
    if name == "getIndexes":
        return {
            "indexes": {
                "lastModified": int(idx.created * 1000),
                "ignoredArticles": _ARTICLES,
                "index": view.indexed_artists(),
            }
        }
    if name == "getArtists":
        return {
            "artists": {"ignoredArticles": _ARTICLES, "index": view.indexed_artists()}
        }
    if name == "getArtist":
        artist = view.get_artist(_required(params, "id"))
        return {
            "artist": {
                **view.artist(artist),
                "album": [view.album(a) for a in artist.albums],
            }
        }
    if name == "getAlbum":
        album = view.get_album(_required(params, "id"))
        return {
            "album": {**view.album(album), "song": [view.song(s) for s in album.songs]}
        }
    if name == "getSong":
        return {"song": view.song(view.get_song(_required(params, "id")))}
    if name == "getMusicDirectory":
        item = _required(params, "id")
        if item in idx.artists:
            artist = idx.artists[item]
            return {
                "directory": {
                    "id": artist.id,
                    "name": artist.name,
                    "child": [view.album(a, folder=True) for a in artist.albums],
                }
            }
        album = view.get_album(item)
        return {
            "directory": {
                "id": album.id,
                "name": album.name,
                "parent": sub.artist_id(album.artists[0]) if album.artists else None,
                "child": [view.song(s) for s in album.songs],
            }
        }
    if name in ("getArtistInfo", "getArtistInfo2"):
        view.get_artist(_required(params, "id"))
        key = "artistInfo2" if name.endswith("2") else "artistInfo"
        return {key: {"biography": "", "similarArtist": []}}
    if name in ("getAlbumInfo", "getAlbumInfo2"):
        view.get_album(_required(params, "id"))
        return {"albumInfo": {"notes": ""}}
    if name in ("getSimilarSongs", "getSimilarSongs2"):
        key = "similarSongs2" if name.endswith("2") else "similarSongs"
        return {key: {"song": []}}
    if name == "getTopSongs":
        wanted = _one(params, "artist").lower()
        songs = [
            s for s in idx.songs.values() if any(n.lower() == wanted for n in s.artists)
        ]
        songs.sort(key=lambda s: -view.plays.get(s.id, (0, 0.0))[0])
        count = _int(params, "count", 50)
        return {"topSongs": {"song": [view.song(s) for s in songs[:count]]}}
    if name == "getGenres":
        counts: dict[str, tuple[int, set[str]]] = {}
        for s in idx.songs.values():
            if s.genre:
                n, albums = counts.get(s.genre, (0, set()))
                albums.add(s.album_id)
                counts[s.genre] = (n + 1, albums)
        return {
            "genres": {
                "genre": [
                    {"songCount": n, "albumCount": len(a), "value": g}
                    for g, (n, a) in sorted(counts.items())
                ]
            }
        }
    return None


def _album_list(params: dict[str, list[str]], view: _View) -> list[sub.Album]:
    kind = _required(params, "type")
    albums = list(view.idx.albums.values())
    if kind == "random":
        random.shuffle(albums)
    elif kind == "alphabeticalByName":
        albums.sort(key=lambda a: _sort_name(a.name))
    elif kind == "alphabeticalByArtist":
        albums.sort(key=lambda a: (_sort_name(a.artist), a.year, a.name.lower()))
    elif kind == "byYear":
        lo = _int(params, "fromYear", 0, 9999)
        hi = _int(params, "toYear", 9999, 9999)
        albums = [a for a in albums if min(lo, hi) <= a.year <= max(lo, hi)]
        albums.sort(key=lambda a: a.year, reverse=lo > hi)
    elif kind == "byGenre":
        genre = _required(params, "genre").lower()
        albums = [a for a in albums if a.genre.lower() == genre]
    elif kind in ("frequent", "highest"):
        albums = sorted(
            (a for a in albums if _plays_of(view, a)), key=lambda a: -_plays_of(view, a)
        )
    elif kind == "recent":
        albums = sorted(
            (a for a in albums if _last_played(view, a)),
            key=lambda a: -_last_played(view, a),
        )
    elif kind == "starred":
        albums = [a for a in albums if a.id in view.stars]
    elif kind == "newest":
        albums.sort(key=lambda a: (-(a.year or 0), a.name.lower()))
    else:
        raise _Fail(0, f"Unknown list type: {kind}")
    offset = _int(params, "offset", 0, 10**9)
    return albums[offset : offset + _int(params, "size", 10)]


def _lists(name: str, params: dict[str, list[str]], view: _View) -> Any:
    idx = view.idx
    if name == "getAlbumList2":
        return {
            "albumList2": {"album": [view.album(a) for a in _album_list(params, view)]}
        }
    if name == "getAlbumList":
        listed = _album_list(params, view)
        return {"albumList": {"album": [view.album(a, folder=True) for a in listed]}}
    if name == "getRandomSongs":
        genre = _one(params, "genre").lower()
        lo = _int(params, "fromYear", 0, 9999)
        hi = _int(params, "toYear", 9999, 9999)
        songs = [
            s
            for s in idx.songs.values()
            if (not genre or s.genre.lower() == genre)
            and (not s.year or lo <= s.year <= hi)
        ]
        picked = random.sample(songs, min(_int(params, "size", 10), len(songs)))
        return {"randomSongs": {"song": [view.song(s) for s in picked]}}
    if name == "getSongsByGenre":
        genre = _required(params, "genre").lower()
        in_genre = [s for s in idx.songs.values() if s.genre.lower() == genre]
        offset = _int(params, "offset", 0, 10**9)
        shown = in_genre[offset : offset + _int(params, "count", 10)]
        return {"songsByGenre": {"song": [view.song(s) for s in shown]}}
    if name in ("search2", "search3"):
        # An empty query lists everything: Symfonium syncs a library that way.
        query = _one(params, "query").strip().strip('"').strip("*")

        def paged(items: list, kind: str) -> list:
            offset = _int(params, f"{kind}Offset", 0, 10**9)
            return items[offset : offset + _int(params, f"{kind}Count", 20, 10**6)]

        found_artists = [
            a for a in idx.artists.values() if not query or _matches(query, a.name)
        ]
        found_albums = [
            a
            for a in idx.albums.values()
            if not query or _matches(query, a.name, a.artist)
        ]
        found_songs = [
            s
            for s in idx.songs.values()
            if not query or _matches(query, s.title, s.artist, s.album)
        ]
        folder = name == "search2"
        return {
            ("searchResult3" if name == "search3" else "searchResult2"): {
                "artist": [view.artist(a) for a in paged(found_artists, "artist")],
                "album": [
                    view.album(a, folder=folder) for a in paged(found_albums, "album")
                ],
                "song": [view.song(s) for s in paged(found_songs, "song")],
            }
        }
    if name in ("getStarred", "getStarred2"):
        folder = name == "getStarred"
        return {
            ("starred2" if name.endswith("2") else "starred"): {
                "artist": [
                    view.artist(a) for i, a in idx.artists.items() if i in view.stars
                ],
                "album": [
                    view.album(a, folder=folder)
                    for i, a in idx.albums.items()
                    if i in view.stars
                ],
                "song": [view.song(s) for i, s in idx.songs.items() if i in view.stars],
            }
        }
    if name == "getNowPlaying":
        return {"nowPlaying": {"entry": []}}
    return None


def _annotate(name: str, params: dict[str, list[str]], view: _View) -> Any:
    user = view.user
    if name in ("star", "unstar"):
        ids = (
            params.get("id", [])
            + params.get("albumId", [])
            + params.get("artistId", [])
        )
        sub.set_starred(user, ids, name == "star")
        return {}
    if name == "scrobble":
        ids = params.get("id", [])
        if not ids:
            raise _Fail(10, "Required parameter is missing: id")
        if _one(params, "submission", "true").lower() != "false":
            times = params.get("time", [])
            for n, song_id in enumerate(ids):
                stamp = times[n] if n < len(times) else ""
                when = float(stamp) / 1000 if stamp.isdigit() else None
                sub.scrobble(user, [song_id], when)
        return {}
    if name == "setRating":
        return {}
    if name == "getPlaylists":
        return {
            "playlists": {
                "playlist": [_playlist_head(view, p) for p in sub.playlists(user)]
            }
        }
    if name == "getPlaylist":
        found = sub.playlist(_required(params, "id"))
        if not found or (found["owner"] != user and not found["public"]):
            raise _Fail(70, "Playlist not found")
        return {"playlist": _playlist_full(view, found)}
    if name == "createPlaylist":
        existing = _one(params, "playlistId")
        songs = params.get("songId", [])
        if existing:
            _own_playlist(existing, user)
            new_id = sub.save_playlist(user, playlist_id=existing, songs=songs)
        else:
            new_id = sub.save_playlist(
                user, name=_required(params, "name"), songs=songs
            )
        return {"playlist": _playlist_full(view, sub.playlist(new_id))}
    if name == "updatePlaylist":
        found = _own_playlist(_required(params, "playlistId"), user)
        remove = {int(i) for i in params.get("songIndexToRemove", []) if i.isdigit()}
        songs = [s for n, s in enumerate(found["songs"]) if n not in remove]
        public = _one(params, "public")
        sub.save_playlist(
            user,
            playlist_id=found["id"],
            name=_one(params, "name") or None,
            comment=params["comment"][0] if "comment" in params else None,
            public=None if not public else public.lower() == "true",
            songs=songs + params.get("songIdToAdd", []),
        )
        return {}
    if name == "deletePlaylist":
        sub.delete_playlist(_own_playlist(_required(params, "id"), user)["id"])
        return {}
    if name == "getPlayQueue":
        saved = sub.play_queue(user)
        if not saved:
            return {}
        queued = [view.idx.songs[i] for i in saved["songs"] if i in view.idx.songs]
        return {
            "playQueue": {
                "current": saved["current"] or None,
                "position": saved["position"],
                "username": user,
                "changed": _iso(saved["changed"]),
                "changedBy": saved["changed_by"],
                "entry": [view.song(s) for s in queued],
            }
        }
    if name == "savePlayQueue":
        sub.save_play_queue(
            user,
            params.get("id", []),
            _one(params, "current"),
            _int(params, "position", 0, 10**12),
            _one(params, "c"),
        )
        return {}
    return None


def _lyrics(name: str, params: dict[str, list[str]], view: _View) -> Any:
    if name == "getLyrics":
        artist = _one(params, "artist").lower()
        title = _one(params, "title").lower()
        for s in view.idx.songs.values():
            if s.title.lower() != title or (artist and artist not in s.artist.lower()):
                continue
            found = sub.lyrics_for(s)
            if found:
                text = "\n".join(line for _, line in found[1])
                return {"lyrics": {"artist": s.artist, "title": s.title, "value": text}}
        return {"lyrics": {}}
    if name == "getLyricsBySongId":
        song = view.get_song(_required(params, "id"))
        structured = sub.structured_lyrics_for(song)
        if not structured:
            return {"lyricsList": {"structuredLyrics": []}}
        synced, lines = structured
        entry: dict[str, Any] = {
            "displayArtist": song.artist,
            "displayTitle": song.title,
            "lang": "und",
            "synced": synced,
            "offset": 0,
            "line": [
                (
                    {"start": line.start, "value": line.value}
                    if synced
                    else {"value": line.value}
                )
                for line in lines
            ],
        }
        # songLyrics version 2: word timing, only when the client asks for it.
        if _one(params, "enhanced").lower() == "true":
            entry["kind"] = "main"
            cues = [_cue_line(i, line) for i, line in enumerate(lines) if line.words]
            if synced and cues:
                entry["cueLine"] = cues
        return {"lyricsList": {"structuredLyrics": [entry]}}
    return None


def _cue_line(index: int, line: sub.LyricLine) -> dict[str, Any]:
    """An OpenSubsonic ``cueLine``: the words of ``line[index]``."""
    cues: list[dict[str, Any]] = []
    for word in line.words:
        cue: dict[str, Any] = {"start": word.start}
        if word.end is not None:
            cue["end"] = word.end
        cue.update(value=word.value, byteStart=word.byte_start, byteEnd=word.byte_end)
        cues.append(cue)
    out: dict[str, Any] = {"index": index, "start": line.words[0].start}
    if line.words[-1].end is not None:
        out["end"] = line.words[-1].end
    out.update(value=line.value, cue=cues)
    return out


_EMPTY: dict[str, dict[str, Any]] = {
    "getPodcasts": {"podcasts": {"channel": []}},
    "getInternetRadioStations": {"internetRadioStations": {"internetRadioStation": []}},
    "getBookmarks": {"bookmarks": {"bookmark": []}},
    "getShares": {"shares": {"share": []}},
}


def _media(name: str, params: dict[str, list[str]]) -> Response:
    idx = sub.index()
    if name in ("stream", "download"):
        song = idx.songs.get(_required(params, "id"))
        if song is None or not Path(song.path).is_file():
            raise _Fail(70, "Song not found")
        attachment = name == "download"
        return FileResponse(
            song.path,
            media_type=sub.content_type(song.suffix),
            filename=Path(song.path).name if attachment else None,
            content_disposition_type="attachment" if attachment else "inline",
        )
    # getCoverArt: an album, a song or an artist id.
    item = _required(params, "id")
    path = ""
    if item in idx.albums and idx.albums[item].songs:
        path = idx.albums[item].songs[0].path
    elif item in idx.songs:
        path = idx.songs[item].path
    elif item in idx.artists and idx.artists[item].albums:
        path = idx.artists[item].albums[0].songs[0].path
    data = _cover_bytes(path, _int(params, "size", 0, 2000)) if path else None
    if not data:
        raise _Fail(70, "Cover art not found")
    return Response(
        data,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


def build_subsonic_router(
    roots: Callable[[], list[str]],
    authenticate: Callable[[dict[str, str]], str] | None = None,
) -> APIRouter:
    """The /rest routes. `roots` names the folders a scan syncs.

    `authenticate` replaces the MORNYE_SUBSONIC_* login: Mornye Link passes
    one that accepts its paired devices' keys (core/link.py), and the API
    then answers without those variables. It returns the user whose stars
    and plays a request sees, or raises `sub.AuthError`."""
    router = APIRouter(prefix="/rest", include_in_schema=False)
    first_scan = {"done": False}

    def ensure_library() -> None:
        # A server nobody has opened the Player on has no stored library yet.
        if not first_scan["done"]:
            first_scan["done"] = True
            if not sub.index().songs:
                sub.start_scan(roots())

    def handle(name: str, params: dict[str, list[str]], user: str) -> Any:
        found = _system(name, params, user, roots)
        if found is not None:
            return found
        view = _View(user)
        for section in (_browse, _lists, _annotate, _lyrics):
            found = section(name, params, view)
            if found is not None:
                return found
        if name in _EMPTY:
            return _EMPTY[name]
        raise _Fail(0, f"Method not supported: {name}")

    @router.api_route("/{method}", methods=["GET", "POST"])
    async def rest(method: str, request: Request) -> Response:
        params = await _params(request)
        name = method[:-5] if method.endswith(".view") else method
        if authenticate is None and not sub.enabled():
            return _error(
                params,
                0,
                "The Subsonic API is off: set MORNYE_SUBSONIC_PASSWORD "
                "(or MORNYE_SUBSONIC_API_KEY) to turn it on.",
            )
        try:
            user = (authenticate or sub.authenticate)(
                {k: v[0] for k, v in params.items() if v}
            )
        except sub.AuthError as exc:
            # OpenSubsonic: the extension list answers without credentials,
            # so a client can learn the supported auth methods first.
            if name == "getOpenSubsonicExtensions":
                return _respond(params, handle(name, params, sub.username()))
            return _error(params, exc.code, str(exc))
        try:
            ensure_library()
            if name in ("stream", "download", "getCoverArt"):
                return _media(name, params)
            return _respond(params, handle(name, params, user))
        except _Fail as exc:
            return _error(params, exc.code, str(exc))
        except Exception:
            logger.exception("[subsonic] %s failed", name)
            return _error(params, 0, "Internal error")

    return router


__all__ = ["build_subsonic_router"]
