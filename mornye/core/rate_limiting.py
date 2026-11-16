from __future__ import annotations

import asyncio
import math
import random
import re
import threading
import time
from collections.abc import Awaitable
from typing import Any, Literal
from collections.abc import Callable

from .errors import ErrorKind, RateLimitedError

RateLimitCategory = Literal["metadata", "provider"]
_RETRY_AFTER_RE = re.compile(r"\bretry[- ]after\s*[:=]?\s*(\d+(?:\.\d+)?)", re.I)
_HTTP_429_RE = re.compile(r"\b429\b")


class DownloadStopRequested(Exception):
    """Raised when a batch's stop signal interrupts a rate-limit wait."""


class DownloadRateLimiter:
    """Process-wide randomized request spacing and shared 429 cooldown."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_allowed = {"metadata": 0.0, "provider": 0.0}
        self._cooldown_until = 0.0

    async def wait(
        self,
        category: RateLimitCategory,
        minimum_s: float = 0.0,
        maximum_s: float = 0.0,
        *,
        stop_event: threading.Event | None = None,
    ) -> None:
        """Wait for a category's next slot, respecting global cooldown."""
        minimum_s = float(minimum_s)
        maximum_s = float(maximum_s)
        if (
            not math.isfinite(minimum_s)
            or not math.isfinite(maximum_s)
            or minimum_s < 0
            or maximum_s < minimum_s
        ):
            raise ValueError("Rate-limit delays must be finite and maximum >= minimum")
        while True:
            if stop_event is not None and stop_event.is_set():
                raise DownloadStopRequested("Download stopped.")
            now = time.monotonic()
            with self._lock:
                slot = max(now, self._next_allowed[category], self._cooldown_until)
                if slot <= now:
                    self._next_allowed[category] = now + random.uniform(
                        minimum_s, maximum_s
                    )
                    return
            await asyncio.sleep(min(max(slot - now, 0.01), 0.2))

    def cooldown_after_429(
        self,
        error: object,
        *,
        fallback_s: float = 60.0,
        maximum_s: float = 900.0,
    ) -> float:
        """Extend the global cooldown after a detected rate-limit response."""
        retry_after = getattr(error, "retry_after", None)
        if retry_after is None:
            match = _RETRY_AFTER_RE.search(str(error))
            retry_after = float(match.group(1)) if match else fallback_s
        try:
            seconds = min(maximum_s, max(1.0, float(retry_after)))
        except (TypeError, ValueError):
            seconds = min(maximum_s, max(1.0, fallback_s))
        until = time.monotonic() + seconds
        with self._lock:
            self._cooldown_until = max(self._cooldown_until, until)
        return seconds

    @staticmethod
    def is_rate_limited(error: object) -> bool:
        status_code = getattr(error, "status_code", None)
        return (
            isinstance(error, RateLimitedError)
            or getattr(error, "kind", None) == ErrorKind.RATE_LIMITED
            or (isinstance(status_code, int) and status_code == 429)
            or bool(_HTTP_429_RE.search(str(error)))
        )

    def reset(self) -> None:
        """Reset limiter state for isolated tests."""
        with self._lock:
            self._next_allowed = {"metadata": 0.0, "provider": 0.0}
            self._cooldown_until = 0.0


async def await_or_stop(
    awaitable: Awaitable[Any],
    stop_event: threading.Event | None,
    *,
    on_stop: Callable[[], None] | None = None,
) -> Any:
    """Cancel an in-flight operation promptly when its batch is stopped."""
    if stop_event is None:
        return await awaitable

    operation = asyncio.ensure_future(awaitable)

    async def watch_stop() -> None:
        # stop_event is a threading.Event set from another thread (the GUI's
        # Stop): it cannot be awaited, so it is polled.
        while not stop_event.is_set():  # noqa: ASYNC110
            await asyncio.sleep(0.1)

    watcher = asyncio.create_task(watch_stop())
    try:
        done, _ = await asyncio.wait(
            (operation, watcher), return_when=asyncio.FIRST_COMPLETED
        )
        if stop_event.is_set():
            if on_stop is not None:
                on_stop()
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            raise DownloadStopRequested("Download stopped.")
        if watcher in done:
            raise DownloadStopRequested("Download stopped.")
        return operation.result()
    except asyncio.CancelledError:
        operation.cancel()
        raise
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)


download_rate_limiter = DownloadRateLimiter()


__all__ = [
    "DownloadRateLimiter",
    "DownloadStopRequested",
    "RateLimitCategory",
    "await_or_stop",
    "download_rate_limiter",
]
