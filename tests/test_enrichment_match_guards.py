"""Enrichment only takes what it can show belongs to the track and release.

These pin the rules borrowed from Mornye's own re-enrich: a search hit is
verified before any field is used, release fields need the same release,
and an ISRC answer whose running time is a preview's or another take's is
not the track.
"""

from __future__ import annotations

import asyncio

import pytest

from mornye.core import metadata_enrichment as me


class _Response:
    status_code = 200
    is_success = True

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


@pytest.mark.parametrize(
    ("expected_ms", "found_ms", "expected", "found", "other"),
    [
        (200_000, 205_000, "Song", "Song", False),  # within 10s
        (200_000, 30_000, "Song", "Song", True),  # a preview clip
        (200_000, 260_000, "Song", "Song (Extended Mix)", True),  # another take
        (200_000, 260_000, "Song", "song", False),  # same title, catalog length
        (0, 30_000, "Song", "Song", False),  # unknown length proves nothing
        (200_000, 0, "Song", "Other", False),
    ],
)
def test_other_length(expected_ms, found_ms, expected, found, other) -> None:
    assert me._other_length(expected_ms, found_ms, expected, found) is other


def test_apple_takes_no_release_fields_from_another_release(monkeypatch) -> None:
    apple = me._AppleMusicMeta()
    item = {
        "trackName": "Blinding Lights",
        "artistName": "The Weeknd",
        "collectionName": "Now That's What I Call Music! 105",
        "trackTimeMillis": 200_000,
        "primaryGenreName": "R&B/Soul",
        "artworkUrl100": "https://x/100x100.jpg",
        "releaseDate": "2020-05-01T07:00:00Z",
        "trackCount": 44,
        "discCount": 2,
    }

    async def search(*_args):
        return item

    monkeypatch.setattr(apple, "_search_async", search)
    out = asyncio.run(
        apple.fetch_async("Blinding Lights", "The Weeknd", "", "After Hours", 200_000)
    )
    assert out.genre == "R&B/Soul"
    assert (out.cover_url_hd, out.release_date) == ("", "")
    assert (out.total_tracks, out.total_discs, out.album_type) == (0, 0, "")


def test_deezer_rejects_an_isrc_answer_that_is_a_preview(monkeypatch) -> None:
    class _Client:
        async def get(self, url, timeout=None, headers=None):
            if "/track/" in url:
                return _Response(
                    {"title": "Song", "isrc": "X", "duration": 30, "album": {"id": 1}}
                )
            return _Response({"title": "Album", "label": "L"})

    async def client_safe():
        return _Client()

    monkeypatch.setattr(me.NetworkManager, "get_async_client_safe", client_safe)
    out = asyncio.run(me._DeezerMeta().fetch_async("X", "Album", "Song", 200_000))
    assert out.label == ""
    assert out.isrc == ""


def test_deezer_still_answers_when_the_length_agrees(monkeypatch) -> None:
    class _Client:
        async def get(self, url, timeout=None, headers=None):
            if "/track/" in url:
                return _Response(
                    {"title": "Song", "isrc": "X", "duration": 201, "album": {"id": 1}}
                )
            return _Response({"title": "Album", "label": "L"})

    async def client_safe():
        return _Client()

    monkeypatch.setattr(me.NetworkManager, "get_async_client_safe", client_safe)
    out = asyncio.run(me._DeezerMeta().fetch_async("X", "Album", "Song", 200_000))
    assert out.label == "L"
    assert out.isrc == "X"
