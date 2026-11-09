"""Issue #267: playlists sharing tracks must not download them twice.

A track another playlist already downloaded is linked from this playlist's
M3U (``../Playlist1/Track.flac``) instead of being fetched again, and every
playlist download leaves an M3U in its folder.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from mornye.app import MornyeAPI
from mornye.application.download_engine import DownloadOptions
from mornye.core.models import TrackMetadata
from mornye.core.playlist_sync import M3UEntry, append_m3u
from mornye.downloader import MornyeDownloader
from tests.application_download_capture import capture_service


def _track(track_id: str, title: str, artist: str = "Artist") -> TrackMetadata:
    return TrackMetadata(
        id=track_id,
        title=title,
        artists=artist,
        album="Album",
        album_artist=artist,
        duration_ms=180_000,
    )


class _FakeWorker:
    def __init__(self, output_dir: Path, completed: dict[str, str]) -> None:
        self.output_dir = str(output_dir)
        self.completed_paths = completed


# ---------------------------------------------------------------------------
# append_m3u
# ---------------------------------------------------------------------------


def test_append_m3u_starts_a_new_file(tmp_path: Path) -> None:
    target = tmp_path / "Mix" / "Mix.m3u8"
    entry = M3UEntry(tmp_path / "Other" / "Song.flac", "Song", "Artist", 180)

    content = append_m3u("", [entry], target)

    assert content == "#EXTM3U\n#EXTINF:180,Artist - Song\n../Other/Song.flac\n"


def test_append_m3u_keeps_existing_and_skips_listed(tmp_path: Path) -> None:
    target = tmp_path / "Mix" / "Mix.m3u8"
    existing = "#EXTM3U\n#EXTINF:100,A - Old\nOld.flac\n"
    old = M3UEntry(tmp_path / "Mix" / "Old.flac", "Old", "A", 100)
    new = M3UEntry(tmp_path / "Mix" / "New.flac", "New", "B", 200)

    content = append_m3u(existing, [old, new], target)

    assert content.splitlines() == [
        "#EXTM3U",
        "#EXTINF:100,A - Old",
        "Old.flac",
        "#EXTINF:200,B - New",
        "New.flac",
    ]


# ---------------------------------------------------------------------------
# Engine: M3U written after a playlist run
# ---------------------------------------------------------------------------


def test_whole_playlist_rewrites_m3u_with_cross_folder_paths(tmp_path: Path) -> None:
    shared = tmp_path / "Playlist1" / "Shared - Artist.flac"
    own = tmp_path / "Playlist2" / "Own - Artist.flac"
    tracks = [_track("t1", "Shared"), _track("t2", "Own"), _track("t3", "Failed")]
    worker = _FakeWorker(tmp_path / "Playlist2", {"t1": str(shared), "t2": str(own)})

    asyncio.run(
        MornyeDownloader._write_run_playlist_file_async(
            DownloadOptions(output_dir=str(tmp_path)),
            "rewrite",
            "Playlist2",
            tracks,
            worker,  # type: ignore[arg-type]
        )
    )

    m3u = (tmp_path / "Playlist2" / "Playlist2.m3u8").read_text(encoding="utf-8")
    assert m3u.splitlines() == [
        "#EXTM3U",
        "#EXTINF:180,Artist - Shared",
        "../Playlist1/Shared - Artist.flac",
        "#EXTINF:180,Artist - Own",
        "Own - Artist.flac",
    ]


def test_followed_playlist_appends_to_m3u(tmp_path: Path) -> None:
    folder = tmp_path / "Top Global"
    folder.mkdir()
    (folder / "Top Global.m3u8").write_text(
        "#EXTM3U\n#EXTINF:1,X - First\nFirst.flac\n", encoding="utf-8"
    )
    new = tmp_path / "Playlist1" / "New - Artist.flac"
    worker = _FakeWorker(folder, {"t9": str(new)})

    asyncio.run(
        MornyeDownloader._write_run_playlist_file_async(
            DownloadOptions(output_dir=str(folder), library_dir=str(tmp_path)),
            "append",
            "",
            [_track("t9", "New")],
            worker,  # type: ignore[arg-type]
        )
    )

    lines = (folder / "Top Global.m3u8").read_text(encoding="utf-8").splitlines()
    assert lines[:3] == ["#EXTM3U", "#EXTINF:1,X - First", "First.flac"]
    assert lines[-1] == "../Playlist1/New - Artist.flac"


def test_m3u_none_writes_nothing(tmp_path: Path) -> None:
    worker = _FakeWorker(tmp_path, {"t1": str(tmp_path / "a.flac")})

    asyncio.run(
        MornyeDownloader._write_run_playlist_file_async(
            DownloadOptions(output_dir=str(tmp_path), m3u_format="none"),
            "rewrite",
            "P",
            [_track("t1", "A")],
            worker,  # type: ignore[arg-type]
        )
    )

    assert not list(tmp_path.glob("*.m3u*"))


# ---------------------------------------------------------------------------
# Engine: loose tracks looked up across the whole library
# ---------------------------------------------------------------------------


def test_library_lookup_finds_track_in_another_playlist_folder(tmp_path: Path) -> None:
    other = tmp_path / "Playlist1"
    other.mkdir()
    existing = other / "Shared - Artist.flac"
    existing.write_bytes(b"not really audio, but not empty")
    target = tmp_path / "Playlist2"
    target.mkdir()

    downloader = MornyeDownloader(
        DownloadOptions(output_dir=str(target), library_dir=str(tmp_path))
    )
    matches = asyncio.run(
        downloader._library_matches_async(
            [_track("t1", "Shared"), _track("t2", "Missing")]
        )
    )

    assert matches == {"t1": existing}


def test_library_lookup_is_off_without_library_dir(tmp_path: Path) -> None:
    (tmp_path / "Shared - Artist.flac").write_bytes(b"x")
    downloader = MornyeDownloader(DownloadOptions(output_dir=str(tmp_path)))

    assert (
        asyncio.run(downloader._library_matches_async([_track("t1", "Shared")])) == {}
    )


# ---------------------------------------------------------------------------
# GUI: a followed playlist's new tracks get the library root
# ---------------------------------------------------------------------------


class _FakeTrack:
    id = "track-id"
    title = "Title"
    external_url = "https://open.spotify.com/track/track-id"


def _gui_options(tmp_path: Path, monkeypatch: Any, **batch: Any) -> dict:
    seen: list[dict] = []
    capture_service(monkeypatch, seen)
    api = MornyeAPI()
    api.download_dir = str(tmp_path)
    api.current_tracks = [_FakeTrack()]
    api.current_url = _FakeTrack.external_url
    api._download_task([0], {"services": ["tidal"]}, **batch)
    assert seen, "_download_task never reached the download call"
    return seen[-1]


def test_followed_playlist_tracks_search_the_whole_library(
    tmp_path: Path, monkeypatch: Any
) -> None:
    opts = _gui_options(tmp_path, monkeypatch, playlist_name="Top Global")

    assert Path(opts["output_dir"]) == tmp_path / "Top Global"
    assert Path(opts["library_dir"]) == tmp_path
    assert opts["m3u_format"] == "m3u8"


def test_plain_download_has_no_library_dir(tmp_path: Path, monkeypatch: Any) -> None:
    opts = _gui_options(tmp_path, monkeypatch)

    assert opts["library_dir"] is None
