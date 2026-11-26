"""Mornye Link "Update metadata" (Mornye mobile 5.4.5), the computer's side.

A phone that saved songs from this computer walks its Subsonic catalog (an
empty `search3`, paged, once `getAlbumList2` answers `type: "mornye"`) and
compares each saved copy with the computer's song: title, artist, album,
album artist, genre, year, track and disc number, ISRC
(link_metadata_update.dart). These check the catalog gives it all of that,
with the placeholders the phone knows, and the companion fix: a track or
disc number written alone keeps its total.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from mornye.core import player_library
from mornye.core import subsonic as sub
from mornye.core.tag_editor import keep_number_totals


def _store(tmp_path: Path, **row) -> Path:
    path = tmp_path / "song.flac"
    path.write_bytes(b"fLaC" + b"x" * 100)
    player_library.store(
        [
            (
                str(path),
                1,
                path.stat().st_size,
                {
                    "path": str(path),
                    "title": "Song",
                    "artist": "",
                    "album": "",
                    "album_artist": "",
                    "track": 3,
                    "disc": 2,
                    "year": "2021-05-01",
                    "genre": "Rock; Alternative",
                    "isrc": "usabc2100001",
                    "duration_s": 200,
                    **row,
                },
            )
        ]
    )
    return path


def _song_view(user: str = "me") -> dict:
    from mornye.webapi.subsonic import _View

    view = _View(user)
    (song,) = view.idx.songs.values()
    return view.song(song)


def test_catalog_song_carries_every_tag_the_phone_compares(tmp_path):
    _store(tmp_path, artist="Artist", album="Album", album_artist="Various Artists")
    song = _song_view()
    assert song["title"] == "Song"
    assert song["displayArtist"] == "Artist"
    assert song["album"] == "Album"
    assert song["displayAlbumArtist"] == "Various Artists"
    # The whole genre tag, as Mornye's own Link host sends it.
    assert song["genre"] == "Rock; Alternative"
    assert song["isrc"] == ["USABC2100001"]
    assert song["year"] == 2021
    assert (song["track"], song["discNumber"]) == (3, 2)


def test_empty_tags_use_the_placeholders_the_phone_ignores(tmp_path):
    _store(tmp_path)
    song = _song_view()
    # link_metadata_update.dart skips exactly these spellings.
    assert song["artist"] == "Unknown Artist"
    assert song["album"] == "Unknown Album"
    assert song["displayAlbumArtist"] == "Unknown Artist"


def test_genres_still_group_by_the_first_name(tmp_path):
    _store(tmp_path, artist="A")
    idx = sub.build_index(player_library.all_rows())
    (song,) = idx.songs.values()
    assert song.genre == "Rock" and song.genre_tag == "Rock; Alternative"


def test_the_catalog_walk_the_phone_does(tmp_path, monkeypatch):
    """getAlbumList2 says "mornye", then an empty search3 pages songs."""
    from fastapi.testclient import TestClient

    from mornye import webapp

    _store(tmp_path, artist="Artist", album="Album")
    monkeypatch.setenv(sub.PASSWORD_ENV, "pw")
    monkeypatch.setenv(sub.USER_ENV, "me")
    client = TestClient(webapp.create_app(token=None))
    token = hashlib.md5(b"pwsalt").hexdigest()
    auth = {
        "u": "me",
        "t": token,
        "s": "salt",
        "c": "phone",
        "v": "1.16.1",
        "f": "json",
    }
    first = client.get(
        "/rest/getAlbumList2.view", params={**auth, "type": "newest", "size": "200"}
    )
    assert first.json()["subsonic-response"]["type"] == "mornye"

    def page(offset: int) -> dict:
        params = {
            **auth,
            "query": "",
            "artistCount": "0",
            "albumCount": "0",
            "songCount": "500",
            "songOffset": str(offset),
        }
        body = client.get("/rest/search3.view", params=params).json()
        return body["subsonic-response"]["searchResult3"]

    songs = page(0)["song"]
    assert [s["title"] for s in songs] == ["Song"]
    assert songs[0]["isrc"] == ["USABC2100001"]
    assert page(1).get("song", []) == []


# ── A track or disc number keeps its total ─────────────────────────────────


def test_a_number_written_alone_keeps_the_total_it_carried():
    current = {"TRACKNUMBER": "3/12", "DISCNUMBER": "1/2"}
    tags = {"TRACKNUMBER": "4", "DISCNUMBER": "2"}
    assert keep_number_totals(current, tags) == {
        "TRACKNUMBER": "4",
        "TRACKTOTAL": "12",
        "DISCNUMBER": "2",
        "DISCTOTAL": "2",
    }


def test_a_total_of_its_own_or_named_by_the_edit_is_left_alone():
    kept = keep_number_totals(
        {"TRACKNUMBER": "3/12"}, {"TRACKNUMBER": "4", "TRACKTOTAL": "13"}
    )
    assert kept["TRACKTOTAL"] == "13"
    # The edit cleared the total on purpose.
    cleared = keep_number_totals(
        {"TRACKNUMBER": "3/12"}, {"TRACKNUMBER": "4"}, {"TRACKNUMBER", "TRACKTOTAL"}
    )
    assert "TRACKTOTAL" not in cleared
    # Nothing to carry.
    assert keep_number_totals({"TRACKNUMBER": "3"}, {"TRACKNUMBER": "4"}) == {
        "TRACKNUMBER": "4"
    }
    assert keep_number_totals({"TRACKNUMBER": "3/12"}, {"TRACKNUMBER": "4/10"}) == {
        "TRACKNUMBER": "4/10"
    }


def test_saving_a_track_number_keeps_the_files_total(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("needs ffmpeg")
    from mutagen.flac import FLAC

    from mornye.core.tag_editor import save_file_tags_async

    path = tmp_path / "tone.flac"
    subprocess.run(
        [
            "ffmpeg",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=1",
            "-y",
            str(path),
            "-loglevel",
            "error",
        ],
        check=True,
    )
    audio = FLAC(str(path))
    audio["TITLE"] = "t"
    audio["TRACKNUMBER"] = "3/12"
    audio.save()
    asyncio.run(save_file_tags_async(path, {"TRACKNUMBER": "4"}, backup=False))
    written = {k.upper(): v for k, v in FLAC(str(path)).items()}
    number = written["TRACKNUMBER"][0]
    total = written.get("TRACKTOTAL", written.get("TOTALTRACKS", [""]))[0]
    assert number.split("/")[0] == "4"
    assert total == "12" or number == "4/12"
