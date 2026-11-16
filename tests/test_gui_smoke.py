"""Browser smoke pass over the web GUI (M4, docs/redesign-plan.md).

Opt-in (``MORNYE_TEST_BROWSER=1``): needs the ``visual`` extra (Playwright)
and Google Chrome, and starts ``mornye --web`` on a throw-away HOME through
tests/visual/capture_gui.py's server helper. Every view and Settings tab is
opened with a real click, under the page's real Content-Security-Policy;
the pass fails on any page error, console error or CSP violation.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("MORNYE_TEST_BROWSER") != "1"
    or importlib.util.find_spec("playwright") is None,
    reason="browser smoke pass is opt-in: MORNYE_TEST_BROWSER=1 and Playwright",
)

VIEWS = (
    "explore",
    "player",
    "queue",
    "history",
    "local",
    "following",
    "stats",
    "logs",
    "settings",
    "home",
)
TABS = ("General", "Sources", "Download", "Files", "Metadata", "Status", "Extensions")


def test_every_view_opens_without_errors():
    sys.path.insert(0, str(Path(__file__).with_name("visual")))
    from capture_gui import _server  # type: ignore[import-not-found]
    from playwright.sync_api import sync_playwright

    problems: list[str] = []
    with (
        tempfile.TemporaryDirectory() as tmp,
        _server(Path(tmp)) as url,
        sync_playwright() as pw,
    ):
        browser = pw.chromium.launch(channel="chrome", headless=True)
        for width, height in ((1280, 800), (375, 812)):
            page = browser.new_page(viewport={"width": width, "height": height})
            page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
            page.on(
                "console",
                lambda m: m.type == "error" and problems.append(f"console: {m.text}"),
            )
            page.add_init_script(
                "window.__csp = []; document.addEventListener('securitypolicyviolation',"
                " e => window.__csp.push(e.violatedDirective + ' ' + e.blockedURI))"
            )
            page.goto(url, wait_until="networkidle")
            page.wait_for_function("typeof switchView === 'function'")
            for view in VIEWS:
                page.evaluate(f"switchView('{view}')")
                page.wait_for_timeout(150)
                assert page.evaluate(
                    f"$('view-{view}').classList.contains('active')"
                ), view
            page.evaluate("switchView('settings')")
            for tab in TABS:
                page.click(f".stab:has-text('{tab}')")
                page.wait_for_timeout(100)
            problems.extend(f"csp: {v}" for v in page.evaluate("window.__csp"))
            page.close()
        browser.close()
    assert not problems, problems
