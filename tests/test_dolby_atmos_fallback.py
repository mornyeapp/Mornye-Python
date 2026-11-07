"""A transient Atmos gateway failure can fall through to lower lossless tiers."""

from __future__ import annotations

import asyncio
from pathlib import Path

from mornye.application import download_engine as dl
from mornye.core.models import DownloadResult, TrackMetadata


def _track() -> TrackMetadata:
    return TrackMetadata(
        id="track-1",
        title="Track",
        artists="Artist",
        album="Album",
        album_artist="Artist",
    )


class _Provider:
    name = "tidal"

    def __init__(self, path: Path, responses: list[DownloadResult]) -> None:
        self.path = path
        self.responses = responses
        self.qualities: list[str] = []

    def set_progress_callback(self, _callback) -> None:
        pass

    async def download_track_async(self, _metadata, _output_dir, **kwargs):
        self.qualities.append(kwargs["quality"])
        result = self.responses.pop(0)
        if result.success:
            self.path.write_bytes(b"audio")
            return DownloadResult.ok(self.name, str(self.path))
        return result


def _isolate_download(monkeypatch) -> None:
    async def unchanged(_metadata, _requested, result, _provider):
        return result

    async def no_op(*_args, **_kwargs):
        return None

    monkeypatch.setattr(dl, "_reject_wrong_recording_async", unchanged)
    monkeypatch.setattr(dl, "_inject_provider_metadata_async", no_op)
    monkeypatch.setattr(dl, "_record_provider_outcome", no_op)
    monkeypatch.setattr(dl, "_schedule_hires_check", lambda *_args, **_kwargs: None)


def test_atmos_502_falls_through_successive_lossless_tiers(
    tmp_path, monkeypatch
) -> None:
    _isolate_download(monkeypatch)
    provider = _Provider(
        tmp_path / "track.flac",
        [
            DownloadResult.fail("tidal", "HTTP 502 Bad Gateway"),
            DownloadResult.fail("tidal", "HTTP 502 Bad Gateway"),
            DownloadResult.ok("tidal", str(tmp_path / "track.flac")),
        ],
    )
    opts = dl.DownloadOptions(
        output_dir=str(tmp_path),
        quality="DOLBY_ATMOS",
        allow_fallback=True,
        embed_lyrics=False,
    )

    result = asyncio.run(
        dl.download_one_async(_track(), str(tmp_path), [provider], opts)
    )

    assert result.success
    assert provider.qualities == [
        "DOLBY_ATMOS",
        "HI_RES_LOSSLESS",
        "LOSSLESS",
    ]


def test_atmos_502_without_fallback_fails_the_track_explicitly(
    tmp_path, monkeypatch
) -> None:
    _isolate_download(monkeypatch)
    provider = _Provider(
        tmp_path / "track.flac",
        [DownloadResult.fail("tidal", "HTTP 502 Bad Gateway")],
    )
    next_provider = _Provider(
        tmp_path / "other.flac",
        [DownloadResult.ok("tidal", str(tmp_path / "other.flac"))],
    )
    opts = dl.DownloadOptions(
        output_dir=str(tmp_path),
        quality="DOLBY_ATMOS",
        allow_fallback=False,
        embed_lyrics=False,
    )

    result = asyncio.run(
        dl.download_one_async(
            _track(),
            str(tmp_path),
            [provider, next_provider],
            opts,
        )
    )

    assert not result.success
    assert "Dolby Atmos is unavailable" in (result.error or "")
    assert provider.qualities == ["DOLBY_ATMOS"]
    assert next_provider.qualities == []
