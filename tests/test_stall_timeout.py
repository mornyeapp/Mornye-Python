"""`timeout_s` bounds stalls, not the duration of a transfer that is moving."""

import asyncio

import pytest

from mornye.application.download_engine import (
    _await_with_stall_timeout,
    _TransferActivity,
)


def _activity():
    seen = []
    return _TransferActivity(lambda w, t: seen.append((w, t))), seen


def test_progressing_transfer_outlives_timeout():
    activity, seen = _activity()

    async def slow_but_steady():
        for i in range(8):
            await asyncio.sleep(0.05)
            activity(i * 1000 + 1000, 10_000)
        return "ok"

    async def run():
        return await _await_with_stall_timeout(
            slow_but_steady(), 0.2, activity, poll_s=0.02
        )

    # 0.4s total, far past the 0.2s timeout — but bytes never stop moving.
    assert asyncio.run(run()) == "ok"
    assert len(seen) == 8


def test_stalled_transfer_times_out():
    activity, _ = _activity()

    async def stalls():
        activity(1000, 10_000)
        await asyncio.sleep(10)

    async def run():
        await _await_with_stall_timeout(stalls(), 0.15, activity, poll_s=0.02)

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(run())


def test_no_first_byte_times_out():
    activity, _ = _activity()

    async def never_starts():
        await asyncio.sleep(10)

    async def run():
        await _await_with_stall_timeout(never_starts(), 0.1, activity, poll_s=0.02)

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(run())


def test_repeated_same_byte_count_is_not_activity():
    activity, _ = _activity()

    async def heartbeat_without_bytes():
        while True:
            activity(500, 10_000)
            await asyncio.sleep(0.02)

    async def run():
        await _await_with_stall_timeout(
            heartbeat_without_bytes(), 0.15, activity, poll_s=0.02
        )

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(run())


def test_result_and_exception_pass_through():
    async def ok():
        return 42

    async def boom():
        raise ValueError("x")

    assert asyncio.run(_await_with_stall_timeout(ok(), 1, None)) == 42
    with pytest.raises(ValueError):
        asyncio.run(_await_with_stall_timeout(boom(), 1, None))
