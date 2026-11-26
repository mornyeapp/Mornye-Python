"""Enrichment from the services' own metadata, through JS and Python together.

Pinned here: an extension's answer is believed only about the recording and
release asked for; each service's built-in lookup keeps first say and its
extension fills what is left; a slow extension cannot cost the built-in
answers; the runtimes are reused rather than started per track; and the
Qobuz and Tidal lookups that were quietly reading less than they had (or
nothing) now read it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import mornye.core.extension_enrichment as ext_enrich
import mornye.core.metadata_enrichment as me
from mornye.core.extension_enrichment import (
    enriched_from_track,
    extensions_for_service,
    fetch_extension_enrichment,
)
from mornye.core.metadata_enrichment import EnrichedMetadata
from mornye.extensions.manager import InstalledExtension

TIDAL_WEB_TRACK = {
    "name": "Blinding Lights",
    "album_name": "After Hours",
    "isrc": "USUG11904206",
    "label": "XO Records, Island, XO, Republic",
    "copyright": "℗ 2019 The Weeknd XO, Inc.",
    "composer": "Max Martin, Oscar Holter",
    "release_date": "2020-03-20",
    "total_tracks": 14,
    "total_discs": 1,
    "cover_url": "https://resources.tidal.com/images/cover/1280x1280.jpg",
    "credits": [
        {
            "type": "Producer",
            "contributors": [{"name": "Max Martin"}, {"name": "Oscar Holter"}],
        },
        {"type": "Mixing Engineer", "contributors": [{"name": "Serban Ghenea"}]},
        {"type": "Mastering Engineer", "contributors": [{"name": "Kevin Peterson"}]},
        {"type": "Lyricist", "contributors": [{"name": "Abel Tesfaye"}]},
        {"type": "Record Label", "contributors": [{"name": "Republic"}]},
    ],
}


def test_an_extension_track_becomes_enrichment_with_credits() -> None:
    out = enriched_from_track(
        TIDAL_WEB_TRACK,
        title="Blinding Lights",
        isrc="USUG11904206",
        album="After Hours",
    )
    assert out.label == "XO Records, Island, XO, Republic"
    assert out.copyright == "℗ 2019 The Weeknd XO, Inc."
    assert (out.total_tracks, out.release_date) == (14, "2020-03-20")
    assert out.producer == "Max Martin; Oscar Holter"
    assert (out.mixer, out.engineer, out.lyricist) == (
        "Serban Ghenea",
        "Kevin Peterson",
        "Abel Tesfaye",
    )
    tags = out.as_tags()
    assert tags["PRODUCER"] == "Max Martin; Oscar Holter"
    assert tags["ORGANIZATION"] == "XO Records, Island, XO, Republic"


def test_an_answer_about_another_recording_is_discarded_whole() -> None:
    assert enriched_from_track(TIDAL_WEB_TRACK, title="Save Your Tears").as_tags() == {}
    other_isrc = enriched_from_track(
        TIDAL_WEB_TRACK, title="Blinding Lights", isrc="USUG12000001"
    )
    assert other_isrc.as_tags() == {}


def test_another_release_keeps_only_what_belongs_to_the_recording() -> None:
    out = enriched_from_track(
        TIDAL_WEB_TRACK, title="Blinding Lights", album="The Highlights"
    )
    assert out.composer == "Max Martin, Oscar Holter"
    assert out.producer == "Max Martin; Oscar Holter"
    assert (out.label, out.upc, out.copyright, out.total_tracks, out.cover_url_hd) == (
        "",
        "",
        "",
        0,
        "",
    )


def test_an_extension_echoing_its_input_adds_nothing() -> None:
    echo = {
        "name": "Blinding Lights",
        "artists": "The Weeknd",
        "album_name": "After Hours",
    }
    out = enriched_from_track(echo, title="Blinding Lights", album="After Hours")
    assert out.as_tags() == {}


# ---------------------------------------------------------------------------
# Which extensions a service gets
# ---------------------------------------------------------------------------


def _installed(name, types, runtime="javascript", root=None, enrich=True):
    """An installed extension; with `root`, its entry point exists on disk,
    defining enrichTrack or not."""
    manifest = {"name": name, "type": list(types)}
    ext_dir = Path("/nonexistent") / name
    if runtime != "javascript":
        manifest["runtime"] = runtime
    if root is not None:
        ext_dir = Path(root) / name
        ext_dir.mkdir(parents=True, exist_ok=True)
        body = "function enrichTrack(track) { return track; }\n" if enrich else ""
        (ext_dir / "index.js").write_text(
            body + "registerExtension({ initialize: function () {} });\n"
        )
    return InstalledExtension(
        name=name,
        display_name=name,
        version="1",
        description="",
        ext_dir=ext_dir,
        manifest=manifest,
    )


class _Manager:
    def __init__(self, *exts):
        self.exts = exts

    def list_installed(self):
        return list(self.exts)


def test_each_service_gets_its_javascript_metadata_extensions(tmp_path) -> None:
    manager = _Manager(
        _installed(
            "tidal-web", ["metadata_provider", "download_provider"], root=tmp_path
        ),
        _installed("tidal-py", ["download_provider"], runtime="python", root=tmp_path),
        _installed(
            "apple-music", ["metadata_provider", "lyrics_provider"], root=tmp_path
        ),
        _installed(
            "qobuz-web", ["metadata_provider", "download_provider"], root=tmp_path
        ),
        _installed("helper", ["runtime_utility"], root=tmp_path),
        _installed("melon-music", ["metadata_provider"], root=tmp_path),
    )
    assert extensions_for_service("tidal", manager) == ["tidal-web"]
    assert extensions_for_service("apple", manager) == ["apple-music"]
    assert extensions_for_service("qobuz", manager) == ["qobuz-web"]
    assert extensions_for_service("deezer", manager) == []


def test_every_installed_extension_that_implements_enrich_track_takes_part(
    tmp_path,
) -> None:
    """Not only the services in the enrichment list: a Korean catalogue, a
    metadata-only extension — any JavaScript metadata extension whose code
    defines enrichTrack. One without the function, a Python extension or a
    utility is not asked."""
    manager = _Manager(
        _installed(
            "tidal-web", ["metadata_provider", "download_provider"], root=tmp_path
        ),
        _installed("melon-music", ["metadata_provider"], root=tmp_path),
        _installed("no-enrich", ["metadata_provider"], root=tmp_path, enrich=False),
        _installed("tidal-py", ["metadata_provider"], runtime="python", root=tmp_path),
        _installed("helper", ["runtime_utility"], root=tmp_path),
        _installed("gone", ["metadata_provider"]),  # entry point missing
    )
    assert ext_enrich.enrichment_extensions(manager) == ["melon-music", "tidal-web"]


def test_an_extension_shipping_both_runtimes_enriches_through_its_javascript(
    tmp_path,
) -> None:
    """`runtime` answers "python" for it, and its entry point is the Python
    file — but enrichTrack runs in the JavaScript runtime, from index.js."""
    ext = _installed("merged", ["metadata_provider"], root=tmp_path)
    ext.manifest["runtimes"] = ["python", "javascript"]
    ext.manifest["entryPoints"] = {"python": "merged.py", "javascript": "index.js"}
    (ext.ext_dir / "merged.py").write_text("# no enrichTrack here\n")
    assert ext.runtime == "python"
    assert ext_enrich.can_enrich(ext) is True

    python_only = _installed("py-only", ["metadata_provider"], root=tmp_path)
    python_only.manifest["runtimes"] = ["python"]
    assert ext_enrich.can_enrich(python_only) is False


def test_an_updated_extension_is_read_again(tmp_path) -> None:
    import os

    ext = _installed("later", ["metadata_provider"], root=tmp_path, enrich=False)
    assert ext_enrich.implements_enrich_track(ext) is False

    entry = ext.ext_dir / "index.js"
    entry.write_text("function enrichTrack(t) { return t; }\n")
    stat = entry.stat()
    os.utime(entry, (stat.st_atime, stat.st_mtime + 10))
    assert ext_enrich.implements_enrich_track(ext) is True


# ---------------------------------------------------------------------------
# Calling them
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_pool():
    ext_enrich.close_extension_providers()
    yield
    ext_enrich.close_extension_providers()


def test_the_runtime_is_started_once_and_reused(fresh_pool) -> None:
    built, calls = [], []

    class _Provider:
        def _call(self, method, payload, options):
            calls.append((method, payload["name"]))
            return {**TIDAL_WEB_TRACK, "name": payload["name"]}

    def factory(name):
        built.append(name)
        return _Provider()

    async def two_tracks():
        for title in ("Blinding Lights", "Blinding Lights"):
            await fetch_extension_enrichment(
                "tidal-web", title, "The Weeknd", "USUG11904206", "After Hours",
                provider_factory=factory,
            )  # fmt: skip

    asyncio.run(two_tracks())
    assert built == ["tidal-web"]
    assert calls == [("enrichTrack", "Blinding Lights")] * 2


def test_a_failing_extension_is_an_empty_answer(fresh_pool) -> None:
    class _Broken:
        def _call(self, *args):
            raise RuntimeError("node died")

    out = asyncio.run(
        fetch_extension_enrichment(
            "qobuz-web", "x", "y", provider_factory=lambda n: _Broken()
        )
    )
    assert out.as_tags() == {}


# ---------------------------------------------------------------------------
# enrich_metadata_async: both halves together
# ---------------------------------------------------------------------------


@pytest.fixture
def both_halves(monkeypatch):
    """Built-in Deezer and Apple lookups, plus apple-music as Apple's extension."""
    monkeypatch.setenv(ext_enrich.ENABLE_ENV, "1")
    me._enrichment_cache.clear()
    monkeypatch.setattr(me, "_put_cached", lambda isrc, data: None)
    monkeypatch.setattr(me, "_get_cached", lambda isrc: None)

    async def deezer(isrc, album_name="", track_name="", duration_ms=0):
        return EnrichedMetadata(
            genre="R&B", label="Republic Records", upc="602508836060"
        )

    async def apple(track_name, artist_name, isrc="", album_name="", duration_ms=0):
        return EnrichedMetadata()  # the built-in Apple lookup finding nothing

    monkeypatch.setattr(me, "_deezer_fetch_async", deezer)
    monkeypatch.setattr(me, "_apple_fetch_async", apple)
    monkeypatch.setattr(
        ext_enrich,
        "extensions_for_service",
        lambda service, manager=None: ["apple-music"] if service == "apple" else [],
    )
    state = {"delay": 0.0, "asked": [], "installed": ["apple-music"]}
    monkeypatch.setattr(
        ext_enrich, "enrichment_extensions", lambda manager=None: state["installed"]
    )
    #: What an extension outside the list answers: fields no listed source has.
    other_track = {"name": "Blinding Lights", "bpm": 171, "label": "Someone Else"}

    async def fake_ext(
        ext, track_name, artist_name, isrc="", album_name="", duration_ms=0
    ):
        state["asked"].append(ext)
        await asyncio.sleep(state["delay"])
        track = TIDAL_WEB_TRACK if ext == "apple-music" else other_track
        return enriched_from_track(track, title=track_name, album=album_name)

    monkeypatch.setattr(ext_enrich, "fetch_extension_enrichment", fake_ext)
    return state


