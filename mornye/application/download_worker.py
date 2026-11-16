from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mornye.application.post_processing import PostProcessingService
from mornye.core.models import DownloadResult, TrackMetadata

TrackExecutor = Callable[[TrackMetadata, str, int], Awaitable[DownloadResult]]
OutputDirectory = Callable[[TrackMetadata], Awaitable[str]]
ResultCallback = Callable[[TrackMetadata, DownloadResult], Awaitable[None]]
StartCallback = Callable[[TrackMetadata, int], Awaitable[None]]

logger = logging.getLogger(__name__)


def _inside(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def _same_file(a: Path, b: Path) -> bool:
    """The same audio: one inode, or a copy made earlier (same size and
    modification time, which shutil.copy2 keeps)."""
    try:
        if a.samefile(b):
            return True
        sa, sb = a.stat(), b.stat()
        return sa.st_size == sb.st_size and int(sa.st_mtime) == int(sb.st_mtime)
    except OSError:
        return False


def _free_target(existing: Path, folder: Path) -> Path:
    """Where `existing` goes in `folder`: its own name, unless a different
    track already holds it ("Intro.flac" by two artists); then "Name (2)",
    "Name (3)"… An earlier placement of the same file is found again."""
    candidate = folder / existing.name
    n = 2
    while candidate.exists() and not _same_file(existing, candidate):
        candidate = folder / f"{existing.stem} ({n}){existing.suffix}"
        n += 1
    return candidate


def bring_into_library(existing: Path, output_dir: str) -> Path:
    """`existing` placed in `output_dir`: a hard link, else a copy.

    For a playlist's M3U a track found outside the library (one of the
    user's other music folders, issue #272) is listed as ``../../elsewhere``,
    a path a media server scanning only the library cannot follow (issue
    #277). Placing the file beside the playlist keeps what #272 was for, no
    second download, and the link costs no space on the same disk. Falls
    back to `existing` itself if neither works.
    """
    try:
        target = _free_target(existing, Path(output_dir))
        if target.exists():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(existing, target)
        except OSError:
            shutil.copy2(existing, target)
        return target
    except OSError as exc:
        logger.warning(
            "[library] Could not place %s in %s: %s", existing, output_dir, exc
        )
        return existing


@dataclass
class WorkerReport:
    failed: list[tuple[str, str, str, str]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    completed: dict[str, str] = field(default_factory=dict)
    results: dict[str, DownloadResult] = field(default_factory=dict)


class ApplicationDownloadWorker:
    """Concurrent application worker independent of legacy provider details."""

    def __init__(
        self,
        tracks: list[TrackMetadata],
        *,
        max_concurrent: int,
        executor: TrackExecutor,
        output_directory: OutputDirectory,
        post_processing: PostProcessingService,
        existing_paths: dict[str, Path] | None = None,
        skipped_provider: str = "none",
        positions: list[int] | None = None,
        on_result: ResultCallback | None = None,
        on_start: StartCallback | None = None,
        library_root: str | None = None,
    ) -> None:
        self._tracks = tracks
        self._max_concurrent = max(1, max_concurrent)
        self._executor = executor
        self._output_directory = output_directory
        self._post_processing = post_processing
        self._existing_paths = existing_paths or {}
        self._skipped_provider = skipped_provider
        self._positions = positions or list(range(1, len(tracks) + 1))
        self._on_result = on_result
        self._on_start = on_start
        # Set for a playlist run: a track already on disk outside this
        # folder is placed inside it rather than linked where it is.
        self._library_root = Path(library_root) if library_root else None

    async def run(self, options: Any) -> WorkerReport:
        report = WorkerReport()
        semaphore = asyncio.Semaphore(self._max_concurrent)

        async def process(index: int, track: TrackMetadata) -> None:
            async with semaphore:
                if self._on_start is not None:
                    await self._on_start(track, index)
                existing = self._existing_paths.get(track.id)
                if existing is not None and self._library_root is not None:
                    if not await asyncio.to_thread(
                        _inside, existing, self._library_root
                    ):
                        existing = await asyncio.to_thread(
                            bring_into_library,
                            existing,
                            await self._output_directory(track),
                        )
                if existing is not None:
                    result = DownloadResult.skipped_result(
                        self._skipped_provider,
                        str(existing),
                    )
                else:
                    output_dir = await self._output_directory(track)
                    try:
                        result = await self._executor(
                            track, output_dir, self._positions[index]
                        )
                    except Exception as exc:
                        result = DownloadResult.fail("none", f"Unexpected error: {exc}")

                result = await self._post_processing.process(result, track, options)
                report.results[track.id] = result.model_copy(
                    update={"source": track.external_url or f"spotify:track:{track.id}"}
                )
                if result.success and result.file_path:
                    report.completed[track.id] = result.file_path
                if result.success and result.skipped:
                    report.skipped.append((track.id, track.title))
                elif not result.success:
                    report.failed.append(
                        (
                            track.id,
                            track.title,
                            track.artists,
                            result.error or "unknown",
                        )
                    )
                if self._on_result is not None:
                    await self._on_result(track, result)

        await asyncio.gather(
            *(process(index, track) for index, track in enumerate(self._tracks))
        )
        return report
