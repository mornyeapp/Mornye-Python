"""core/connect/link_playlists.py — the Player's playlists over Mornye Link.

A phone sees and edits the same playlists as the Player page (feature
``playlists``, docs/architecture/link-protocol.md), not Subsonic's separate
ones. Songs travel as TrackRefs, the shape every other Link op uses: a song in
this library carries its ``songId`` (the ``/rest/stream`` id), a song not
downloaded yet only its title, artist, album, ISRC and duration. Each entry
also has an opaque ``key``: sending it back keeps that entry as it is.

Every change, whoever made it (the Player page, a phone, the follow
scheduler), reaches the connected phones as a ``playlists.changed`` frame; a
change a phone made reaches the account's pages too, as
``app_player_playlists_changed``.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from typing import Any

from .. import player_playlists
from .model import CommandError, TrackRef

logger = logging.getLogger(__name__)

#: The page event a playlist change made elsewhere is announced with.
PAGE_EVENT = "app_player_playlists_changed"


def _file_key(path: str) -> str:
    return "file:" + hashlib.sha1(path.encode("utf-8", "replace")).hexdigest()[:20]


def _text(args: dict[str, Any], name: str, required: bool = False) -> str | None:
    value = args.get(name)
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise CommandError("invalid", f"{name} is required")
    return value


def _ms(seconds: Any) -> int:
    try:
        return int(round(float(seconds or 0) * 1000))
    except (TypeError, ValueError):
        return 0


def encode_cover(data_url: str) -> str:
    """A cover as the Player stores it (a 512 px JPEG data URL)."""
    from ...api_mixins.player_playlists import PlayerPlaylistsMixin

    return PlayerPlaylistsMixin()._playlist_cover(data_url)


class LinkPlaylists:
    """The ``playlists*`` ops of one Link server. ``owner()`` is the account
    whose playlists phones see (None: the desktop app's); ``notify_pages(ids,
    deleted)`` tells that account's pages."""

    def __init__(
        self,
        send: Callable[[str, dict[str, Any]], None],
        connected: Callable[[], list[str]],
        *,
        catalog: Callable[[], Any],
        owner: Callable[[], str | None],
        notify_pages: Callable[[list[str], bool], None] = lambda ids, deleted: None,
    ) -> None:
        self._send = send
        self._connected = connected
        self._catalog = catalog
        self._owner_fn = owner
        self._notify_pages = notify_pages
        self._unlisten = player_playlists.add_change_listener(self._on_change)

    def close(self) -> None:
        self._unlisten()

    def owner(self) -> str:
        return self._owner_fn() or ""

    # ── pushes ──
    def _on_change(self, owner: str, ids: list[str], deleted: bool) -> None:
        if owner != self.owner():
            return
        frame = {"type": "playlists.changed", "ids": ids, "deleted": deleted}
        for device_id in self._connected():
            self._send(device_id, frame)

    # ── ops ──
    @staticmethod
    def handles(op: str) -> bool:
        return op == "playlists" or op.startswith("playlists/")

    def op(self, op: str, method: str, args: dict[str, Any], device_id: str) -> Any:
        handlers: dict[tuple[str, str], Callable[[dict[str, Any]], Any]] = {
            ("playlists", "GET"): self._list,
            ("playlists/get", "GET"): self._get,
            ("playlists/cover", "GET"): self._cover,
            ("playlists/create", "POST"): self._create,
            ("playlists/update", "POST"): self._update,
            ("playlists/delete", "POST"): self._delete,
        }
        handler = handlers.get((op, method))
        if handler is None:
            raise CommandError("not_found", f"No such op: {op}")
        try:
            return handler(args)
        except player_playlists.PlaylistConflict as exc:
            raise CommandError("conflict", str(exc)) from None
        except ValueError as exc:
            raise CommandError("invalid", str(exc)) from None

    # ── reading ──
    def _summary(self, playlist: dict[str, Any]) -> dict[str, Any]:
        tracks = playlist["tracks"]
        return {
            "id": playlist["id"],
            "name": playlist["name"],
            "description": playlist["description"],
            "trackCount": len(tracks),
            "downloadedCount": sum(1 for t in tracks if t["downloaded"]),
            "durationMs": sum(_ms(t["duration_s"]) for t in tracks),
            "createdMs": _ms(playlist["created_at"]),
            "updatedMs": player_playlists.updated_ms(playlist),
            "hasCover": bool(playlist["cover"]),
            "followsUrl": playlist["source_url"],
            "syncedMs": _ms(playlist["synced_at"]),
        }

    def _track_json(self, track: dict[str, Any], catalog: Any) -> dict[str, Any]:
        song = (
            catalog.song_for_path(track["path"])
            if catalog is not None and track["downloaded"]
            else None
        )
        if song is not None:
            out = catalog.ref_for_song(song).to_json()
            key = song.id
        else:
            ref = catalog.ref_for_item(track) if catalog is not None else None
            out = (
                ref.to_json()
                if ref is not None
                else {"title": track["title"], "artist": track["artist"]}
            )
            key = (
                track["path"]
                if player_playlists.is_remote(track["path"])
                else _file_key(track["path"])
            )
        out["key"] = key
        out["downloaded"] = song is not None
        if track["source_url"]:
            out["sourceUrl"] = track["source_url"]
        return out

    def full(self, playlist: dict[str, Any]) -> dict[str, Any]:
        catalog = self._catalog()
        return {
            **self._summary(playlist),
            "tracks": [self._track_json(t, catalog) for t in playlist["tracks"]],
        }

    def _require(self, args: dict[str, Any]) -> dict[str, Any]:
        playlist_id = _text(args, "id", required=True)
        playlist = player_playlists.get_playlist(str(playlist_id), self.owner())
        if playlist is None:
            raise CommandError("not_found", "No such playlist")
        return playlist

    def _list(self, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "playlists": [
                self._summary(p) for p in player_playlists.list_playlists(self.owner())
            ]
        }

    def _get(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"playlist": self.full(self._require(args))}

    def _cover(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"cover": self._require(args)["cover"]}

    # ── writing ──
    def _entries(self, raw: Any, current: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """A phone's track list as stored entries: a ``key`` it was sent
        keeps that entry, a TrackRef is the library's song when there is
        one, else a song not downloaded."""
        if not isinstance(raw, list):
            raise CommandError("invalid", "tracks must be a list")
        if len(raw) > player_playlists.MAX_TRACKS:
            raise CommandError("invalid", "The playlist has too many tracks")
        catalog = self._catalog()
        by_key = {self._track_json(t, catalog)["key"]: t for t in current}
        entries: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                raise CommandError("invalid", "A track must be an object")
            kept = by_key.get(str(item.get("key") or ""))
            if kept is not None:
                entries.append(kept)
                continue
            ref = TrackRef.from_json(item)
            if ref is None:
                raise CommandError("invalid", "A track needs a key or a title")
            song = catalog.song_for_ref(ref) if catalog is not None else None
            if song is not None:
                entries.append(
                    {
                        "path": song.path,
                        "title": song.title,
                        "artist": song.artist,
                        "album": song.album,
                        "duration_s": float(song.duration or 0),
                        "isrc": song.isrc,
                    }
                )
                continue
            source_url = item.get("sourceUrl")
            entry: dict[str, Any] = {
                "title": ref.title[:500],
                "artist": ref.artist[:500],
                "album": (ref.album or "")[:500],
                "duration_s": (ref.durationMs or 0) / 1000,
                "isrc": (ref.isrc or "").strip().upper()[:32],
                "source_url": (
                    source_url[:2048]
                    if isinstance(source_url, str)
                    and source_url.startswith(("http://", "https://"))
                    else ""
                ),
            }
            entry["path"] = player_playlists.remote_key(entry)
            entries.append(entry)
        return entries

    @staticmethod
    def _cover_in(args: dict[str, Any], stored: str = "") -> str:
        """``cover`` absent keeps the stored one, "" removes it, a data URL
        replaces it."""
        cover = _text(args, "cover")
        if cover is None:
            return stored
        if cover == stored or not cover:
            return cover
        return encode_cover(cover)

    def changed_here(self, ids: list[str], deleted: bool = False) -> None:
        """Tells the pages about a change the phone made."""
        try:
            self._notify_pages(ids, deleted)
        except Exception:
            logger.debug("Link: could not tell the pages", exc_info=True)

    def _create(self, args: dict[str, Any]) -> dict[str, Any]:
        name = _text(args, "name", required=True)
        tracks = args.get("tracks")
        playlist = player_playlists.create_playlist(
            str(name),
            _text(args, "description") or "",
            self._cover_in(args),
            self._entries([] if tracks is None else tracks, []),
            self.owner(),
        )
        self.changed_here([playlist["id"]])
        return {"playlist": self.full(playlist)}

    def _update(self, args: dict[str, Any]) -> dict[str, Any]:
        playlist = self._require(args)
        base = args.get("baseUpdatedMs")
        if base is not None and (not isinstance(base, int) or isinstance(base, bool)):
            raise CommandError("invalid", "baseUpdatedMs must be a number")
        if base is not None and base != player_playlists.updated_ms(playlist):
            raise CommandError("conflict", "The playlist changed; reload it")
        name, description = _text(args, "name"), _text(args, "description")
        if name is not None or description is not None or "cover" in args:
            updated = player_playlists.update_playlist(
                playlist["id"],
                self.owner(),
                name=playlist["name"] if name is None else name,
                description=(
                    playlist["description"] if description is None else description
                ),
                cover=self._cover_in(args, playlist["cover"]),
            )
            playlist = updated or playlist
        if "tracks" in args:
            entries = self._entries(args["tracks"], playlist["tracks"])
            updated = player_playlists.set_tracks(playlist["id"], self.owner(), entries)
            playlist = updated or playlist
        self.changed_here([playlist["id"]])
        return {"playlist": self.full(playlist)}

    def _delete(self, args: dict[str, Any]) -> dict[str, Any]:
        playlist = self._require(args)
        player_playlists.delete_playlist(playlist["id"], self.owner())
        self.changed_here([playlist["id"]], deleted=True)
        return {"ok": True}
