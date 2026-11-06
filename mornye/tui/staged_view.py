"""staged_view.py — the Queue: things to download later, started on demand.

The same list the GUI's Queue page shows (core/staged_queue.py), so an album
queued in the window is here too and the other way round. Items arrive from
the Tracks panel (``l`` queues the selection for later) or from the GUI;
Start runs the highlighted one, Start all runs every waiting one, one after
another, through the same runner a Ctrl+R download uses.

Kept in step with the file by a light poll: the GUI may change it at any
moment, and a stale list here would offer to start something already done.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message
from textual.widgets import Button, Input, Label, OptionList, Select
from textual.widgets.option_list import Option

from ..core import staged_queue

KIND_LABELS = {"track": "Track", "album": "Album", "playlist": "Playlist", "artist": "Artist"}
STATUS_LABELS = {
    "pending": "WAITING",
    "running": "↓ RUNNING",
    "done": "✓ DONE",
    "failed": "✗ FAILED",
}


def describe(item: dict) -> str:
    """One row: status, title, then what and how many."""
    status = STATUS_LABELS.get(item.get("status", ""), "?")
    kind = KIND_LABELS.get(item.get("kind", ""), "Item")
    count = int(item.get("track_count") or 0)
    parts = [f"{status:<10}", item.get("title") or "Untitled"]
    if item.get("subtitle"):
        parts.append(f"— {item['subtitle']}")
    tail = f"{kind} · {count} track{'s' if count != 1 else ''}"
    if item.get("error"):
        tail += f" · {item['error']}"
    return "  ".join(parts) + f"   ({tail})"


class StagedQueuePanel(VerticalScroll):
    """The staged queue, with the filters the GUI page has."""

    BORDER_TITLE = "Queue"

    class StartRequested(Message):
        """Start these item ids (empty = every waiting item)."""

        def __init__(self, ids: list[str]) -> None:
            super().__init__()
            self.ids = ids

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._shown: list[dict] = []
        self._signature = ""
        self._confirm_clear = False

    def compose(self) -> ComposeResult:
        yield Label(
            "Queued for later — from the Tracks panel (l) or the desktop window. "
            "Enter starts the highlighted item.",
            classes="panel-intro",
        )
        yield Horizontal(
            Select(
                [("All types", ""), *[(f"{v}s", k) for k, v in KIND_LABELS.items()]],
                value="", allow_blank=False, id="staged-kind",
            ),
            Select(
                [("All statuses", ""), ("Waiting", "pending"), ("Running", "running"),
                 ("Done", "done"), ("Failed", "failed")],
                value="", allow_blank=False, id="staged-status",
            ),
            Input(placeholder="Search the queue…", id="staged-search"),
            classes="setting-row staged-filters",
        )
        yield Label("", id="staged-summary")
        yield OptionList(id="staged-list")
        yield Horizontal(
            Button("Start", id="staged-start", variant="primary"),
            Button("Start all", id="staged-start-all"),
            Button("Remove", id="staged-remove"),
            Button("Clear finished", id="staged-clear-finished"),
            Button("Clear all", id="staged-clear-all", variant="error"),
            classes="setting-row",
        )

    def on_mount(self) -> None:
        self.reload(force=True)
        self.set_interval(2.0, self.reload)

    # ------------------------------------------------------------------

    def _filters(self) -> tuple[str, str, str]:
        kind = self.query_one("#staged-kind", Select).value or ""
        status = self.query_one("#staged-status", Select).value or ""
        query = self.query_one("#staged-search", Input).value.strip().lower()
        return str(kind), str(status), query

    def reload(self, force: bool = False) -> None:
        items = staged_queue.list_items()
        kind, status, query = self._filters()
        shown = [
            i for i in items
            if (not kind or i.get("kind") == kind)
            and (not status or i.get("status") == status)
            and (not query or query in f"{i.get('title', '')} {i.get('subtitle', '')}".lower())
        ]
        signature = repr([(i["id"], i.get("status"), i.get("error")) for i in shown]) + repr(
            (kind, status, query, len(items))
        )
        if signature == self._signature and not force:
            return
        self._signature = signature
        self._shown = shown

        option_list = self.query_one("#staged-list", OptionList)
        highlighted = option_list.highlighted
        option_list.clear_options()
        if shown:
            option_list.add_options(Option(describe(i), id=i["id"]) for i in shown)
            if highlighted is not None:
                option_list.highlighted = min(highlighted, len(shown) - 1)
        else:
            option_list.add_option(
                Option(
                    "Nothing matches these filters." if items
                    else "The queue is empty. Press l on the Tracks panel to add the selection.",
                    disabled=True,
                )
            )

        waiting = sum(1 for i in items if i.get("status") == "pending")
        running = sum(1 for i in items if i.get("status") == "running")
        counts = {k: sum(1 for i in items if i.get("kind") == k) for k in KIND_LABELS}
        by_kind = " · ".join(
            f"{counts[k]} {v.lower()}{'s' if counts[k] != 1 else ''}" for k, v in KIND_LABELS.items()
        )
        self.query_one("#staged-summary", Label).update(
            f"{len(items)} item(s) — {waiting} waiting, {running} running  ·  {by_kind}"
        )

    def _highlighted_item(self) -> dict | None:
        index = self.query_one("#staged-list", OptionList).highlighted
        if index is None or not (0 <= index < len(self._shown)):
            return None
        return self._shown[index]

    # ------------------------------------------------------------------

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id in ("staged-kind", "staged-status"):
            self.reload(force=True)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "staged-search":
            self.reload(force=True)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        item = self._highlighted_item()
        if item and item.get("status") == "pending":
            self.post_message(self.StartRequested([item["id"]]))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button = event.button.id
        if button != "staged-clear-all":
            self._confirm_clear = False
            self.query_one("#staged-clear-all", Button).label = "Clear all"
        item = self._highlighted_item()
        if button == "staged-start":
            if item and item.get("status") in ("failed", "done"):
                staged_queue.set_status(item["id"], "pending")
            if item:
                self.post_message(self.StartRequested([item["id"]]))
        elif button == "staged-start-all":
            self.post_message(self.StartRequested([]))
        elif button == "staged-remove" and item:
            staged_queue.remove([item["id"]])
        elif button == "staged-clear-finished":
            staged_queue.clear("finished")
        elif button == "staged-clear-all":
            if not self._confirm_clear:
                self._confirm_clear = True
                event.button.label = "Press again to clear"
                return
            self._confirm_clear = False
            event.button.label = "Clear all"
            staged_queue.clear("all")
        self.reload(force=True)
