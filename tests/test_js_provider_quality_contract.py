"""Native Qobuz quality codes must reach JS as its declared quality IDs."""

import asyncio
from types import SimpleNamespace

import pytest

from mornye.core.models import DownloadResult, TrackMetadata
from mornye.core.quality import quality_for_provider
from mornye.extensions.provider import JSExtensionProvider


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        ("HI_RES_LOSSLESS", "HI_RES_LOSSLESS"),
        ("HI_RES", "HI_RES"),
        ("LOSSLESS", "LOSSLESS"),
    ],
)
@pytest.mark.parametrize("declared_contract", [True, False])
def test_engine_quality_reaches_the_js_manifest_contract(
    monkeypatch,
    requested,
    expected,
    declared_contract,
):
    provider = object.__new__(JSExtensionProvider)
    provider.name = "ext:qobuz-web"
    provider._ext = SimpleNamespace(
        manifest={
            "qualityOptions": (
                [{"id": name} for name in ("HI_RES_LOSSLESS", "HI_RES", "LOSSLESS")]
                if declared_contract
                else []
            ),
        }
    )
    received = []

    async def capture(self, metadata, output_dir, **kwargs):
        received.append(kwargs["quality"])
        return DownloadResult.fail("test", "no network in contract test")

    monkeypatch.setattr(JSExtensionProvider, "_do_download_async", capture)
    metadata = TrackMetadata(
        id="test",
        title="Test",
        artists="Artist",
        album="Album",
        album_artist="Artist",
    )
    native_quality = quality_for_provider(provider.name, requested)
    asyncio.run(
        provider.download_track_async(
            metadata,
            "/tmp/unused",
            quality=native_quality,
        )
    )
    assert received == [expected if declared_contract else native_quality]
