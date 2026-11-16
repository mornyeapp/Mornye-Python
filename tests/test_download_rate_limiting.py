from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timedelta, UTC
from email.utils import format_datetime

import httpx
import pytest

from mornye.core.config import DownloadConfig
from mornye.core.errors import RateLimitedError
from mornye.core.http import AsyncHttpClient
from mornye.core.job_queue import JobQueue
from mornye.core.rate_limiting import (
    DownloadRateLimiter,
    DownloadStopRequested,
    await_or_stop,
)


def test_random_spacing_is_separate_for_metadata_and_provider(monkeypatch) -> None:
    limiter = DownloadRateLimiter()
    monkeypatch.setattr(
        "mornye.core.rate_limiting.random.uniform", lambda low, high: 0.04
    )

    async def run() -> float:
        await limiter.wait("metadata", 0.04, 0.04)
        started = time.monotonic()
        await limiter.wait("provider")
        provider_wait = time.monotonic() - started
        started = time.monotonic()
        await limiter.wait("metadata", 0.04, 0.04)
        metadata_wait = time.monotonic() - started
        assert metadata_wait >= 0.025
        return provider_wait

    assert asyncio.run(run()) < 0.025


def test_429_cooldown_honors_retry_after_and_caps_it() -> None:
    limiter = DownloadRateLimiter()

    assert limiter.cooldown_after_429(RateLimitedError("spotify", 23)) == 23
    assert limiter.cooldown_after_429(RateLimitedError("spotify", 5000)) == 900
    assert limiter.is_rate_limited(RateLimitedError("spotify", 3))
    assert limiter.is_rate_limited("HTTP 429 Too Many Requests")
    assert not limiter.is_rate_limited("HTTP 503 Service Unavailable")


def test_http_date_retry_after_is_supported() -> None:
    retry_at = datetime.now(UTC) + timedelta(seconds=30)
    response = httpx.Response(
        429,
        headers={"Retry-After": format_datetime(retry_at, usegmt=True)},
        request=httpx.Request("GET", "https://example.test"),
    )

    with pytest.raises(RateLimitedError) as error:
        AsyncHttpClient("test")._raise_for_status(response)

    assert 20 <= error.value.retry_after <= 30


def test_stop_signal_cancels_an_in_flight_operation() -> None:
    stop_event = threading.Event()
    cancelled = asyncio.Event()

    async def operation() -> None:
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    async def run() -> None:
        task = asyncio.create_task(await_or_stop(operation(), stop_event))
        await asyncio.sleep(0.02)
        stop_event.set()
        with pytest.raises(DownloadStopRequested):
            await task
        assert cancelled.is_set()

    asyncio.run(run())


def test_download_config_rejects_reversed_or_negative_delay_ranges() -> None:
    with pytest.raises(ValueError, match="metadata_delay_max_s"):
        DownloadConfig(metadata_delay_min_s=2, metadata_delay_max_s=1)
    with pytest.raises(ValueError, match="finite number"):
        DownloadConfig(provider_delay_min_s=-1)


def test_stop_all_pending_jobs_prevents_them_from_starting() -> None:
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def handle(payload: dict) -> None:
        calls.append(payload["name"])
        if payload["name"] == "active":
            started.set()
            release.wait(timeout=2)

    queue = JobQueue(handle, workers=1, max_pending_per_owner=None)
    queue.submit("owner", {"name": "active"})
    assert started.wait(timeout=2)
    pending = queue.submit("owner", {"name": "pending"})

    assert queue.cancel_pending("owner") == 1
    release.set()

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        job = queue.get(pending.id)
        if job is not None and job.status.value == "failed":
            break
        time.sleep(0.01)

    assert calls == ["active"]
    assert queue.get(pending.id).error == "Cancelled before starting."
