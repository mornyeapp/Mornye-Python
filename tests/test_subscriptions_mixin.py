"""api_mixins/subscriptions.py — what a check turns into for the GUI.

"Check & download" used to hand each new release to fetch_metadata(), which
only loads a tracklist into the page: nothing was downloaded, and each
release replaced the last one on screen. New items are now queued as real
download jobs — a playlist's added tracks as one job, an artist's releases one
whole job each — and scheduled checks do the same with saved settings.
"""

from __future__ import annotations

import pytest

from mornye.api_mixins.subscriptions import SubscriptionsMixin
from mornye.core import subscriptions as subs
from mornye.core.models import TrackMetadata

PLAYLIST_URL = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
ARTIST_URL = "https://open.spotify.com/artist/0000000000000000000000"


def _track(track_id: str) -> TrackMetadata:
    return TrackMetadata(
        id=track_id,
        title=f"Song {track_id}",
        artists="Artist",
        album="Album",
        album_artist="Artist",
    )


class FakeApi(SubscriptionsMixin):
    """The bits of MornyeAPI the mixin leans on, recording instead."""

    def __init__(self, owner: str = "") -> None:
        self.owner = owner
        self.download_dir = "/music"
        self.jobs: list[dict] = []
        self.pushed: list[tuple] = []
        self.logs: list[tuple] = []
        self.release_tracks: dict[str, list] = {}

    def _start_download_job(self, payload: dict) -> None:
        self.jobs.append(payload)

    def _push(self, fn_name: str, *args) -> None:
        self.pushed.append((fn_name, args))

    def log(self, message: str, level: str = "info") -> None:
        self.logs.append((level, message))

    def _release_tracks(self, url: str) -> list:
        return self.release_tracks[url]


@pytest.fixture
def playlist(monkeypatch):
    """The playlist's current tracks; edit the list to add some."""
    tracks = [_track("t1")]

    async def fake_listing(url, *, client=None):
        return "My Playlist", list(tracks)

    monkeypatch.setattr(subs, "list_playlist_tracks_async", fake_listing)
    return tracks


def test_a_scheduled_check_queues_only_the_added_tracks(playlist):
    api = FakeApi()
    sub = subs.add(
        PLAYLIST_URL, interval_minutes=15, download_config={"quality": "LOSSLESS"}
    )

    api._run_scheduled_check(sub)  # first check: the baseline
    assert api.jobs == []

    playlist.append(_track("t2"))
    api._run_scheduled_check(subs.get(sub.id))

    [job] = api.jobs
    assert [t["id"] for t in job["tracks"]] == ["t2"]
    assert [t["id"] for t in job["playlist_tracks"]] == ["t1", "t2"]
    assert job["indices"] == [0]
    assert job["source_url"] == PLAYLIST_URL
    assert job["whole"] is False
    assert job["config"] == {"quality": "LOSSLESS"}
    # So the added tracks land in the playlist's own subfolder (issue #263).
    assert job["playlist_name"] == "My Playlist"
    fn_name, (payload,) = api.pushed[-1]
    assert fn_name == "subscriptionsChecked"
    assert payload["scheduled"] is True and payload["new"] == 1


def test_check_and_download_queues_each_new_release_whole(monkeypatch):
    api = FakeApi()
    sub = subs.add(ARTIST_URL)
    subs.mark_seen(sub.id, [subs.Release(id="old")])  # not a first check
    releases = [subs.Release(id="old"), subs.Release(id="new1", title="New")]

    async def fake_releases(url, groups, *, client=None):
        return "Artist", releases

    monkeypatch.setattr(subs, "list_releases_async", fake_releases)
    api.release_tracks[releases[1].url] = [_track("a"), _track("b")]

    api._check_subscriptions_thread(True, {"quality": "HI_RES_LOSSLESS"})

    [job] = api.jobs
    assert job["source_url"] == "https://open.spotify.com/album/new1"
    assert job["whole"] is True
    assert [t["id"] for t in job["tracks"]] == ["a", "b"]
    assert job["config"] == {"quality": "HI_RES_LOSSLESS"}
    # The settings sent with a manual download are kept for scheduled ones.
    assert subs.get(sub.id).download_config == {"quality": "HI_RES_LOSSLESS"}


