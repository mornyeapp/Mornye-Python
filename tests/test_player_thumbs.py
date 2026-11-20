"""Player covers are kept on disk and fetched in batches.

On a network share one cover took ~145 ms to read, every launch, one bridge
call each. The shrunk cover is now stored under the data folder keyed by the
file's path, mtime and size, and the rows on screen come in one call.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mornye.api_mixins import library_tools


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    target = tmp_path / "data"
    monkeypatch.setenv("MORNYE_DATA_DIR", str(target))
    return target


def _song(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(b"not really audio")
    return path


def test_second_read_comes_from_disk(tmp_path, data_dir, monkeypatch):
    song = _song(tmp_path, "a.m4a")
    calls = []

    def fake_thumb(path, px):
        calls.append(path)
        return b"JPEG"

    monkeypatch.setattr("mornye.core.library_scan.thumbnail_jpeg", fake_thumb)
    assert library_tools._cached_thumbnail(str(song), 96) == b"JPEG"
    assert library_tools._cached_thumbnail(str(song), 96) == b"JPEG"
    assert calls == [str(song)]
    assert list(data_dir.rglob("*.jpg"))


def test_no_cover_is_remembered_and_edits_refresh(tmp_path, data_dir, monkeypatch):
    song = _song(tmp_path, "b.m4a")
    calls = []
    result = {"raw": None}

    def fake_thumb(path, px):
        calls.append(path)
        return result["raw"]

    monkeypatch.setattr("mornye.core.library_scan.thumbnail_jpeg", fake_thumb)
    assert library_tools._cached_thumbnail(str(song), 96) is None
    assert library_tools._cached_thumbnail(str(song), 96) is None
    assert len(calls) == 1

    # A new cover written into the file changes its size and mtime.
    song.write_bytes(b"now with a cover, longer")
    st = song.stat()
    os.utime(song, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    result["raw"] = b"NEW"
    assert library_tools._cached_thumbnail(str(song), 96) == b"NEW"
    assert len(calls) == 2


def test_batch_returns_one_url_per_path(tmp_path, data_dir, monkeypatch):
    songs = [str(_song(tmp_path, f"{i}.m4a")) for i in range(5)]
    monkeypatch.setattr(
        "mornye.core.library_scan.thumbnail_jpeg",
        lambda path, px: None if path.endswith("3.m4a") else b"J",
    )

    class Api(library_tools.LibraryToolsMixin):
        _library_thumb_cache: dict = {}

        def _is_approved_path(self, candidate):
            return True

    out = Api().get_track_thumbs(songs + [songs[0], "", 7], 0)
    assert list(out) == songs
    assert out[songs[3]] == ""
    assert all(
        out[p].startswith("data:image/jpeg;base64,") for p in songs if p != songs[3]
    )
