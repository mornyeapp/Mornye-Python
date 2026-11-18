"""A provider that answered "not there" is not asked again.

From a user's log: a track no service carries was asked of each provider
three times, each time getting the same "Track not available", and the
final 503 of one backend was reported as the reason for all of it.
"""

from __future__ import annotations

import pytest

from mornye.application.download_engine import _definitive_failure


@pytest.mark.parametrize(
    "error",
    [
        "Track not available: No verified YouTube Music track match found",
        "HTTP 404 for /dl/amazeamazeamaze",
        "Track not found on Qobuz",
    ],
)
def test_not_there_is_definitive(error):
    assert _definitive_failure(error)


@pytest.mark.parametrize(
    "error",
    [
        "Failed to resolve Deezer download: HTTP 503 for /dl/dzr",
        "HTTP 429 Too Many Requests",
        "Download timed out after 120s",
        "connection reset by peer",
        "",
        None,
    ],
)
def test_a_hiccup_is_retried(error):
    assert not _definitive_failure(error)


# ── the attempt loop ─────────────────────────────────────────────────────

import asyncio  # noqa: E402
from typing import Any, cast  # noqa: E402

import mornye.application.download_engine as dl  # noqa: E402
from mornye.core.models import DownloadResult, TrackMetadata  # noqa: E402


class _Provider:
    def __init__(self, name: str, error: str) -> None:
        self.name, self.error, self.calls = name, error, 0

    def set_progress_callback(self, cb) -> None:
        pass

    async def download_track_async(self, metadata, output_dir, **kwargs):
        self.calls += 1
        return DownloadResult.fail(self.name, self.error)


def _run(tmp_path, monkeypatch, providers):
    async def no_lookup(isrc):
        return None

    async def no_sleep(_s):
        return None

    monkeypatch.setattr("mornye.core.recording_guard._lookup_isrc_async", no_lookup)
    monkeypatch.setattr(dl.asyncio, "sleep", no_sleep)
    track = TrackMetadata(
        id="t",
        title="Song",
        artists="A",
        artist_names=["A"],
        album="B",
        album_artist="A",
        duration_ms=200000,
    )
    opts = dl.DownloadOptions(
        output_dir=str(tmp_path), embed_lyrics=False, track_max_retries=2
    )
    return asyncio.run(
        dl.download_one_async(track, str(tmp_path), cast(Any, providers), opts)
    )


def test_not_there_is_asked_once_and_the_rest_retried(tmp_path, monkeypatch):
    gone = _Provider("ext:ytmusic", "Track not available: No verified match found")
    busy = _Provider("ext:deezer", "Failed to resolve Deezer download: HTTP 503")
    result = _run(tmp_path, monkeypatch, [gone, busy])
    assert not result.success
    assert gone.calls == 1
    assert busy.calls == 3
    # Every provider's own reason is in the report, not just the last one.
    assert "not available" in result.error and "503" in result.error


def test_no_retry_when_every_provider_said_not_there(tmp_path, monkeypatch):
    a = _Provider("ext:qobuz-web", "Track not available")
    b = _Provider("ext:amazon", "HTTP 404 for /dl/amazeamazeamaze")
    result = _run(tmp_path, monkeypatch, [a, b])
    assert (a.calls, b.calls) == (1, 1)
    assert "1 attempt(s)" in result.error


# ── issue #284: a wrong file is not downloaded only to be removed again ──


@pytest.mark.parametrize(
    "error",
    [
        "Validation failed: Wrong duration: file is 181s, expected ~139s — file removed",
        "Wrong track: different recording: asked for ISRC QZNWX2296615, got USUM72306884",
    ],
)
def test_a_wrong_file_is_definitive(error):
    assert _definitive_failure(error)


def test_a_truncated_file_is_retried():
    assert not _definitive_failure(
        "Validation failed: Wrong duration (short track truncated): "
        "file is 20s, expected ~90s — file removed"
    )
