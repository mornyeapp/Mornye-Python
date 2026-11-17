"""Reissue ISRCs accepted via MusicBrainz, and per-track bars released on return."""

from __future__ import annotations

import asyncio

import pytest

from mornye.core import musicbrainz
from mornye.core.progress import ProgressManager


@pytest.fixture(autouse=True)
def _fresh_pair_cache(monkeypatch):
    monkeypatch.setattr(musicbrainz, "_isrc_pair_cache", {})
    monkeypatch.setattr(musicbrainz, "should_skip_mb", lambda: False)


def _answer(monkeypatch, recordings=None, exc=None):
    calls = []

    async def fake_query(query: str) -> dict:
        calls.append(query)
        if exc is not None:
            raise exc
        return {"recordings": recordings or []}

    monkeypatch.setattr(musicbrainz, "_query_recordings_async", fake_query)
    return calls


def test_two_isrcs_on_one_recording_are_the_same_recording(monkeypatch) -> None:
    # Michael Bublé, "Holly Jolly Christmas": 2011 album vs 2013 deluxe.
    calls = _answer(
        monkeypatch,
        [{"title": "Holly Jolly Christmas", "isrcs": ["USRE11300228", "USRE11100705"]}],
    )
    shared = asyncio.run(
        musicbrainz.isrcs_share_recording_async("USRE11100705", "usre11300228")
    )
    assert shared is True
    assert calls == ["isrc:USRE11100705"]
    # Cached per pair, in either order.
    assert asyncio.run(
        musicbrainz.isrcs_share_recording_async("USRE11300228", "USRE11100705")
    )
    assert len(calls) == 1


def test_isrcs_on_separate_recordings_are_not_merged(monkeypatch) -> None:
    _answer(
        monkeypatch,
        [
            {"isrcs": ["USUM70504267"]},
            {"isrcs": ["USIR10300033"]},
        ],
    )
    assert (
        asyncio.run(
            musicbrainz.isrcs_share_recording_async("USUM70504267", "USIR10300033")
        )
        is False
    )


def test_an_unreachable_musicbrainz_is_unknown_not_a_match(monkeypatch) -> None:
    _answer(monkeypatch, exc=RuntimeError("offline"))
    assert (
        asyncio.run(musicbrainz.isrcs_share_recording_async("AAA111", "BBB222")) is None
    )


def test_a_paused_musicbrainz_is_not_asked(monkeypatch) -> None:
    calls = _answer(monkeypatch, [{"isrcs": ["AAA111", "BBB222"]}])
    monkeypatch.setattr(musicbrainz, "should_skip_mb", lambda: True)
    assert (
        asyncio.run(musicbrainz.isrcs_share_recording_async("AAA111", "BBB222")) is None
    )
    assert calls == []


def test_a_bar_whose_total_was_overestimated_is_released_on_return(
    monkeypatch,
) -> None:
    """The extension said 89 MB, delivered 87: the bar never reaches its
    total, so only clear_item() can remove it — and a progress event still
    queued when it is called must not bring it back.
    """
    import mornye.core.progress as progress

    monkeypatch.setattr(progress, "progress_bars_enabled", lambda: True)
    monkeypatch.setattr(progress, "sink_active", lambda: False)
    ProgressManager._bars.clear()
    ProgressManager._slot_map.clear()
    ProgressManager._event_queue = None
    ProgressManager._worker_task = None
    ProgressManager._queue_loop = None

    async def scenario() -> None:
        ProgressManager.enqueue_progress("t1", "Track", 10, 89)
        await asyncio.sleep(0.01)
        assert "t1" in ProgressManager._bars
        ProgressManager.enqueue_progress("t1", "Track", 87, 89)
        ProgressManager.clear_item("t1")
        await asyncio.sleep(0.01)
        assert "t1" not in ProgressManager._bars
        await ProgressManager.clear_all()

    asyncio.run(scenario())
