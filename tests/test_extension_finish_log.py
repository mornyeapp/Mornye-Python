"""The "Download finished" line must report the size of the file that was made.

A DASH/fMP4 extension hands back segments, not a file: nothing exists at
``output_path`` until ``_finalize_segments_async`` has merged them. The line
used to be written before that step, so every segmented download was reported
as "0 B" (and without a throughput figure) although the merged file was fine.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from mornye.core.models import TrackMetadata
from mornye.extensions.provider import JSExtensionProvider

_MERGED = b"fLaC" + b"\x00" * (3 * 1024 * 1024)


@pytest.fixture
def provider(tmp_path, monkeypatch):
    instance = JSExtensionProvider.__new__(JSExtensionProvider)
    instance.name = "ext:segmented"
    instance._stop_requested = lambda: False

    async def match_options(metadata):
        return {}

    def available(**kwargs):
        return {"available": True, "track_id": "t1"}

    def download(method, *args, **kwargs):
        # A segmenting extension reports no ready file, only its segments.
        assert method == "download"
        return {"success": True, "segments": ["unused"]}

    instance._provider_match_options = match_options
    instance.check_availability = available
    instance._call = download

    async def stub_poll(*args, **kwargs):
        return None

    instance._poll_file_progress_async = stub_poll

    async def merge(dl_result, output_path):
        # What the real merge does: the final file appears only now.
        output_path.write_bytes(_MERGED)
        return str(output_path)

    instance._finalize_segments_async = merge

    async def stop_here(*args, **kwargs):
        return False, "stop after the log line under test"

    monkeypatch.setattr(
        "mornye.core.download_validation.validate_downloaded_track_async",
        stop_here,
    )
    return instance


def _finish_lines(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("[ext:segmented] Download finished")
    ]


def test_a_segmented_download_reports_the_merged_file_size(provider, tmp_path, caplog):
    metadata = TrackMetadata(
        id="x",
        title="Tone",
        artists="Test",
        album="Fixtures",
        album_artist="Test",
        duration_ms=1000,
    )
    with caplog.at_level(logging.INFO, logger="mornye.extensions.provider"):
        asyncio.run(provider.download_track_async(metadata, str(tmp_path / "out")))

    lines = _finish_lines(caplog)
    assert len(lines) == 1, lines
    assert "0 B" not in lines[0], lines[0]
    assert "3.0 MB" in lines[0], lines[0]


def test_an_unmergeable_download_is_not_reported_as_finished(
    provider, tmp_path, caplog
):
    async def nothing_usable(dl_result, output_path):
        return None

    provider._finalize_segments_async = nothing_usable
    metadata = TrackMetadata(
        id="x",
        title="Tone",
        artists="Test",
        album="Fixtures",
        album_artist="Test",
        duration_ms=1000,
    )
    with caplog.at_level(logging.INFO, logger="mornye.extensions.provider"):
        result = asyncio.run(
            provider.download_track_async(metadata, str(tmp_path / "out"))
        )

    assert not result.success
    assert _finish_lines(caplog) == []
