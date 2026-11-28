"""What an extension is told about quality, and what its refusal does.

Strict Hi-Res (quality fallback off) deletes a 16-bit file after the
download. An extension can refuse earlier, but only if checkAvailability()
is told the quality and that nothing lower is acceptable, and its refusal
must settle the provider: asking again can spend API quota for the same
answer.
"""

from __future__ import annotations

import asyncio

import pytest

from mornye.application.download_engine import _definitive_failure
from mornye.core.models import TrackMetadata
from mornye.extensions.provider import JSExtensionProvider

_AMAZON_OPTIONS = [
    {"id": "best", "label": "FLAC Best Available", "kind": "lossless"},
    {"id": "opus", "label": "Opus 320kbps", "kind": "lossy"},
]


def _metadata() -> TrackMetadata:
    return TrackMetadata(
        id="x",
        title="Tone",
        artists="Test",
        album="Fixtures",
        album_artist="Test",
        duration_ms=1000,
    )


@pytest.fixture
def provider(monkeypatch):
    instance = JSExtensionProvider.__new__(JSExtensionProvider)
    instance.name = "ext:strict"
    instance._stop_requested = lambda: False
    instance.seen = {}
    instance.answer = {
        "success": False,
        "error_message": "CD-quality stream; transfer skipped",
        "error_type": "quality_unavailable",
    }
    monkeypatch.setattr(
        JSExtensionProvider,
        "quality_options",
        property(lambda self: _AMAZON_OPTIONS),
    )

    async def match_options(metadata):
        return {"spotify_id": "x"}

    def available(**kwargs):
        instance.seen["availability"] = kwargs["options"]
        return {"available": True, "track_id": "t1"}

    def download(method, track_id, quality, path, on_progress, options, **kwargs):
        assert method == "download"
        instance.seen["download"] = options
        return instance.answer

    async def stub_poll(*args, **kwargs):
        return None

    instance._provider_match_options = match_options
    instance.check_availability = available
    instance._call = download
    instance._poll_file_progress_async = stub_poll
    return instance


def _run(provider, tmp_path, **kwargs):
    return asyncio.run(
        provider.download_track_async(_metadata(), str(tmp_path / "out"), **kwargs)
    )


@pytest.mark.parametrize("allow_fallback", [True, False])
@pytest.mark.parametrize("tier", ["HI_RES_LOSSLESS", "LOSSLESS"])
def test_both_calls_are_told_the_quality_and_the_fallback_policy(
    provider, tmp_path, tier, allow_fallback
):
    _run(provider, tmp_path, quality=tier, allow_fallback=allow_fallback)

    for call in ("availability", "download"):
        options = provider.seen[call]
        # The id the extension declared, which is the same for both tiers...
        assert options["quality"] == "best", call
        # ...and the tier it stands for, which is not.
        assert options["requestedQuality"] == tier, call
        assert options["allowFallback"] is allow_fallback, call
        assert options["spotify_id"] == "x", call


def test_a_per_service_quality_is_never_strict(provider, tmp_path):
    _run(
        provider,
        tmp_path,
        quality="HI_RES_LOSSLESS",
        allow_fallback=False,
        extension_quality="best",
    )

    assert provider.seen["availability"]["allowFallback"] is True
    assert provider.seen["download"]["allowFallback"] is True


def test_a_strict_quality_refusal_is_not_retried(provider, tmp_path):
    result = _run(provider, tmp_path, quality="HI_RES_LOSSLESS", allow_fallback=False)

    assert not result.success
    assert "CD-quality stream; transfer skipped" in result.error
    assert _definitive_failure(result.error)


@pytest.mark.parametrize(
    ("allow_fallback", "error_type", "message"),
    [
        (True, "quality_unavailable", "lower tier"),
        (False, "api_error", "lower tier"),
        # A transient failure reported beside the refusal stays retryable.
        (False, "quality_unavailable", "lower tier | API attempt 2: HTTP 503"),
    ],
)
def test_other_failures_stay_retryable(
    provider, tmp_path, allow_fallback, error_type, message
):
    provider.answer = {
        "success": False,
        "error_message": message,
        "error_type": error_type,
    }

    result = _run(
        provider, tmp_path, quality="HI_RES_LOSSLESS", allow_fallback=allow_fallback
    )

    assert not result.success
    assert not _definitive_failure(result.error)
