"""A library path may name a network share (smb://, UNC) once it is mounted."""

from __future__ import annotations

from pathlib import Path

import pytest

from mornye.core import network_paths as np_
from mornye.core.network_paths import Mount, parse_share_ref, resolve_library_path

MAC_MOUNT = """\
/dev/disk3s1s1 on / (apfs, sealed, local, read-only, journaled)
//GUEST:@nas.local/Music on /Volumes/Music (smbfs, nodev, nosuid, mounted by me)
nas.local:/export/media on /Volumes/media (nfs, asynchronous)
"""

PROC_MOUNTS = """\
/dev/sda1 / ext4 rw 0 0
//nas/Music\\040Library /mnt/music cifs rw,vers=3.0 0 0
"""


def test_share_references_are_recognised() -> None:
    assert parse_share_ref("smb://user@NAS.local/Music/A%20B") == np_.ShareRef(
        "nas.local", "Music", ("A B",)
    )
    assert parse_share_ref(r"\\nas\Music\Albums") == np_.ShareRef(
        "nas", "Music", ("Albums",)
    )
    assert parse_share_ref("//nas/Music") == np_.ShareRef("nas", "Music", ())
    assert parse_share_ref("/Users/me/Music") is None
    assert parse_share_ref("smb://nas") is None  # no share named


def test_mount_tables_list_only_network_shares() -> None:
    mac = np_.parse_mount_output(MAC_MOUNT)
    assert [(m.host, m.share, m.mount_point) for m in mac] == [
        ("nas.local", "Music", "/Volumes/Music"),
        ("nas.local", "media", "/Volumes/media"),
    ]
    linux = np_.parse_proc_mounts(PROC_MOUNTS)
    assert [(m.host, m.share, m.mount_point) for m in linux] == [
        ("nas", "Music Library", "/mnt/music")
    ]


def test_smb_url_maps_to_the_mount_point() -> None:
    mounts = np_.parse_mount_output(MAC_MOUNT)
    path, error = resolve_library_path(
        "smb://nas.local/Music/Albums/X", mounts=mounts, platform="darwin"
    )
    assert error == ""
    assert path == str(Path("/Volumes/Music/Albums/X"))
    # Short host name and a different case still find it.
    path, error = resolve_library_path(
        "smb://NAS/music", mounts=mounts, platform="darwin"
    )
    assert (path, error) == (str(Path("/Volumes/Music")), "")


def test_unmounted_share_explains_how_to_connect() -> None:
    path, error = resolve_library_path(
        "smb://other/Share", mounts=[], platform="darwin"
    )
    assert path == ""
    assert "not mounted" in error and "'Share'" in error


def test_windows_turns_a_share_into_unc() -> None:
    path, error = resolve_library_path(
        "smb://nas/Music/Albums", mounts=[], platform="win32"
    )
    assert (path, error) == (r"\\nas\Music\Albums", "")


def test_plain_paths_pass_through(tmp_path: Path) -> None:
    path, error = resolve_library_path(f"'{tmp_path}'", mounts=[])
    assert (path, error) == (str(tmp_path), "")
    assert resolve_library_path("", mounts=[]) == ("", "No path given")


def test_scan_local_accepts_a_mounted_share(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Outside home and the download folder, but mounted: allowed."""
    from mornye.app import MornyeAPI

    share_root = tmp_path / "Volumes" / "Music"
    (share_root / "Albums").mkdir(parents=True)
    mounts = [Mount("nas.local", "Music", str(share_root), "smbfs")]
    monkeypatch.setattr(np_, "current_mounts", lambda: mounts)

    api = MornyeAPI.__new__(MornyeAPI)
    api.download_dir = str(tmp_path / "downloads")
    started = []
    monkeypatch.setattr(
        api, "_scan_local_thread", lambda p: started.append(p), raising=False
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))

    result = api.scan_local("smb://nas.local/Music/Albums")
    assert result == {"status": "started"}