def test_check_for_new_does_not_download(playlist):
    api = FakeApi()
    subs.add(PLAYLIST_URL)
    api._check_subscriptions_thread(False)
    playlist.append(_track("t2"))

    api._check_subscriptions_thread(False)

    assert api.jobs == []
    assert api.pushed[-1][1][0]["new"] == 1


def test_subscriptions_are_scoped_to_their_owner():
    alice, bob = FakeApi("alice"), FakeApi("bob")
    created = alice.add_subscription(PLAYLIST_URL, interval_minutes=60)["subscription"]

    assert [s["id"] for s in alice.get_subscriptions()] == [created["id"]]
    assert bob.get_subscriptions() == []
    assert bob.set_subscription_interval(created["id"], 15)["ok"] is False
    assert subs.get(created["id"]).interval_minutes == 60


def test_set_subscription_interval_saves_the_settings():
    api = FakeApi()
    sub = api.add_subscription(PLAYLIST_URL)["subscription"]

    res = api.set_subscription_interval(sub["id"], 30, {"quality": "LOSSLESS"})

    assert res["ok"] is True
    assert res["subscription"]["interval_minutes"] == 30
    assert subs.get(sub["id"]).download_config == {"quality": "LOSSLESS"}
    assert api.set_subscription_interval(sub["id"], 5)["ok"] is False


def test_the_scheduler_dispatches_to_the_owners_instance(playlist):
    host = FakeApi()
    bob = FakeApi("bob")
    sub = subs.add(PLAYLIST_URL, owner="bob", interval_minutes=15)
    subs.mark_seen(sub.id, [subs.Release(id="t1", type="track")])
    playlist.append(_track("t2"))
    instances = {"bob": bob}

    scheduler = host._start_subscription_scheduler(instances.__getitem__)
    try:
        assert host._start_subscription_scheduler() is scheduler  # once
        assert scheduler.tick() == 1
    finally:
        scheduler.stop()

    assert host.jobs == []
    assert [t["id"] for t in bob.jobs[0]["tracks"]] == ["t2"]


def test_only_the_owner_can_reset_remove_or_pause(playlist):
    alice, bob = FakeApi("alice"), FakeApi("bob")
    sub = alice.add_subscription(PLAYLIST_URL)["subscription"]
    subs.mark_seen(sub["id"], [subs.Release(id="t1", type="track")])

    assert bob.reset_subscription(sub["id"]) == {
        "ok": False,
        "error": "No such subscription.",
    }
    assert bob.remove_subscription(sub["id"])["ok"] is False
    assert bob.set_subscription_enabled(sub["id"], False)["ok"] is False

    # Untouched, and still the owner's to change.
    assert subs.count_seen(sub["id"]) == 1
    assert subs.get(sub["id"]).enabled is True
    assert alice.reset_subscription(sub["id"])["ok"] is True
    assert subs.count_seen(sub["id"]) == 0


class FakeQueue:
    def __init__(self) -> None:
        self.submitted: list[tuple[str, dict]] = []
        self.enforced: list[bool] = []

    def submit(
        self, owner: str, payload: dict, *, enforce_pending_limit: bool = True
    ) -> None:
        self.submitted.append((owner, payload))
        self.enforced.append(enforce_pending_limit)


def test_multiuser_scheduled_downloads_go_through_the_shared_queue(playlist):
    api = FakeApi("bob")
    api._subscription_download_queue = queue = FakeQueue()
    sub = subs.add(PLAYLIST_URL, owner="bob", interval_minutes=15)
    api._run_scheduled_check(sub)  # baseline
    playlist.append(_track("t2"))

    api._run_scheduled_check(subs.get(sub.id))

    assert api.jobs == []  # not the direct path
    [(owner, payload)] = queue.submitted
    assert owner == "bob" and payload["owner"] == "bob"
    assert [t["id"] for t in payload["tracks"]] == ["t2"]
    # The scheduler is not a looping client: the pending cap is not its to
    # hit (issue #271).
    assert queue.enforced == [False]


def test_a_queue_refusal_is_reported_not_raised(playlist):
    api = FakeApi("bob")

    class FullQueue:
        def submit(self, owner, payload, **_kwargs):
            raise RuntimeError("quota exceeded")

    api._subscription_download_queue = FullQueue()
    sub = subs.add(PLAYLIST_URL, owner="bob", interval_minutes=15)
    api._run_scheduled_check(sub)
    playlist.append(_track("t2"))

    api._run_scheduled_check(subs.get(sub.id))

    assert any("quota exceeded" in message for _level, message in api.logs)


