"""banner.py — The wordmark at the top, and the key hints at the bottom.

MovieBox opens on a centred block-letter wordmark with a version line under
it, and closes every screen with a row of ``[key] action`` hints. Those two
bands are most of what makes it recognisable before you have read a word of
it, so they are widgets here rather than decoration sprinkled into the app.

Both resize themselves. The wordmark drops from six rows of letterform to
two, and then to a word, as the terminal narrows or shortens — a logo that
eats a quarter of a short screen is a logo in the way. The hint bar drops
hints from the right, keeping the ones you cannot work without.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Static

from .branding import TAGLINE as TAGLINE_ONLY
from .branding import header_line, hint_bar, hint_bar_markup, version

#: Hints, most important first — the tail is what gets dropped when narrow.
DEFAULT_HINTS: tuple[tuple[str, str], ...] = (
    ("Ctrl+R", "Run"),
    ("Ctrl+C", "Stop"),
    ("Ctrl+F", "Finish"),
    ("/", "Search"),
    ("Ctrl+Y", "Copy CLI"),
    ("Ctrl+L", "Log"),
    ("Ctrl+O", "Copy log"),
    ("?", "Help"),
    ("t", "Theme"),
    ("q", "Quit"),
)


class Banner(Container):
    """The wordmark and the line under it, centred, full width."""

    def compose(self) -> ComposeResult:
        yield Static("", id="wordmark")
        yield Static("", id="wordmark-subtitle")

    def on_mount(self) -> None:
        self._redraw()
        # The published version replaces the installed one once PyPI answers;
        # in a thread, so an offline start is never held up by it.
        self.run_worker(self._fetch_version, thread=True, exit_on_error=False)

    def _fetch_version(self) -> None:
        from .branding import fetch_published_version

        if fetch_published_version():
            self.app.call_from_thread(self._redraw)

    def on_resize(self) -> None:
        self._redraw()

    def _redraw(self) -> None:
        # One line (redesign §2): ``MORNYE 2.0.6 ── tagline``. The subtitle
        # row is kept, empty and hidden, for anything that still looks it up.
        width = self.size.width or 80
        self.query_one("#wordmark", Static).update(header_line(width - 2))
        subtitle = self.query_one("#wordmark-subtitle", Static)
        subtitle.update("")
        subtitle.display = False
        self.styles.height = 1
        self.set_class(True, "banner-plain")

    @staticmethod
    def _subtitle_for(art: str, width: int) -> str:
        """The version, hung off the wordmark's right edge.

        MovieBox tucks `v0.1.13` under the last letter rather than centring
        it, which is what stops the two lines reading as one centred block
        with a stray number in it. Reproduced by padding rather than by
        alignment, because the wordmark is centred and only its own width
        says where its right edge fell.
        """
        released = version()
        if not released:
            return TAGLINE_ONLY

        art_width = max(len(line) for line in art.split("\n"))
        if art_width <= 1 or art_width >= width:
            return f"v{released}  ·  {TAGLINE_ONLY}"

        left_margin = (width - art_width) // 2
        label = f"v{released}"
        pad = max(0, left_margin + art_width - len(label))
        return " " * pad + label


class HintBar(Static):
    """``[Ctrl+R] Run   [/] Search   …`` along the bottom."""

    def __init__(self, hints: tuple[tuple[str, str], ...] = DEFAULT_HINTS, **kwargs):
        super().__init__("", **kwargs)
        self._hints = hints

    def on_mount(self) -> None:
        self._redraw()

    def on_resize(self) -> None:
        self._redraw()

    def _redraw(self) -> None:
        width = self.size.width or 80
        hints = list(self._hints)
        # Measured on the plain form and rendered as markup: the colour codes
        # take no columns, so measuring the marked-up string would drop hints
        # that fit perfectly well.
        while hints and len(hint_bar(*hints)) > width - 2:
            hints.pop()
        self.update(hint_bar_markup(*hints))
