"""Queue page (staged queue) and History page backends."""

from __future__ import annotations

from pathlib import Path

import pytest

from mornye.core import download_log, staged_queue


@pytest.fixture(autouse=True)
def _queue_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "staged-queue.json"
    monkeypatch.setattr(staged_queue, "_file", lambda: path)
    return path


def _track(title: str) -> dict:
    return {"title": title, "artists": "Someone", "album": "Record"}


def test_items_are_kept_with_their_tracks_until_removed() -> None:
    item = staged_queue.add(
        kind="album",
        title="Record",
        subtitle="Someone",
        tracks=[_track("One"), _track("Two")],
    )
    assert item["status"] == "pending" and item["track_count"] == 2
    stored = staged_queue.get(item["id"])
    assert [t["title"] for t in stored["tracks"]] == ["One", "Two"]
    assert "tracks" not in staged_queue.public(stored)
    assert staged_queue.public(stored)["preview"] == ["One", "Two"]
    assert staged_queue.remove([item["id"]]) == 1
    assert staged_queue.list_items() == []


def test_unknown_kind_is_guessed_and_empty_is_refused() -> None:
    assert (
        staged_queue.add(kind="?", title="", tracks=[_track("Solo")])["kind"] == "track"
    )
    with pytest.raises(ValueError):
        staged_queue.add(kind="album", title="x", tracks=[])


def test_running_items_survive_clear_and_recover_after_a_crash() -> None:
    a = staged_queue.add(kind="track", title="A", tracks=[_track("A")])
    b = staged_queue.add(kind="track", title="B", tracks=[_track("B")])
    c = staged_queue.add(kind="track", title="C", tracks=[_track("C")])
    staged_queue.set_status(a["id"], "running")
    staged_queue.set_status(b["id"], "done")
    assert staged_queue.clear("finished") == 1  # only B
    assert {i["id"] for i in staged_queue.list_items()} == {a["id"], c["id"]}
    assert staged_queue.clear("all") == 1  # C; A is mid-download
    staged_queue.recover_interrupted()
    assert staged_queue.get(a["id"])["status"] == "pending"


def _api():
    from mornye.app import MornyeAPI

    api = MornyeAPI.__new__(MornyeAPI)
    api.owner = ""
    api._download_queue = None
    api.current_url = "https://open.spotify.com/album/x"
    api.log = lambda *a, **k: None
    api._push = lambda *a, **k: None
    api._staged_runner = None
    return api


def test_start_runs_each_pending_item_as_a_download_job(monkeypatch) -> None:
    api = _api()
    ran: list[dict] = []
    api.run_download_job = lambda payload: ran.append(payload) or {
        "tracks": len(payload["tracks"])
    }
    first = staged_queue.add(
        kind="album", title="One", tracks=[_track("a"), _track("b")]
    )
    second = staged_queue.add(kind="track", title="Two", tracks=[_track("c")])

    res = api.start_staged_items([], {"quality": "LOSSLESS"})
    assert res == {"ok": True, "started": 2}
    api._staged_runner.join(timeout=5)

    assert [len(p["tracks"]) for p in ran] == [2, 1]
    assert ran[0]["indices"] == [0, 1] and ran[0]["whole"] is True
    assert ran[0]["config"] == {"quality": "LOSSLESS"}
    assert staged_queue.get(first["id"])["status"] == "done"
    assert staged_queue.get(second["id"])["status"] == "done"


def test_a_failing_item_is_marked_and_the_rest_still_run() -> None:
    api = _api()

    def job(payload):
        if payload["tracks"][0]["title"] == "bad":
            raise RuntimeError("no provider")
        return {}

    api.run_download_job = job
    bad = staged_queue.add(kind="track", title="Bad", tracks=[_track("bad")])
    good = staged_queue.add(kind="track", title="Good", tracks=[_track("good")])
    api.start_staged_items()
    api._staged_runner.join(timeout=5)
    assert staged_queue.get(bad["id"])["status"] == "failed"
    assert staged_queue.get(bad["id"])["error"] == "no provider"
    assert staged_queue.get(good["id"])["status"] == "done"
    api.retry_staged_items([bad["id"]])
    assert staged_queue.get(bad["id"])["status"] == "pending"


def test_nothing_pending_is_reported() -> None:
    assert _api().start_staged_items() == {
        "ok": False,
        "error": "Nothing waiting to start.",
    }


def test_download_history_search_sort_delete_clear() -> None:
    download_log.record(
        title="Night Drive",
        artist="Zed",
        album="Dark",
        file_path="/a.flac",
        size_bytes=10,
    )
    download_log.record(
        title="Morning", artist="Abe", album="Light", file_path="/b.flac", size_bytes=20
    )
    api = _api()

    res = api.get_download_history("night")
    assert [r["title"] for r in res["items"]] == ["Night Drive"]
    by_artist = api.get_download_history("", "artist")["items"]
    assert [r["artist"] for r in by_artist] == ["Abe", "Zed"]
    assert [r["title"] for r in api.get_download_history("", "size")["items"]] == [
        "Morning",
        "Night Drive",
    ]
    # LIKE wildcards in the query are literal.
    assert api.get_download_history("%")["items"] == []

    first_id = by_artist[0]["id"]
    assert api.remove_download_history([first_id]) == {"ok": True, "removed": 1}
    assert [r["title"] for r in api.get_download_history()["items"]] == ["Night Drive"]
    assert api.clear_download_history()["removed"] == 1
    assert api.get_download_history()["items"] == []