def test_many_refused_releases_are_summed_up_in_one_error() -> None:
    """Issue #271: a big artist sync logged one error per refused release."""
    api = FakeApi("bob")

    class FullQueue:
        def submit(self, owner, payload, **_kwargs):
            raise RuntimeError("quota exceeded")

    api._subscription_download_queue = FullQueue()
    api.release_tracks = {
        f"https://open.spotify.com/album/a{i}": [_track(f"t{i}")] for i in range(60)
    }
    sub = subs.add(ARTIST_URL, owner="bob")
    result = subs.CheckResult(
        subscription=sub,
        new=[subs.Release(id=f"a{i}", type="album") for i in range(60)],
    )

    api._download_subscription_results([result])

    errors = [message for level, message in api.logs if level == "error"]
    assert len(errors) == 1
    assert errors[0].startswith("60 new release(s) could not be queued")


# ---------------------------------------------------------------------------
# Issue #280: every check keeps a followed playlist's M3U on its current listing
# ---------------------------------------------------------------------------


class M3uRecordingApi(FakeApi):
    def __init__(self) -> None:
        super().__init__()
        self.m3u_writes: list[tuple] = []

    def _write_playlist_m3u(self, config, playlist_name, tracks, *, quiet=False):
        self.m3u_writes.append((playlist_name, [t.id for t in tracks], quiet))
        return {"ok": True}


def test_a_check_with_only_removed_tracks_rewrites_the_m3u(playlist):
    api = M3uRecordingApi()
    playlist.append(_track("t2"))
    sub = subs.add(PLAYLIST_URL, interval_minutes=15)
    api._run_scheduled_check(sub)  # the baseline

    playlist.pop(0)  # t1 left the chart, nothing was added
    api._run_scheduled_check(subs.get(sub.id))

    assert api.jobs == []
    assert api.m3u_writes[-1] == ("My Playlist", ["t2"], True)


def test_check_for_new_only_leaves_the_m3u_alone(playlist):
    api = M3uRecordingApi()
    subs.add(PLAYLIST_URL)
    api._check_subscriptions_thread(False)

    assert api.m3u_writes == []


def test_added_tracks_still_go_through_the_download_job(playlist):
    api = M3uRecordingApi()
    sub = subs.add(PLAYLIST_URL, interval_minutes=15)
    api._run_scheduled_check(sub)
    api.m3u_writes.clear()

    playlist.append(_track("t2"))
    api._run_scheduled_check(subs.get(sub.id))

    # The job rewrites the M3U itself, once the new track is on disk.
    [job] = api.jobs
    assert [t["id"] for t in job["playlist_tracks"]] == ["t1", "t2"]
    assert api.m3u_writes == []


def test_the_m3u_on_disk_drops_a_track_that_left_the_playlist(playlist, tmp_path):
    from mornye.app import MornyeAPI

    folder = tmp_path / "My Playlist"
    folder.mkdir()
    (folder / "Song t1 - Artist.flac").write_bytes(b"audio")
    (folder / "Song t2 - Artist.flac").write_bytes(b"audio")
    m3u = folder / "My Playlist.m3u8"
    m3u.write_text(
        "#EXTM3U\n#EXTINF:-1,Artist - Song t1\nSong t1 - Artist.flac\n"
        "#EXTINF:-1,Artist - Song t2\nSong t2 - Artist.flac\n",
        encoding="utf-8",
    )
    api = FakeApi()
    api.download_dir = str(tmp_path)
    api._write_playlist_m3u = MornyeAPI._write_playlist_m3u.__get__(api)  # type: ignore[attr-defined]
    playlist.append(_track("t2"))
    sub = subs.add(PLAYLIST_URL, interval_minutes=15)
    api._run_scheduled_check(sub)

    playlist.pop(0)
    api._run_scheduled_check(subs.get(sub.id))

    assert m3u.read_text(encoding="utf-8").splitlines() == [
        "#EXTM3U",
        "#EXTINF:-1,Artist - Song t2",
        "Song t2 - Artist.flac",
    ]
