"""Share → Copy Link (api_mixins/music_extras.py track_share_links)."""

from __future__ import annotations

import pytest

from mornye.api_mixins import search as search_mod
from mornye.api_mixins.music_extras import MusicExtrasMixin
from mornye.core import link_resolver


@pytest.fixture
def resolver(monkeypatch):
    calls: list[tuple[str, str]] = []

    async def by_isrc(self, isrc):
        calls.append(("isrc", isrc))
        return "https://www.deezer.com/track/1" if isrc == "USRC17607839" else ""

    async def by_url(self, url):
        calls.append(("url", url))
        return {
            "tidal": "https://tidal.com/track/2",
            "isrc": "USRC17607839",
            "bad": "javascript:x",
        }

    monkeypatch.setattr(
        link_resolver.LinkResolver, "_get_deezer_url_by_isrc_async", by_isrc
    )
    monkeypatch.setattr(
        link_resolver.LinkResolver, "_get_resolve_links_by_url_async", by_url
    )
    return calls


def _search(monkeypatch, tracks):
    async def fake(query, limit=50, source=None):
        return {"tracks": tracks}

    monkeypatch.setattr(search_mod, "search_metadata_async", fake)


def test_share_links_by_isrc(resolver):
    res = MusicExtrasMixin().track_share_links("Song", "Artist", "usrc17607839")
    assert res == {
        "status": "ok",
        "links": {
            "deezer": "https://www.deezer.com/track/1",
            "tidal": "https://tidal.com/track/2",
        },
    }
    assert resolver[0] == ("isrc", "USRC17607839")


def test_share_links_by_search_keeps_only_a_matching_result(resolver, monkeypatch):
    _search(
        monkeypatch,
        [
            {
                "name": "Other Song",
                "artists": "Artist",
                "external_url": "https://open.spotify.com/track/a",
            },
            {
                "name": "Song",
                "artists": "Someone Else",
                "external_url": "https://open.spotify.com/track/b",
            },
            {
                "name": "Song (Remastered)",
                "artists": "Artist, Guest",
                "external_url": "https://open.spotify.com/track/c",
            },
        ],
    )
    res = MusicExtrasMixin().track_share_links("Song", "Artist")
    assert res["links"]["spotify"] == "https://open.spotify.com/track/c"
    assert ("url", "https://open.spotify.com/track/c") in resolver


def test_share_links_not_found(resolver, monkeypatch):
    _search(
        monkeypatch, [{"name": "Else", "artists": "X", "external_url": "https://x"}]
    )
    res = MusicExtrasMixin().track_share_links("Song", "Artist")
    assert res["status"] == "error"
