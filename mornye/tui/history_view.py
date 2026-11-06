"""history_view.py — what was downloaded, and what was opened.

The TUI half of the GUI's History page. Two lists that already existed:

* **Downloads** — `core/download_log.py`, every track written to disk, with
  search and the same sort orders as the window. Removing a row or clearing
  the list forgets the record; the files stay where they are.
* **Fetches** — the URL history (`core/session_memory.py`). Enter on one puts
  it back in the Download form, as the Session panel's list does.
"""

from __future__ import annotations

import time

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message
from textual.widgets import Button, Input, Label, OptionList, Select
from textual.widgets.option_list import Option

from ..core import download_log

SORTS = [
    ("Newest first", "newest"),
    ("Oldest first", "oldest"),
    ("Title A–Z", "title"),
    ("Artist A–Z", "artist"),
    ("Largest first", "size"),
]


def ago(seconds: float) -> str:
    t = float(seconds or 0)
    if not t:
        return ""
    diff = max(0.0, time.time() - t)
    if diff < 60:
        return "just now"
    if diff < 3600:
        return f"{int(diff // 60)} min ago"
    if diff < 86400:
        return f"{int(diff // 3600)} h ago"
    if diff < 86400 * 7:
        return f"{int(diff // 86400)} d ago"
    return time.strftime("%Y-%m-%d", time.localtime(t))


def size(num: int) -> str:
    n = float(num or 0)
    if not n:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" or n >= 100 else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def describe_download(record: dict) -> str:
    who = " · ".join(x for x in (record.get("artist"), record.get("album")) if x)
    meta = " · ".join(
        x for x in (
            record.get("provider"),
            (record.get("format") or "").upper(),
            size(record.get("bytes") or 0),
            ago(record.get("downloaded_at") or 0),
        ) if x
    )
    mark = "✓" if record.get("success", True) else "✗"
    return (
        f"{mark} {record.get('title') or 'Untitled'}"
        + (f" — {who}" if who else "")
        + (f"   ({meta})" if meta else "")
    )


def describe_fetch(entry: dict) -> str:
    kind = (entry.get("url_type") or "link").capitalize()
    extra = " · ".join(
        x for x in (
            kind,
            f"{entry['track_count']} tracks" if entry.get("track_count") else "",
            ago(entry.get("at") or 0),
        ) if x
    )
    who = f" — {entry['artist']}" if entry.get("artist") else ""
    return f"{entry.get('label') or entry.get('url') or '—'}{who}   ({extra})"


class HistoryPanel(VerticalScroll):
    """Downloads and fetched links, searchable."""

    BORDER_TITLE = "History"

    class UrlChosen(Message):
        """A fetched link was picked; it should go back into the form."""

        def __init__(self, url: str) -> None:
            super().__init__()
            self.url = url

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._rows: list[dict] = []
        self._confirm_clear = False

    def compose(self) -> ComposeResult:
        yield Label(
            "What was downloaded, and the links you opened. Enter on a link opens it again.",
            classes="panel-intro",
        )
        yield Horizontal(
            Select([("Downloads", "downloads"), ("Fetches", "fetches")],
                   value="downloads", allow_blank=False, id="history-tab"),
            Input(placeholder="Search…", id="history-search"),
            Select(SORTS, value="newest", allow_blank=False, id="history-sort"),
            classes="setting-row history-filters",
        )
        yield Label("", id="history-summary")
        yield OptionList(id="history-list")
        yield Horizontal(
            Button("Open", id="history-open", variant="primary"),
            Button("Remove", id="history-remove"),
            Button("Refresh", id="history-refresh"),
            Button("Clear all", id="history-clear", variant="error"),
            classes="setting-row",
        )

    def on_mount(self) -> None:
        self._refresh()

    def _tab(self) -> str:
        return str(self.query_one("#history-tab", Select).value or "downloads")

    async def reload(self) -> None:
        tab = self._tab()
        query = self.query_one("#history-search", Input).value.strip()
        self.query_one("#history-sort", Select).display = tab == "downloads"
        self.query_one("#history-open", Button).display = tab == "fetches"
        option_list = self.query_one("#history-list", OptionList)
        option_list.clear_options()

        if tab == "downloads":
            sort = str(self.query_one("#history-sort", Select).value or "newest")
            records = [r.to_dict() for r in download_log.search(query, sort=sort, limit=500)]
            self._rows = records
            totals = download_log.totals()
            summary = f"{totals['tracks']} track(s) downloaded · {size(totals['bytes']) or '0 B'}"
            options = [Option(describe_download(r)) for r in records]
            empty = "No downloads match." if query else "No download history yet."
        else:
            try:
                from ..core.session_memory import get_url_history_async

                entries = await get_url_history_async()
            except Exception:
                entries = []
            ql = query.lower()
            entries = [
                e for e in entries
                if not ql or ql in f"{e.get('label', '')} {e.get('artist', '')} {e.get('url', '')}".lower()
            ]
            self._rows = entries
            summary = f"{len(entries)} link(s)"
            options = [Option(describe_fetch(e)) for e in entries]
            empty = "No fetched links match." if query else "Nothing fetched yet."

        self.query_one("#history-summary", Label).update(summary)
        if options:
            option_list.add_options(options)
        else:
            option_list.add_option(Option(empty, disabled=True))

    def _current(self) -> dict | None:
        index = self.query_one("#history-list", OptionList).highlighted
        if index is None or not (0 <= index < len(self._rows)):
            return None
        return self._rows[index]

    def _refresh(self) -> None:
        # The method, not a coroutine object: a worker cancelled before it
        # starts would otherwise leave an un-awaited coroutine behind.
        self.run_worker(self.reload, exclusive=True, group="history")

    # ------------------------------------------------------------------

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id in ("history-tab", "history-sort"):
            self._refresh()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "history-search":
            self._refresh()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        row = self._current()
        if row and self._tab() == "fetches" and row.get("url"):
            self.post_message(self.UrlChosen(row["url"]))

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button = event.button.id
        if button != "history-clear":
            self._confirm_clear = False
            self.query_one("#history-clear", Button).label = "Clear all"
        row = self._current()
        if button == "history-open" and row and row.get("url"):
            self.post_message(self.UrlChosen(row["url"]))
            return
        if button == "history-remove" and row:
            if self._tab() == "downloads":
                download_log.delete([int(row["id"])])
            else:
                from ..core.session_memory import remove_url_from_history_async

                await remove_url_from_history_async(row["url"])
        elif button == "history-clear":
            if not self._confirm_clear:
                self._confirm_clear = True
                event.button.label = "Press again to clear"
                return
            self._confirm_clear = False
            event.button.label = "Clear all"
            if self._tab() == "downloads":
                download_log.clear()
            else:
                from ..core.session_memory import clear_url_history_async

                await clear_url_history_async()
        self._refresh()
