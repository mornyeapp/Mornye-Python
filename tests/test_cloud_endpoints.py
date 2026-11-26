"""The cloud endpoint list (core/__init__.py) is not fetched unless asked."""

from __future__ import annotations

import mornye.core as core


def test_the_cloud_is_not_contacted_by_default(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(core.CLOUD_ENDPOINTS_ENV, raising=False)
    monkeypatch.setattr(core, "_cache_file", lambda: str(tmp_path / "none.txt"))

    def no_network(*args, **kwargs):
        raise AssertionError("the cloud was contacted")

    monkeypatch.setattr(core.httpx, "get", no_network)
    assert core._load_registry() == {}


def test_the_switch_turns_the_cloud_fetch_back_on(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(core.CLOUD_ENDPOINTS_ENV, "1")
    monkeypatch.setattr(core, "_cache_file", lambda: str(tmp_path / "none.txt"))
    asked = []

    def offline(*args, **kwargs):
        asked.append(args[0])
        raise OSError("offline")

    monkeypatch.setattr(core.httpx, "get", offline)
    assert core._load_registry() == {}
    assert len(asked) == 1
