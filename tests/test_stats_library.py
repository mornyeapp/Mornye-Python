"""The Stats page counts the library from before Mornye too.

The dashboard was built only from the download log, so music that was
already in the library when Mornye arrived counted for nothing. The Player
stores a row for every file it has seen (core/player_library.py); the files
Mornye did not download now count in the totals and rankings.
"""

from __future__ import annotations

import time

from mornye.core import player_library, stats
from tests.test_stats import _add


def _in_library(path: str, title: str, artist: str, **extra) -> None:
    row = {
        "path": path,
        "title": title,
        "artist": artist,
        "album": extra.get("album", "Old Album"),
        "year": extra.get("year", "1994"),
        "genre": extra.get("genre", "Hip-Hop"),
        "duration_s": 200.0,
        "format": "FLAC",
    }
    player_library.store([(path, 1, extra.get("size", 30_000_000), row)])


def test_library_files_count_in_totals_and_rankings():
    _add(title="New Song", artist="Doja Cat", file_path="/m/new.flac")
    _in_library("/m/old1.flac", "Juicy", "The Notorious B.I.G.")
    _in_library("/m/old2.flac", "Big Poppa", "The Notorious B.I.G.")

    doc = stats.wrapped()

    assert doc["totals"]["tracks"] == 3
    assert doc["totals"]["library_tracks"] == 2
    assert doc["top_artists"][0]["name"] == "The Notorious B.I.G."
    providers = {e["name"] for e in doc["providers"]}
    assert providers == {"ext:tidal-web", stats.LIBRARY_SOURCE}
    assert any(e["name"] == "1990s" for e in doc["decades"]["entries"])
    assert any(e["name"] == "Hip-Hop" for e in doc["top_genres"]["entries"])
    assert doc["totals"]["listening_ms"] >= 400_000


def test_a_downloaded_file_is_not_counted_twice():
    _add(title="Kiss Me More", artist="Doja Cat, SZA", file_path="/m/kmm.flac")
    _in_library("/m/kmm.flac", "Kiss Me More", "Doja Cat;SZA")
    # Moved since it was downloaded: matched by title and first artist.
    _add(title="Bad Habit", artist="Steve Lacy", file_path="/old/place.flac")
    _in_library("/m/new/place.flac", "Bad Habit", "Steve Lacy")

    doc = stats.wrapped()

    assert doc["totals"]["tracks"] == 2
    assert doc["totals"]["library_tracks"] == 0


def test_library_files_stay_out_of_everything_measured_in_time():
    _add(title="New Song", artist="A", at=time.time())
    _in_library("/m/old.flac", "Old Song", "B")

    doc = stats.wrapped()

    assert sum(m["tracks"] for m in doc["timeline"]) == 1
    assert sum(doc["activity"]["by_weekday"]) == 1
    assert doc["first"]["title"] == "New Song"


def test_only_the_whole_history_and_the_whole_instance_count_them():
    _add(title="New Song", artist="A")
    _in_library("/m/old.flac", "Old Song", "B")

    recent = stats.wrapped(window=stats.days_window(30))
    assert recent["totals"]["library_tracks"] == 0
    assert stats.wrapped(owner="alice")["totals"]["library_tracks"] == 0
    assert stats.wrapped(include_library=False)["totals"]["library_tracks"] == 0


def test_a_library_alone_is_enough_for_a_dashboard():
    _in_library("/m/old.flac", "Old Song", "B")

    doc = stats.wrapped()

    assert doc["totals"]["tracks"] == 1
    assert doc["first"] is None and doc["timeline"] == []
