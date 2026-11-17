"""The Player's library is made of several folders, network shares included."""

from __future__ import annotations

from pathlib import Path

import pytest

from mornye.api_mixins import library_tools as lt
from mornye.app import MornyeAPI


@pytest.fixture
def api(tmp_path, monkeypatch):
    api = MornyeAPI()
    api.download_dir = str(tmp_path / "downloads")
    Path(api.download_dir).mkdir()
    monkeypatch.setattr(lt, "_network_roots_cached", lambda: [])
    started: list = []
    monkeypatch.setattr(
        lt.threading,
        "Thread",
        lambda target, args, daemon: type(
            "T", (), {"start": lambda self: started.append(args)}
        )(),
    )
    api._started = started
    return api


def test_several_folders_are_scanned_as_one_library(api, tmp_path):
    a = Path(api.download_dir, "A")
    b = Path(api.download_dir, "B")
    a.mkdir()
    b.mkdir()
    res = api.scan_music_library([str(a), str(b), str(a)])
    assert res["status"] == "started"
    assert res["folders"] == [str(a), str(b)]
    assert api._started[0][1] == [str(a), str(b)]


def test_one_folder_as_before(api):
    res = api.scan_music_library(api.download_dir)
    assert res["status"] == "started"
    assert res["folders"] == [api.download_dir]


def test_a_share_address_is_mapped_to_its_mount(api, tmp_path, monkeypatch):
    mounted = tmp_path / "Volumes" / "Music"
    (mounted / "Albums").mkdir(parents=True)
    import mornye.core.network_paths as np_

    monkeypatch.setattr(
        np_,
        "current_mounts",
        lambda: [
            np_.Mount(
                host="nas", share="Music", mount_point=str(mounted), fs_type="smbfs"
            )
        ],
    )
    monkeypatch.setattr(lt, "_network_roots_cached", lambda: [mounted.resolve()])
    res = api.scan_music_library("smb://nas/Music/Albums")
    assert res["status"] == "started", res
    assert res["folders"] == [str(mounted / "Albums")]


def test_a_share_that_is_not_mounted_says_how_to_mount_it(api, monkeypatch):
    import mornye.core.network_paths as np_

    # macOS and Linux need the share mounted; Windows opens it as UNC.
    monkeypatch.setattr(np_.sys, "platform", "darwin")
    monkeypatch.setattr(np_, "current_mounts", lambda: [])
    res = api.scan_music_library(["smb://nas/Music"])
    assert res["status"] == "error"
    assert "not mounted" in res["error"]


def test_a_unc_share_is_approved_on_windows(api, monkeypatch):
    import mornye.core.network_paths as np_

    monkeypatch.setattr(np_.sys, "platform", "win32")
    assert api._is_approved_path("\\\\nas\\Music\\song.flac")


def test_a_folder_outside_the_approved_roots_is_refused(api, tmp_path, monkeypatch):
    monkeypatch.setattr(lt.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    res = api.scan_music_library([str(outside)])
    assert res["status"] == "error"
    assert "outside approved" in res["error"]


def test_a_mounted_share_is_an_approved_root(api, tmp_path, monkeypatch):
    share = tmp_path / "share"
    share.mkdir()
    monkeypatch.setattr(lt.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(lt, "_network_roots_cached", lambda: [share.resolve()])
    assert api._is_approved_path(str(share / "song.flac"))
