"""Speed and time-left, computed the way Mornye (the Flutter/Rust app) does."""

from __future__ import annotations

from mornye.core.progress import (
    ETA_MAX_SECONDS,
    ProgressCallback,
    estimate_eta_seconds,
    format_eta,
)
from mornye.tui.runner import make_status_line


def test_eta_is_remaining_over_speed_and_only_when_known() -> None:
    assert estimate_eta_seconds(2.0, 12.0, 2.0) == 5
    assert estimate_eta_seconds(2.0, 0.0, 2.0) is None  # size unknown
    assert estimate_eta_seconds(2.0, 12.0, 0.0) is None  # no speed yet
    assert estimate_eta_seconds(12.0, 12.0, 2.0) is None  # finished
    assert estimate_eta_seconds(0.0, ETA_MAX_SECONDS * 2 + 1, 1.0) is None  # > 1 h


def test_eta_format_matches_the_queue_labels() -> None:
    assert format_eta(None) == ""
    assert format_eta(42) == "~42s"
    assert format_eta(185) == "~3m05s"


def test_speed_is_sampled_over_128k_or_250ms_not_every_tick() -> None:
    cb = ProgressCallback()
    t0 = 1000.0
    assert cb._sample_speed(t0, 0) == 0.0
    # 64 KiB after 100 ms: neither threshold met, no new reading.
    assert cb._sample_speed(t0 + 0.1, 64 * 1024) == 0.0
    # 1 MiB after 250 ms: 4 MiB/s.
    assert cb._sample_speed(t0 + 0.25, 1024 * 1024) == 4.0
    # A small step in between keeps the last reading instead of spiking.
    assert cb._sample_speed(t0 + 0.26, 1024 * 1024 + 10) == 4.0


def test_tui_status_line_shows_time_left() -> None:
    line = make_status_line(
        {"completed": 1, "queued": 2, "current_speed": 4.0, "eta_seconds": 65}
    )
    assert "4.0 MB/s" in line and "~1m05s left" in line
