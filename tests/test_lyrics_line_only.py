"""Line-only lyrics written in the enhanced form, and the [by:] source credit."""

from __future__ import annotations

from mornye.core.lyrics import (
    add_lrc_metadata,
    collapse_line_only_word_tags,
    lyrics_provider_label,
)

LINE_ONLY = (
    "[00:04.24]<00:04.24>One summer night<00:08.12>\n"
    "[00:08.52]<00:08.52>We ran away for a while<00:14.77>"
)


def test_line_only_enhanced_lrc_becomes_line_synced():
    assert collapse_line_only_word_tags(LINE_ONLY) == (
        "[00:04.24]One summer night\n[00:08.52]We ran away for a while"
    )


def test_real_word_timing_is_kept():
    wbw = LINE_ONLY + "\n[00:15.47]<00:15.47>Laughing, <00:16.10>we <00:16.40>hurried"
    assert collapse_line_only_word_tags(wbw) == wbw


def test_voice_prefix_does_not_count_as_a_word():
    lrc = "[00:01.00]v1:<00:01.00>Hello there<00:02.00>"
    assert collapse_line_only_word_tags(lrc) == "[00:01.00]v1:Hello there"


def test_plain_lrc_is_untouched():
    assert collapse_line_only_word_tags("[00:01.00]Hello") == "[00:01.00]Hello"


def test_header_names_the_source_service():
    out = add_lrc_metadata("[00:01.00]Hi", "Song", "Artist", "apple")
    assert out.startswith("[ti:Song]\n[ar:Artist]\n[by:Apple Music]\n[re:Mornye]\n\n")


def test_header_without_provider_keeps_old_credit():
    assert "[by:Mornye]" in add_lrc_metadata("[00:01.00]Hi", "S", "A")


def test_extension_provider_keeps_its_name():
    assert lyrics_provider_label("ext:musixmatch") == "musixmatch"


def test_fetch_file_lyrics_searches_with_the_editor_fields(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import mornye.core.lyrics as lyrics_mod
    import mornye.core.tag_editor as tag_editor
    from mornye.api_mixins.library_tools import LibraryToolsMixin

    song = tmp_path / "song.flac"
    song.write_bytes(b"")
    monkeypatch.setattr(
        tag_editor,
        "read_file_tags",
        lambda p: SimpleNamespace(tags={"TITLE": "Old", "ARTIST": "Mariah Carey"}),
    )
    seen = {}

    async def fake_fetch(**kw):
        seen.update(kw)
        return "[by:Apple Music]\n[00:01.00]Hi", "apple"

    monkeypatch.setattr(lyrics_mod, "fetch_lyrics_async", fake_fetch)
    api = LibraryToolsMixin()
    api.download_dir = str(tmp_path)
    api.load_settings = lambda: {"lyrics_providers": ["apple", "lrclib"]}

    res = api.fetch_file_lyrics(
        str(song), {"TITLE": "Underneath the Stars", "ISRC": ""}
    )
    assert res == {
        "status": "ok",
        "lyrics": "[by:Apple Music]\n[00:01.00]Hi",
        "provider": "Apple Music",
    }
    assert seen["track_name"] == "Underneath the Stars"
    assert seen["artist_name"] == "Mariah Carey"
    assert seen["providers"] == ["apple", "lrclib"]


def test_fetch_file_lyrics_can_ask_one_service(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import mornye.core.lyrics as lyrics_mod
    import mornye.core.tag_editor as tag_editor
    from mornye.api_mixins.library_tools import LibraryToolsMixin

    song = tmp_path / "song.flac"
    song.write_bytes(b"")
    monkeypatch.setattr(
        tag_editor,
        "read_file_tags",
        lambda p: SimpleNamespace(tags={"TITLE": "T", "ARTIST": "A"}),
    )
    seen = {}

    async def fake_fetch(**kw):
        seen.update(kw)
        return "", ""

    monkeypatch.setattr(lyrics_mod, "fetch_lyrics_async", fake_fetch)
    api = LibraryToolsMixin()
    api.download_dir = str(tmp_path)
    api.load_settings = lambda: {"lyrics_providers": ["apple", "lrclib"]}

    res = api.fetch_file_lyrics(str(song), {}, "lrclib")
    assert seen["providers"] == ["lrclib"]
    assert res == {
        "status": "error",
        "error": "No lyrics found on LRCLIB for this track.",
    }
    assert api.fetch_file_lyrics(str(song), {}, "nope")["status"] == "error"
