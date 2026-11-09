"""M3U/M3U8 playlists as an input source (core/m3u_source.py)."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from mornye.core import csv_source
from mornye.core.errors import MornyeError
from mornye.core.m3u_source import looks_like_m3u, read_m3u_text

PLAYLIST = """\ufeff#EXTM3U
#PLAYLIST:Road trip
#EXTINF:241,Queen - Bohemian Rhapsody
https://open.spotify.com/track/4u7EnebtmKWzUH433cf5Qv
#EXTINF:-1,Daft Punk - One More Time
/nowhere/Daft Punk/Discovery/01 - One More Time.flac
#EXTART:Radiohead
#EXTALB:OK Computer
#EXTINF:384,Paranoid Android
../Music/02 Paranoid Android.mp3
C:\\Music\\Massive Attack - Teardrop.flac
spotify:track:4cOdK2wGLETKBW3PvgPWqT
"""


def test_detection():
    assert looks_like_m3u("", "list.M3U8")
    assert looks_like_m3u("#EXTM3U\nfoo", "x.txt")
    assert looks_like_m3u("\ufeff#EXTM3U\n", "")
    assert not looks_like_m3u("Title,Artist\nA,B\n", "list.csv")


def test_entries():
    doc = read_m3u_text(PLAYLIST, name="road.m3u8")
    rows = doc.rows
    assert len(rows) == 5

    assert rows[0].url == "https://open.spotify.com/track/4u7EnebtmKWzUH433cf5Qv"
    assert rows[0].duration_ms == 241_000

    # Missing local file: #EXTINF wins over the file name; -1 is "unknown".
    assert (rows[1].artist, rows[1].title, rows[1].duration_ms) == (
        "Daft Punk",
        "One More Time",
        0,
    )

    # #EXTART/#EXTALB fill what #EXTINF leaves out.
    assert (rows[2].artist, rows[2].title, rows[2].album) == (
        "Radiohead",
        "Paranoid Android",
        "OK Computer",
    )

    # No #EXTINF: the (Windows) file name, track number dropped.
    assert (rows[3].artist, rows[3].title) == ("Massive Attack", "Teardrop")

    assert rows[4].url == "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"


def test_track_number_stripped_from_filename():
    doc = read_m3u_text("#EXTM3U\n03 - Song Name.flac\n", name="x.m3u")
    assert doc.rows[0].title == "Song Name"


def test_empty_playlist_is_an_error():
    with pytest.raises(MornyeError):
        read_m3u_text("#EXTM3U\n#EXTINF:1,\n", name="x.m3u")


def test_csv_entry_points_accept_m3u(tmp_path):
    path = tmp_path / "list.m3u8"
    path.write_text(
        "#EXTM3U\n#EXTINF:200,Artist - Title\nsong.flac\n", encoding="utf-8"
    )
    doc = csv_source.read_rows(path)
    assert (doc.rows[0].artist, doc.rows[0].title) == ("Artist", "Title")
    # Contents with only a name, as the GUI posts them.
    doc = csv_source.read_text(path.read_text(), name="list.m3u8")
    assert doc.rows[0].title == "Title"


def test_csv_is_unaffected():
    doc = csv_source.read_text("Title,Artist\nSong,Band\n", name="x.csv")
    assert (doc.rows[0].title, doc.rows[0].artist) == ("Song", "Band")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_local_file_tags_beat_extinf(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=3",
            "-metadata",
            "title=Real Title",
            "-metadata",
            "artist=Real Artist",
            "-metadata",
            "ISRC=USABC9900001",
            "-c:a",
            "flac",
            str(music / "track.flac"),
        ],
        check=True,
    )
    playlist = tmp_path / "list.m3u"
    playlist.write_text("#EXTINF:99,Wrong - Name\nmusic/track.flac\n", encoding="utf-8")
    row = csv_source.read_rows(playlist).rows[0]
    assert (row.title, row.artist, row.isrc) == (
        "Real Title",
        "Real Artist",
        "USABC9900001",
    )
    assert row.duration_ms == pytest.approx(3000, abs=100)
