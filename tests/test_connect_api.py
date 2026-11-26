"""The page's Connect calls (api_mixins/connect.py): two pages of one account
see and steer each other; another account sees neither."""

from __future__ import annotations

import pytest

from mornye.api_mixins import connect as connect_mixin
from mornye.api_mixins.connect import ConnectMixin
from mornye.core import subsonic
from mornye.core.connect.hub import reset_connect_hubs
from mornye.core.connect.resolver import Catalog


class Page(ConnectMixin):
    def __init__(self, owner: str = "", allowed=lambda path: True) -> None:
        self.owner = owner
        self._window = None
        self.pushed: list[tuple[str, dict]] = []
        self._allowed = allowed

    def _push(self, fn, payload):
        self.pushed.append((fn, payload))

    def _is_media_path(self, path):
        return self._allowed(path)


CATALOG = Catalog(
    subsonic.build_index(
        [
            ("/m/a.flac", 1, {"title": "Alpha", "artist": "Ann", "duration_s": 200}),
            ("/m/b.flac", 1, {"title": "Beta", "artist": "Bob", "duration_s": 100}),
            (
                "/secret/c.flac",
                1,
                {"title": "Gamma", "artist": "Cat", "duration_s": 100},
            ),
        ]
    ),
    "srv",
)


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    reset_connect_hubs()
    monkeypatch.setattr(ConnectMixin, "_connect_catalog", staticmethod(lambda: CATALOG))
    monkeypatch.setattr("mornye.core.link.link_dir", lambda: tmp_path)
    yield
    reset_connect_hubs()


def _page_state(playing=True, index=0, position=1000):
    return {
        "playing": playing,
        "positionMs": position,
        "durationMs": 200000,
        "items": [
            {"path": "/m/a.flac", "title": "Alpha", "artist": "Ann"},
            {"path": "/m/b.flac", "title": "Beta", "artist": "Bob"},
        ],
        "index": index,
        "shuffle": False,
        "repeat": "off",
        "volume": 0.7,
    }


def test_register_report_and_follow_within_one_account():
    mac, tab = Page("ann"), Page("ann")
    # Only the desktop window may call itself a window.
    assert mac.connect_register("mac", "Mac", "window")["device"]["kind"] == "browser"
    tab.connect_register("tab", "Tab")
    assert mac.connect_report_state("mac", _page_state())["status"] == "ok"
    snap = tab.connect_devices()
    assert snap["activeId"] == "mac"
    active = next(d for d in snap["devices"] if d["id"] == "mac")
    assert active["state"]["track"]["songId"] == subsonic._id("so-", "/m/a.flac")
    assert active["state"]["repeat"] == "none"
    assert active["extra"]["paths"] == ["/m/a.flac", "/m/b.flac"]
    # One Api per account pushes to every page of it; here that is mac's.
    states = [p for fn, p in mac.pushed if fn == "app_connect_state"]
    assert states and states[-1]["deviceId"] == "mac"


def test_another_account_sees_nothing_and_cannot_steer():
    ann, bob = Page("ann"), Page("bob")
    ann.connect_register("mac", "Mac")
    ann.connect_report_state("mac", _page_state())
    assert bob.connect_devices()["devices"] == []
    res = bob.connect_command("pause", None, "mac")
    assert res["status"] == "error" and res["code"] == "not_found"
    assert bob.connect_transfer("mac")["status"] == "error"
    assert not [p for fn, p in bob.pushed if fn != "app_connect_devices"]


def test_commands_and_transfers_reach_the_right_page():
    mac, tab = Page("ann"), Page("ann")
    mac.connect_register("mac", "Mac")
    tab.connect_register("tab", "Tab")
    mac.connect_report_state("mac", _page_state(index=1))
    assert tab.connect_command("seek", {"positionMs": 5000}, "mac")["status"] == "ok"
    cmd = [p for fn, p in tab.pushed if fn == "app_connect_command"][-1]
    assert cmd == {"target": "mac", "action": "seek", "args": {"positionMs": 5000}}
    bad = tab.connect_command("remove", {"index": 1}, "mac")
    assert bad["code"] == "invalid"

    handoff = {
        "items": _page_state()["items"],
        "index": 1,
        "positionMs": 3000,
        "playing": True,
    }
    assert mac.connect_transfer("tab", handoff)["activeId"] == "tab"
    commands = [p for fn, p in mac.pushed if fn == "app_connect_command"]
    assert {"target": "mac", "action": "pause", "args": {}} in commands
    play = next(p for p in commands if p["action"] == "play_queue")
    assert play["target"] == "tab" and play["args"]["index"] == 1
    assert [q["title"] for q in play["args"]["queue"]] == ["Alpha", "Beta"]
    assert "path" not in play["args"]["queue"][0]


def test_play_queue_from_a_remote_is_sent_as_trackrefs():
    mac, tab = Page("ann"), Page("ann")
    mac.connect_register("mac", "Mac")
    tab.connect_register("tab", "Tab")
    mac.connect_report_state("mac", _page_state())
    args = {
        "items": [{"path": "/m/b.flac", "title": "Beta"}],
        "index": 0,
        "playing": True,
    }
    assert tab.connect_command("play_queue", args, "mac")["status"] == "ok"
    cmd = [p for fn, p in tab.pushed if fn == "app_connect_command"][-1]
    assert cmd["args"]["queue"][0]["songId"] == subsonic._id("so-", "/m/b.flac")


def test_resolve_only_returns_files_this_account_may_play():
    page = Page("ann", allowed=lambda path: not path.startswith("/secret/"))
    refs = [
        {"title": "Alpha", "artist": "Ann"},
        {"title": "Gamma", "artist": "Cat"},
        {"title": "Nope"},
    ]
    res = page.connect_resolve(refs)
    assert res["items"][0]["path"] == "/m/a.flac"
    assert res["items"][1] is None and res["items"][2] is None
    assert res["missing"] == [1, 2]


def test_bad_input_is_an_error_not_an_exception():
    page = Page("ann")
    assert page.connect_register("", "x")["status"] == "error"
    assert page.connect_report_state("nobody", _page_state())["code"] == "not_found"
    assert page.connect_report_state("x", "nope")["code"] == "invalid"
    assert page.connect_transfer("x", {"items": []})["status"] == "error"
    assert connect_mixin._error(RuntimeError("boom"))["code"] == "unavailable"
