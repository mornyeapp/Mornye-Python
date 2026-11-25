"""Issue #291: an opt-in " [year]" on album subfolders.

Two editions of one album otherwise share a folder ("Flower Boy").
Off by default: switching it on renames where new downloads of an album go.
"""

from __future__ import annotations

from pathlib import Path

from mornye.application.output_service import OutputService
from mornye.core.config import OutputConfig, apply_output_layout
from mornye.core.models import TrackMetadata, album_dir_name


def _track(release_date: str = "2017-07-21") -> TrackMetadata:
    return TrackMetadata(
        id="x",
        title="See You Again",
        artists="Tyler, The Creator",
        album="Flower Boy",
        album_artist="Tyler, The Creator",
        release_date=release_date,
    )


def test_album_folder_gets_the_year_when_asked():
    assert album_dir_name(_track(), with_year=True) == "Flower Boy [2017]"


def test_album_folder_is_unchanged_by_default():
    assert album_dir_name(_track()) == "Flower Boy"


def test_album_folder_without_a_known_year_is_the_plain_name():
    assert album_dir_name(_track(release_date=""), with_year=True) == "Flower Boy"


def test_output_service_puts_the_year_on_the_album_folder(tmp_path: Path):
    config = OutputConfig(
        directory=tmp_path, album_subfolders=True, album_folder_year=True
    )

    directory = OutputService(config).track_directory(track=_track())

    assert directory == tmp_path / "Flower Boy [2017]"


def test_saved_settings_carry_the_option_to_rest_downloads():
    output = OutputConfig()

    apply_output_layout(output, {"album_folder_year": True})

    assert output.album_folder_year is True
    apply_output_layout(output, {})
    assert output.album_folder_year is False
