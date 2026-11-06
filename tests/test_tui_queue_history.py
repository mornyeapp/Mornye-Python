"""TUI: the Queue (staged) and History panels, and queueing from Tracks."""

from __future__ import annotations

from types import SimpleNamespace

from tui_harness import app_of, drives_the_ui

from mornye.core import download_log, error_hints, staged_queue
from mornye.core.tracklist import Tracklist
from mornye.tui.app import MODES, MornyeTui
from mornye.tui.config_state import ConfigState
from mornye.tui.history_view import HistoryPanel
from mornye.tui.staged_view import StagedQueuePanel, describe
from mornye.tui.tracklist_view import TracklistPanel

_KEYS = [key for key, _ in MODES]


def _ready_state() -> ConfigState:
    return ConfigState(
        url="https://open.spotify.com/album/x", output_dir="/tmp/mornye-test"
    )


def test_new_modes_are_in_the_sidebar() -> None:
    assert "staged" in _KEYS and "history" in _KEYS
    assert _KEYS.index("staged") < _KEYS.index("queue") < _KEYS.index("history")


def test_describe_reads_like_the_gui_row() -> None:
    line = describe(
        {
            "status": "failed",
            "title": "Mix",
            "subtitle": "me",
            "kind": "playlist",
            "track_count": 2,
            "error": "no provider",
        }
    )
    assert (
        "FAILED" in line
        and "Mix" in line
        and "Playlist · 2 tracks · no provider" in line
    )


def test_download_target_prefers_the_collection_when_whole() -> None:
    whole = staged_queue.add(
        kind="album",
        title="A",
        source_url="https://open.spotify.com/album/1",
        tracks=[{"title": "x", "id": "1"}],
        whole=True,
    )
    assert staged_queue.download_target(whole) == "https://open.spotify.com/album/1"
    part = staged_queue.add(
        kind="album",
        title="B",
        source_url="https://open.spotify.com/album/1",
        tracks=[{"title": "y", "external_url": "https://open.spotify.com/track/9"}],
    )
    assert staged_queue.download_target(part) == ["https://open.spotify.com/track/9"]
    staged_queue.set_target(part["id"], ["https://example.com/t"])
    assert staged_queue.download_target(staged_queue.get(part["id"])) == [
        "https://example.com/t"
    ]


def test_error_hints_name_the_missing_extension() -> None:
    msg = "The optional utilities extension is not installed; 'x' requires 'utilities.sflx'"
    assert error_hints.kind(msg) == "setup"
    assert "Utilities extension" in error_hints.explain(msg, "Search")
    assert error_hints.kind("Connection timed out") == "network"
    assert error_hints.explain("boom", "Search") == "Search failed — boom"


@drives_the_ui
async def test_l_on_tracks_queues_the_selection() -> None:
    async with MornyeTui(_ready_state()).run_test() as pilot:
        app = app_of(pilot)
        panel = app.query_one("#tracks", TracklistPanel)
        panel.tracklist = Tracklist(
            name="Record",
            tracks=[
                SimpleNamespace(
                    title="One", id="1", external_url="https://open.spotify.com/track/1"
                ),
                SimpleNamespace(
                    title="Two", id="2", external_url="https://open.spotify.com/track/2"
                ),
            ],
            source_url="https://open.spotify.com/album/x",
        )
        panel.selected = {1}
        panel.action_queue_later()
        await pilot.pause()
        items = staged_queue.list_items()
        assert len(items) == 1
        assert items[0]["title"] == "Record" and items[0]["kind"] == "album"
        assert staged_queue.download_target(items[0]) == [
            "https://open.spotify.com/track/2"
        ]


@drives_the_ui
async def test_queue_panel_lists_and_removes_items() -> None:
    staged_queue.add(kind="track", title="Keep", tracks=[{"title": "k"}])
    gone = staged_queue.add(kind="track", title="Gone", tracks=[{"title": "g"}])
    async with MornyeTui(_ready_state()).run_test() as pilot:
        app = app_of(pilot)
        app.query_one("#sidebar").index = _KEYS.index("staged")
        await pilot.pause()
        panel = app.query_one("#staged", StagedQueuePanel)
        assert [i["title"] for i in panel._shown] == ["Keep", "Gone"]
        from textual.widgets import OptionList

        panel.query_one("#staged-list", OptionList).highlighted = 1
        panel.query_one("#staged-remove").press()
        await pilot.pause()
        assert staged_queue.get(gone["id"]) is None


@drives_the_ui
async def test_history_panel_shows_downloads() -> None:
    download_log.record(
        title="Night Drive",
        artist="Zed",
        album="Dark",
        file_path="/a.flac",
        size_bytes=10,
    )
    async with MornyeTui(_ready_state()).run_test() as pilot:
        app = app_of(pilot)
        app.query_one("#sidebar").index = _KEYS.index("history")
        await pilot.pause(0.3)
        panel = app.query_one("#history", HistoryPanel)
        await panel.reload()
        assert [r["title"] for r in panel._rows] == ["Night Drive"]
