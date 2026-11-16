"""Issue #277: a playlist's tracks stay in its folder and on its M3U.

Two leftovers of the report. A track found in one of the user's other music
folders (issue #272) was listed in the M3U as ``../../elsewhere/Track.flac``,
which a media server scanning only the download folder cannot follow; it is
now placed beside the playlist. (The retry half is pinned in
test_failed_tracks_and_resume.py.)
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from mornye.application.download_worker import (
    ApplicationDownloadWorker,
    bring_into_library,
)
from mornye.application.post_processing import PostProcessingService
from mornye.core.models import DownloadResult, TrackMetadata


def _track(track_id: str) -> TrackMetadata:
    return TrackMetadata(
        id=track_id, title=track_id, artists="A", album="B", album_artist="A"
    )


def _audio(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not really audio, but not empty")
    return path


async def _passthrough(result, _track, _options):
    return result


def _run(existing: dict[str, Path], playlist_dir: Path, root: Path | None):
    async def never(*_args):
        raise AssertionError("a track already on disk is not downloaded")

    async def output_directory(_track):
        return str(playlist_dir)

    worker = ApplicationDownloadWorker(
        [_track(t) for t in existing],
        max_concurrent=2,
        executor=never,
        output_directory=output_directory,
        post_processing=PostProcessingService(_passthrough),
        existing_paths=existing,
        library_root=str(root) if root else None,
    )
    return asyncio.run(worker.run(None))


def test_a_track_outside_the_library_is_placed_in_the_playlist(tmp_path: Path):
    elsewhere = _audio(tmp_path / "unknown_folder" / "RAP" / "PHOBIA.flac")
    library = tmp_path / "Mornye"
    playlist = library / "Top 50 - India"

    report = _run({"t1": elsewhere}, playlist, library)

    placed = Path(report.completed["t1"])
    assert placed == playlist / "PHOBIA.flac"
    assert placed.read_bytes() == elsewhere.read_bytes()
    assert elsewhere.exists(), "the user's own copy is left where it was"
    assert report.skipped == [("t1", "t1")]


def test_a_track_from_another_playlist_folder_is_linked_where_it_is(tmp_path):
    """Inside the library a relative M3U path works: issue #267 stands."""
    library = tmp_path / "Mornye"
    other = _audio(library / "Other Mix" / "Song.flac")

    report = _run({"t1": other}, library / "Top 50 - India", library)

    assert Path(report.completed["t1"]) == other
    assert not (library / "Top 50 - India").exists()


def test_without_a_playlist_a_found_track_is_only_skipped(tmp_path: Path):
    elsewhere = _audio(tmp_path / "old" / "Song.flac")

    report = _run({"t1": elsewhere}, tmp_path / "out", None)

    assert Path(report.completed["t1"]) == elsewhere


def test_placing_reuses_a_file_already_there(tmp_path: Path):
    source = _audio(tmp_path / "old" / "Song.flac")
    there = _audio(tmp_path / "pl" / "Song.flac")
    stamp = there.stat().st_mtime_ns

    assert bring_into_library(source, str(tmp_path / "pl")) == there
    assert there.stat().st_mtime_ns == stamp


def test_placing_prefers_a_hard_link(tmp_path: Path):
    source = _audio(tmp_path / "old" / "Song.flac")

    placed = bring_into_library(source, str(tmp_path / "pl"))

    if hasattr(os, "link"):
        assert os.path.samefile(placed, source)


def test_a_failed_result_is_unaffected(tmp_path: Path):
    async def fail(*_args):
        return DownloadResult.fail("p", "nope")

    async def output_directory(_track):
        return str(tmp_path)

    worker = ApplicationDownloadWorker(
        [_track("t1")],
        max_concurrent=1,
        executor=fail,
        output_directory=output_directory,
        post_processing=PostProcessingService(_passthrough),
        library_root=str(tmp_path),
    )
    report = asyncio.run(worker.run(None))
    assert report.failed and not report.completed
