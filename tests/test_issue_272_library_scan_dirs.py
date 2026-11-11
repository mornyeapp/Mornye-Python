"""Issue #272: music already on disk elsewhere is not downloaded again.

A user with an older library (another downloader's output, a folder of
FLACs) points Mornye at it. A playlist track or a loose track found there is
skipped and the playlist's M3U links to it where it is; albums keep
downloading in full.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from mornye.app import MornyeAPI
from mornye.application.download_engine import (
    DownloadOptions,
    _match_in_libraries_async,
)
from mornye.core.env_settings import gui_env_defaults
from mornye.core.models import TrackMetadata
from mornye.core.playlist_sync import library_scan_dirs_from
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


def _audio(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not really audio, but not empty")
    return path


# ---------------------------------------------------------------------------
# The setting itself
# ---------------------------------------------------------------------------


def test_setting_accepts_text_lists_and_nothing() -> None:
    assert library_scan_dirs_from("/a; /b\n/c;;") == ["/a", "/b", "/c"]
    assert library_scan_dirs_from(["/a", " ", "/a", "/b"]) == ["/a", "/b"]
    # Compared as paths: on Windows expanduser keeps the "/" the user typed.
    assert [Path(d) for d in library_scan_dirs_from("~/Music")] == [
        Path("~/Music").expanduser()
    ]
    assert len(library_scan_dirs_from(["~/Music", "~/Music "])) == 1
    assert library_scan_dirs_from(None) == []
    assert library_scan_dirs_from("") == []
    assert library_scan_dirs_from(42) == []


def test_environment_variable_sets_the_gui_default() -> None:
    env = {"MORNYE_LIBRARY_SCAN_DIRS": "/music/old,/music/spotiflac"}

    assert gui_env_defaults(env)["library_scan_dirs"] == [
        "/music/old",
        "/music/spotiflac",
    ]


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def test_lookup_finds_tracks_in_scanned_folders(tmp_path: Path) -> None:
    first = _audio(tmp_path / "old" / "Shared - Artist.flac")
    _audio(tmp_path / "other" / "Shared - Artist.flac")
    second = _audio(tmp_path / "other" / "Only Here - Artist.flac")
    tracks = [
        _track("t1", "Shared"),
        _track("t2", "Only Here"),
        _track("t3", "Missing"),
    ]

    matches = asyncio.run(
        _match_in_libraries_async(
            [str(tmp_path / "old"), str(tmp_path / "other")],
            tracks,
            DownloadOptions(output_dir=str(tmp_path / "out")),
        )
    )

    # The first folder wins for a track both have.
    assert matches == {"t1": first, "t2": second}


def test_lookup_skips_missing_folders_and_excluded_tracks(tmp_path: Path) -> None:
    _audio(tmp_path / "lib" / "Shared - Artist.flac")

    matches = asyncio.run(
        _match_in_libraries_async(
            [str(tmp_path / "unmounted"), str(tmp_path / "lib")],
            [_track("t1", "Shared")],
            DownloadOptions(output_dir=str(tmp_path / "out")),
            exclude={"t1"},
        )
    )

    assert matches == {}


def test_loose_tracks_are_looked_up_in_scanned_folders(tmp_path: Path) -> None:
    existing = _audio(tmp_path / "old" / "Shared - Artist.flac")
    downloader = MornyeDownloader(
        DownloadOptions(
            output_dir=str(tmp_path / "out"),
            library_scan_dirs=[str(tmp_path / "old")],
        )
    )

    matches = asyncio.run(
        downloader._library_matches_async(
            [_track("t1", "Shared"), _track("t2", "Missing")]
        )
    )

    assert matches == {"t1": existing}


def _whole_run(tmp_path: Path, kind: str) -> dict[str, Any]:
    """Runs `_run_once_async` for one collection, capturing the worker call."""
    downloader = MornyeDownloader(
        DownloadOptions(
            output_dir=str(tmp_path / "out"),
            library_scan_dirs=[str(tmp_path / "old")],
        )
    )
    tracks = [_track("t1", "Shared"), _track("t2", "New")]
    captured: dict[str, Any] = {}

    async def resolve_metadata(_url: str):
        return "Mix", list(tracks), {"type": kind}

    async def resolve_isrc(pending):
        return list(pending)

    async def record_history(*_args, **_kwargs) -> None:
        return None

    async def run_worker(tracks_, *_args, **kwargs):
        captured["tracks"] = tracks_
        captured.update(kwargs)
        return []

    downloader._resolve_metadata_async = resolve_metadata  # type: ignore[method-assign]
    downloader._resolve_isrc_bulk_async = resolve_isrc  # type: ignore[method-assign]
    downloader._record_history_async = record_history  # type: ignore[method-assign]
    downloader._run_worker_async = run_worker  # type: ignore[method-assign]

    asyncio.run(downloader._run_once_async("https://open.spotify.com/playlist/abc"))
    return captured


def test_a_whole_playlist_links_tracks_from_scanned_folders(tmp_path: Path) -> None:
    existing = _audio(tmp_path / "old" / "Shared - Artist.flac")

    captured = _whole_run(tmp_path, "playlist")

    assert captured["existing_paths"] == {"t1": existing}
    assert captured["playlist_file"] == "rewrite"


def test_an_album_is_not_looked_up_in_scanned_folders(tmp_path: Path) -> None:
    _audio(tmp_path / "old" / "Shared - Artist.flac")

    captured = _whole_run(tmp_path, "album")

    assert captured["existing_paths"] == {}


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------


class _FakeTrack:
    id = "track-id"
    title = "Title"
    external_url = "https://open.spotify.com/track/track-id"


def test_gui_setting_reaches_the_download_options(
    tmp_path: Path, monkeypatch: Any
) -> None:
    seen: list[dict] = []
    capture_service(monkeypatch, seen)
    api = MornyeAPI()
    api.download_dir = str(tmp_path)
    api.current_tracks = [_FakeTrack()]
    api.current_url = _FakeTrack.external_url

    api._download_task(
        [0],
        {"services": ["tidal"], "library_scan_dirs": "/music/old; /music/flac"},
    )

    assert seen, "_download_task never reached the download call"
    assert seen[-1]["library_scan_dirs"] == ["/music/old", "/music/flac"]
