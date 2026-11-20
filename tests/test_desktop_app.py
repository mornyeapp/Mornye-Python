"""The packaged desktop app's entry point (mornye/desktop_app.py)."""

from __future__ import annotations

import io
import sys

import pytest

from mornye import desktop_app


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        # A double-click: no arguments opens the GUI.
        (["Mornye"], ["Mornye", "--gui"]),
        # Older macOS adds the process serial number when Finder opens an app.
        (["Mornye", "-psn_0_12345"], ["Mornye", "--gui"]),
        # From a terminal the binary is still the command line.
        (["Mornye", "--help"], ["Mornye", "--help"]),
        (["Mornye", "--tui"], ["Mornye", "--tui"]),
        (
            ["Mornye", "https://x/track/1", "./out"],
            ["Mornye", "https://x/track/1", "./out"],
        ),
    ],
)
def test_desktop_argv(argv: list[str], expected: list[str]) -> None:
    assert desktop_app.desktop_argv(argv) == expected


def test_missing_streams_go_to_the_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("MORNYE_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    log = desktop_app.redirect_missing_streams()
    assert log is not None
    try:
        assert sys.stdout is log
        assert sys.stderr is log
        print("hello")
    finally:
        log.close()
    assert (tmp_path / "desktop.log").read_text(encoding="utf-8") == "hello\n"


def test_present_streams_are_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)

    assert desktop_app.redirect_missing_streams() is None
    assert sys.stdout is out
    assert sys.stderr is err