def _enrich(**kwargs):
    return asyncio.run(
        me.enrich_metadata_async(
            "Blinding Lights",
            "The Weeknd",
            "USUG11904206",
            ["deezer", "apple"],
            album_name="After Hours",
            **kwargs,
        )
    )


@pytest.mark.uses_extension_enrichment
def test_extensions_fill_what_the_built_in_lookups_left(both_halves) -> None:
    merged = _enrich()
    # Deezer's built-in lookup came first and keeps its fields…
    assert (merged.label, merged.upc) == ("Republic Records", "602508836060")
    # …and apple-music supplies what nothing built in had.
    assert merged.copyright == "℗ 2019 The Weeknd XO, Inc."
    assert merged.producer == "Max Martin; Oscar Holter"
    assert merged._sources["label"] == "deezer"
    assert merged._sources["copyright"] == "ext:apple-music"


@pytest.mark.uses_extension_enrichment
def test_a_slow_extension_does_not_cost_the_built_in_answers(
    both_halves, monkeypatch
) -> None:
    monkeypatch.setattr(ext_enrich, "EXTENSION_TIMEOUT_S", 0.05)
    both_halves["delay"] = 1.0
    merged = _enrich(timeout_s=0.05)
    assert merged.label == "Republic Records"
    assert merged.copyright == ""


