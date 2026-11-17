"""'Finish current': tracks in flight complete, nothing further starts."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

from mornye.application.download_worker import ApplicationDownloadWorker
from mornye.application.post_processing import PostProcessingService
from mornye.core.models import DownloadResult, TrackMetadata


def _track(track_id: str) -> TrackMetadata:
    return TrackMetadata(
        id=track_id, title=track_id, artists="A", album="B", album_artist="A"
    )


async def _passthrough(result, _track, _options):
    return result


def test_running_tracks_finish_and_waiting_ones_never_start(tmp_path) -> None:
    finish = threading.Event()
    started: list[str] = []

    async def executor(track, _out, _pos):
        started.append(track.id)
        if len(started) == 2:
            # Requested while both slots are busy: these two must still
            # complete; t3…t5 must not be started.
            finish.set()
        await asyncio.sleep(0.01)
        return DownloadResult(
            success=True, provider="test", file_path=str(tmp_path / track.id)
        )

    async def output_directory(_track):
        return str(tmp_path)

    worker = ApplicationDownloadWorker(
        [_track(f"t{i}") for i in range(1, 6)],
        max_concurrent=2,
        executor=executor,
        output_directory=output_directory,
        post_processing=PostProcessingService(_passthrough),
    )
    report = asyncio.run(worker.run(SimpleNamespace(finish_event=finish)))

    assert started == ["t1", "t2"]
    assert set(report.completed) == {"t1", "t2"}
    assert [tid for tid, _ in report.not_started] == ["t3", "t4", "t5"]
    assert not report.failed


def test_without_a_finish_request_everything_runs(tmp_path) -> None:
    async def executor(track, _out, _pos):
        return DownloadResult(
            success=True, provider="test", file_path=str(tmp_path / track.id)
        )

    async def output_directory(_track):
        return str(tmp_path)

    worker = ApplicationDownloadWorker(
        [_track(f"t{i}") for i in range(1, 4)],
        max_concurrent=2,
        executor=executor,
        output_directory=output_directory,
        post_processing=PostProcessingService(_passthrough),
    )
    report = asyncio.run(worker.run(SimpleNamespace(finish_event=threading.Event())))
    assert len(report.completed) == 3
    assert not report.not_started
