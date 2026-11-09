"""Extension `storage` survives restarts, encrypted, across a runtime pool."""

from __future__ import annotations

import os
import shutil
import time

import pytest

from mornye.extensions.runtime import JSRuntime
from mornye.extensions.storage import ExtensionStorage

PROBE = """
registerExtension({
  initialize: function () {},
  run: function (source) { return eval(source); }
});
"""


def test_round_trip_is_encrypted(tmp_path):
    st = ExtensionStorage("tidal-web", base_dir=tmp_path)
    assert st.load() == {}
    st.apply("set", "token", "s3cret-value")
    st.apply("set", "n", {"a": [1, 2]})

    raw = st.path.read_bytes()
    assert b"s3cret-value" not in raw
    assert ExtensionStorage("tidal-web", base_dir=tmp_path).load() == {
        "token": "s3cret-value",
        "n": {"a": [1, 2]},
    }

    st.apply("delete", "token")
    assert st.load() == {"n": {"a": [1, 2]}}
    st.apply("delete", "n")
    assert not st.path.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_files_are_private(tmp_path):
    st = ExtensionStorage("x", base_dir=tmp_path / "s")
    st.apply("set", "k", "v")
    assert st.path.stat().st_mode & 0o077 == 0
    assert (tmp_path / "s" / ".master.key").stat().st_mode & 0o077 == 0


def test_unreadable_file_starts_empty(tmp_path):
    st = ExtensionStorage("x", base_dir=tmp_path)
    st.path.parent.mkdir(parents=True, exist_ok=True)
    st.path.write_bytes(b"not a fernet token")
    assert st.load() == {}


def test_names_cannot_escape_directory(tmp_path):
    st = ExtensionStorage("../../etc/passwd", base_dir=tmp_path)
    assert st.path.parent == tmp_path


def test_oversized_value_is_not_saved(tmp_path):
    st = ExtensionStorage("x", base_dir=tmp_path)
    assert st.apply("set", "big", "x" * (1024 * 1024 + 10)) is False
    assert st.load() == {}


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")
def test_bridge_persists_across_restarts_and_pool(tmp_path):
    ext = tmp_path / "ext" / "index.js"
    ext.parent.mkdir()
    ext.write_text(PROBE)
    st = ExtensionStorage("probe", base_dir=tmp_path / "store")

    with JSRuntime(ext, storage=st) as rt1, JSRuntime(ext, storage=st) as rt2:
        assert rt1.call("run", "storage.get('token')") is None
        rt1.call("run", "storage.set('token', 'abc')")
        rt2.call("run", "storage.set('device', 7)")
        assert _wait_for(lambda: st.load() == {"token": "abc", "device": 7})

    with JSRuntime(ext, storage=st) as rt:
        assert rt.call("run", "storage.get('token')") == "abc"
        assert rt.call("run", "storage.get('device')") == 7
        # The snapshot is handed over, not left in the extension's env.
        assert rt.call("run", "process.env.MORNYE_EXT_STORAGE || null") is None
        rt.call("run", "storage.delete('token')")
        assert _wait_for(lambda: st.load() == {"device": 7})


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")
def test_runtime_without_storage_stays_in_memory(tmp_path):
    ext = tmp_path / "index.js"
    ext.write_text(PROBE)
    with JSRuntime(ext) as rt:
        rt.call("run", "storage.set('k', 1)")
        assert rt.call("run", "storage.get('k')") == 1
    with JSRuntime(ext) as rt:
        assert rt.call("run", "storage.get('k')") is None


def test_uninstall_removes_saved_storage(tmp_path):
    import json

    from mornye.extensions import storage as storage_mod
    from mornye.extensions.manager import ExtensionManager

    ext_dir = tmp_path / "ext"
    folder = ext_dir / "tidal-folder"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"name": "tidal-web"}))
    (folder / "index.js").write_text("")

    keep = ExtensionStorage("other")
    keep.apply("set", "k", "v")
    gone = ExtensionStorage("tidal-web")
    gone.apply("set", "token", "abc")
    assert gone.path.parent == storage_mod.storage_dir()

    mgr = ExtensionManager(ext_dir=ext_dir, auto_install_downloads=False)
    assert mgr.uninstall("tidal-folder") is True
    assert not gone.path.exists()
    assert keep.load() == {"k": "v"}
