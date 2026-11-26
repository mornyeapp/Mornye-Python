"""core/connect/resolver.py — finding a TrackRef in this computer's library.

The order is the Mornye app's desktop host (``link_desktop_ops.dart``
``_resolveQueue``, ``link_catalog_index.dart`` ``songIdForRef``):

1. ``songId`` when ``serverId`` is this computer's (an id with no server
   also has to carry the same title, since it may come from another
   desktop's catalog);
2. ISRC, the closest duration winning;
3. ``matchKey`` (or the title and artist made into one), durations within
   two seconds;
4. the loose title/artist key, durations within two seconds;
5. the copy a phone uploaded as ``received/<fileId>.<ext>``;
6. else missing.

The library is the Subsonic index (``core/subsonic.index()``), so a song's
``songId`` here is the id the phone streams it by through ``/rest``.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .model import (
    DURATION_TOLERANCE_MS,
    TrackRef,
    is_file_id,
    loose_key,
    match_key,
    normalize_isrc,
)


@dataclass(frozen=True)
class Resolved:
    """A playable file: ``song`` is the library entry when there is one,
    None for a phone's upload."""

    path: str
    title: str
    artist: str
    album: str
    duration_ms: int | None
    song: Any = None

    def item(self) -> dict[str, Any]:
        """The page's queue entry (``playerQueue``) for this file."""
        out: dict[str, Any] = {
            "path": self.path,
            "title": self.title,
            "artist": self.artist,
        }
        if self.album:
            out["album"] = self.album
        if self.duration_ms:
            out["duration_s"] = round(self.duration_ms / 1000)
        return out


def _duration_ms(song: Any) -> int | None:
    seconds = getattr(song, "duration", 0) or 0
    return int(seconds) * 1000 if seconds else None


class Catalog:
    """Lookups over one Subsonic index. Built once per index (the index is
    rebuilt only when the stored library changes)."""

    def __init__(self, index: Any, server_id: str) -> None:
        self.index = index
        self.server_id = server_id
        self._by_isrc: dict[str, list[Any]] = {}
        self._by_match: dict[str, list[Any]] = {}
        self._by_loose: dict[str, list[Any]] = {}
        self._by_path: dict[str, Any] = {}
        for song in index.songs.values():
            self._by_path[song.path] = song
            isrc = normalize_isrc(getattr(song, "isrc", ""))
            if isrc:
                self._by_isrc.setdefault(isrc, []).append(song)
            key = match_key(song.title, song.artist)
            if key:
                self._by_match.setdefault(key, []).append(song)
            loose = loose_key(song.title, song.artist)
            if loose:
                self._by_loose.setdefault(loose, []).append(song)

    # ── refs out ──
    def song_for_path(self, path: str) -> Any:
        return self._by_path.get(path)

    def ref_for_song(self, song: Any) -> TrackRef:
        key = match_key(song.title, song.artist)
        isrc = normalize_isrc(getattr(song, "isrc", ""))
        return TrackRef(
            title=song.title,
            artist=song.artist,
            album=song.album or None,
            durationMs=_duration_ms(song),
            isrc=isrc or None,
            matchKey=key or None,
            songId=song.id,
            serverId=self.server_id or None,
        )

    def ref_for_item(self, item: dict[str, Any]) -> TrackRef | None:
        """A page's queue entry (``{path, title, artist, ...}``) as other
        devices find it: the library's song when it knows the file."""
        song = self.song_for_path(str(item.get("path") or ""))
        if song is not None:
            return self.ref_for_song(song)
        title = str(item.get("title") or "").strip()
        if not title:
            path = str(item.get("path") or "")
            title = Path(path).stem if path else ""
        if not title:
            return None
        artist = str(item.get("artist") or "")
        duration = item.get("duration_s")
        key = match_key(title, artist)
        return TrackRef(
            title=title,
            artist=artist,
            album=str(item.get("album") or "") or None,
            durationMs=(
                int(float(duration) * 1000)
                if isinstance(duration, (int, float))
                and not isinstance(duration, bool)
                and duration > 0
                else None
            ),
            isrc=normalize_isrc(str(item.get("isrc") or "")) or None,
            matchKey=key or None,
        )

    # ── refs in ──
    def song_for_ref(self, ref: TrackRef) -> Any:
        if ref.songId:
            song = self.index.songs.get(ref.songId)
            if song is not None:
                same = (
                    loose_key(song.title, "") == loose_key(ref.title, "")
                    if ref.serverId is None
                    else ref.serverId == self.server_id
                )
                if same:
                    return song
        isrc = normalize_isrc(ref.isrc)
        if isrc:
            hit = _closest(self._by_isrc.get(isrc), ref.durationMs, require=False)
            if hit is not None:
                return hit
        key = (ref.matchKey or "").strip().lower() or match_key(ref.title, ref.artist)
        if key:
            hit = _closest(self._by_match.get(key), ref.durationMs)
            if hit is not None:
                return hit
        loose = loose_key(ref.title, ref.artist)
        if loose:
            return _closest(self._by_loose.get(loose), ref.durationMs)
        return None

    def resolve(
        self,
        refs: Iterable[TrackRef | None],
        received: dict[str, str] | None = None,
    ) -> list[Resolved | None]:
        """Each ref as this computer plays it: the library's song, else the
        file a phone uploaded for it (``received``: fileId → path), else
        None."""
        received = received or {}
        out: list[Resolved | None] = []
        for ref in refs:
            if ref is None:
                out.append(None)
                continue
            song = self.song_for_ref(ref)
            if song is not None:
                out.append(
                    Resolved(
                        song.path,
                        song.title,
                        song.artist,
                        song.album,
                        _duration_ms(song),
                        song,
                    )
                )
                continue
            path = received.get(ref.fileId or "")
            out.append(
                Resolved(path, ref.title, ref.artist, ref.album or "", ref.durationMs)
                if path
                else None
            )
        return out


