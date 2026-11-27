"""Word-by-word lyrics over Subsonic: OpenSubsonic songLyrics version 2.

A phone that remotes the computer reads the lyrics of what plays there with
getLyricsBySongId. Enhanced LRC keeps its word timing, sent as ``cueLine``
when the client asks with ``enhanced=true``; without it the answer is the
version 1 one, lines only.
"""

from __future__ import annotations

from mornye.core import subsonic as sub
from mornye.core.subsonic import LyricLine, LyricWord, parse_lyrics
from tests.test_subsonic import _get, _song_id, client, library  # noqa: F401

ENHANCED = (
    "[ti:Song]\n"
    "[00:05.00]<00:05.00>We <00:05.40>can <00:05.90>remember<00:07.00>\n"
    "[00:09.50]Heading for the city\n"
)


def test_enhanced_lrc_keeps_each_word_with_its_bytes():
    synced, lines = parse_lyrics(ENHANCED)
    assert synced is True
    assert lines[0] == LyricLine(
        5000,
        "We can remember",
        (
            LyricWord(5000, 5400, "We", 0, 1),
            LyricWord(5400, 5900, "can", 3, 5),
            LyricWord(5900, 7000, "remember", 7, 14),
        ),
    )
    assert lines[1] == LyricLine(9500, "Heading for the city")


def test_byte_offsets_count_utf8_bytes_inclusive():
    _, lines = parse_lyrics(
        "[00:02.74]<00:02.74>눈을 <00:03.02>뜬 <00:03.30>순간<00:06.21>"
    )
    words = lines[0].words
    assert [(w.value, w.byte_start, w.byte_end) for w in words] == [
        ("눈을", 0, 5),
        ("뜬", 7, 9),
        ("순간", 11, 16),
    ]
    value = lines[0].value.encode("utf-8")
    for w in words:
        assert value[w.byte_start : w.byte_end + 1].decode("utf-8") == w.value


def test_a_line_whose_last_word_is_open_has_no_ends():
    _, lines = parse_lyrics("[00:01.00]<00:01.00>One <00:01.50>two")
    assert [w.end for w in lines[0].words] == [None, None]
    assert [w.start for w in lines[0].words] == [1000, 1500]


def test_line_only_inline_tags_are_line_synced():
    """``[t]<t>whole line<end>`` times lines, not words."""
    _, lines = parse_lyrics(
        "[00:01.00]<00:01.00>Whole line<00:03.00>\n[00:04.00]<00:04.00>Next<00:05.00>\n"
    )
    assert [line.words for line in lines] == [(), ()]
    assert [line.value for line in lines] == ["Whole line", "Next"]


def test_jellyfin_word_gaps_become_spaces():
    gap = "⠀"
    _, lines = parse_lyrics(f"[00:01.00]<00:01.00>Nuij{gap}<00:01.40>simm<00:02.00>")
    assert lines[0].value == "Nuij simm"
    assert [w.value for w in lines[0].words] == ["Nuij", "simm"]


def test_times_that_run_backwards_drop_the_words_not_the_line():
    _, lines = parse_lyrics(
        "[00:01.00]<00:02.00>Bad <00:01.00>order<00:03.00>\n"
        "[00:04.00]<00:04.00>Good <00:04.50>one<00:05.00>\n"
    )
    assert lines[0].value == "Bad order" and lines[0].words == ()
    assert len(lines[1].words) == 2


def test_a_repeated_line_is_listed_at_each_stamp_without_words():
    _, lines = parse_lyrics(
        "[00:01.00][00:30.00]<00:01.00>La <00:01.50>la<00:02.00>\n"
        "[00:10.00]<00:10.00>Verse <00:10.50>one<00:11.00>\n"
    )
    assert [(line.start, line.words) for line in lines if line.value == "La la"] == [
        (1000, ()),
        (30000, ()),
    ]


def test_plain_lyrics_are_unchanged():
    assert parse_lyrics("Hola\nMundo\n") == (
        False,
        [LyricLine(0, "Hola"), LyricLine(0, "Mundo")],
    )
    assert parse_lyrics("  \n") is None


# ── over the API ─────────────────────────────────────────────────────────


def test_song_lyrics_version_2_is_advertised(client):  # noqa: F811
    found = _get(client, "getOpenSubsonicExtensions")["openSubsonicExtensions"]
    assert {"name": "songLyrics", "versions": [1, 2]} in found


def test_enhanced_lyrics_send_cue_lines(client, library):  # noqa: F811
    library["kmm"].with_suffix(".lrc").write_text(ENHANCED, encoding="utf-8")
    sid = _song_id(client, "Kiss Me More")
    lyrics = _get(client, "getLyricsBySongId", id=sid, enhanced="true")["lyricsList"][
        "structuredLyrics"
    ][0]
    assert lyrics["kind"] == "main"
    assert lyrics["line"] == [
        {"start": 5000, "value": "We can remember"},
        {"start": 9500, "value": "Heading for the city"},
    ]
    assert lyrics["cueLine"] == [
        {
            "index": 0,
            "start": 5000,
            "end": 7000,
            "value": "We can remember",
            "cue": [
                {
                    "start": 5000,
                    "end": 5400,
                    "value": "We",
                    "byteStart": 0,
                    "byteEnd": 1,
                },
                {
                    "start": 5400,
                    "end": 5900,
                    "value": "can",
                    "byteStart": 3,
                    "byteEnd": 5,
                },
                {
                    "start": 5900,
                    "end": 7000,
                    "value": "remember",
                    "byteStart": 7,
                    "byteEnd": 14,
                },
            ],
        }
    ]


def test_without_enhanced_the_answer_is_version_1(client, library):  # noqa: F811
    library["kmm"].with_suffix(".lrc").write_text(ENHANCED, encoding="utf-8")
    sid = _song_id(client, "Kiss Me More")
    lyrics = _get(client, "getLyricsBySongId", id=sid)["lyricsList"][
        "structuredLyrics"
    ][0]
    assert "cueLine" not in lyrics and "kind" not in lyrics
    assert lyrics["line"][0] == {"start": 5000, "value": "We can remember"}


def test_line_synced_lyrics_have_no_cue_lines_even_enhanced(
    client, library  # noqa: F811
):
    sid = _song_id(client, "Kiss Me More")
    lyrics = _get(client, "getLyricsBySongId", id=sid, enhanced="true")["lyricsList"][
        "structuredLyrics"
    ][0]
    assert lyrics["kind"] == "main"
    assert "cueLine" not in lyrics


def test_lyrics_for_still_gives_lines_only(tmp_path):
    song = tmp_path / "song.flac"
    song.write_bytes(b"")
    song.with_suffix(".lrc").write_text(ENHANCED, encoding="utf-8")

    class Song:
        path = str(song)

    assert sub.lyrics_for(Song()) == (
        True,
        [(5000, "We can remember"), (9500, "Heading for the city")],
    )
