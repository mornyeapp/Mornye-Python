"""Quality fallback walks the tiers across every service: Hi-Res Lossless from
each service in turn, and only then Lossless from the first one again.

DownloadService runs one engine per service (LegacyDownloadAdapter scopes the
options to it), so this has to be ordered there: each engine on its own would
fall back to Lossless before the next service was ever asked for Hi-Res.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from mornye import DownloadRequest, MornyeConfig
from mornye.application import DownloadService, LegacyDownloadAdapter, ProviderResolver
from mornye.application import download_engine
from mornye.core.models import DownloadResult
from mornye.core.providers import ProviderProfile


def _run(monkeypatch, *, quality, allow_fallback, has):
    """`has`: service -> the tier it can deliver. Returns the runs made, as
    (service, quality, allow_fallback)."""
    runs: list[tuple[str, str, bool]] = []
    rank = {"LOSSLESS": 1, "HI_RES_LOSSLESS": 2}

    class FakeDownloader:
        def __init__(self, options):
            self.options = options

        async def run_async(self, source):
            service = self.options.services[0]
            wanted = self.options.quality
            runs.append((service, wanted, self.options.allow_fallback))
            good = rank[has[service]] >= rank.get(wanted, 1)
            if good or self.options.allow_fallback:
                return DownloadResult.ok(
                    service, f"/music/{service}.flac", source=source
                )
            return DownloadResult.fail(service, "Quality below requested")

    monkeypatch.setattr(download_engine, "MornyeDownloader", FakeDownloader)
    services = list(has)
    options = SimpleNamespace(
        services=services, quality=quality, allow_fallback=allow_fallback
    )
    config = MornyeConfig()
    config.download.services = services
    config.download.quality = quality
    config.download.allow_fallback = allow_fallback
    config.download.retries = 0
    report = asyncio.run(
        DownloadService(
            provider_resolver=ProviderResolver(
                [ProviderProfile(name) for name in services]
            ),
            provider_executor=LegacyDownloadAdapter.from_options(options),
        ).download(DownloadRequest(sources=["spotify:track:x"], config=config))
    )
    return runs, report


def test_every_service_is_asked_for_hi_res_before_any_falls_back(monkeypatch):
    runs, report = _run(
        monkeypatch,
        quality="HI_RES_LOSSLESS",
        allow_fallback=True,
        has={"ext:tidal-web": "LOSSLESS", "ext:qobuz-web": "HI_RES_LOSSLESS"},
    )

    assert runs == [
        ("ext:tidal-web", "HI_RES_LOSSLESS", False),
        ("ext:qobuz-web", "HI_RES_LOSSLESS", False),
    ]
    assert report.succeeded[0].provider == "ext:qobuz-web"


def test_lossless_starts_again_from_the_first_service(monkeypatch):
    runs, report = _run(
        monkeypatch,
        quality="HI_RES_LOSSLESS",
        allow_fallback=True,
        has={
            "ext:tidal-web": "LOSSLESS",
            "ext:qobuz-web": "LOSSLESS",
            "ext:amazon": "LOSSLESS",
        },
    )

    assert runs == [
        ("ext:tidal-web", "HI_RES_LOSSLESS", False),
        ("ext:qobuz-web", "HI_RES_LOSSLESS", False),
        ("ext:amazon", "HI_RES_LOSSLESS", False),
        ("ext:tidal-web", "LOSSLESS", True),
    ]
    assert report.succeeded[0].provider == "ext:tidal-web"


def test_with_fallback_off_each_service_keeps_the_requested_quality(monkeypatch):
    runs, _report = _run(
        monkeypatch,
        quality="HI_RES_LOSSLESS",
        allow_fallback=False,
        has={"ext:tidal-web": "LOSSLESS", "ext:qobuz-web": "LOSSLESS"},
    )

    assert runs == [
        ("ext:tidal-web", "HI_RES_LOSSLESS", False),
        ("ext:qobuz-web", "HI_RES_LOSSLESS", False),
    ]
