"""following_view.py — Followed artists and playlists in the TUI (N3).

The GUI's *Following* view and the CLI's ``--subscribe`` flags manage the
same store (core/subscriptions.py); until now the TUI had no way to see it.
This panel lists every subscription with its state and schedule, follows a
new link, and pauses, resumes or removes the selected one. Checking for new
releases stays with the scheduler and ``--subscribe-check``: a check talks to
Spotify for every row and downloads, which is the Download panel's job.

Everything here is a local SQLite read or write, so it loads on mount and
re-reads after each change; nothing touches the network.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, cast

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, DataTable, Input, Label

_COLUMNS = ("Name", "Kind", "State", "Every", "Last check", "Seen", "Detail")


def _ago(ts: float | None) -> str:
    if not ts:
        return "never"
    minutes = int(max(0.0, time.time() - ts) // 60)
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    return f"{hours} h ago" if hours < 48 else f"{hours // 24} d ago"


def _every(minutes: int) -> str:
    if not minutes:
        return "manual"
    if minutes % 1440 == 0:
        return f"{minutes // 1440} d"
    if minutes % 60 == 0:
        return f"{minutes // 60} h"
    return f"{minutes} min"


class FollowingPanel(VerticalScroll):
    """One row per subscription, with follow, pause/resume and remove."""

    BORDER_TITLE = "Following"

    def __init__(
        self, output_dir: Callable[[], str] = lambda: "", **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        #: Where a newly followed link downloads to: the Download panel's
        #: folder, read when the button is pressed.
        self._output_dir = output_dir

    def compose(self) -> ComposeResult:
        yield Label(
            "Artists and playlists Mornye watches for new releases — the same "
            "list as the GUI's Following view and --subscribe. New releases are "
            "picked up by the scheduler or --subscribe-check.",
            classes="panel-intro",
        )
        with Horizontal(classes="setting-row"):
            yield Input(
                placeholder="https://open.spotify.com/artist/…",
                id="following-url",
            )
            yield Button("Follow", id="following-add", variant="primary")
        with Horizontal(classes="setting-row"):
            yield Button("Refresh", id="following-refresh")
            yield Button("Pause / resume", id="following-toggle")
            yield Button("Remove", id="following-remove")
        yield Label("", id="following-status")
        table: DataTable = DataTable(id="following-table", zebra_stripes=True)
        table.cursor_type = "row"
        yield table

    def on_mount(self) -> None:
        table = self.query_one("#following-table", DataTable)
        for column in _COLUMNS:
            table.add_column(column, key=column)
        self.reload()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        handlers = {
            "following-add": self.follow,
            "following-refresh": self.reload,
            "following-toggle": self.toggle_selected,
            "following-remove": self.remove_selected,
        }
        handler = handlers.get(event.button.id or "")
        if handler is not None:
            handler()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "following-url":
            self.follow()

    def _status(self, text: str) -> None:
        self.query_one("#following-status", Label).update(text)

    def reload(self) -> None:
        from ..core.subscriptions import list_all

        try:
            subs = list_all(owner="")
        except Exception as exc:
            self._status(f"Could not read the subscriptions — {exc}")
            return
        table = self.query_one("#following-table", DataTable)
        cursor = table.cursor_row
        table.clear()
        for sub in subs:
            row = sub.to_dict()
            table.add_row(
                row["name"] or row["url"],
                row["kind"],
                "on" if row["enabled"] else "paused",
                _every(int(row.get("interval_minutes") or 0)),
                _ago(row.get("last_checked_at")),
                str(row.get("seen_count") or 0),
                (row.get("last_error") or "")[:60],
                key=row["id"],
            )
        if subs:
            table.move_cursor(row=min(cursor, len(subs) - 1))
            paused = sum(1 for s in subs if not s.enabled)
            text = f"Following {len(subs)}."
            self._status(text + (f" {paused} paused." if paused else ""))
        else:
            self._status(
                "Not following anything yet. Paste an artist or playlist link."
            )

    def _selected(self) -> str | None:
        table = self.query_one("#following-table", DataTable)
        if not table.row_count:
            return None
        try:
            row_key, _ = cast(Any, table).coordinate_to_cell_key((table.cursor_row, 0))
        except Exception:
            return None
        return str(row_key.value) if row_key.value is not None else None

    def follow(self) -> None:
        from ..core.subscriptions import add

        field = self.query_one("#following-url", Input)
        url = field.value.strip()
        if not url:
            self._status("Paste an artist or playlist link first.")
            return
        try:
            sub = add(url, output_dir=str(self._output_dir() or ""), owner="")
        except Exception as exc:
            self._status(f"Could not follow it — {exc}")
            return
        field.value = ""
        self.reload()
        self.app.notify(f"Following {sub.name or sub.url}.")

    def toggle_selected(self) -> None:
        from ..core.subscriptions import get, set_enabled

        sub_id = self._selected()
        sub = get(sub_id) if sub_id else None
        if sub is None:
            self._status("Select a subscription first.")
            return
        try:
            set_enabled(sub.id, not sub.enabled)
        except Exception as exc:
            self._status(f"Could not change it — {exc}")
            return
        self.reload()

    def remove_selected(self) -> None:
        from ..core.subscriptions import get, remove

        sub_id = self._selected()
        sub = get(sub_id) if sub_id else None
        if sub is None:
            self._status("Select a subscription first.")
            return
        try:
            remove(sub.id)
        except Exception as exc:
            self._status(f"Could not remove it — {exc}")
            return
        self.reload()
        self.app.notify(f"Stopped following {sub.name or sub.url}.")
