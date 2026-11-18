"""Issue #284: failed tracks retried in a loop that a restart did not stop.

Each click on Retry queued a full copy of the failure list, and in --web mode
the queue is persisted: the copies outlived restarts and kept retrying the
same tracks. Clearing the list did not help either — the queued copies still
ran, and every track they failed went back on the list.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mornye.app import MornyeAPI
from mornye.core import failed_tracks
from mornye.core.job_queue import JobStatus
from mornye.core.models import DownloadResult, TrackMetadata

PLAYLIST = "https://open.spotify.com/playlist/p"


def _track(n: int) -> TrackMetadata:
    return TrackMetadata(
        id=f"sp{n}",
        title=f"Song {n}",
        artists="Artist",
        album="Album",
        album_artist="Artist",
        isrc=f"ITAAA000000{n}",
        external_url=f"https://open.spotify.com/track/sp{n}",
    )


class _Queue:
    """Stands in for the persisted JobQueue: submit, and list what is held."""

    def __init__(self) -> None:
        self.jobs: list[SimpleNamespace] = []

    def submit(self, owner: str, payload: dict) -> None:
        self.jobs.append(
            SimpleNamespace(owner=owner, payload=payload, status=JobStatus.QUEUED)
        )

    def list_for(self, owner: str) -> list[SimpleNamespace]:
        return [j for j in self.jobs if j.owner == owner]


def _fail(*numbers: int, name: str = "Mix") -> None:
    hook = failed_tracks.hook(source_url=PLAYLIST, playlist_name=name)
    for n in numbers:
        hook(DownloadResult.fail("t", "e"), _track(n))


@pytest.fixture()
def api() -> MornyeAPI:
    api = MornyeAPI()
    api._download_queue = _Queue()
    return api


def test_a_second_retry_does_not_queue_the_same_tracks_again(api):
    _fail(1, 2)

    assert api.retry_failed_tracks({})["queued"] == 2
    second = api.retry_failed_tracks({})

    assert second == {"queued": 0, "already_queued": 2}
    assert len(api._download_queue.jobs) == 1


def test_a_finished_retry_no_longer_blocks_the_next_one(api):
    _fail(1)
    api.retry_failed_tracks({})
    api._download_queue.jobs[0].status = JobStatus.DONE

    assert api.retry_failed_tracks({})["queued"] == 1


def test_only_the_tracks_not_already_held_are_queued(api):
    _fail(1)
    api.retry_failed_tracks({})
    _fail(2)

    assert api.retry_failed_tracks({})["queued"] == 1
    assert [t["id"] for t in api._download_queue.jobs[1].payload["tracks"]] == ["sp2"]


def test_a_queued_retry_skips_what_was_cleared_meanwhile(api, monkeypatch):
    _fail(1, 2)
    api.retry_failed_tracks({})
    [job] = api._download_queue.jobs
    [first_key] = [k for k in job.payload["retry_keys"] if k.startswith("id:sp1")]
    failed_tracks.clear("", [first_key])

    ran: list[dict] = []
    monkeypatch.setattr(api, "_download_task", lambda idx, cfg, **kw: ran.append(kw))
    api.run_download_job(job.payload)

    [batch] = ran
    assert [t.id for t in batch["tracks_by_index"].values()] == ["sp2"]
    assert batch["is_retry"] is True


def test_a_queued_retry_of_a_cleared_list_runs_nothing(api, monkeypatch):
    _fail(1)
    api.retry_failed_tracks({})
    failed_tracks.clear("")
    monkeypatch.setattr(
        api, "_download_task", lambda *a, **k: pytest.fail("nothing to retry")
    )

    assert api.run_download_job(api._download_queue.jobs[0].payload) == {"tracks": 0}


def test_a_retry_failing_again_does_not_bring_a_cleared_track_back():
    _fail(1)
    failed_tracks.clear("")

    retry_hook = failed_tracks.hook(source_url=PLAYLIST, only_existing=True)
    retry_hook(DownloadResult.fail("t", "again"), _track(1))

    assert failed_tracks.list_for() == []


def test_a_retry_failing_again_still_counts_the_attempt():
    _fail(1)
    retry_hook = failed_tracks.hook(source_url=PLAYLIST, only_existing=True)
    retry_hook(DownloadResult.fail("t", "again"), _track(1))

    [item] = failed_tracks.list_for()
    assert item["attempts"] == 2
    assert item["error"] == "again"
    [(_, name, _)] = failed_tracks.retry_groups()
    assert name == "Mix", "the playlist name is kept"


def test_without_a_queue_a_running_retry_also_blocks_a_second_one(monkeypatch):
    _fail(1)
    api = MornyeAPI()
    started: list[dict] = []
    monkeypatch.setattr(api, "_start_download_job", started.append)

    api.retry_failed_tracks({})
    assert api.retry_failed_tracks({})["queued"] == 0

    monkeypatch.setattr(api, "_download_task", lambda *a, **k: None)
    api.run_download_job(started[0])
    assert api.retry_failed_tracks({})["queued"] == 1, "released once it ran"
