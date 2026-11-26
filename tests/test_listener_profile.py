"""The full listener profile (core/listener_profile.py), ported from the
profile of Mornye mobile 5.4.5 (profile_overview_provider.dart)."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta

from mornye.core import listener_profile as lp, listening

TODAY = datetime(2026, 11, 26, 12)
H = lp.HOUR


def _at(days_ago: int, hour: int = 12) -> float:
    d = TODAY.date() - timedelta(days=days_ago)
    return datetime(d.year, d.month, d.day, hour).timestamp()


def _play(days_ago, title, artist, ms, *, plays=1, hour=12, **extra):
    listening.record(
        "",
        {"title": title, "artist": artist, "ms": ms, **extra},
        at=_at(days_ago, hour),
    )
    for _ in range(plays):
        listening.record(
            "", {"title": title, "artist": artist, "play": True}, at=_at(days_ago, hour)
        )


def test_empty_record():
    out = lp.profile("", now=TODAY)
    assert out["empty"] and out["traits"] == [] and out["since"] is None
    assert out["collection"]["songs"] == 0


def test_totals_streaks_and_windows():
    for d in (0, 1, 2, 5, 6, 40):
        _play(d, f"S{d}", "A", 10 * 60000)
    _play(0, "S0", "A", 0, plays=2)
    out = lp.profile("", now=TODAY)
    at = out["all_time"]
    assert at["milliseconds"] == 60 * 60000
    assert at["active_days"] == 6
    assert at["current_streak"] == 3
    assert at["longest_streak"] == 3
    assert at["songs"] == 6
    assert at["replay"]["plays"] == 3 and at["replay"]["title"] == "S0"
    m = out["last_30_days"]
    assert m["milliseconds"] == 50 * 60000
    assert m["previous_ms"] == 10 * 60000
    assert m["change_percent"] == 400
    assert out["since"] == (TODAY.date() - timedelta(days=40)).isoformat()
    assert (
        len(out["habits"]["recent"]) == 30 and out["habits"]["recent"][-1] == 10 * 60000
    )
    assert len(out["habits"]["months"]) == 12


def test_streak_survives_a_day_that_has_only_begun():
    _play(1, "x", "A", 60000)
    _play(2, "y", "A", 60000)
    assert lp.profile("", now=TODAY)["all_time"]["current_streak"] == 2


def test_new_artists_need_something_recorded_before():
    _play(1, "x", "New", 60000)
    assert lp.profile("", now=TODAY)["last_30_days"]["new_artist_count"] == 0
    _play(45, "y", "Old", 60000)
    _play(2, "z", "Old", 60000)
    assert lp.profile("", now=TODAY)["last_30_days"]["new_artists"] == ["New"]


def test_quality_tiers_follow_the_mobile():
    assert lp.quality_of("FLAC", 24, 96000) == "hiRes"
    assert lp.quality_of("FLAC", 16, 44100) == "lossless"
    assert lp.quality_of("FLAC", 0, 0) == "lossless"
    assert lp.quality_of("MP3", 0, 0) == "lossy"
    assert lp.quality_of("M4A", 0, 0) == "lossy"
    assert lp.quality_of("M4A", 24, 48000) == "hiRes"
    assert lp.quality_of("", 0, 0) is None


def test_genres_take_their_most_heard_spelling():
    tracks = [
        {"genre": "Hip-Hop; Rap", "milliseconds": 10},
        {"genre": "hip-hop", "milliseconds": 3},
        {"genre": "", "milliseconds": 50},
    ]
    found, known = lp.genres(tracks)
    assert known == 13
    assert found[0] == {"name": "Hip-Hop", "milliseconds": 13}
    assert found[1] == {"name": "Rap", "milliseconds": 10}


def test_rhythm_dayparts_and_play_through():
    sessions = [{"outcome": "listened", "hour": 23}] * 18 + [
        {"outcome": "skipped", "hour": 10}
    ] * 2
    rh = lp.rhythm(sessions)
    assert rh["enough"] and rh["daypart"] == "night" and rh["peak_hour"] == 23
    assert rh["through_share"] == 0.9
    assert lp.daypart_of(4) == "night" and lp.daypart_of(5) == "morning"
    assert lp.daypart_of(17) == "evening" and lp.daypart_of(21) == "night"


def _snd(quality=None, quality_ms=0, decades=(), decade_ms=0, recent_ms=0):
    return {
        "quality": quality or {},
        "quality_ms": quality_ms,
        "decades": list(decades),
        "decade_ms": decade_ms,
        "recent_ms": recent_ms,
    }


def _base(**over):
    args = dict(
        all_time=lp.Window(),
        month=lp.Window(),
        hab={"current_streak": 0, "weekdays": [0] * 7},
        snd=_snd(),
        rh=lp.rhythm([]),
        genre_list=[],
        genre_ms=0,
        since=None,
        today=TODAY.date(),
    )
    args.update(over)
    return args


def _names(found):
    return [t["trait"] for t in found]


def test_traits_thresholds_match_the_dart_source():
    # On a streak from 7 days.
    assert (
        _names(lp.traits(**_base(hab={"current_streak": 6, "weekdays": [0] * 7}))) == []
    )
    found = lp.traits(**_base(hab={"current_streak": 7, "weekdays": [0] * 7}))
    assert _names(found) == ["onAStreak"]
    assert (
        found[0]["name"] == "On a streak"
        and found[0]["text"] == "7 days in a row with music"
    )
    # Audiophile from half Hi-Res, else lossless purist from 95% lossless.
    snd = _snd({"hiRes": 3 * H, "lossy": H}, 4 * H)
    assert _names(lp.traits(**_base(snd=snd))) == ["audiophile"]
    snd = _snd({"lossless": int(0.96 * H), "lossy": int(0.04 * H)}, H)
    assert _names(lp.traits(**_base(snd=snd))) == ["losslessPurist"]
    # Weekend listener from 45% of a week of at least two hours.
    found = lp.traits(
        **_base(hab={"current_streak": 0, "weekdays": [H, 0, 0, 0, 0, 2 * H, 0]})
    )
    assert _names(found) == ["weekender"]
    assert found[0]["text"] == "67% of your listening falls on Saturday and Sunday"


def test_traits_rank_by_how_marked_and_keep_five():
    w = lp.window(
        [
            {
                "day": "2026-11-20",
                "track_key": f"k{i}",
                "title": f"t{i}",
                "artist": a,
                "milliseconds": ms,
                "plays": 20 if i == 0 else 1,
            }
            for i, (a, ms) in enumerate([("Big", 10 * H), ("B", H), ("C", H)])
        ]
    )
    genres = [
        {"name": "Jazz", "milliseconds": 9 * H},
        {"name": "Pop", "milliseconds": H},
    ]
    snd = _snd(
        {"hiRes": 12 * H}, 12 * H, [{"decade": 1950, "milliseconds": 12 * H}], 12 * H
    )
    hab = {"current_streak": 30, "weekdays": [0, 0, 0, 0, 0, 6 * H, 6 * H]}
    found = lp.traits(
        **_base(all_time=w, snd=snd, hab=hab, genre_list=genres, genre_ms=10 * H)
    )
    assert len(found) == 5
    # Fully marked traits (rank 2) lead, in the mobile's order on ties.
    assert _names(found)[:4] == ["onAStreak", "marathon", "audiophile", "weekender"]
    every = lp.traits(**_base(all_time=w), limit=22)
    assert any(t["trait"] == "loyal" and t["subject"] == "Big" for t in every)
    assert any(t["trait"] == "onRepeat" and t["count"] == 20 for t in every)


def test_all_twenty_two_traits_have_texts():
    assert len(lp.TRAITS) == 22
    for t in lp.TRAITS:
        name, text = lp.trait_text(
            {"trait": t, "share": 0.5, "count": 3, "subject": "X"}
        )
        assert name and text


def test_profile_end_to_end_reads_sessions_and_sound():
    for d in range(10):
        _play(
            d,
            f"Song {d}",
            "Night Band",
            40 * 60000,
            hour=23,
            format="FLAC",
            quality="FLAC 24-bit / 96 kHz",
            year=2025,
            genre="Jazz",
        )
        for _ in range(3):
            listening.record(
                "",
                {"title": f"Song {d}", "artist": "Night Band", "outcome": "listened"},
                at=_at(d, 23),
            )
    out = lp.profile("", now=TODAY)
    assert out["rhythm"]["played_through"] == 30
    assert out["sound"]["quality"]["hiRes"] == 400 * 60000
    assert out["genres"][0]["name"] == "Jazz"
    names = _names(out["traits"])
    assert "audiophile" in names and "nightOwl" in names
    # A 10-day streak holds but ranks below these, past the first five.
    assert "onAStreak" not in names
    assert out["top_artists"][0]["name"] == "Night Band"
    assert len(out["traits"]) <= 5


def test_habits_months_roll_over_the_year():
    hab = lp.habits(Counter({"2026-01-15": 5, "2025-12-31": 7}), date(2026, 1, 20))
    assert hab["months"][-1] == 5 and hab["months"][-2] == 7
    assert (
        hab["month_labels"][-1] == "Jan 2026" and hab["month_labels"][-2] == "Dec 2025"
    )
