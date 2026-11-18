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
        "mornye.core.library_scan.iter_audio_files", lambda root: iter(())
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