def _closest(
    candidates: list[Any] | None, duration_ms: int | None, require: bool = True
) -> Any:
    """The candidate whose duration is closest to ``duration_ms``. Unless
    ``require`` is false, known durations further apart than the tolerance
    never match; a candidate of unknown duration is the fallback."""
    if not candidates:
        return None
    if duration_ms is None:
        return candidates[0]
    best, best_diff, unknown = None, None, None
    for song in candidates:
        song_ms = _duration_ms(song)
        if song_ms is None:
            unknown = unknown or song
            continue
        diff = abs(song_ms - duration_ms)
        if require and diff > DURATION_TOLERANCE_MS:
            continue
        if best_diff is None or diff < best_diff:
            best, best_diff = song, diff
    return best if best is not None else unknown


def received_files(directory: Path) -> dict[str, str]:
    """fileId → path of the songs phones uploaded (``<fileId>.<ext>``)."""
    out: dict[str, str] = {}
    try:
        entries = list(directory.iterdir())
    except OSError:
        return out
    for entry in entries:
        stem = entry.name.split(".", 1)[0]
        if is_file_id(stem) and entry.is_file():
            out[stem] = str(entry)
    return out


_lock = threading.Lock()
_cached: dict[str, Any] = {"index": None, "server": None, "catalog": None}


def catalog(server_id: str | None = None) -> Catalog:
    """The catalog over the current library index, rebuilt when the index
    or the Link server id changes."""
    from .. import subsonic

    if server_id is None:
        server_id = _server_id()
    index = subsonic.index()
    with _lock:
        if _cached["index"] is not index or _cached["server"] != server_id:
            _cached.update(
                index=index, server=server_id, catalog=Catalog(index, server_id)
            )
        return _cached["catalog"]


def _server_id() -> str:
    """This computer's Link server id: the ``serverId`` phones see in its
    TrackRefs and in ``info``."""
    try:
        from ...link_server import get_link_server

        return get_link_server().store.server_id
    except Exception:
        return ""
