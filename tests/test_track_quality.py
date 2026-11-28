"""The on-demand "what quality do Qobuz and TIDAL have" check."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from types import SimpleNamespace

import pytest

from mornye.core import extension_enrichment, track_quality
from mornye.core.track_quality import check_track_quality_async, describe

TRACK = {
    "id": "123",
    "title": "Signal",
    "artist": "Artist",
    "isrc": "usaaa0000001",
    "duration_ms": 180000,
    "external_url": "https://music.example.com/song/123",
}

HIRES = {
    "available": True,
    "quality": {"max_bit_depth": 24, "max_sampling_rate": 96, "hires": True},
}


class _Manager:
    def __init__(self, *extensions):
        self.extensions = extensions

    def list_installed(self):
        return list(self.extensions)


def _extension(tmp_path, name, version="1.0.0"):
    index_js = tmp_path / f"{name}.js"
    index_js.write_text("")
    return SimpleNamespace(
        name=name,
        version=version,
        index_js=index_js,
        enabled=True,
        manifest={"runtimes": ["javascript"]},
    )


class _Provider:
    def __init__(self, answer, service=None, spotify_url=None, audio_quality=None):
        self.answer = answer
        self.service = service
        self.spotify_url = spotify_url
        self.calls = []
        self.resolve_links = None
        self.enrichment_calls = []
        self.resolved_track = (
            {
                f"{service}_id": "123456",
                "external_urls": f"https://{service}.example/track/converted",
                "audio_quality": audio_quality
                or ("24bit/96kHz" if service == "qobuz" else "24bit"),
            }
            if service
            else {}
        )

    async def _provider_match_options(self, metadata, *, resolve_links=True):
        self.resolve_links = resolve_links
        options = {"duration_ms": metadata.duration_ms}
        if resolve_links and self.spotify_url:
            options["spotify_url"] = self.spotify_url
        if resolve_links and self.service:
            options[f"{self.service}_url"] = (
                f"https://{self.service}.example/track/converted"
            )
            options[f"{self.service}_id"] = "123456"
        return options

    def enrich_track(self, track, options):
        self.enrichment_calls.append((track, options))
        return self.resolved_track

    def _provider_track_id(self, provider, url):
        from mornye.extensions.provider import JSExtensionProvider

        return JSExtensionProvider._provider_track_id(provider, url)

    def check_availability(self, isrc, track_name, artist_name, options):
        self.calls.append((isrc, track_name, artist_name, options))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(
        track_quality, "cache_path", lambda name: tmp_path / "cache" / name
    )
    monkeypatch.setattr(extension_enrichment, "_providers", {})


def _check(manager, providers, track=TRACK):
    return asyncio.run(
        check_track_quality_async(
            track, manager=manager, provider_factory=providers.__getitem__
        )
    )


@pytest.fixture
def installed(tmp_path):
    return _Manager(
        _extension(tmp_path, "tidal-web"),
        _extension(tmp_path, "qobuz-web"),
        _extension(tmp_path, "amzn"),
    )


@pytest.mark.parametrize(
    ("service", "answer", "state", "label"),
    [
        ("qobuz", HIRES, "hires", "Qobuz 24-bit/96 kHz"),
        (
            "qobuz",
            {
                "available": True,
                "quality": {"max_bit_depth": 16, "max_sampling_rate": 44.1},
            },
            "standard",
            "Qobuz 16-bit/44.1 kHz",
        ),
        (
            "qobuz",
            {
                "available": True,
                "quality": {"max_bit_depth": 24, "max_sampling_rate": 192000},
            },
            "hires",
            "Qobuz 24-bit/192 kHz",
        ),
        (
            "qobuz",
            {"available": True, "quality": {"max_bit_depth": 0, "hires": None}},
            "unknown",
            "Qobuz unknown",
        ),
        (
            "tidal",
            {"available": True, "quality": {"hires": True}},
            "hires",
            "TIDAL Hi-Res",
        ),
        (
            "tidal",
            {
                "available": True,
                "quality": {"hires": True, "max_bit_depth": 24},
            },
            "hires",
            "TIDAL 24-bit",
        ),
        (
            "tidal",
            {"available": True, "quality": {"hires": True, "max_quality": "MQA"}},
            "hires",
            "TIDAL MQA",
        ),
        (
            "tidal",
            {
                "available": True,
                "quality": {"hires": False, "max_quality": "LOSSLESS"},
            },
            "standard",
            "TIDAL Lossless",
        ),
        (
            "tidal",
            {"available": True, "quality": {"hires": False, "max_quality": "HIGH"}},
            "standard",
            "TIDAL High",
        ),
        # An older extension says nothing about quality: not "low quality".
        ("tidal", {"available": True}, "unknown", "TIDAL unknown"),
        (
            "tidal",
            {"available": False, "reason": "No verified TIDAL track match found"},
            "unavailable",
            "TIDAL not found",
        ),
        (
            "qobuz",
            {
                "available": False,
                "reason": "session.signedFetch: no session_handler configured",
            },
            "unknown",
            "Qobuz unknown",
        ),
        # A service that failed has not said the track is missing.
        (
            "qobuz",
            {"available": False, "reason": "HTTP 429 for https://www.qobuz.com"},
            "error",
            "Qobuz check failed",
        ),
        ("qobuz", {"available": False}, "error", "Qobuz check failed"),
    ],
)
def test_describe(service, answer, state, label):
    result = describe(service, answer)

    assert (result["state"], result["label"]) == (state, label)


def test_asks_qobuz_then_tidal_and_never_amazon(installed):
    providers = {
        "qobuz-web": _Provider(HIRES, "qobuz"),
        "tidal-web": _Provider(
            {"available": True, "quality": {"hires": False}}, "tidal"
        ),
    }

    answer = _check(installed, providers)

    assert answer["status"] == "ok"
    assert [r["service"] for r in answer["results"]] == ["qobuz", "tidal"]
    qobuz = providers["qobuz-web"]
    tidal = providers["tidal-web"]
    assert qobuz.resolve_links is False
    assert tidal.resolve_links is False
    isrc, title, artist, qobuz_options = qobuz.calls[0]
    assert (isrc, title, artist) == ("usaaa0000001", "Signal", "Artist")
    assert qobuz.enrichment_calls[0][0]["name"] == "Signal"
    assert qobuz_options["qobuz_url"] == "https://qobuz.example/track/converted"
    assert qobuz_options["qobuz_id"] == "123456"
    tidal_options = tidal.calls[0][3]
    assert tidal_options["tidal_url"] == "https://tidal.example/track/converted"
    assert tidal_options["tidal_id"] == "123456"


def test_quality_checks_do_not_emit_info_logs(installed, caplog):
    providers = {
        "qobuz-web": _Provider(HIRES, "qobuz"),
        "tidal-web": _Provider(
            {"available": True, "quality": {"hires": False}}, "tidal"
        ),
    }

    with caplog.at_level(logging.INFO, logger="mornye.core.track_quality"):
        answer = _check(installed, providers)

    assert answer["status"] == "ok"
    assert not [
        record
        for record in caplog.records
        if record.name == "mornye.core.track_quality" and record.levelno == logging.INFO
    ]


@pytest.mark.parametrize(
    ("service", "audio_quality", "expected_state", "expected_label"),
    [
        ("qobuz", "24bit/44.1kHz", "hires", "Qobuz 24-bit/44.1 kHz"),
        ("tidal", "24bit", "hires", "TIDAL 24-bit"),
        ("tidal", "16bit/44.1kHz", "standard", "TIDAL Lossless"),
    ],
)
def test_uses_audio_quality_from_the_extension_native_resolve(
    tmp_path, service, audio_quality, expected_state, expected_label
):
    extension_name = f"{service}-web"
    manager = _Manager(_extension(tmp_path, extension_name))
    providers = {
        extension_name: _Provider(
            {"available": True},
            service,
            audio_quality=audio_quality,
        )
    }

    answer = _check(manager, providers)

    assert answer["results"][0]["state"] == expected_state
    assert answer["results"][0]["label"] == expected_label


def test_uses_metadata_hints_when_extension_resolve_returns_no_native_track(installed):
    providers = {
        "qobuz-web": _Provider(HIRES),
        "tidal-web": _Provider(HIRES),
    }

    _check(installed, providers)

    for service, provider in providers.items():
        assert provider.resolve_links is False
        options = provider.calls[0][3]
        assert options["duration_ms"] == TRACK["duration_ms"]
        assert f"{service.removesuffix('-web')}_id" not in options


def test_a_known_answer_is_not_asked_for_again(installed):
    providers = {"qobuz-web": _Provider(HIRES), "tidal-web": _Provider(HIRES)}

    first = _check(installed, providers)
    second = _check(installed, providers)

    assert second == first
    assert [len(p.calls) for p in providers.values()] == [1, 1]


def test_only_known_answers_are_kept(installed):
    providers = {
        "qobuz-web": _Provider(RuntimeError("boom")),
        "tidal-web": _Provider({"available": True}),
    }

    first = _check(installed, providers)
    _check(installed, providers)

    assert [r["state"] for r in first["results"]] == ["error", "unknown"]
    assert [len(p.calls) for p in providers.values()] == [2, 2]


def test_an_old_answer_or_a_new_extension_version_asks_again(
    installed, monkeypatch, tmp_path
):
    providers = {"qobuz-web": _Provider(HIRES), "tidal-web": _Provider(HIRES)}
    now = [1_000_000.0]
    monkeypatch.setattr(track_quality.time, "time", lambda: now[0])
    _check(installed, providers)

    now[0] += track_quality.CACHE_TTL_S + 1
    _check(installed, providers)
    assert len(providers["qobuz-web"].calls) == 2

    updated = _Manager(_extension(tmp_path, "qobuz-web", version="1.0.1"))
    _check(updated, providers)
    assert len(providers["qobuz-web"].calls) == 3


def test_the_cache_stays_bounded_and_survives_a_damaged_file(
    installed, monkeypatch, tmp_path
):
    cache = tmp_path / "cache" / track_quality.CACHE_FILE_NAME
    cache.parent.mkdir()
    cache.write_text("not json")
    monkeypatch.setattr(track_quality, "CACHE_MAX_ENTRIES", 3)
    providers = {"qobuz-web": _Provider(HIRES), "tidal-web": _Provider(HIRES)}

    for number in range(4):
        _check(installed, providers, {**TRACK, "isrc": f"USAAA000000{number}"})

    assert len(json.loads(cache.read_text())) == 3


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://open.qobuz.com/track/101686517", "101686517"),
        ("https://tidal.com/browse/track/101686517", None),
        ("https://notqobuz.com/track/101686517", None),
    ],
)
def test_a_row_of_the_service_itself_passes_its_id(tmp_path, url, expected):
    providers = {"qobuz-web": _Provider(HIRES)}
    manager = _Manager(_extension(tmp_path, "qobuz-web"))

    _check(manager, providers, {**TRACK, "external_url": url})

    options = providers["qobuz-web"].calls[0][3]
    assert options.get("qobuz_id") == expected


def test_a_spotify_row_gets_its_isrc_looked_up(installed, monkeypatch):
    async def _isrc(self, track_id):
        return "USAAA0000009"

    monkeypatch.setattr("mornye.core.isrc_helper.IsrcHelper.get_isrc_async", _isrc)
    providers = {"qobuz-web": _Provider(HIRES), "tidal-web": _Provider(HIRES)}

    _check(
        installed,
        providers,
        {**TRACK, "isrc": "", "id": "0VjIjW4GlUZAMYd2vXMi3b"},
    )

    assert providers["qobuz-web"].calls[0][0] == "USAAA0000009"


def test_nothing_to_ask(tmp_path):
    assert _check(_Manager(), {}, {"title": ""})["status"] == "error"
    nothing = _check(_Manager(_extension(tmp_path, "amzn")), {})
    assert nothing["status"] == "error"
    assert "Qobuz or TIDAL" in nothing["error"]


def test_the_check_has_its_own_runtime_and_both_are_closed():
    closed = []

    def factory(name):
        provider = SimpleNamespace()
        provider.close = lambda: closed.append(provider)
        return provider

    normal = extension_enrichment._provider("qobuz-web", factory)
    public = extension_enrichment._provider("qobuz-web", factory, signed_requests=False)

    assert normal is not public
    extension_enrichment.close_extension_provider("qobuz-web")
    assert closed == [normal, public]
    assert extension_enrichment._providers == {}


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")
def test_without_signed_requests_the_extension_is_refused(monkeypatch, tmp_path):
    """The real runtime: nothing is signed, and the refusal reads as the one
    `describe` knows."""
    from mornye.extensions import provider as provider_module

    folder = tmp_path / "ext" / "qobuz-web"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(
        json.dumps(
            {
                "name": "qobuz-web",
                "version": "1.0.0",
                "type": ["download_provider"],
                "requiredRuntimeFeatures": ["signedSession@3"],
                "signedSession": {"namespace": "x", "baseUrl": "https://x.invalid"},
            }
        )
    )
    (folder / "index.js").write_text(
        "registerExtension({ checkAvailability: function () {"
        " var r = session.signedFetch('GET', '/track/get', null, {});"
        " return { available: false, reason: r.error }; } });"
    )
    monkeypatch.setattr(
        provider_module,
        "signed_session_client",
        lambda manifest: pytest.fail("no signed session may be built"),
    )

    provider = provider_module.JSExtensionProvider(
        "qobuz-web", ext_dir=str(tmp_path / "ext"), signed_requests=False
    )
    try:
        answer = provider.check_availability("", "Signal", "Artist")
    finally:
        provider.close()

    assert describe("qobuz", answer)["state"] == "unknown"