@pytest.mark.uses_extension_enrichment
def test_only_the_listed_services_extensions_are_asked(both_halves) -> None:
    # An extension whose service is not in the enrichment list is left
    # alone: asking it cost a signed session (a captcha) per service.
    both_halves["installed"] = ["melon-music", "apple-music"]
    merged = _enrich()
    assert both_halves["asked"] == ["apple-music"]
    assert merged._sources.get("bpm") != "ext:melon-music"
    assert merged.label == "Republic Records"


@pytest.mark.uses_extension_enrichment
def test_the_extension_that_served_the_file_is_not_asked_again(both_halves) -> None:
    both_halves["installed"] = ["apple-music"]
    asyncio.run(
        me.enrich_metadata_async(
            "Blinding Lights",
            "The Weeknd",
            "USUG11904206",
            ["apple"],
            album_name="After Hours",
            skip_extensions=("ext:apple-music",),
        )
    )
    assert both_halves["asked"] == []


@pytest.mark.uses_extension_enrichment
def test_an_empty_list_asks_no_extension(both_halves) -> None:
    both_halves["installed"] = ["melon-music", "apple-music"]
    asyncio.run(
        me.enrich_metadata_async(
            "Blinding Lights",
            "The Weeknd",
            "USUG11904206",
            [],
            album_name="After Hours",
        )
    )
    assert both_halves["asked"] == []


