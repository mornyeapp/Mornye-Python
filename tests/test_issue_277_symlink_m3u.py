"""Issue #277: playlists in a library reached through a symlink.

With ~/Music/Mornye -> ~/Music/unknown_folder, the playlist was written under
the link and the track came resolved, so relpath wrote
"../../unknown_folder/<playlist>/x.flac" for a file beside the playlist.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mornye.core.playlist_sync import M3UEntry, append_m3u, render_m3u


@pytest.fixture()
def linked_library(tmp_path: Path) -> tuple[Path, Path]:
    real = tmp_path / "unknown_folder"
    (real / "Mix").mkdir(parents=True)
    (real / "Mix" / "a.flac").write_bytes(b"")
    link = tmp_path / "Mornye"
    try:
        os.symlink(real, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    return real, link


def test_a_track_beside_its_playlist_is_listed_by_name(linked_library):
    real, link = linked_library
    entry = M3UEntry(path=real / "Mix" / "a.flac", title="A", artists="X", duration_s=1)

    content = render_m3u([entry], link / "Mix" / "Mix.m3u8")

    assert content.splitlines()[-1] == "a.flac"


def test_an_old_playlist_is_repaired_rather_than_listing_a_track_twice(
    linked_library,
):
    real, link = linked_library
    existing = "#EXTM3U\n#EXTINF:1,X - A\n../../unknown_folder/Mix/a.flac\n"
    entry = M3UEntry(path=real / "Mix" / "a.flac", title="A", artists="X", duration_s=1)

    content = append_m3u(existing, [entry], link / "Mix" / "Mix.m3u8")

    assert content == "#EXTM3U\n#EXTINF:1,X - A\na.flac\n"
