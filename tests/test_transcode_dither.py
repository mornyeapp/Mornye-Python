"""The dither type of a conversion that lowers the bit depth: TPDF or
TPDF HP (high-pass), as Mornye mobile 5.4.5 offers it, plumbed like
transcode_max_bit_depth from the CLI, the environment, profiles, the TUI,
the download engine and the GUI's convert dialog down to ffmpeg."""

from __future__ import annotations

from pathlib import Path

import pytest

from mornye.core import transcode


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("tpdf", "tpdf"),
        ("TPDF HP", "tpdf_hp"),
        ("tpdf-hp", "tpdf_hp"),
        ("triangular_hp", "tpdf_hp"),
        ("triangular", "tpdf"),
        (None, "tpdf"),
        ("", "tpdf"),
    ],
)
def test_normalize_dither(value, expected):
    assert transcode.normalize_dither(value) == expected


def test_an_unknown_dither_is_refused():
    with pytest.raises(ValueError):
        transcode.normalize_dither("shibata")


def _af(monkeypatch, **kwargs) -> str:
    monkeypatch.setattr(transcode, "soxr_available", lambda: False)
    cmd = transcode._encode_command(
        transcode.format_spec(transcode.FLAC),
        Path("in.flac"),
        Path("out"),
        "320k",
        24,
        sample_rate=96_000,
        max_bit_depth=16,
        **kwargs,
    )
    return cmd[cmd.index("-af") + 1]


def test_the_dither_reaches_aresample(monkeypatch):
    assert _af(monkeypatch) == "aresample=dither_method=triangular"
    assert _af(monkeypatch, dither="tpdf_hp") == "aresample=dither_method=triangular_hp"


def test_no_dither_without_a_lower_depth(monkeypatch):
    monkeypatch.setattr(transcode, "soxr_available", lambda: False)
    cmd = transcode._encode_command(
        transcode.format_spec(transcode.FLAC),
        Path("in.flac"),
        Path("out"),
        "320k",
        16,
        dither="tpdf_hp",
    )
    assert "-af" not in cmd


def test_download_options_normalise_the_dither():
    from mornye.application.download_engine import DownloadOptions

    def options(**kw):
        return DownloadOptions(output_dir=".", **kw)

    assert options(transcode_dither="TPDF HP").transcode_dither == "tpdf_hp"
    assert options().transcode_dither == "tpdf"
    with pytest.raises(ValueError):
        options(transcode_dither="nope")


def test_the_cli_flag_and_the_environment(monkeypatch):
    from mornye import launcher
    from mornye.core.env_settings import env_defaults

    url = "https://open.spotify.com/album/x"
    monkeypatch.setattr("sys.argv", ["mornye", url])
    assert launcher.parse_args(profile_defaults={}).transcode_dither == "tpdf"
    monkeypatch.setattr("sys.argv", ["mornye", url, "--transcode-dither", "tpdf_hp"])
    assert launcher.parse_args(profile_defaults={}).transcode_dither == "tpdf_hp"
    monkeypatch.setenv("MORNYE_TRANSCODE_DITHER", "tpdf_hp")
    monkeypatch.setattr("sys.argv", ["mornye", url])
    assert (
        launcher.parse_args(profile_defaults={**env_defaults()}).transcode_dither
        == "tpdf_hp"
    )


def test_profiles_and_the_tui_carry_it():
    from mornye.core.profiles import ProfileConfig
    from mornye.tui.config_state import ConfigState

    assert ProfileConfig().transcode_dither == "tpdf"
    assert ProfileConfig(transcode_dither="tpdf_hp").transcode_dither == "tpdf_hp"
    assert ConfigState().transcode_dither == "tpdf"


def test_post_processing_passes_it_to_the_conversion(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace

    from mornye.application import post_processing

    seen = {}

    async def fake_transcode(source, **kwargs):
        seen.update(kwargs)
        return Path(source).with_suffix(".flac")

    monkeypatch.setattr(post_processing, "transcode_file_async", fake_transcode)
    monkeypatch.setattr(post_processing, "already_in_target_format", lambda *a: False)
    src = tmp_path / "x.flac"
    src.write_bytes(b"x")
    result = SimpleNamespace(file_path=str(src), provider="p", success=True)
    options = SimpleNamespace(
        transcode_to="flac",
        transcode_bitrate="320k",
        transcode_keep_original=False,
        transcode_max_sample_rate=0,
        transcode_max_bit_depth=16,
        transcode_dither="tpdf_hp",
    )
    try:
        asyncio.run(post_processing._transcode_result(result, options))
    except Exception:
        pass
    assert seen.get("dither") == "tpdf_hp" and seen.get("max_bit_depth") == 16