@pytest.mark.uses_extension_enrichment
def test_qobuz_and_tidal_are_no_longer_enrichment_sources(
    both_halves, monkeypatch
) -> None:
    # A saved setting that still names them is read without them: neither
    # a built-in lookup nor their extensions (a captcha session per track).
    monkeypatch.setattr(
        ext_enrich, "extensions_for_service", lambda service, manager=None: [service]
    )
    both_halves["installed"] = ["qobuz", "tidal", "deezer"]
    asyncio.run(
        me.enrich_metadata_async(
            "Blinding Lights",
            "The Weeknd",
            "USUG11904206",
            ["qobuz", "tidal", "deezer"],
            album_name="After Hours",
        )
    )
    assert both_halves["asked"] == ["deezer"]


@pytest.mark.uses_extension_enrichment
def test_the_switch_turns_the_extension_half_off(both_halves, monkeypatch) -> None:
    monkeypatch.setenv(ext_enrich.ENABLE_ENV, "0")
    merged = _enrich()
    assert both_halves["asked"] == []
    assert merged.copyright == ""


# ---------------------------------------------------------------------------
# The built-in lookups that read less than they had
# ---------------------------------------------------------------------------


def test_a_lookup_answering_with_another_isrc_is_ignored(monkeypatch) -> None:
    class _Response:
        status_code = 200
        is_success = True

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class _Client:
        async def get(self, url, timeout=None, headers=None):
            return _Response({"title": "Blinding Lights", "isrc": "USUG11904999"})

    async def client_safe():
        return _Client()

    monkeypatch.setattr(me.NetworkManager, "get_async_client_safe", client_safe)
    deezer = asyncio.run(
        me._DeezerMeta().fetch_async("USUG11904206", "After Hours", "Blinding Lights")
    )
    assert deezer.as_tags() == {}


def test_deezer_is_asked_for_english_genre_names(monkeypatch) -> None:
    seen = []

    class _Response:
        status_code = 200
        is_success = True

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class _Client:
        async def get(self, url, timeout=None, headers=None):
            seen.append(headers)
            if "/track/" in url:
                return _Response({"album": {"id": 1}, "isrc": "X"})
            return _Response({"genres": {"data": [{"name": "Asian Music"}]}})

    async def client_safe():
        return _Client()

    monkeypatch.setattr(me.NetworkManager, "get_async_client_safe", client_safe)
    out = asyncio.run(me._DeezerMeta().fetch_async("X"))
    assert out.genre == "Asian Music"
    assert all(h.get("Accept-Language", "").startswith("en") for h in seen)
