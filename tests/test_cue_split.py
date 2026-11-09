"""Single-file album + .cue → one file per track (core/cue_split.py)."""

from __future__ import annotations

import asyncio
import shutil
import subprocess

import pytest

from mornye.core import cue_split as cs

SHEET = """REM GENRE Rock
REM DATE 1999
PERFORMER "The Band"
TITLE "Test Album"
FILE "Album Rip.wav" WAVE
  TRACK 01 AUDIO
    TITLE "Intro"
    INDEX 01 00:00:00
  TRACK 02 AUDIO
    TITLE "Song: Two?"
    PERFORMER "Guest"
    ISRC USABC9900001
    INDEX 00 00:03:70
    INDEX 01 00:04:00
  TRACK 03 MODE1/2352
    INDEX 01 00:08:00
  TRACK 04 AUDIO
    TITLE "Outro"
    INDEX 01 00:09:37
"""

OUTRO_START = 9 + 37 / 75


def test_timestamp_frames():
    assert cs.parse_timestamp("00:09:37") == pytest.approx(OUTRO_START)
    assert cs.parse_timestamp("72:00:74") == pytest.approx(72 * 60 + 74 / 75)
    for bad in ("1:60:00", "00:00:75", "abc", ""):
        with pytest.raises(cs.CueError):
            cs.parse_timestamp(bad)


def test_parse_sheet():
    sheet = cs.parse_cue(SHEET)
    assert (sheet.performer, sheet.title, sheet.genre, sheet.date) == (
        "The Band",
        "Test Album",
        "Rock",
        "1999",
    )
    # The data track is dropped; INDEX 00 (pregap) is not the start.
    assert [t.number for t in sheet.tracks] == [1, 2, 4]
    two = sheet.tracks[1]
    assert (two.title, two.performer, two.isrc, two.start_s) == (
        "Song: Two?",
        "Guest",
        "USABC9900001",
        4.0,
    )
    assert sheet.files == ["Album Rip.wav"]


def test_missing_index_is_an_error():
    with pytest.raises(cs.CueError):
        cs.parse_cue('FILE "a.flac" WAVE\n  TRACK 01 AUDIO\n    TITLE "x"\n')


def test_encoding_fallbacks():
    text = 'TITLE "Café"'
    bom = b"\xef\xbb\xbf"
    assert cs.decode_cue_bytes(bom + text.encode("utf-8")) == text
    zh = 'TITLE "晴天"'
    assert cs.decode_cue_bytes(zh.encode("gb18030")) == zh
    assert cs.decode_cue_bytes(text.encode("cp1252"), "cp1252") == text


def _album(tmp_path, audio_name="Album Rip.flac"):
    (tmp_path / audio_name).write_bytes(b"")
    cue = tmp_path / "album.cue"
    cue.write_text(SHEET, encoding="utf-8")
    return cue


def test_plan_resolves_compressed_copy_and_bounds(tmp_path):
    cue = _album(tmp_path)
    plans = cs.plan_split(cs.read_cue(cue), tmp_path / "out")
    assert [p.source.name for p in plans] == ["Album Rip.flac"] * 3
    assert [(p.start_s, p.end_s) for p in plans] == [
        (0.0, 4.0),
        (4.0, pytest.approx(OUTRO_START)),
        (pytest.approx(OUTRO_START), None),
    ]
    assert [p.output.name for p in plans] == [
        "01 - Intro.flac",
        "02 - Song_ Two_.flac",
        "04 - Outro.flac",
    ]


def test_file_path_never_leaves_sheet_folder(tmp_path):
    cue = tmp_path / "a.cue"
    cue.write_text(
        'FILE "../../secret.flac" WAVE\n TRACK 01 AUDIO\n  INDEX 01 00:00:00\n'
    )
    (tmp_path / "secret.flac").write_bytes(b"")
    # Only the basename is used, so this resolves beside the sheet.
    plans = cs.plan_split(cs.read_cue(cue))
    assert plans[0].source == (tmp_path / "secret.flac").resolve()


def test_missing_audio_is_reported(tmp_path):
    cue = tmp_path / "a.cue"
    cue.write_text('FILE "nope.wav" WAVE\n TRACK 01 AUDIO\n  INDEX 01 00:00:00\n')
    result = asyncio.run(cs.split_cue_async(cue, dry_run=True))
    assert "not found" in result.error


def test_ffmpeg_command_tags_and_range(tmp_path):
    cue = _album(tmp_path)
    sheet = cs.read_cue(cue)
    plan = cs.plan_split(sheet)[1]
    cmd = cs.ffmpeg_command(plan, sheet, "flac", 3)
    assert cmd[cmd.index("-ss") + 1] == "4.000000"
    assert "-to" in cmd
    assert "artist=Guest" in cmd and "album_artist=The Band" in cmd
    assert "track=2/3" in cmd and "isrc=USABC9900001" in cmd


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_split_end_to_end(tmp_path):
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
            "sine=frequency=440:duration=12",
            "-ar",
            "44100",
            "-c:a",
            "flac",
            str(tmp_path / "Album Rip.flac"),
        ],
        check=True,
    )
    cue = tmp_path / "album.cue"
    cue.write_text(SHEET, encoding="utf-8")
    out = tmp_path / "out"

    result = asyncio.run(cs.split_cue_async(cue, out))
    assert result.error == ""
    assert [p.name for p in result.written] == [
        "01 - Intro.flac",
        "02 - Song_ Two_.flac",
        "04 - Outro.flac",
    ]
    assert not list(out.glob(".*part*"))

    from mutagen.flac import FLAC

    first = FLAC(out / "01 - Intro.flac")
    assert first.info.length == pytest.approx(4.0, abs=0.05)
    assert first["title"] == ["Intro"]
    second = FLAC(out / "02 - Song_ Two_.flac")
    assert second.info.length == pytest.approx(OUTRO_START - 4, abs=0.05)
    assert second["artist"] == ["Guest"]

    again = asyncio.run(cs.split_cue_async(cue, out))
    assert again.written == [] and len(again.skipped) == 3
