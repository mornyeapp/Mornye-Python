"""Chromaprint is found where package managers install it, not only on PATH.

An app started from the Finder gets launchd's PATH, with no Homebrew in it,
and the library's bare-name load only searches /usr/local/lib and /usr/lib,
so "Find Duplicates" reported Chromaprint missing on a Mac that had it.
"""

from __future__ import annotations

import os
import stat

from mornye.core import audio_fingerprint as af


def test_fpcalc_is_found_in_a_package_prefix_when_path_lacks_it(tmp_path, monkeypatch):
    fpcalc = tmp_path / "bin" / "fpcalc"
    fpcalc.parent.mkdir()
    fpcalc.write_text("#!/bin/sh\n")
    fpcalc.chmod(fpcalc.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(af.shutil, "which", lambda name: None)
    monkeypatch.setattr(af, "_PREFIXES", (str(tmp_path),))
    assert af._find_fpcalc() == str(fpcalc)


def test_no_fpcalc_anywhere_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(af.shutil, "which", lambda name: None)
    monkeypatch.setattr(af, "_PREFIXES", (str(tmp_path),))
    assert af._find_fpcalc() is None


def test_the_library_import_is_retried_with_the_prefix_path(tmp_path, monkeypatch):
    import builtins
    import ctypes

    lib = tmp_path / "lib" / "libchromaprint.1.dylib"
    lib.parent.mkdir()
    lib.write_bytes(b"")
    monkeypatch.setattr(af, "_PREFIXES", (str(tmp_path),))
    loaded: list[str] = []
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "chromaprint":
            # What pyacoustid's module does: load the library by bare name.
            ctypes.CDLL("libchromaprint.1.dylib")
            return object()
        return real_import(name, *args, **kwargs)

    def fake_cdll(name, *args, **kwargs):
        loaded.append(name)
        if name == "libchromaprint.1.dylib":
            raise OSError("not on the default search path")
        return object()

    monkeypatch.setattr(ctypes, "CDLL", fake_cdll)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    module, error = af._import_chromaprint()
    assert error is None and module is not None
    assert loaded == ["libchromaprint.1.dylib", str(lib)]
    assert ctypes.CDLL is fake_cdll, "the loader is put back"
    assert os.path.isfile(str(lib))
