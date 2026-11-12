"""Extensions of type ``lyrics_provider`` as lyrics providers (Jellyfin, J2).

Jellyfin, Apple Music and others export ``fetchLyrics``; nothing called it,
because every lyrics provider was built in. Each such extension is now a
provider named ``ext:<name>``, ranked with the rest.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from mornye.core import extension_lyrics as EL
from mornye.core import lyrics as L


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    monkeypatch.setattr(L, "get_cached_response", lambda *a, **k: None)
    monkeypatch.setattr(L, "put_cached_response", lambda *a, **k: None)


JELLYFIN_SYNCED = {
    "lines": [
        {"startTimeMs": 2640, "words": "Damn, every time", "endTimeMs": 5000},
        {"startTimeMs": 65_010, "words": "second line", "endTimeMs": 0},
    ],
    "syncType": "LINE_SYNCED",
    "instrumental": False,
    "plainLyrics": "",
    "provider": "Jellyfin",
}


def test_line_synced_answers_become_lrc() -> None:
    assert EL.result_to_lrc(JELLYFIN_SYNCED) == (
        "[00:02.64]Damn, every time\n[01:05.01]second line"
    )


def test_unsynced_answers_stay_plain() -> None:
    assert (
        EL.result_to_lrc(
            {"lines": [], "syncType": "UNSYNCED", "plainLyrics": "one\ntwo"}
        )
        == "one\ntwo"
    )


def test_nothing_instrumental_or_odd_shapes_give_no_lyrics() -> None:
    assert EL.result_to_lrc(None) == ""
    assert EL.result_to_lrc({"instrumental": True, "plainLyrics": "x"}) == ""
    assert EL.result_to_lrc([1, 2]) == ""
    assert EL.result_to_lrc(" [00:01.00]already lrc ") == "[00:01.00]already lrc"


def _ext(tmp_path, name, types, source):
    js = tmp_path / f"{name}.js"
    js.write_text(source)
    return SimpleNamespace(
        name=name,
        display_name=name.title(),
        types=types,
        runtime="javascript",
        manifest={},
        index_js=str(js),
    )


def test_only_lyrics_providers_that_define_fetch_lyrics_are_listed(tmp_path) -> None:
    manager = SimpleNamespace(
        list_installed=lambda: [
            _ext(tmp_path, "jellyfin", ["lyrics_provider"], "function fetchLyrics(){}"),
            _ext(
                tmp_path, "tidal-web", ["download_provider"], "function fetchLyrics(){}"
            ),
            _ext(tmp_path, "broken", ["lyrics_provider"], "function other(){}"),
        ]
    )
    assert EL.lyrics_extensions(manager) == [
        {"id": "ext:jellyfin", "name": "jellyfin", "label": "Jellyfin"}
    ]


def test_the_extension_is_called_with_the_mobile_arguments() -> None:
    calls = []

    class Provider:
        def _call(self, method, *args):
            calls.append((method, args))
            return JELLYFIN_SYNCED

    text = asyncio.run(
        EL.fetch_extension_lyrics(
            "ext:jf-test",
            "Song",
            "Artist",
            "Album",
            215,
            provider_factory=lambda name: Provider(),
        )
    )
    assert calls == [("fetchLyrics", ("Song", "Artist", "Album", 215))]
    assert text.startswith("[00:02.64]Damn")
    from mornye.core.extension_enrichment import close_extension_provider

    close_extension_provider("jf-test")


def test_a_failing_extension_gives_no_lyrics() -> None:
    class Provider:
        def _call(self, *args):
            raise RuntimeError("server down")

    assert (
        asyncio.run(
            EL.fetch_extension_lyrics(
                "ext:jf-fail", "S", "A", provider_factory=lambda name: Provider()
            )
        )
        == ""
    )
    from mornye.core.extension_enrichment import close_extension_provider

    close_extension_provider("jf-fail")


def test_an_extension_is_ranked_like_a_built_in_provider(monkeypatch) -> None:
    async def lrclib(_ctx):
        return "[00:01.00]from lrclib"

    async def from_extension(provider_id, *args, **kw):
        assert provider_id == "ext:jellyfin"
        return "[00:01.00]from jellyfin"

    monkeypatch.setattr(L, "_PROVIDER_MAP", {"lrclib": lrclib})
    monkeypatch.setattr(EL, "fetch_extension_lyrics", from_extension)

    text, provider = asyncio.run(
        L.fetch_lyrics_async("S", "A", providers=["ext:jellyfin", "lrclib"])
    )
    assert provider == "ext:jellyfin" and "from jellyfin" in text

    text, provider = asyncio.run(
        L.fetch_lyrics_async("S", "A", providers=["lrclib", "ext:jellyfin"])
    )
    assert provider == "lrclib"


def test_the_cli_accepts_extension_providers() -> None:
    import argparse

    from mornye import launcher

    assert launcher._lyrics_provider_arg("ext:jellyfin") == "ext:jellyfin"
    assert launcher._lyrics_provider_arg("lrclib") == "lrclib"
    for bad in ("nope", "ext:"):
        with pytest.raises(argparse.ArgumentTypeError):
            launcher._lyrics_provider_arg(bad)


def test_the_tui_offers_installed_and_saved_extension_providers(monkeypatch) -> None:
    from mornye.tui import config_state

    monkeypatch.setattr(
        EL,
        "lyrics_extensions",
        lambda manager=None: [{"id": "ext:jellyfin", "name": "jellyfin", "label": "J"}],
    )
    choices = config_state.lyrics_provider_choices(["lrclib", "ext:gone"])
    assert choices[: len(config_state.LYRICS_PROVIDERS)] == list(
        config_state.LYRICS_PROVIDERS
    )
    assert choices[-2:] == ["ext:jellyfin", "ext:gone"]


def test_the_web_frontend_can_list_them() -> None:
    webapp = pytest.importorskip("mornye.webapp")
    assert "get_lyrics_extensions" in webapp.ALLOWED_METHODS
