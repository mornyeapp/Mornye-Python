"""The recent-links card shows the collection's own picture.

Downloading an artist re-recorded its link with the first track's cover,
replacing the artist photo the fetch had stored.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from mornye.application.download_engine import MornyeDownloader
from mornye.core import session_memory


def _record(info, tracks, monkeypatch):
    seen = {}

    async def fake_add(url, **kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(session_memory, "add_url_to_history_async", fake_add)
    engine = MornyeDownloader.__new__(MornyeDownloader)
    asyncio.run(engine._record_history_async("https://x", "Name", tracks, info))
    return seen


def test_an_artist_keeps_its_photo_not_the_first_songs_cover(monkeypatch):
    tracks = [SimpleNamespace(cover_url="https://song", artists="A")]
    seen = _record({"type": "artist", "cover": "https://photo"}, tracks, monkeypatch)
    assert seen["cover"] == "https://photo"


def test_an_artist_without_a_photo_does_not_borrow_a_songs(monkeypatch):
    tracks = [SimpleNamespace(cover_url="https://song", artists="A")]
    assert _record({"type": "artist"}, tracks, monkeypatch)["cover"] == ""


def test_an_album_may_use_its_tracks_cover(monkeypatch):
    tracks = [SimpleNamespace(cover_url="https://album", artists="A")]
    assert _record({"type": "album"}, tracks, monkeypatch)["cover"] == "https://album"


def test_a_record_without_a_cover_keeps_the_one_stored(monkeypatch):
    store = {"url_history": []}

    async def load():
        return store

    async def save(data):
        store.update(data)

    monkeypatch.setattr(session_memory, "_load_async", load)
    monkeypatch.setattr(session_memory, "_save_async", save)
    url = "https://open.spotify.com/artist/abc"
    asyncio.run(
        session_memory.add_url_to_history_async(url, label="A", cover="https://photo")
    )
    asyncio.run(session_memory.add_url_to_history_async(url, label="A", cover=""))
    assert store["url_history"][0]["cover"] == "https://photo"
    assert len(store["url_history"]) == 1
