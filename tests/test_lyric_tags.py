"""Translation tags and the .mlrc sidecar (core/lyric_tags.py), in the
format the Mornye app writes, so the two read each other's files."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from mornye.api_mixins.music_extras import MusicExtrasMixin
from mornye.core import subsonic
from mornye.core.lyric_tags import (
    build_mlrc,
    read_tag,
    sidecar_lyrics,
    strip_tags,
    tag_row,
)


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def test_read_tag_readable_and_legacy():
    tag = read_tag(r"[x-translated:1000:Line ]one\nline \\two <3]")
    assert (tag.kind, tag.key, tag.text) == (
        "translation",
        1000,
        "Line ]one\nline \\two <3",
    )
    assert read_tag("[x-romanized:5:bokura wa]").kind == "romaji"
    assert read_tag(f"[x-romaji:1000:{_b64(' Reading ')}]").text == "Reading"
    assert read_tag("[x-translation:1000:%%%]").text is None
    assert read_tag("[x-translated-words:1000:<00:01.00>Hi<00:02.00>]").text is None
    assert read_tag("[x-translated:bad:Text]") is None
    assert read_tag("[00:01.00]Hola") is None


def test_tag_row_escapes_like_the_app():
    assert tag_row("translation", 1000, "Line ]one\nline \\two <3") == (
        r"[x-translated:1000:Line ]one\nline \\two <3]"
    )
    assert read_tag(tag_row("romaji", 0, "a\\b\nc")).text == "a\\b\nc"


def test_build_mlrc_tags_synced_lines_by_time():
    original = "[ar:Someone]\n[00:01.00]Hola\n[00:02.5]v1: Mundo\n\n[00:04.00]Okay"
    translation = "[ar:Someone]\n[00:01.00]Hello\n[00:02.5]World\n\n[00:04.00]Okay"
    assert build_mlrc(original, translation) == (
        "[x-mlrc:2]\n[ar:Someone]\n[00:01.00]Hola\n[x-translated:1000:Hello]\n"
        "[00:02.5]v1: Mundo\n[x-translated:2500:World]\n\n[00:04.00]Okay\n"
    )


def test_build_mlrc_without_timing_uses_the_index_and_replaces_old_tags():
    original = "[x-mlrc:2]\nHola\n[x-translated:0:Old]\n[x-romanized:0:o-la]\nMundo"
    out = build_mlrc(original, "Hello\nWorld")
    assert out == (
        "[x-mlrc:2]\nHola\n[x-translated:0:Hello]\n[x-romanized:0:o-la]\n"
        "Mundo\n[x-translated:1:World]\n"
    )


def test_build_mlrc_refuses_mismatched_or_empty_translations():
    with pytest.raises(ValueError):
        build_mlrc("[00:01.00]A\n[00:02.00]B", "[00:01.00]X")
    with pytest.raises(ValueError):
        build_mlrc("[00:01.00]A", "[00:01.00]A")


def test_sidecar_prefers_mlrc_and_strip_tags(tmp_path):
    song = tmp_path / "song.flac"
    song.write_bytes(b"")
    assert sidecar_lyrics(song) == ""
    (tmp_path / "song.lrc").write_text("[00:01.00]Hola\n", encoding="utf-8")
    assert sidecar_lyrics(song) == "[00:01.00]Hola\n"
    mlrc = "[x-mlrc:2]\n[00:01.00]Hola\n[x-translated:1000:Hello]\n"
    (tmp_path / "song.mlrc").write_text(mlrc, encoding="utf-8")
    assert sidecar_lyrics(song) == mlrc
    assert strip_tags(mlrc) == "[00:01.00]Hola"


def test_subsonic_lyrics_leave_the_tags_out(tmp_path):
    song = tmp_path / "song.flac"
    song.write_bytes(b"")
    (tmp_path / "song.mlrc").write_text(
        "[x-mlrc:2]\nHola\n[x-translated:0:Hello]\nMundo\n", encoding="utf-8"
    )

    class Song:
        path = str(song)

    assert subsonic.lyrics_for(Song()) == (False, [(0, "Hola"), (0, "Mundo")])


class _Extras(MusicExtrasMixin):
    def __init__(self, root: Path):
        self.root = root

    def _is_approved_path(self, path: str) -> bool:
        return str(path).startswith(str(self.root))


def test_save_translation_writes_mlrc_or_falls_back(tmp_path):
    song = tmp_path / "song.flac"
    song.write_bytes(b"")
    api = _Extras(tmp_path)
    res = api.save_translated_lyrics(
        str(song), "en", "[00:01.00]Hello", "[00:01.00]Hola"
    )
    assert res == {"status": "ok", "path": str(tmp_path / "song.mlrc")}
    assert "[x-translated:1000:Hello]" in (tmp_path / "song.mlrc").read_text(
        encoding="utf-8"
    )
    # Lines that no longer pair up, or no original: the plain per-language file.
    res = api.save_translated_lyrics(str(song), "en", "A\nB", "Hola")
    assert res["path"] == str(tmp_path / "song.en.lrc")
    assert (
        api.save_translated_lyrics("/etc/x.flac", "en", "A", "B")["status"] == "error"
    )
