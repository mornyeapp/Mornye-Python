"""An Atmos request is answered only by an Atmos file already on disk.

Before the fix, _quality_to_ext() had no Atmos case and fell back to .flac,
so asking for a track in Dolby Atmos found the Hi-Res FLAC of the same track
under the same name and skipped the download.
"""

import asyncio
from pathlib import Path

import pytest

from mornye.extensions import provider as provider_module
from mornye.extensions.provider import (
    _is_atmos_quality,
    _quality_to_ext,
    _resolve_atmos_target,
)


def _exists(path):
    return path.is_file() and path.stat().st_size > 0


@pytest.fixture
def codecs(monkeypatch):
    """Codec ffprobe would report, by file name."""
    table: dict[str, str] = {}

    async def fake_codec(filepath: str) -> str:
        return table.get(Path(filepath).name, "flac")

    monkeypatch.setattr(provider_module, "_get_codec_async", fake_codec)
    return table


def _touch(path):
    path.write_bytes(b"x")
    return path


def resolve(output_path):
    return asyncio.run(_resolve_atmos_target(output_path, _exists))


@pytest.mark.parametrize(
    "quality",
    [
        "DOLBY_ATMOS",
        "dolby_atmos",
        "ATMOS",
        "atmos",
        "DOLBY",
        "EAC3",
        "EC3",
        "EAC3_JOC",
    ],
)
def test_every_name_of_the_atmos_tier_is_recognised(quality):
    assert _is_atmos_quality(quality)
    assert _quality_to_ext(quality) == ".m4a"


@pytest.mark.parametrize(
    "quality", ["HI_RES_LOSSLESS", "LOSSLESS", "FLAC", "MP3_320", "AAC"]
)
def test_stereo_tiers_are_not_atmos(quality):
    assert not _is_atmos_quality(quality)


def test_hi_res_flac_of_the_same_track_does_not_count_as_the_atmos_download(
    tmp_path, codecs
):
    _touch(tmp_path / "Song - Artist.flac")
    target, existing = resolve(tmp_path / "Song - Artist.flac")
    assert existing is None
    assert target == tmp_path / "Song - Artist.m4a"


def test_an_atmos_m4a_already_there_is_skipped(tmp_path, codecs):
    atmos = _touch(tmp_path / "Song - Artist.m4a")
    codecs[atmos.name] = "eac3"
    target, existing = resolve(tmp_path / "Song - Artist.m4a")
    assert existing == atmos
    assert target == atmos


def test_amazon_ac4_mp4_already_there_is_skipped(tmp_path, codecs):
    atmos = _touch(tmp_path / "Song - Artist.mp4")
    codecs[atmos.name] = "ac4"
    _, existing = resolve(tmp_path / "Song - Artist.m4a")
    assert existing == atmos


def test_a_stereo_m4a_keeps_its_name_and_the_atmos_download_gets_its_own(
    tmp_path, codecs
):
    stereo = _touch(tmp_path / "Song - Artist.m4a")
    codecs[stereo.name] = "alac"
    target, existing = resolve(tmp_path / "Song - Artist.m4a")
    assert existing is None
    assert target == tmp_path / "Song - Artist (Dolby Atmos).m4a"


def test_the_renamed_atmos_download_is_found_next_time(tmp_path, codecs):
    stereo = _touch(tmp_path / "Song - Artist.m4a")
    codecs[stereo.name] = "aac"
    atmos = _touch(tmp_path / "Song - Artist (Dolby Atmos).m4a")
    codecs[atmos.name] = "eac3"
    _, existing = resolve(tmp_path / "Song - Artist.m4a")
    assert existing == atmos


def _track():
    from mornye.core.models import TrackMetadata

    return TrackMetadata(
        id="t1", title="Song", artists="Artist", album="Album", album_artist="Artist"
    )


def test_an_atmos_request_has_no_transcoded_file_to_skip_on(tmp_path):
    # Dolby audio is kept as it arrives, never transcoded: with ALAC as the
    # transcode target, the Hi-Res download's ALAC .m4a answered "already
    # downloaded as ALAC" and the Atmos request never reached a provider.
    from mornye.application.download_engine import (
        DownloadOptions,
        transcode_target_path,
    )

    atmos = DownloadOptions(
        output_dir=str(tmp_path), quality="DOLBY_ATMOS", transcode_to="alac"
    )
    assert transcode_target_path(_track(), str(tmp_path), atmos) is None

    hires = DownloadOptions(
        output_dir=str(tmp_path), quality="HI_RES_LOSSLESS", transcode_to="alac"
    )
    assert transcode_target_path(_track(), str(tmp_path), hires) is not None


def test_an_empty_leftover_is_not_a_download(tmp_path, codecs):
    (tmp_path / "Song - Artist.m4a").write_bytes(b"")
    codecs["Song - Artist.m4a"] = "eac3"
    target, existing = resolve(tmp_path / "Song - Artist.m4a")
    assert existing is None
    assert target == tmp_path / "Song - Artist.m4a"
