"""Follow-ups to issues #277, #279 and #280 found reviewing their fixes.

* The Queue sends each batch with the tracklist its positions point into, so
  tracks queued from playlist A and started after opening playlist B are
  not read against B (wrong tracks, wrong folder).
* A partial playlist download names its folder with safe_dir_name(), the
  function the whole-playlist download uses.
* A followed-playlist refresh only rewrites the M3U inside a library, and
  places old tracks found outside it beside the playlist.
* A file placed in a playlist folder never takes over a different track
  with the same file name.
* A request delay whose maximum is below its minimum is a fixed delay, not
  a batch that fails.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest

from mornye.app import MornyeAPI
from mornye.application.download_engine import DownloadOptions
from mornye.application.download_worker import _free_target, bring_into_library
from mornye.core.models import TrackMetadata
from mornye.downloader import MornyeDownloader
from tests.application_download_capture import capture_service

A = "https://open.spotify.com/playlist/a"
B = "https://open.spotify.com/playlist/b"


def _track(track_id: str, title: str = "Song") -> TrackMetadata:
    return TrackMetadata(
        id=track_id,
        title=title,
        artists="Artist",
        album="Album",
        album_artist="Artist",
        duration_ms=1000,
    )


class _RecordingQueue:
    def __init__(self) -> None:
        self.jobs: list[dict] = []

    def submit(self, owner: str, payload: dict) -> None:
        self.jobs.append(payload)


def _api_with_two_lists() -> tuple[MornyeAPI, int, int]:
    api = MornyeAPI()
    api._download_queue = _RecordingQueue()
    tracks_a = [_track("a0"), _track("a1")]
    tracks_b = [_track("b0"), _track("b1"), _track("b2")]
    list_a = api._remember_tracklist(tracks_a, A, "Top 50 - India")
    list_b = api._remember_tracklist(tracks_b, B, "Top 50 - Global")
    api.current_tracks, api.current_url = tracks_b, B
    api.current_playlist_name = "Top 50 - Global"
    return api, list_a, list_b


# ── Queue: one tracklist per batch ────────────────────────────────────────


def test_queued_tracks_download_from_their_own_list() -> None:
    api, list_a, _ = _api_with_two_lists()

    api.download_tracks([1], {"services": ["tidal"]}, list_a)

    [job] = api._download_queue.jobs
    assert [t["id"] for t in job["tracks"]] == ["a1"]
    assert job["source_url"] == A
    assert job["collection_name"] == "Top 50 - India"
    assert job["whole"] is False


def test_a_whole_list_is_whole_against_its_own_length() -> None:
    api, list_a, _ = _api_with_two_lists()

    api.download_tracks([0, 1], {}, list_a)

    assert api._download_queue.jobs[0]["whole"] is True


def test_without_a_list_id_the_current_list_is_meant() -> None:
    api, _, _ = _api_with_two_lists()

    api.download_tracks([2], {})

    assert api._download_queue.jobs[0]["tracks"][0]["id"] == "b2"


def test_a_forgotten_list_is_refused_not_guessed() -> None:
    api, _, _ = _api_with_two_lists()
    pushed: list[tuple[str, Any]] = []
    api._push = lambda event, *args: pushed.append((event, *args))  # type: ignore[method-assign]

    api.download_tracks([0], {}, 999)

    assert api._download_queue.jobs == []
    assert ("app_download_finished", False) in pushed


def test_only_the_last_sixteen_lists_are_kept() -> None:
    api = MornyeAPI()
    first = api._remember_tracklist([_track("x")], A, "")
    for _ in range(16):
        api._remember_tracklist([_track("y")], B, "")
    assert first not in api._tracklists
    assert len(api._tracklists) == 16


# ── Partial playlist folder: safe_dir_name() ──────────────────────────────


def _gui_options(tmp_path: Path, monkeypatch: Any, config: dict, **batch: Any) -> dict:
    seen: list[dict] = []
    capture_service(monkeypatch, seen)
    api = MornyeAPI()
    api.download_dir = str(tmp_path)
    api.current_tracks = [_track("t0")]
    api.current_url = "https://open.spotify.com/track/t0"
    api._download_task([0], {"services": ["tidal"], **config}, **batch)
    assert seen, "_download_task never reached the download call"
    return seen[-1]


def test_partial_playlist_folder_matches_the_whole_playlist_one(
    tmp_path: Path, monkeypatch: Any
) -> None:
    opts = _gui_options(tmp_path, monkeypatch, {}, playlist_name="Chill Vibes. ")

    # safe_dir_name() drops the trailing dot and space, as the downloader
    # does for the folder of the whole playlist.
    assert Path(opts["output_dir"]) == tmp_path / "Chill Vibes"


# ── Request delays (#279) ─────────────────────────────────────────────────


def test_a_delay_maximum_below_its_minimum_is_a_fixed_delay(
    tmp_path: Path, monkeypatch: Any
) -> None:
    opts = _gui_options(
        tmp_path,
        monkeypatch,
        {
            "metadata_delay_min_s": 2,
            "metadata_delay_max_s": 0,
            "provider_delay_min_s": 1,
            "provider_delay_max_s": 3,
        },
    )

    assert (opts["metadata_delay_min_s"], opts["metadata_delay_max_s"]) == (2.0, 2.0)
    assert (opts["provider_delay_min_s"], opts["provider_delay_max_s"]) == (1.0, 3.0)


# ── Placing a file in a playlist folder ───────────────────────────────────


def test_a_different_track_with_the_same_name_gets_its_own_file(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    playlist = tmp_path / "Mornye" / "P"
    playlist.mkdir(parents=True)
    (playlist / "Intro.flac").write_bytes(b"another artist's intro")
    mine = elsewhere / "Intro.flac"
    mine.write_bytes(b"this artist's intro, longer")

    placed = bring_into_library(mine, str(playlist))

    assert placed == playlist / "Intro (2).flac"
    assert placed.read_bytes() == mine.read_bytes()
    # Placed again (the next run): the same file is found, not a third one.
    assert bring_into_library(mine, str(playlist)) == placed


def test_an_earlier_placement_of_the_same_file_is_reused(tmp_path: Path) -> None:
    src = tmp_path / "a" / "Song.flac"
    src.parent.mkdir()
    src.write_bytes(b"audio")
    dest = tmp_path / "b"
    dest.mkdir()
    os.link(src, dest / "Song.flac")

    assert _free_target(src, dest) == dest / "Song.flac"


# ── Followed-playlist refresh (#280) ──────────────────────────────────────


class _FakeWorker:
    def __init__(self, output_dir: Path, completed: dict[str, str]) -> None:
        self.output_dir = str(output_dir)
        self.completed_paths = completed


def test_refresh_places_old_tracks_from_other_folders_beside_the_playlist(
    tmp_path: Path,
) -> None:
    library = tmp_path / "Mornye"
    folder = library / "Top Global"
    folder.mkdir(parents=True)
    other_music = tmp_path / "Other Music"
    other_music.mkdir()
    old = other_music / "Old.flac"
    old.write_bytes(b"old track")
    added = folder / "New.flac"
    worker = _FakeWorker(folder, {"t2": str(added)})

    asyncio.run(
        MornyeDownloader._write_run_playlist_file_async(
            DownloadOptions(output_dir=str(folder), library_dir=str(library)),
            "rewrite",
            "",
            [_track("t2", "New")],
            worker,  # type: ignore[arg-type]
            playlist_tracks=[_track("t1", "Old"), _track("t2", "New")],
            existing_paths={"t1": old},
        )
    )

    lines = (folder / "Top Global.m3u8").read_text(encoding="utf-8").splitlines()
    assert [line for line in lines if not line.startswith("#")] == [
        "Old.flac",
        "New.flac",
    ]
    assert (folder / "Old.flac").read_bytes() == b"old track"


@pytest.mark.parametrize(
    ("library", "mode"),
    [(False, None), (True, "rewrite")],
    ids=["no-library", "library"],
)
def test_refresh_rewrites_the_m3u_only_inside_a_library(
    tmp_path: Path, monkeypatch: Any, library: bool, mode: str | None
) -> None:
    """Playlist subfolders off: nothing old was looked up, so no rewrite."""
    seen: list[dict] = []

    async def fake_worker(self: Any, *args: Any, **kwargs: Any) -> list:
        seen.append(kwargs)
        return []

    async def resolved(self: Any, urls: Any, prefetched: Any = None) -> list:
        return [_track("t2")]

    async def same(self: Any, tracks: Any) -> Any:
        return tracks

    async def no_matches(self: Any, tracks: Any) -> dict:
        return {}

    monkeypatch.setattr(MornyeDownloader, "_run_worker_async", fake_worker)
    monkeypatch.setattr(MornyeDownloader, "_resolve_track_list_async", resolved)
    monkeypatch.setattr(MornyeDownloader, "_resolve_isrc_bulk_async", same)
    monkeypatch.setattr(MornyeDownloader, "_library_matches_async", no_matches)
    downloader = MornyeDownloader(
        DownloadOptions(
            output_dir=str(tmp_path / "Top Global"),
            library_dir=str(tmp_path) if library else None,
            m3u_playlist_tracks=[_track("t1"), _track("t2")],
        )
    )

    asyncio.run(downloader.run_tracks_async(["https://open.spotify.com/track/t2"]))

    assert seen[0]["playlist_file"] == (mode or ("append" if library else None))
    assert (seen[0]["playlist_tracks"] is not None) is library
