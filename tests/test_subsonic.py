"""The Subsonic / OpenSubsonic API under /rest (webapi/subsonic.py).

Apps such as Symfonium or Feishin play a --web instance's library through
it. These drive the real app with a TestClient, over a stored library of
real files, the way such a client would.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from mornye import webapp
from mornye.core import player_library
from mornye.core import subsonic as sub

PASSWORD = "sesame"


def _row(path: Path, title: str, artist: str, album: str, **extra) -> tuple:
    row = {
        "path": str(path),
        "title": title,
        "artist": artist,
        "album": album,
        "album_artist": extra.get("album_artist", ""),
        "track": extra.get("track", 1),
        "disc": 1,
        "year": extra.get("year", "2021"),
        "genre": extra.get("genre", "Pop"),
        "duration_s": 200.0,
        "format": "FLAC",
    }
    return (str(path), 1, path.stat().st_size, row)


@pytest.fixture()
def library(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "Music"
    root.mkdir()
    files = {}
    for name in ("kmm", "woman", "massacre", "candy"):
        files[name] = root / f"{name}.flac"
        files[name].write_bytes(b"fLaC" + name.encode() * 500)
    (root / "kmm.lrc").write_text(
        "[00:05.00]We can remember swimming\n[00:09.50]Heading for the city\n",
        encoding="utf-8",
    )
    player_library.store(
        [
            _row(
                files["kmm"],
                "Kiss Me More",
                "Doja Cat;SZA",
                "Planet Her",
                album_artist="Doja Cat",
                track=3,
            ),
            _row(
                files["woman"],
                "Woman",
                "Doja Cat",
                "Planet Her",
                album_artist="Doja Cat",
                track=1,
            ),
            _row(
                files["massacre"],
                "In Da Club",
                "50 Cent",
                "Get Rich",
                genre="Hip-Hop",
                year="2003",
            ),
            _row(
                files["candy"],
                "Candy Shop",
                "50 Cent;Olivia",
                "The Massacre",
                album_artist="50 Cent;Olivia",
                genre="Hip-Hop",
                year="2005",
            ),
        ]
    )
    return files


@pytest.fixture()
def client(monkeypatch) -> TestClient:
    monkeypatch.setenv(sub.PASSWORD_ENV, PASSWORD)
    monkeypatch.setenv(sub.USER_ENV, "me")
    return TestClient(webapp.create_app(token=None))


def _auth(**params) -> dict:
    salt = "c19b2d"
    token = hashlib.md5((PASSWORD + salt).encode()).hexdigest()
    return {
        "u": "me",
        "t": token,
        "s": salt,
        "c": "test",
        "v": "1.16.1",
        "f": "json",
        **params,
    }


def _get(client: TestClient, method: str, **params) -> dict:
    resp = client.get(f"/rest/{method}.view", params=_auth(**params))
    assert resp.status_code == 200
    body = resp.json()["subsonic-response"]
    assert body["status"] == "ok", body
    return body


# ── switching on, and signing in ─────────────────────────────────────────


def test_off_until_a_password_is_set(monkeypatch):
    monkeypatch.delenv(sub.PASSWORD_ENV, raising=False)
    monkeypatch.delenv(sub.API_KEY_ENV, raising=False)
    client = TestClient(webapp.create_app(token=None))

    body = client.get("/rest/ping", params={"f": "json"}).json()["subsonic-response"]

    assert body["status"] == "failed"
    assert "MORNYE_SUBSONIC_PASSWORD" in body["error"]["message"]


@pytest.mark.parametrize(
    "params",
    [
        {"u": "me", "p": PASSWORD},
        {"u": "me", "p": "enc:" + PASSWORD.encode().hex()},
    ],
)
def test_every_sign_in_form_clients_use(client, params):
    body = client.get("/rest/ping", params={**params, "f": "json"}).json()
    assert body["subsonic-response"]["status"] == "ok"


def test_a_wrong_password_is_refused_with_code_40(client):
    resp = client.get("/rest/ping", params={"u": "me", "p": "nope", "f": "json"})
    error = resp.json()["subsonic-response"]["error"]
    assert error["code"] == 40


def test_an_api_key_signs_in(monkeypatch):
    monkeypatch.setenv(sub.API_KEY_ENV, "k3y")
    client = TestClient(webapp.create_app(token=None))
    body = client.get("/rest/ping", params={"apiKey": "k3y", "f": "json"}).json()
    assert body["subsonic-response"]["status"] == "ok"


def test_the_web_token_does_not_lock_subsonic_clients_out(monkeypatch):
    monkeypatch.setenv(sub.PASSWORD_ENV, PASSWORD)
    monkeypatch.setenv(sub.USER_ENV, "me")
    client = TestClient(webapp.create_app(token="s3cret"))

    assert client.get("/").status_code == 401, "the web UI still needs the token"
    body = client.get("/rest/ping", params=_auth()).json()["subsonic-response"]
    assert body["status"] == "ok"


def test_xml_is_the_default_answer(client):
    resp = client.get("/rest/ping", params={"u": "me", "p": PASSWORD})
    root = ET.fromstring(resp.content)
    assert root.tag.endswith("subsonic-response")
    assert root.get("status") == "ok" and root.get("openSubsonic") == "true"


def test_form_post_works(client):
    resp = client.post("/rest/ping.view", data={"u": "me", "p": PASSWORD, "f": "json"})
    assert resp.json()["subsonic-response"]["status"] == "ok"


def test_form_post_needs_no_multipart_parser(client, monkeypatch):
    """Starlette's request.form() needs python-multipart, which Mornye does
    not depend on: on a machine without it (the Windows CI runner) every
    parameter of a POST was lost."""

    async def no_parser(self, *a, **k):
        raise AssertionError("python-multipart is not installed")

    monkeypatch.setattr("starlette.requests.Request.form", no_parser)
    resp = client.post("/rest/ping.view", data={"u": "me", "p": PASSWORD, "f": "json"})
    assert resp.json()["subsonic-response"]["status"] == "ok"


def test_repeated_parameters_in_a_form_post(client, library):
    ids = [
        s["id"]
        for s in _get(client, "search3", query='""', songCount=10)["searchResult3"][
            "song"
        ][:2]
    ]
    resp = client.post(
        "/rest/createPlaylist.view",
        data={**_auth(), "name": "Posted", "songId": ids},
    )
    playlist = resp.json()["subsonic-response"]["playlist"]
    assert playlist["songCount"] == 2


# ── browsing ─────────────────────────────────────────────────────────────


def test_every_credited_artist_is_an_artist(client, library):
    body = _get(client, "getArtists")
    names = [a["name"] for i in body["artists"]["index"] for a in i["artist"]]
    assert sorted(names) == ["50 Cent", "Doja Cat", "Olivia", "SZA"]


def test_an_album_shared_by_two_artists_belongs_to_both(client, library):
    olivia = _get(client, "getArtist", id=sub.artist_id("Olivia"))["artist"]
    assert [a["name"] for a in olivia["album"]] == ["The Massacre"]
    fifty = _get(client, "getArtist", id=sub.artist_id("50 Cent"))["artist"]
    assert [a["name"] for a in fifty["album"]] == ["Get Rich", "The Massacre"]
    sza = _get(client, "getArtist", id=sub.artist_id("SZA"))["artist"]
    assert [a["name"] for a in sza["album"]] == ["Planet Her"]


def test_an_album_lists_its_songs_in_order(client, library):
    doja = _get(client, "getArtist", id=sub.artist_id("Doja Cat"))["artist"]
    album = _get(client, "getAlbum", id=doja["album"][0]["id"])["album"]
    assert [s["title"] for s in album["song"]] == ["Woman", "Kiss Me More"]
    kmm = album["song"][1]
    assert [a["name"] for a in kmm["artists"]] == ["Doja Cat", "SZA"]
    assert kmm["displayArtist"] == "Doja Cat, SZA"


def test_an_empty_search_lists_the_whole_library(client, library):
    """Symfonium syncs a library with search3 and an empty query."""
    found = _get(client, "search3", query='""', songCount=500)["searchResult3"]
    assert len(found["song"]) == 4
    candy = _get(client, "search3", query="candy")["searchResult3"]["song"]
    assert [s["title"] for s in candy] == ["Candy Shop"]


def test_album_lists_and_genres(client, library):
    by_name = _get(client, "getAlbumList2", type="alphabeticalByName", size=10)
    names = [a["name"] for a in by_name["albumList2"]["album"]]
    assert names == ["Get Rich", "The Massacre", "Planet Her"], "articles ignored"
    genres = {
        g["value"]: g["songCount"] for g in _get(client, "getGenres")["genres"]["genre"]
    }
    assert genres == {"Hip-Hop": 2, "Pop": 2}
    hiphop = _get(client, "getAlbumList2", type="byGenre", genre="Hip-Hop")
    assert {a["name"] for a in hiphop["albumList2"]["album"]} == {
        "Get Rich",
        "The Massacre",
    }


# ── playing ──────────────────────────────────────────────────────────────


def _song_id(client: TestClient, title: str) -> str:
    return _get(client, "search3", query=title)["searchResult3"]["song"][0]["id"]


def test_stream_sends_the_file_and_honours_ranges(client, library):
    sid = _song_id(client, "Woman")
    whole = client.get("/rest/stream", params=_auth(id=sid))
    assert whole.status_code == 200
    assert whole.content == library["woman"].read_bytes()
    part = client.get(
        "/rest/stream", params=_auth(id=sid), headers={"Range": "bytes=0-3"}
    )
    assert part.status_code == 206 and part.content == b"fLaC"


def test_synced_lyrics_come_from_the_lrc_beside_the_file(client, library):
    sid = _song_id(client, "Kiss Me More")
    found = _get(client, "getLyricsBySongId", id=sid)["lyricsList"]
    lyrics = found["structuredLyrics"][0]
    assert lyrics["synced"] is True
    assert lyrics["line"][0] == {"start": 5000, "value": "We can remember swimming"}


def test_a_cover_that_is_missing_is_a_not_found(client, library):
    sid = _song_id(client, "Woman")
    body = client.get("/rest/getCoverArt", params=_auth(id=sid)).json()
    assert body["subsonic-response"]["error"]["code"] == 70


# ── what a client keeps on the server ────────────────────────────────────


def test_stars_and_play_counts(client, library):
    sid = _song_id(client, "Candy Shop")
    _get(client, "star", id=sid)
    _get(client, "scrobble", id=sid)
    _get(client, "scrobble", id=sid)

    starred = _get(client, "getStarred2")["starred2"]
    assert [s["title"] for s in starred["song"]] == ["Candy Shop"]
    frequent = _get(client, "getAlbumList2", type="frequent")["albumList2"]["album"]
    assert frequent[0]["name"] == "The Massacre" and frequent[0]["playCount"] == 2

    _get(client, "unstar", id=sid)
    assert _get(client, "getStarred2")["starred2"]["song"] == []


def test_playlists(client, library):
    a, b = _song_id(client, "Woman"), _song_id(client, "In Da Club")
    created = _get(client, "createPlaylist", name="Gym", songId=[a, b])["playlist"]
    assert created["songCount"] == 2

    _get(
        client,
        "updatePlaylist",
        playlistId=created["id"],
        songIndexToRemove="0",
        name="Gym 2",
    )
    got = _get(client, "getPlaylist", id=created["id"])["playlist"]
    assert got["name"] == "Gym 2"
    assert [s["title"] for s in got["entry"]] == ["In Da Club"]

    _get(client, "deletePlaylist", id=created["id"])
    assert _get(client, "getPlaylists")["playlists"] == {"playlist": []}


def test_the_play_queue_carries_to_another_device(client, library):
    a, b = _song_id(client, "Woman"), _song_id(client, "Kiss Me More")
    _get(client, "savePlayQueue", id=[a, b], current=b, position="42000")

    queue = _get(client, "getPlayQueue")["playQueue"]
    assert [s["title"] for s in queue["entry"]] == ["Woman", "Kiss Me More"]
    assert queue["current"] == b and queue["position"] == 42000


def test_a_folder_nobody_opened_the_player_on_is_synced(client, tmp_path, monkeypatch):
    fresh = tmp_path / "Fresh"
    fresh.mkdir()
    (fresh / "x.flac").write_bytes(b"x")
    monkeypatch.setattr(
        "mornye.core.library_scan.read_track_row",
        lambda path: {
            "path": str(path),
            "title": "Found",
            "artist": "Someone",
            "genre": "",
        },
    )

    assert player_library.sync([str(fresh)]) == {"files": 1, "read": 1, "removed": 0}
    found = _get(client, "search3", query="found")["searchResult3"]["song"]
    assert [s["title"] for s in found] == ["Found"]
