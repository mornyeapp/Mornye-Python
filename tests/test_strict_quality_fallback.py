"""With quality fallback off, a provider that quietly serves a lower tier is
skipped in favour of the next provider at the same quality."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from mornye.application import download_engine as dl
from mornye.core.models import DownloadResult, TrackMetadata


def _track() -> TrackMetadata:
    return TrackMetadata(
        id="track-1",
        title="Track",
        artists="Artist",
        album="Album",
        album_artist="Artist",
    )


class _Provider:
    def __init__(self, name: str, path: Path) -> None:
        self.name = name
        self.path = path
        self.calls: list[str] = []

    def set_progress_callback(self, _callback) -> None:
        pass

    async def download_track_async(self, _metadata, _output_dir, **kwargs):
        self.calls.append(kwargs["quality"])
        self.path.write_bytes(b"audio")
        return DownloadResult.ok(self.name, str(self.path))


def _isolate(monkeypatch, tiers: dict[str, tuple[str, str]]) -> None:
    async def unchanged(_metadata, _requested, result, _provider):
        return result

    async def no_op(*_args, **_kwargs):
        return None

    monkeypatch.setattr(dl, "_reject_wrong_recording_async", unchanged)
    monkeypatch.setattr(dl, "_inject_provider_metadata_async", no_op)
    monkeypatch.setattr(dl, "_record_provider_outcome", no_op)
    monkeypatch.setattr(dl, "_schedule_hires_check", lambda *_a, **_k: None)
    monkeypatch.setattr(dl, "_delivered_tier", lambda path: tiers[Path(path).name])


def _run(tmp_path, providers, *, quality: str, allow_fallback: bool):
    opts = dl.DownloadOptions(
        output_dir=str(tmp_path),
        quality=quality,
        allow_fallback=allow_fallback,
        embed_lyrics=False,
    )
    return asyncio.run(dl.download_one_async(_track(), str(tmp_path), providers, opts))


def test_fallback_off_moves_to_the_next_provider_at_the_same_quality(
    tmp_path, monkeypatch
) -> None:
    _isolate(
        monkeypatch,
        {
            "a.flac": ("LOSSLESS", "16-bit/44.1 kHz"),
            "b.flac": ("HI_RES", "24-bit/96 kHz"),
        },
    )
    first = _Provider("ext:first", tmp_path / "a.flac")
    second = _Provider("ext:second", tmp_path / "b.flac")

    result = _run(
        tmp_path, [first, second], quality="HI_RES_LOSSLESS", allow_fallback=False
    )

    assert result.success
    assert result.provider == "ext:second"
    assert not (tmp_path / "a.flac").exists()
    assert len(first.calls) == 1  # a definitive answer: not retried


def test_fallback_off_fails_when_no_provider_has_the_tier(
    tmp_path, monkeypatch
) -> None:
    _isolate(monkeypatch, {"a.flac": ("LOSSLESS", "16-bit/44.1 kHz")})
    only = _Provider("ext:only", tmp_path / "a.flac")

    result = _run(tmp_path, [only], quality="HI_RES_LOSSLESS", allow_fallback=False)

    assert not result.success
    assert "Quality below requested" in (result.error or "")
    assert not (tmp_path / "a.flac").exists()


class _LoggingProvider(_Provider):
    """Also writes every request into one log shared by all providers."""

    def __init__(self, name: str, path: Path, log: list, quality_options=None) -> None:
        super().__init__(name, path)
        self.log = log
        if quality_options is not None:
            self.quality_options = quality_options

    async def download_track_async(self, metadata, output_dir, **kwargs):
        self.log.append((self.name, kwargs["quality"]))
        return await super().download_track_async(metadata, output_dir, **kwargs)


def test_fallback_on_asks_every_service_for_hi_res_before_lossless(
    tmp_path, monkeypatch
) -> None:
    _isolate(
        monkeypatch,
        {
            "a.flac": ("LOSSLESS", "16-bit/44.1 kHz"),
            "b.flac": ("HI_RES", "24-bit/96 kHz"),
        },
    )
    log: list = []
    first = _LoggingProvider("ext:first", tmp_path / "a.flac", log)
    second = _LoggingProvider("ext:second", tmp_path / "b.flac", log)

    result = _run(
        tmp_path, [first, second], quality="HI_RES_LOSSLESS", allow_fallback=True
    )

    assert result.success
    assert result.provider == "ext:second"
    assert not (tmp_path / "a.flac").exists()
    assert log == [("ext:first", "HI_RES_LOSSLESS"), ("ext:second", "HI_RES_LOSSLESS")]


def test_fallback_on_goes_back_to_the_first_service_for_lossless(
    tmp_path, monkeypatch
) -> None:
    _isolate(
        monkeypatch,
        {
            "a.flac": ("LOSSLESS", "16-bit/44.1 kHz"),
            "b.flac": ("LOSSLESS", "16-bit/44.1 kHz"),
            "c.flac": ("LOSSLESS", "16-bit/44.1 kHz"),
        },
    )
    log: list = []
    providers = [
        _LoggingProvider("ext:svc1", tmp_path / "a.flac", log),
        _LoggingProvider("ext:svc2", tmp_path / "b.flac", log),
        _LoggingProvider("ext:svc3", tmp_path / "c.flac", log),
    ]

    result = _run(tmp_path, providers, quality="HI_RES_LOSSLESS", allow_fallback=True)

    assert result.success
    assert result.provider == "ext:svc1"
    assert log == [
        ("ext:svc1", "HI_RES_LOSSLESS"),
        ("ext:svc2", "HI_RES_LOSSLESS"),
        ("ext:svc3", "HI_RES_LOSSLESS"),
        ("ext:svc1", "LOSSLESS"),
    ]


def test_a_service_with_no_hi_res_option_waits_for_the_lossless_pass(
    tmp_path, monkeypatch
) -> None:
    _isolate(
        monkeypatch,
        {
            "a.flac": ("LOSSLESS", "16-bit/44.1 kHz"),
            "d.flac": ("LOSSLESS", "16-bit/44.1 kHz"),
        },
    )
    log: list = []
    no_hi_res = _LoggingProvider(
        "ext:no-hi-res",
        tmp_path / "d.flac",
        log,
        quality_options=[
            {"id": "LOSSLESS", "label": "FLAC 16-bit", "kind": "lossless"}
        ],
    )
    hi_res = _LoggingProvider("ext:hi-res", tmp_path / "a.flac", log)

    result = _run(
        tmp_path, [no_hi_res, hi_res], quality="HI_RES_LOSSLESS", allow_fallback=True
    )

    assert result.success
    assert result.provider == "ext:no-hi-res"
    assert log == [("ext:hi-res", "HI_RES_LOSSLESS"), ("ext:no-hi-res", "LOSSLESS")]


def test_a_lossless_request_is_still_a_single_pass(tmp_path, monkeypatch) -> None:
    _isolate(monkeypatch, {"a.flac": ("LOSSLESS", "16-bit/44.1 kHz")})
    log: list = []
    first = _LoggingProvider("ext:first", tmp_path / "a.flac", log)
    second = _LoggingProvider("ext:second", tmp_path / "b.flac", log)

    result = _run(tmp_path, [first, second], quality="LOSSLESS", allow_fallback=True)

    assert result.success
    assert log == [("ext:first", "LOSSLESS")]


def test_fallback_off_rejects_lossy_for_a_lossless_request(
    tmp_path, monkeypatch
) -> None:
    _isolate(
        monkeypatch,
        {
            "a.m4a": ("LOSSY", "lossy 44.1 kHz"),
            "b.flac": ("LOSSLESS", "16-bit/44.1 kHz"),
        },
    )
    first = _Provider("ext:first", tmp_path / "a.m4a")
    second = _Provider("ext:second", tmp_path / "b.flac")

    result = _run(tmp_path, [first, second], quality="LOSSLESS", allow_fallback=False)

    assert result.success
    assert result.provider == "ext:second"


def test_unreadable_file_is_not_rejected(tmp_path, monkeypatch) -> None:
    _isolate(monkeypatch, {})
    monkeypatch.setattr(dl, "_delivered_tier", lambda _path: None)
    only = _Provider("ext:only", tmp_path / "a.flac")

    result = _run(tmp_path, [only], quality="HI_RES_LOSSLESS", allow_fallback=False)

    assert result.success


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
@pytest.mark.parametrize(
    ("rate", "fmt", "tier"),
    [("44100", "s16", "LOSSLESS"), ("96000", "s32", "HI_RES")],
)
def test_delivered_tier_reads_real_flac(tmp_path, rate, fmt, tier) -> None:
    out = tmp_path / "t.flac"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate={rate}:duration=0.2",
            "-sample_fmt",
            fmt,
            str(out),
        ],
        check=True,
    )
    delivered = dl._delivered_tier(str(out))
    assert delivered is not None
    assert delivered[0] == tier


def test_a_per_extension_quality_choice_is_not_rejected(tmp_path, monkeypatch) -> None:
    _isolate(monkeypatch, {"a.flac": ("LOSSLESS", "16-bit/44.1 kHz")})
    only = _Provider("ext:only", tmp_path / "a.flac")
    only.select_quality = lambda quality, override=None: override or quality
    opts = dl.DownloadOptions(
        output_dir=str(tmp_path),
        quality="HI_RES_LOSSLESS",
        allow_fallback=False,
        embed_lyrics=False,
        extension_quality={"only": "LOSSLESS"},
    )

    result = asyncio.run(dl.download_one_async(_track(), str(tmp_path), [only], opts))

    assert result.success


class _ExtProvider(_Provider):
    """A JS extension: picks from the quality options its manifest declares."""

    def __init__(self, name: str, path: Path, options: list[dict]) -> None:
        super().__init__(name, path)
        self.quality_options = options

    def select_quality(self, requested, override=None):
        from mornye.extensions.quality_options import pick_quality_option

        return pick_quality_option(self.quality_options, requested, override)


_TIDAL_OPTIONS = [
    {"id": "DOLBY_ATMOS", "label": "Dolby Atmos", "kind": "spatial"},
    {"id": "HI_RES_LOSSLESS", "label": "Hi-Res", "kind": "lossless"},
]
_AMAZON_OPTIONS = [
    {"id": "best", "label": "FLAC Best Available", "kind": "lossless"},
    {"id": "eac3", "label": "Dolby Digital Plus", "kind": "spatial"},
    {"id": "ac4", "label": "Dolby Atmos (AC-4)", "kind": "spatial"},
]
_QOBUZ_OPTIONS = [{"id": "HI_RES_LOSSLESS", "label": "Hi-Res", "kind": "lossless"}]


def test_atmos_without_fallback_only_asks_providers_with_an_atmos_option(
    tmp_path, monkeypatch
) -> None:
    _isolate(
        monkeypatch,
        {
            "t.m4a": ("LOSSY", "lossy"),  # never reached: tidal fails
            "a.m4a": ("ATMOS", "Dolby ac-4"),
        },
    )
    qobuz = _ExtProvider("ext:qobuz-web", tmp_path / "q.flac", _QOBUZ_OPTIONS)
    amazon = _ExtProvider("ext:amazon", tmp_path / "a.m4a", _AMAZON_OPTIONS)

    result = _run(
        tmp_path, [qobuz, amazon], quality="DOLBY_ATMOS", allow_fallback=False
    )

    assert result.success
    assert result.provider == "ext:amazon"
    assert qobuz.calls == []
    assert amazon.calls == ["DOLBY_ATMOS"]


def test_atmos_without_fallback_fails_fast_when_no_provider_has_atmos(
    tmp_path, monkeypatch
) -> None:
    _isolate(monkeypatch, {})
    qobuz = _ExtProvider("ext:qobuz-web", tmp_path / "q.flac", _QOBUZ_OPTIONS)

    result = _run(tmp_path, [qobuz], quality="DOLBY_ATMOS", allow_fallback=False)

    assert not result.success
    assert "No enabled provider offers Dolby Atmos" in (result.error or "")
    assert qobuz.calls == []


def test_atmos_without_fallback_rejects_a_stereo_file(tmp_path, monkeypatch) -> None:
    _isolate(
        monkeypatch,
        {
            "a.flac": ("HI_RES", "24-bit/48 kHz stereo"),
            "t.m4a": ("ATMOS", "Dolby ec-3"),
        },
    )
    amazon = _ExtProvider("ext:amazon", tmp_path / "a.flac", _AMAZON_OPTIONS)
    tidal = _ExtProvider("ext:tidal-web", tmp_path / "t.m4a", _TIDAL_OPTIONS)

    result = _run(
        tmp_path, [amazon, tidal], quality="DOLBY_ATMOS", allow_fallback=False
    )

    assert result.success
    assert result.provider == "ext:tidal-web"
    assert not (tmp_path / "a.flac").exists()


def test_atmos_with_fallback_still_asks_every_provider(tmp_path, monkeypatch) -> None:
    # No Atmos option: left out of the Atmos pass, then asked for Hi-Res
    # (its 16-bit file is refused there) and finally for Lossless.
    _isolate(monkeypatch, {"q.flac": ("LOSSLESS", "16-bit/44.1 kHz stereo")})
    qobuz = _ExtProvider("ext:qobuz-web", tmp_path / "q.flac", _QOBUZ_OPTIONS)

    result = _run(tmp_path, [qobuz], quality="DOLBY_ATMOS", allow_fallback=True)

    assert result.success
    assert result.provider == "ext:qobuz-web"
    assert qobuz.calls == ["HI_RES_LOSSLESS", "LOSSLESS"]


def test_an_amazon_override_to_flac_is_not_atmos() -> None:
    amazon = _ExtProvider("ext:amazon", Path("unused"), _AMAZON_OPTIONS)
    opts = dl.DownloadOptions(
        output_dir=".",
        quality="DOLBY_ATMOS",
        allow_fallback=False,
        extension_quality={"amazon": "best"},
    )
    assert dl._offers_atmos(amazon, opts) is False
    opts.extension_quality = {}
    assert dl._offers_atmos(amazon, opts) is True


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_delivered_tier_sees_a_real_eac3_m4a_as_atmos(tmp_path) -> None:
    out = tmp_path / "t.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=0.3",
            "-af",
            "pan=5.1|c0=c0|c1=c0|c2=c0|c3=c0|c4=c0|c5=c0",
            "-c:a",
            "eac3",
            "-f",
            "mp4",
            str(out),
        ],
        check=True,
    )
    delivered = dl._delivered_tier(str(out))
    assert delivered is not None
    assert delivered[0] == "ATMOS"


_KEY = "00112233445566778899aabbccddeeff"


def _encrypted_aac(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=1",
            "-c:a",
            "aac",
            "-encryption_scheme",
            "cenc-aes-ctr",
            "-encryption_key",
            _KEY,
            "-encryption_kid",
            "0123456789abcdef0123456789abcdef",
            "-f",
            "mp4",
            str(path),
        ],
        check=True,
    )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_a_still_encrypted_file_is_reported_as_such(tmp_path) -> None:
    out = tmp_path / "t.mp4"
    _encrypted_aac(out)
    assert dl._delivered_tier(str(out)) == ("ENCRYPTED", "a still-encrypted file")


def test_a_per_service_dolby_digital_plus_pick_keeps_amazon(
    tmp_path, monkeypatch
) -> None:
    """Choosing Dolby Digital Plus for Amazon is a choice, not a fallback:
    Amazon is still asked, for eac3, under a strict Atmos run."""
    _isolate(monkeypatch, {"a.m4a": ("ATMOS", "Dolby ec-3")})
    amazon = _ExtProvider("ext:amazon", tmp_path / "a.m4a", _AMAZON_OPTIONS)
    qobuz = _ExtProvider("ext:qobuz-web", tmp_path / "q.flac", _QOBUZ_OPTIONS)
    opts = dl.DownloadOptions(
        output_dir=str(tmp_path),
        quality="DOLBY_ATMOS",
        allow_fallback=False,
        embed_lyrics=False,
        extension_quality={"amazon": "eac3"},
    )

    result = asyncio.run(
        dl.download_one_async(_track(), str(tmp_path), [qobuz, amazon], opts)
    )

    assert result.success
    assert result.provider == "ext:amazon"
    assert qobuz.calls == []
