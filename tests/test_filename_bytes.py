"""File and folder names stay within the 255-byte limit of Linux filesystems."""

from __future__ import annotations

from mornye.core.models import (
    MAX_DIRNAME_BYTES,
    MAX_FILENAME_BYTES,
    TrackMetadata,
    build_filename,
    fit_name_bytes,
    safe_dir_name,
)

LONG_JA = (
    "交響曲第9番ニ短調作品125「合唱付き」第4楽章：プレスト〜アレグロ・アッサイ〜"
    "アレグロ・アッサイ・ヴィヴァーチェ〜アンダンテ・マエストーソ〜"
    "アダージョ・マ・ノン・トロッポ、マ・ディヴォート"
)


def _track(title="Song", artists="Band"):
    return TrackMetadata(
        id="x", title=title, artists=artists, album="x", album_artist="x"
    )


def test_short_names_unchanged():
    assert fit_name_bytes("Band - Song", suffix=".flac") == "Band - Song.flac"
    assert build_filename(_track(), "{artist} - {title}") == "Band - Song.flac"


def test_cut_on_character_boundary_and_keep_extension():
    name = fit_name_bytes("é" * 200, 51, ".flac")
    assert name.endswith(".flac")
    assert len(name.encode()) <= 51
    name.encode("utf-8").decode("utf-8")  # still valid UTF-8


def test_trailing_separators_dropped():
    assert fit_name_bytes("abc - defgh", 6) == "abc"


def test_long_cjk_title(tmp_path):
    track = _track(LONG_JA, "ベルリン・フィルハーモニー管弦楽団")
    name = build_filename(track, "{artist} - {title}")
    assert name.endswith(".flac")
    assert len(name.encode()) <= MAX_FILENAME_BYTES
    # Room left for the longest temporary name made from it.
    assert len(f".{name[:-5]}.mornye-transcode.flac".encode()) <= 255
    (tmp_path / name).write_bytes(b"")


def test_callable_format_bounded():
    name = build_filename(_track(), lambda m, **_: "a" * 400)
    assert name.endswith(".flac") and len(name.encode()) <= MAX_FILENAME_BYTES


def test_folder_names():
    assert safe_dir_name("AC/DC: Live?") == "AC_DC_ Live_"
    assert safe_dir_name("  ...  ") == "Unknown"
    assert len(safe_dir_name("専" * 200).encode()) <= MAX_DIRNAME_BYTES
