"""N3: the TUI's Following panel drives the same store as the GUI and CLI."""

from __future__ import annotations

from tui_harness import app_of, drives_the_ui

from mornye.core import subscriptions as subs
from mornye.tui.app import MODES, MornyeTui
from mornye.tui.config_state import ConfigState

ARTIST_URL = "https://open.spotify.com/artist/0TnOYISbd1XYRBk9myaseg"


def _state() -> ConfigState:
    return ConfigState(
        url="https://open.spotify.com/track/x", output_dir="/tmp/mornye-test"
    )


async def _open_following(pilot) -> None:
    app = app_of(pilot)
    app.query_one("#sidebar").index = [k for k, _ in MODES].index("following")
    await pilot.pause()
    assert app.query_one("#panels").current == "following"


@drives_the_ui
async def test_follow_pause_and_remove_from_the_panel() -> None:
    from textual.widgets import DataTable, Input

    async with MornyeTui(_state()).run_test(size=(140, 44)) as pilot:
        await _open_following(pilot)
        app = app_of(pilot)
        app.query_one("#following-url", Input).value = ARTIST_URL
        app.query_one("#following").follow()
        await pilot.pause()

        [sub] = subs.list_all()
        assert sub.url == ARTIST_URL and sub.enabled
        # A new subscription downloads where the Download panel points.
        assert sub.output_dir == "/tmp/mornye-test"
        assert app.query_one("#following-table", DataTable).row_count == 1

        app.query_one("#following").toggle_selected()
        await pilot.pause()
        assert subs.get(sub.id).enabled is False

        app.query_one("#following").remove_selected()
        await pilot.pause()
        assert subs.list_all() == []
        assert app.query_one("#following-table", DataTable).row_count == 0


@drives_the_ui
async def test_lists_what_the_cli_followed() -> None:
    from textual.widgets import DataTable

    subs.add(ARTIST_URL, name="Some Artist", output_dir="/music")
    async with MornyeTui(_state()).run_test(size=(140, 44)) as pilot:
        await _open_following(pilot)
        table = app_of(pilot).query_one("#following-table", DataTable)
        assert table.row_count == 1
        assert table.get_row_at(0)[0] == "Some Artist"
