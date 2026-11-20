"""Fix Local Files keeps its results, so scanning a folder again is quick.

Every scan used to read every file and look each one up on Spotify (and
fingerprint what its tags could not settle), then send everything in one
go at the end — the same long wait on a network share each time. Now the
results are stored per file: the next scan shows them at once and redoes
only the files that are new, changed or whose result has grown old, sending
them in batches as they are done.
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
from pathlib import Path

import pytest

from mornye.app import MornyeAPI
from mornye.core import local_processor, local_scan_cache
from mornye.core.local_processor import LocalScanEntry, scan_and_match_files_async
from mornye.core.local_scanner import LocalFileInfo


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def __call__(self, name: str, payload) -> None:
        self.events.append((name, payload))

    def of(self, name: str) -> list:
        return [p for n, p in self.events if n == name]

    def files(self, *, cached: bool | None = None) -> list[str]:
        return [
            Path(f["file_path"]).name
            for p in self.of("app_local_scan_chunk")
            if cached is None or p["cached"] is cached
            for f in p["files"]
        ]


@pytest.fixture()
def folder(tmp_path: Path) -> Path:
    root = tmp_path / "Music"
    root.mkdir()
    for name in ("a.flac", "b.flac", "c.flac"):
        (root / name).write_bytes(b"not really audio")
    (root / "cover.jpg").write_bytes(b"x")
    return root


@pytest.fixture()
def work(monkeypatch) -> list[str]:
    """Counts the files read and matched, which is what made a scan slow."""
    seen: list[str] = []

    def fake_scan_file(path) -> LocalFileInfo:
        seen.append(Path(path).name)
        return LocalFileInfo(file_path=str(path), old_title=Path(path).stem)

    async def fake_match(info, limit=5, client=None):
        return []

    async def no_identify(entries, **_):
        return entries

    monkeypatch.setattr(local_processor, "scan_file", fake_scan_file)
    monkeypatch.setattr(local_processor, "match_local_file", fake_match)
    monkeypatch.setattr(local_processor, "_identify_unresolved_async", no_identify)
    monkeypatch.setattr(
        "mornye.core.spotify_metadata.SpotifyMetadataClient", lambda: object()
    )
    return seen


def _scan(api: MornyeAPI, path: Path) -> _Recorder:
    rec = _Recorder()
    api._push = rec
    api._local_progress_interval = 0
    api._local_scan_id += 1
    api._scan_local_thread(api._local_scan_id, str(path))
    return rec


def test_the_first_scan_reads_every_file_and_stores_its_result(folder, work):
    rec = _scan(MornyeAPI(), folder)

    assert sorted(work) == ["a.flac", "b.flac", "c.flac"]
    assert sorted(rec.files(cached=False)) == ["a.flac", "b.flac", "c.flac"]
    done = rec.of("app_local_scan_done")
    assert done and done[0]["checked"] == 3 and done[0]["removed"] == []
    assert len(local_scan_cache.stored(str(folder))) == 3


def test_scanning_again_shows_the_stored_results_without_reading(folder, work):
    api = MornyeAPI()
    _scan(api, folder)
    work.clear()

    rec = _scan(api, folder)

    assert work == [], "nothing changed on disk, so nothing is read or looked up"
    assert sorted(rec.files(cached=True)) == ["a.flac", "b.flac", "c.flac"]
    assert rec.files(cached=False) == []
    # The stored results come first, before the folder is even listed.
    names = [n for n, _ in rec.events]
    assert names[0] == "app_local_scan_chunk"
    assert rec.of("app_local_scan_done")[0]["checked"] == 0


def test_only_new_or_changed_files_are_worked_out_again(folder, work):
    api = MornyeAPI()
    _scan(api, folder)
    work.clear()

    (folder / "a.flac").write_bytes(b"tags written by Apply, a new size")
    later = os.stat(folder / "a.flac").st_mtime_ns + 5_000_000_000
    os.utime(folder / "a.flac", ns=(later, later))
    (folder / "d.flac").write_bytes(b"new")
    (folder / "c.flac").unlink()

    rec = _scan(api, folder)

    assert sorted(work) == ["a.flac", "d.flac"]
    assert sorted(rec.files(cached=False)) == ["a.flac", "d.flac"]
    removed = rec.of("app_local_scan_done")[0]["removed"]
    assert [Path(p).name for p in removed] == ["c.flac"]
    stored = local_scan_cache.stored(str(folder))
    assert sorted(Path(p).name for p in stored) == ["a.flac", "b.flac", "d.flac"]


def test_a_result_grown_old_is_looked_up_again(folder, work, monkeypatch):
    api = MornyeAPI()
    _scan(api, folder)
    work.clear()
    real_time = local_scan_cache.time.time
    monkeypatch.setattr(
        "mornye.api_mixins.local_tagging.time.time",
        lambda: real_time() + local_scan_cache.MAX_AGE_S + 1,
    )

    rec = _scan(api, folder)

    assert sorted(work) == ["a.flac", "b.flac", "c.flac"]
    assert rec.files(cached=True) == [], "an old result is not shown as current"


def test_a_folder_that_lists_nothing_keeps_its_stored_results(
    folder, work, monkeypatch
):
    """A share that stops answering mid-listing is not a folder emptied."""
    api = MornyeAPI()
    _scan(api, folder)
    monkeypatch.setattr(
        "mornye.core.local_scanner.list_audio_files", lambda path, **_: []
    )

    rec = _scan(api, folder)

    assert rec.of("app_local_scan_done")[0]["removed"] == []
    assert len(local_scan_cache.stored(str(folder))) == 3


def test_the_scan_reports_its_progress(folder, work):
    rec = _scan(MornyeAPI(), folder)

    steps = [
        (p["phase"], p["done"], p["total"]) for p in rec.of("app_local_scan_progress")
    ]
    assert steps[0] == ("listing", 0, 0)
    assert ("listing", 3, 0) in steps
    reading = [s for s in steps if s[0] == "reading"]
    assert reading[0] == ("reading", 0, 3) and reading[-1] == ("reading", 3, 3)


def test_a_newer_scan_stops_the_older_one(folder, work):
    api = MornyeAPI()
    rec = _Recorder()

    def push(name, payload):
        rec(name, payload)
        if name == "app_local_scan_progress" and payload["phase"] == "reading":
            api._local_scan_id += 1  # the user started another scan

    api._push = push
    api._local_scan_id += 1
    api._scan_local_thread(api._local_scan_id, str(folder))

    assert work == []
    assert rec.of("app_local_scan_done") == []


def test_results_are_handed_on_batch_by_batch(work):
    batches: list[int] = []

    asyncio.run(
        scan_and_match_files_async(
            [f"/m/{n}.flac" for n in range(5)],
            on_batch=lambda entries: batches.append(len(entries)),
            batch_size=2,
        )
    )

    assert batches == [2, 2, 1]
    assert all(isinstance(e, str) for e in work)


def _png(size: int) -> str:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (size, size), (200, 40, 40)).save(out, format="PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()


def test_covers_are_sent_as_small_thumbnails():
    big = _png(1200)
    thumb = local_scan_cache.cover_thumbnail(big)

    assert thumb.startswith("data:image/jpeg;base64,")
    from PIL import Image

    raw = base64.b64decode(thumb.split(",", 1)[1])
    with Image.open(io.BytesIO(raw)) as img:
        assert max(img.size) <= local_scan_cache.COVER_PX
    assert local_scan_cache.cover_thumbnail("") == ""
    assert (
        local_scan_cache.cover_thumbnail("data:image/png;base64,bm90IGFuIGltYWdl") == ""
    )


def test_serialized_entries_carry_the_thumbnail_not_the_full_cover():
    info = LocalFileInfo(file_path="/m/a.flac", old_cover_base64=_png(800))
    row = MornyeAPI()._serialize_scan_entry(LocalScanEntry(info=info))

    assert len(row["old_cover_base64"]) < len(info.old_cover_base64) / 4


def test_stop_during_the_listing_of_a_share(folder, work):
    api = MornyeAPI()
    rec = _Recorder()

    def push(name, payload):
        rec(name, payload)
        if (
            name == "app_local_scan_progress"
            and payload["phase"] == "listing"
            and payload["done"] == 1
        ):
            api.cancel_local_scan()

    api._push = push
    api._local_progress_interval = 0
    api._local_scan_id += 1
    scan_id = api._local_scan_id
    api._scan_local_thread(scan_id, str(folder))

    assert work == []
    assert rec.of("app_local_scan_cancelled") == [{"scan": scan_id}]
    assert rec.of("app_local_scan_done") == [] and rec.of("app_local_scan_error") == []


def test_stop_keeps_the_batches_already_done(folder, work, monkeypatch):
    """What was worked out before Stop is stored; the rest is done next time."""
    api = MornyeAPI()
    rec = _Recorder()

    def push(name, payload):
        rec(name, payload)
        if name == "app_local_scan_chunk" and not payload["cached"]:
            api.cancel_local_scan()  # right after the first batch

    from mornye.core import local_processor as lp

    real = lp.scan_and_match_files_async

    async def small_batches(files, **kw):
        kw["batch_size"] = 1
        await real(files, **kw)

    monkeypatch.setattr(lp, "scan_and_match_files_async", small_batches)
    api._push = push
    api._local_progress_interval = 0
    api._local_scan_id += 1
    api._scan_local_thread(api._local_scan_id, str(folder))

    assert work == ["a.flac"]
    assert [Path(p).name for p in local_scan_cache.stored(str(folder))] == ["a.flac"]
    work.clear()
    _scan(api, folder)
    assert sorted(work) == ["b.flac", "c.flac"]


def test_a_batch_cut_short_is_not_handed_on(work):
    """Stopped while fingerprinting, a batch would be stored half settled."""
    batches: list[int] = []
    calls = {"n": 0}

    def cancelled() -> bool:
        calls["n"] += 1
        return calls["n"] > 2  # true by the time the batch is matched

    asyncio.run(
        scan_and_match_files_async(
            ["/m/a.flac", "/m/b.flac"],
            on_batch=lambda entries: batches.append(len(entries)),
            cancelled=cancelled,
        )
    )

    assert batches == []


def test_cancel_is_reachable_from_the_web_ui():
    from mornye import webapp

    shim = (Path(webapp.__file__).parent / "frontend" / "web-shim.js").read_text(
        encoding="utf-8"
    )
    assert '"cancel_local_scan"' in Path(webapp.__file__).read_text(encoding="utf-8")
    assert "'cancel_local_scan'" in shim
