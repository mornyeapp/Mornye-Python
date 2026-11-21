"""The Player's artist photos: the right artist is picked, and the answer —
"nothing found" included — is asked for once."""

from __future__ import annotations

import json

import pytest

from mornye.core import artist_photos as ap


@pytest.fixture(autouse=True)
def _cache_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MORNYE_CACHE_DIR", str(tmp_path))
    ap.reset_for_tests()
    yield
    ap.reset_for_tests()


def _art(name, url):
    return {"name": name, "cover_url": url, "type": "artist"}


def test_exact_name_wins_over_the_first_result():
    results = [
        _art("Low Roar", "https://img/low-roar"),
        _art("Lowkey", "https://img/lowkey"),
        _art("Low", "https://img/low"),
    ]
    assert ap.best_match("Low", results) == "https://img/low"


def test_spelling_differences_still_match():
    results = [_art("The Beatles", "https://img/x"), _art("Beyoncé", "https://img/b")]
    assert ap.best_match("beatles", results) == "https://img/x"
    assert ap.best_match("Beyonce", results) == "https://img/b"
    assert (
        ap.best_match(
            "Simon and Garfunkel", [_art("Simon & Garfunkel", "https://img/s")]
        )
        == "https://img/s"
    )


def test_no_close_result_means_no_photo():
    assert ap.best_match("Low", [_art("Lady Gaga", "https://img/g")]) == ""
    assert ap.best_match("Low", [_art("Low", "")]) == ""
    assert ap.best_match("", [_art("Low", "https://img/low")]) == ""


def test_placeholder_names_are_never_searched():
    calls = []
    out = ap.artist_photos(
        ["Unknown artist", "Various Artists"],
        search=lambda n: calls.append(n) or [_art(n, "https://img/x")],
    )
    assert out == {"Unknown artist": "", "Various Artists": ""}
    assert calls == []


def test_photos_and_misses_are_cached_on_disk(tmp_path):
    calls = []

    def search(name):
        calls.append(name)
        return [_art("Low", "https://img/low")] if name == "Low" else []

    out = ap.artist_photos(["Low", "Nobody At All"], search=search)
    assert out == {"Low": "https://img/low", "Nobody At All": ""}
    assert sorted(calls) == ["Low", "Nobody At All"]

    stored = json.loads((tmp_path / "artist-photos.json").read_text())
    assert stored["low"]["url"] == "https://img/low"
    assert stored["nobody at all"]["url"] == ""

    ap.reset_for_tests()  # a new run reads the file
    again = ap.artist_photos(["Low", "Nobody At All", "LOW"], search=search)
    assert again == {
        "Low": "https://img/low",
        "Nobody At All": "",
        "LOW": "https://img/low",
    }
    assert len(calls) == 2


def test_a_failed_search_is_not_recorded():
    def broken(name):
        raise OSError("offline")

    assert ap.artist_photos(["Low"], search=broken) == {"Low": ""}
    assert ap.cached("Low") is None
    assert ap.artist_photos(
        ["Low"], search=lambda n: [_art("Low", "https://img/low")]
    ) == {"Low": "https://img/low"}


def test_old_answers_are_asked_again():
    ap.artist_photos(["Low", "Gone"], search=lambda n: [_art("Low", "https://img/low")])
    now = ap.time.time()
    assert ap.cached("Gone", now + ap._MISS_TTL + 1) is None
    assert ap.cached("Low", now + ap._MISS_TTL + 1) == "https://img/low"
    assert ap.cached("Low", now + ap._HIT_TTL + 1) is None


def test_spellings_of_one_artist_are_one_search_and_batches_are_capped():
    calls = []

    def search(name):
        calls.append(name)
        return []

    ap.artist_photos(["Beyoncé", "Beyonce", "", None, 7], search=search)
    assert calls == ["Beyoncé"]
    names = [f"Artist {i}" for i in range(100)]
    assert len(ap.artist_photos(names, search=lambda n: [])) == ap._MAX_BATCH


def test_the_web_app_and_its_shim_expose_the_method():
    from pathlib import Path

    from mornye.webapp import ALLOWED_METHODS

    assert "get_artist_photos" in ALLOWED_METHODS
    shim = Path(ap.__file__).parents[1] / "frontend" / "web-shim.js"
    assert "'get_artist_photos'" in shim.read_text(encoding="utf-8")


def test_api_method_answers_from_the_cache(monkeypatch):
    from mornye.api_mixins.library_tools import LibraryToolsMixin

    monkeypatch.setattr(ap, "_spotify_search", lambda n: [_art(n, f"https://img/{n}")])
    assert LibraryToolsMixin.get_artist_photos(object(), ["Low"]) == {
        "Low": "https://img/Low"
    }
    assert LibraryToolsMixin.get_artist_photos(object(), "Low") == {}
