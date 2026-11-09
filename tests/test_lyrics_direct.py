"""KuGou, QQ Music and Genius lyrics (core/lyrics_direct.py), offline."""

from __future__ import annotations

import asyncio
import base64
from urllib.parse import urlsplit

import httpx
import pytest

from mornye.core import lyrics
from mornye.core import lyrics_direct as ld

LRC = "[00:01.00]first line\n[00:05.50]second line"


class _Client:
    """Answers by URL prefix; records every request."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    async def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, dict(params or {}), dict(headers or {})))
        for prefix, answer in self.routes:
            if url.startswith(prefix):
                status, body = answer(params or {}) if callable(answer) else answer
                req = httpx.Request("GET", url)
                if isinstance(body, str):
                    return httpx.Response(status, text=body, request=req)
                return httpx.Response(status, json=body, request=req)
        return httpx.Response(404, request=httpx.Request("GET", url))


def _run(monkeypatch, client, coro_fn):
    async def _get():
        return client

    monkeypatch.setattr(ld.NetworkManager, "get_async_client_safe", _get)
    return asyncio.run(coro_fn())


def test_unanswered_names_are_now_registered():
    for name in ("kugou", "qq", "genius"):
        assert name in lyrics._PROVIDER_MAP


def test_pick_prefers_matching_title_artist_and_closest_duration():
    cands = [
        {"title": "Song", "artist": "Other", "duration": 200},
        {"title": "Song (Live)", "artist": "Band", "duration": 260},
        {"title": "Song", "artist": "Band", "duration": 215},
        {"title": "Song", "artist": "Band", "duration": 201},
        {"title": "Song", "artist": "Band", "duration": 30},  # a preview
    ]
    assert ld._pick(cands, "Song", "Band", 200)["duration"] == 201
    assert ld._pick(cands, "Different", "Band", 200) is None
    only_preview = [{"title": "Song", "artist": "Band", "duration": 30}]
    assert ld._pick(only_preview, "Song", "Band", 200) is None


def test_kugou_decodes_and_retries_with_bare_title(monkeypatch):
    def search(params):
        if params["keyword"] == "Band Song":
            hit = {"songname": "Nope", "singername": "X", "hash": "h0", "duration": 200}
        else:
            hit = {
                "songname": "Song",
                "singername": "Band",
                "hash": "h1",
                "duration": 201,
            }
        return 200, {"data": {"info": [hit]}}

    raw = ("﻿" + LRC.replace("\n", "\r\n")).encode()
    client = _Client(
        [
            (ld._KUGOU_SEARCH, search),
            (
                ld._KUGOU_LYRICS_SEARCH,
                (200, {"candidates": [{"id": "9", "accesskey": "k"}]}),
            ),
            (
                ld._KUGOU_LYRICS_DOWNLOAD,
                (200, {"content": base64.b64encode(raw).decode()}),
            ),
        ]
    )
    text = _run(monkeypatch, client, lambda: ld.fetch_kugou_async("Song", "Band", 200))
    assert text == LRC
    lyric_search = [c for c in client.calls if c[0] == ld._KUGOU_LYRICS_SEARCH][0]
    assert lyric_search[1]["hash"] == "h1"
    assert lyric_search[1]["duration"] == "201000"


def test_kugou_rejects_unsynced_text(monkeypatch):
    hit = {"songname": "Song", "singername": "Band", "hash": "h", "duration": 200}
    client = _Client(
        [
            (ld._KUGOU_SEARCH, (200, {"data": {"info": [hit]}})),
            (
                ld._KUGOU_LYRICS_SEARCH,
                (200, {"candidates": [{"id": "1", "accesskey": "k"}]}),
            ),
            (
                ld._KUGOU_LYRICS_DOWNLOAD,
                (200, {"content": base64.b64encode(b"just text").decode()}),
            ),
        ]
    )
    assert (
        _run(monkeypatch, client, lambda: ld.fetch_kugou_async("Song", "Band", 200))
        == ""
    )


def test_qq_lyric_decoding():
    assert ld.decode_qq_lyric("[00&#58;01.00]a &amp; b") == "[00:01.00]a & b"
    assert ld.decode_qq_lyric(base64.b64encode(LRC.encode()).decode()) == LRC
    assert ld.decode_qq_lyric("") == ""


def test_qq_fetch(monkeypatch):
    song = {"name": "Song", "singer": [{"name": "Band"}], "interval": 199, "mid": "M1"}
    client = _Client(
        [
            (ld._QQ_SEARCH, (200, {"data": {"song": {"list": [song]}}})),
            (ld._QQ_LYRICS, (200, {"lyric": LRC.replace(":", "&#58;")})),
        ]
    )
    assert (
        _run(monkeypatch, client, lambda: ld.fetch_qq_async("Song", "Band", 200)) == LRC
    )
    lyric_call = [c for c in client.calls if c[0] == ld._QQ_LYRICS][0]
    assert lyric_call[1]["songmid"] == "M1"
    assert lyric_call[2]["Referer"] == "https://y.qq.com/"


GENIUS_PAGE = """
<html><body>
<div data-lyrics-container="true" class="a">
  <div data-exclude-from-selection="true">Translations</div>
  [Verse 1]<br/>Line one &amp; more<br><a href="#"><span>Line two</span></a>
</div>
<div class="ad">advert</div>
<div data-lyrics-container="true">[Chorus]<br>Chorus <i>line</i><div>nested</div></div>
</body></html>
"""


def test_genius_keeps_every_container():
    text = ld.extract_genius_lyrics(GENIUS_PAGE)
    assert text == "[Verse 1]\nLine one & more\nLine two\n\n[Chorus]\nChorus linenested"
    assert "advert" not in text and "Translations" not in text


def _genius_hit(url):
    return {"result": {"title": "Song", "primary_artist": {"name": "Band"}, "url": url}}


def test_genius_uses_official_api_with_token(monkeypatch):
    monkeypatch.setenv("MORNYE_GENIUS_TOKEN", "tok")
    page = "https://genius.com/Band-song-lyrics"
    client = _Client(
        [
            (ld._GENIUS_API_SEARCH, (200, {"response": {"hits": [_genius_hit(page)]}})),
            (page, (200, GENIUS_PAGE)),
        ]
    )
    text = _run(monkeypatch, client, lambda: ld.fetch_genius_async("Song", "Band"))
    assert text.startswith("[Verse 1]")
    assert client.calls[0][2]["Authorization"] == "Bearer tok"


def test_genius_without_token_uses_web_search_and_ignores_foreign_urls(monkeypatch):
    monkeypatch.delenv("MORNYE_GENIUS_TOKEN", raising=False)
    page = "https://genius.com/Band-song-lyrics"
    hits = [_genius_hit("https://evil.example/x"), _genius_hit(page)]
    client = _Client(
        [
            (ld._GENIUS_SEARCH, (200, {"response": {"sections": [{"hits": hits}]}})),
            (page, (200, GENIUS_PAGE)),
        ]
    )
    assert _run(monkeypatch, client, lambda: ld.fetch_genius_async("Song", "Band"))
    hosts = {urlsplit(c[0]).hostname for c in client.calls}
    assert "evil.example" not in hosts


@pytest.mark.parametrize("status", [403, 500])
def test_genius_refusal_is_a_quiet_miss(monkeypatch, status):
    monkeypatch.delenv("MORNYE_GENIUS_TOKEN", raising=False)
    client = _Client([(ld._GENIUS_SEARCH, (status, "<html>blocked</html>"))])
    assert (
        _run(monkeypatch, client, lambda: ld.fetch_genius_async("Song", "Band")) == ""
    )
