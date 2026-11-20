"""The Player's library is stored, so opening the Player does not rescan it.

It used to read the tags of every file in every folder each time. Now the
first scan stores each file's row; the next one sends the stored rows at once
and reads only what is new or changed on disk.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mornye.app import MornyeAPI
from mornye.core import player_library


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def __call__(self, name: str, payload: dict) -> None:
        self.events.append((name, payload))

    def names(self) -> list[str]:
        return [name for name, _ in self.events]

    def items(self, name: str) -> list[dict]:
        return [row for n, p in self.events if n == name for row in p.get("items", [])]

    def removed(self) -> list[str]:
        return [
            p
            for n, e in self.events
            if n == "app_library_scan_delta"
            for p in e.get("removed", [])
        ]


@pytest.fixture()
def library(tmp_path: Path) -> Path:
    root = tmp_path / "Music"
    root.mkdir()
    for name in ("a.flac", "b.flac", "c.flac"):
        (root / name).write_bytes(b"not really audio")
    return root


@pytest.fixture()
def reads(monkeypatch) -> list[str]:
    """Counts tag reads, which are what made opening the Player slow."""
    seen: list[str] = []

    def fake_read(path: Path) -> dict:
        seen.append(Path(path).name)
        return {"path": str(path), "title": Path(path).stem, "artist": "A", "genre": ""}

    monkeypatch.setattr("mornye.core.library_scan.read_track_row", fake_read)
    return seen


def _scan(api: MornyeAPI, root: Path) -> _Recorder:
    rec = _Recorder()
    api._push = rec
    api._library_scan_id += 1
    api._scan_music_library_thread(api._library_scan_id, [str(root)])
    return rec


def test_the_first_scan_reads_every_file_and_stores_it(library, reads):
    rec = _scan(MornyeAPI(), library)

    assert sorted(reads) == ["a.flac", "b.flac", "c.flac"]
    assert rec.names()[-1] == "app_library_scan_finished"
    assert len(rec.items("app_library_scan_chunk")) == 3
    assert len(player_library.stored_rows([str(library)])) == 3


def test_opening_again_shows_the_stored_library_without_reading_it(library, reads):
    api = MornyeAPI()
    _scan(api, library)
    reads.clear()

    rec = _scan(api, library)

    assert reads == [], "nothing changed on disk, so no tag is read"
    assert len(rec.items("app_library_scan_chunk")) == 3
    finished = [p for n, p in rec.events if n == "app_library_scan_finished"]
    assert finished and finished[0]["stored"] is True
    assert rec.names()[-1] == "app_library_scan_synced"
    assert rec.items("app_library_scan_delta") == [] and rec.removed() == []


def test_only_what_changed_on_disk_is_read_and_sent(library, reads):
    api = MornyeAPI()
    _scan(api, library)
    reads.clear()

    (library / "a.flac").write_bytes(b"edited, a different size now")
    later = os.stat(library / "a.flac").st_mtime_ns + 5_000_000_000
    os.utime(library / "a.flac", ns=(later, later))
    (library / "d.flac").write_bytes(b"new")
    (library / "c.flac").unlink()

    rec = _scan(api, library)

    assert sorted(reads) == ["a.flac", "d.flac"]
    sent = sorted(Path(r["path"]).name for r in rec.items("app_library_scan_delta"))
    assert sent == ["a.flac", "d.flac"]
    assert [Path(p).name for p in rec.removed()] == ["c.flac"]
    stored = player_library.stored_rows([str(library)])
    assert sorted(Path(p).name for p in stored) == ["a.flac", "b.flac", "d.flac"]


def test_a_folder_that_lists_nothing_keeps_its_stored_library(
    library, reads, monkeypatch
):
    """A share that stops answering mid-walk is not a library emptied."""
    api = MornyeAPI()
    _scan(api, library)
    monkeypatch.setattr(
        "mornye.core.library_scan.iter_audio_files", lambda root, **_: iter(())
    )

    rec = _scan(api, library)

    assert rec.removed() == []
    assert len(player_library.stored_rows([str(library)])) == 3


def test_stored_rows_belong_to_the_folders_asked_for(tmp_path, library, reads):
    other = tmp_path / "Other"
    other.mkdir()
    (other / "x.flac").write_bytes(b"x")
    api = MornyeAPI()
    _scan(api, library)
    _scan(api, other)

    names = sorted(Path(p).name for p in player_library.stored_rows([str(library)]))
    assert names == ["a.flac", "b.flac", "c.flac"]


def test_a_row_stored_before_genres_were_read_is_read_once_more(library, monkeypatch):
    """Rows stored without a genre (for the Stats page) are refreshed once."""
    monkeypatch.setattr(
        "mornye.core.library_scan.read_track_row",
        lambda path: {"path": str(path), "title": Path(path).stem},
    )
    api = MornyeAPI()
    _scan(api, library)
    seen: list[str] = []

    def with_genre(path: Path) -> dict:
        seen.append(Path(path).name)
        return {"path": str(path), "title": Path(path).stem, "genre": "Rock"}

    monkeypatch.setattr("mornye.core.library_scan.read_track_row", with_genre)
    _scan(api, library)
    assert sorted(seen) == ["a.flac", "b.flac", "c.flac"]
    seen.clear()
    _scan(api, library)
    assert seen == []


def _progress(rec: _Recorder) -> list[dict]:
    return [p for n, p in rec.events if n == "app_library_scan_progress"]


def test_the_scan_reports_its_progress_for_the_bar(library, reads):
    """A slow folder (an SMB share) shows how far the scan is: first the
    files found while listing (total unknown), then done of total read."""
    api = MornyeAPI()
    api._library_progress_interval = 0  # every step, for the test
    rec = _scan(api, library)

    events = _progress(rec)
    assert events[0] == {
        "scan": api._library_scan_id,
        "phase": "listing",
        "done": 0,
        "total": 0,
    }
    listing = [p["done"] for p in events if p["phase"] == "listing"]
    assert listing == [0, 1, 2, 3]
    reading = [(p["done"], p["total"]) for p in events if p["phase"] == "reading"]
    assert reading[0] == (0, 3) and reading[-1] == (3, 3)
    assert [d for d, _ in reading] == sorted(d for d, _ in reading)
    # Progress comes before the end of the scan, never after it.
    assert rec.names().index("app_library_scan_finished") > max(
        i for i, n in enumerate(rec.names()) if n == "app_library_scan_progress"
    )


def test_progress_events_are_throttled(library, reads):
    """Thousands of files on a share must not mean thousands of pushes."""
    api = MornyeAPI()
    api._library_progress_interval = 3600
    rec = _scan(api, library)

    # Only the forced ones: listing start, reading start, reading end.
    assert [(p["phase"], p["done"], p["total"]) for p in _progress(rec)] == [
        ("listing", 0, 0),
        ("reading", 0, 3),
        ("reading", 3, 3),
    ]


def test_listing_counts_files_across_every_folder(tmp_path, library, reads):
    other = tmp_path / "NAS"
    other.mkdir()
    (other / "x.flac").write_bytes(b"x")
    api = MornyeAPI()
    api._library_progress_interval = 0
    rec = _push_scan(api, [str(library), str(other)])

    listing = [p["done"] for p in _progress(rec) if p["phase"] == "listing"]
    assert listing[-1] == 4


def _push_scan(api: MornyeAPI, roots: list[str]) -> _Recorder:
    rec = _Recorder()
    api._push = rec
    api._library_scan_id += 1
    api._scan_music_library_thread(api._library_scan_id, roots)
    return rec


def test_iter_audio_files_reports_what_it_finds(library, tmp_path):
    from mornye.core.library_scan import iter_audio_files

    (library / "cover.jpg").write_bytes(b"x")
    (library / ".hidden").mkdir()
    (library / ".hidden" / "z.flac").write_bytes(b"x")
    seen: list[int] = []
    files = list(iter_audio_files(library, on_found=seen.append))

    assert [p.name for p in files] == ["a.flac", "b.flac", "c.flac"]
    assert seen == [1, 2, 3]


class _CancelAt(_Recorder):
    """Stops the scan from the Player the moment `when(payload)` holds."""

    def __init__(self, api: MornyeAPI, when) -> None:
        super().__init__()
        self.api, self.when, self.fired = api, when, False

    def __call__(self, name: str, payload: dict) -> None:
        super().__call__(name, payload)
        if (
            name == "app_library_scan_progress"
            and not self.fired
            and self.when(payload)
        ):
            self.fired = True
            self.api.cancel_music_library_scan()


def _scan_with(api: MornyeAPI, root: Path, rec: _Recorder) -> _Recorder:
    api._push = rec
    api._library_scan_id += 1
    api._scan_music_library_thread(api._library_scan_id, [str(root)])
    return rec


def test_stopping_while_a_share_is_still_listed(library, reads):
    """The listing of a slow share stops at the next file it finds."""
    api = MornyeAPI()
    api._library_progress_interval = 0
    rec = _scan_with(
        api,
        library,
        _CancelAt(api, lambda p: p["phase"] == "listing" and p["done"] == 1),
    )

    assert reads == []
    assert "app_library_scan_finished" not in rec.names()
    cancelled = [p for n, p in rec.events if n == "app_library_scan_cancelled"]
    assert cancelled == [{"scan": api._library_scan_id - 1}]
    listing = [p["done"] for p in _progress(rec) if p["phase"] == "listing"]
    assert listing == [0, 1]


def test_stopping_while_reading_keeps_what_was_read(library, reads):
    api = MornyeAPI()
    api._library_progress_interval = 0
    rec = _scan_with(
        api,
        library,
        _CancelAt(api, lambda p: p["phase"] == "reading" and p["done"] == 2),
    )

    assert reads == ["a.flac"], "the file after the stop is not read"
    assert "app_library_scan_finished" not in rec.names()
    assert [Path(p).name for p in player_library.stored_rows([str(library)])] == [
        "a.flac"
    ]
    # The next scan picks up from there and reads only the rest.
    reads.clear()
    _scan(api, library)
    assert sorted(reads) == ["b.flac", "c.flac"]


def test_cancel_is_reachable_from_the_web_ui():
    from mornye import webapp

    src = (Path(webapp.__file__).parent / "frontend" / "web-shim.js").read_text(
        encoding="utf-8"
    )
    assert "cancel_music_library_scan" in Path(webapp.__file__).read_text(
        encoding="utf-8"
    )
    assert "'cancel_music_library_scan'" in src
