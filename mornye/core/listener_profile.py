"""core/listener_profile.py — the full listener profile of Mornye mobile 5.4.5.

What the listening record (core/listening.py) tells about its listener, as
the mobile's profile_overview_provider.dart reads it:

* all time: listening time, plays, songs, artists, active days, the longest
  and current streak, the biggest day, the song replayed most in one day;
* the last 30 days against the 30 before, with the artists first heard in
  them; the last 30 days one by one and the last 12 months;
* taste: genres, release decades, file quality and formats, by time heard;
* time of day and play-through rate, from the songs played through or
  skipped (`listening_sessions`);
* habits: listening by weekday, the average active day;
* top artists, albums and songs; the collection's counts;
* "listener types": up to five of the mobile's 22 traits, with the same
  names, thresholds and ranking (`traits`).

The download dashboard (core/stats.py) answers "what is in the library";
this answers "what do I listen to". Times are milliseconds, days
`YYYY-MM-DD` in local time.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from . import db, listening

HOUR = 3600 * 1000

#: Days the "last 30 days" window covers, today included.
RECENT_DAYS = 30

#: Months the monthly totals cover, this one included.
RECENT_MONTHS = 12

#: Played-through or skipped songs the time of day needs to say anything.
RHYTHM_ENOUGH = 20

#: A song replayed fewer times than this in one day is not highlighted.
MIN_REPLAYS = 3

TOP = 10

_SPLIT_RE = re.compile(r"\s*[;,/]\s*|\s+&\s+|\s+feat\.?\s+|\s+ft\.?\s+", re.IGNORECASE)

LOSSY = {"AAC", "EAC3", "AC3", "AC4", "MP3", "OPUS", "OGG"}
LOSSLESS = {"FLAC", "ALAC", "WAV", "AIFF", "AIF", "APE", "WV"}


def split_names(value: Any) -> list[str]:
    return [p.strip() for p in _SPLIT_RE.split(str(value or "")) if p.strip()]


def split_genres(value: Any) -> list[str]:
    return [p.strip() for p in re.split(r"\s*[;,/]\s*", str(value or "")) if p.strip()]


def quality_of(fmt: str, bit_depth: int, sample_rate: int) -> str | None:
    """hiRes, lossless or lossy, or None when nothing tells (as
    profileQualityOf: Hi-Res is lossless past 16 bits or 48 kHz)."""
    name = (fmt or "").upper()
    if name in LOSSY:
        return "lossy"
    if bit_depth > 0 or name in LOSSLESS:
        return "hiRes" if bit_depth > 16 or sample_rate > 48000 else "lossless"
    # An MP4 file without a bit depth holds AAC.
    return "lossy" if name == "M4A" else None


def daypart_of(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"


def _share(part: float, whole: float) -> float:
    return part / whole if whole > 0 else 0.0


# ── Windows ─────────────────────────────────────────────────────────────────


@dataclass
class Window:
    """One stretch of the record, summed (the mobile's ListeningRewind)."""

    milliseconds: int = 0
    plays: int = 0
    tracks: dict[str, dict] = field(default_factory=dict)
    artists: Counter = field(default_factory=Counter)
    days: Counter = field(default_factory=Counter)
    previous_milliseconds: int = 0
    new_artists: list[str] | None = None
    replay: dict | None = None

    @property
    def active_days(self) -> int:
        return sum(1 for v in self.days.values() if v > 0)

    @property
    def top_artists(self) -> list[tuple[str, int]]:
        return sorted(
            ((a, v) for a, v in self.artists.items() if v > 0),
            key=lambda e: (-e[1], e[0]),
        )

    @property
    def peak(self) -> tuple[str | None, int]:
        best, ms = None, 0
        for day in sorted(self.days):
            if self.days[day] > ms:
                best, ms = day, self.days[day]
        return best, ms

    @property
    def change_percent(self) -> int | None:
        if self.previous_milliseconds <= 0:
            return None
        return round(
            (self.milliseconds - self.previous_milliseconds)
            * 100
            / self.previous_milliseconds
        )

    def top_albums(self) -> list[dict]:
        albums: dict[tuple[str, str], dict] = {}
        for t in sorted(self.tracks.values(), key=lambda t: -t["milliseconds"]):
            album = (t.get("album") or "").strip()
            if not album:
                continue
            key = (album.lower(), (t.get("artist") or "").lower())
            known = albums.setdefault(
                key,
                {
                    "album": album,
                    "artist": t.get("artist", ""),
                    "path": t.get("path", ""),
                    "milliseconds": 0,
                    "plays": 0,
                },
            )
            known["milliseconds"] += t["milliseconds"]
            known["plays"] += t["plays"]
        return sorted(albums.values(), key=lambda a: -a["milliseconds"])

    def longest_streak(self) -> int:
        longest = streak = 0
        previous: date | None = None
        for text in sorted(d for d, v in self.days.items() if v > 0):
            day = date.fromisoformat(text)
            streak = (
                streak + 1 if previous and day == previous + timedelta(days=1) else 1
            )
            longest = max(longest, streak)
            previous = day
        return longest


def window(
    rows: list[dict],
    previous: list[dict] | None = None,
    before: list[dict] | None = None,
) -> Window:
    """`rows` summed. `previous` are the rows of the window before (for the
    change); `before` every row before the window, for the new artists
    (None when nothing was recorded before it: every artist would be new)."""
    w = Window()
    for r in rows:
        ms, plays = int(r.get("milliseconds") or 0), int(r.get("plays") or 0)
        w.milliseconds += ms
        w.plays += plays
        key = r["track_key"]
        t = w.tracks.get(key)
        if t is None:
            w.tracks[key] = {**r, "milliseconds": ms, "plays": plays}
        else:
            t["milliseconds"] += ms
            t["plays"] += plays
            for name in ("title", "artist", "album", "path", "genre", "format"):
                if r.get(name):
                    t[name] = r[name]
            for name in ("year", "bit_depth", "sample_rate"):
                t[name] = max(int(t.get(name) or 0), int(r.get(name) or 0))
        for name in split_names(r.get("artist")):
            w.artists[name] += ms
        w.days[r["day"]] += ms
        if plays and (w.replay is None or plays > w.replay["plays"]):
            w.replay = {
                "title": r.get("title", ""),
                "artist": r.get("artist", ""),
                "day": r["day"],
                "plays": plays,
            }
    if w.replay and w.replay["plays"] < MIN_REPLAYS:
        w.replay = None
    if previous is not None:
        w.previous_milliseconds = sum(int(r.get("milliseconds") or 0) for r in previous)
    if before:
        heard = {n for r in before for n in split_names(r.get("artist"))}
        w.new_artists = [a for a, _ in w.top_artists if a not in heard]
    return w


# ── Habits, sound, genres, rhythm ───────────────────────────────────────────


def _month_back(today: date, back: int) -> date:
    m = today.month - 1 - back
    return date(today.year + m // 12, m % 12 + 1, 1)


def habits(days: Counter, today: date) -> dict:
    """The streak up to today (yesterday before today's first song),
    listening by weekday (Monday first), the average active day, the last
    30 days and the last 12 months, oldest first (ProfileHabits)."""

    def time(d: date) -> int:
        return int(days.get(d.isoformat(), 0))

    last = today if time(today) > 0 else today - timedelta(days=1)
    streak = 0
    while time(last) > 0:
        streak += 1
        last -= timedelta(days=1)
    weekdays = [0] * 7
    months = [0] * RECENT_MONTHS
    total = active = 0
    for text, ms in days.items():
        if ms <= 0:
            continue
        d = date.fromisoformat(text)
        weekdays[d.weekday()] += ms
        total += ms
        active += 1
        back = (today.year - d.year) * 12 + today.month - d.month
        if 0 <= back < RECENT_MONTHS:
            months[RECENT_MONTHS - 1 - back] += ms
    return {
        "current_streak": streak,
        "weekdays": weekdays,
        "average_ms": total // active if active else 0,
        "recent": [
            time(today - timedelta(days=back))
            for back in range(RECENT_DAYS - 1, -1, -1)
        ],
        "months": months,
        "month_labels": [
            _month_back(today, back).strftime("%b %Y")
            for back in range(RECENT_MONTHS - 1, -1, -1)
        ],
    }


def sound(tracks: list[dict], today: date) -> dict:
    """Release decades, file quality and formats, by time heard."""
    latest = today.year + 1
    recent_from = latest - 2
    decades: Counter = Counter()
    quality: Counter = Counter()
    formats: Counter = Counter()
    dated = rated = recent = 0
    for t in tracks:
        ms = t["milliseconds"]
        year = int(t.get("year") or 0)
        if 1900 <= year <= latest:
            decades[year - year % 10] += ms
            dated += ms
            if year >= recent_from:
                recent += ms
        tier = quality_of(
            t.get("format", ""),
            int(t.get("bit_depth") or 0),
            int(t.get("sample_rate") or 0),
        )
        if tier:
            quality[tier] += ms
            rated += ms
            fmt = (t.get("format") or "").upper()
            formats[{"M4A": "AAC", "OPUS": "Opus"}.get(fmt, fmt)] += ms
    return {
        "decades": [{"decade": d, "milliseconds": decades[d]} for d in sorted(decades)],
        "decade_ms": dated,
        "recent_ms": recent,
        "quality": dict(quality),
        "quality_ms": rated,
        "formats": [{"name": n, "milliseconds": v} for n, v in formats.most_common()],
    }


def genres(tracks: list[dict]) -> tuple[list[dict], int]:
    """Genres by time heard; a song tagged with several counts in full
    under each; every genre takes its most heard spelling."""
    time: Counter = Counter()
    spellings: dict[str, Counter] = {}
    known = 0
    for t in tracks:
        names = split_genres(t.get("genre"))
        if not names:
            continue
        known += t["milliseconds"]
        for name in names:
            key = listening.normalize(name)
            time[key] += t["milliseconds"]
            spellings.setdefault(key, Counter())[name] += t["milliseconds"]
    out = [
        {
            "name": min(spellings[k].items(), key=lambda e: (-e[1], e[0]))[0],
            "milliseconds": v,
        }
        for k, v in time.items()
    ]
    out.sort(key=lambda g: (-g["milliseconds"], g["name"]))
    return out, known


def rhythm(sessions: list[dict]) -> dict:
    """The hours songs were played through at, and the play-through rate
    (ProfileRhythm)."""
    hours = [0] * 24
    through = skipped = 0
    for s in sessions:
        if s.get("outcome") == "listened":
            hours[int(s.get("hour") or 0)] += 1
            through += 1
        elif s.get("outcome") == "skipped":
            skipped += 1
    parts = {p: 0 for p in ("morning", "afternoon", "evening", "night")}
    for h, n in enumerate(hours):
        parts[daypart_of(h)] += n
    daypart = max(parts, key=lambda p: parts[p]) if through else None
    peak = max(range(24), key=lambda h: (hours[h], -h)) if through else None
    return {
        "hours": hours,
        "played_through": through,
        "skipped": skipped,
        "enough": through + skipped >= RHYTHM_ENOUGH,
        "through_share": _share(through, through + skipped),
        "dayparts": parts,
        "daypart": daypart,
        "peak_hour": peak,
    }


# ── Listener types ──────────────────────────────────────────────────────────

#: The mobile's ProfileTrait values, in its order (ties keep it).
TRAITS = (
    "onAStreak",
    "dailyRitual",
    "marathon",
    "onTheRise",
    "audiophile",
    "losslessPurist",
    "nightOwl",
    "earlyBird",
    "daytime",
    "sunset",
    "weekender",
    "workday",
    "loyal",
    "devotee",
    "explorer",
    "songHunter",
    "eclectic",
    "throwback",
    "freshEars",
    "onRepeat",
    "patient",
    "quickSkipper",
)


def _pct(share: float) -> str:
    return f"{round(share * 100)}%"


def duration_text(ms: int) -> str:
    minutes = round(ms / 60000)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if hours else f"{minutes} min"


def _count(n: int, one: str, many: str) -> str:
    return one if n == 1 else many.format(n=n)


def trait_text(fact: dict) -> tuple[str, str]:
    """The trait's name and its line, as the mobile's English strings."""
    t, count, subject = fact["trait"], fact["count"], fact["subject"]
    p = _pct(fact["share"])
    texts = {
        "onAStreak": (
            "On a streak",
            _count(count, "1 day in a row with music", "{n} days in a row with music"),
        ),
        "dailyRitual": ("Daily ritual", f"Music on {p} of days since you started"),
        "marathon": (
            "Marathon listener",
            f"{duration_text(count)} of music in a single day",
        ),
        "onTheRise": ("On the rise", f"{p} more listening than the 30 days before"),
        "audiophile": ("Audiophile", f"{p} of your listening is Hi-Res"),
        "losslessPurist": ("Lossless purist", f"{p} of your listening is lossless"),
        "nightOwl": ("Night owl", f"{p} of your songs play late at night"),
        "earlyBird": ("Early bird", f"{p} of your songs play in the morning"),
        "daytime": ("Daytime listener", f"{p} of your songs play in the afternoon"),
        "sunset": ("Sunset listener", f"{p} of your songs play in the evening"),
        "weekender": (
            "Weekend listener",
            f"{p} of your listening falls on Saturday and Sunday",
        ),
        "workday": ("Workday soundtrack", f"{p} of your listening falls on weekdays"),
        "loyal": ("Loyal fan", f"{subject} takes {p} of your listening"),
        "devotee": (f"{subject} at heart", f"{p} of your listening is {subject}"),
        "explorer": (
            "Explorer",
            _count(
                count,
                "1 new artist in the last 30 days",
                "{n} new artists in the last 30 days",
            ),
        ),
        "songHunter": (
            "Song hunter",
            _count(
                count,
                "1 different song in the last 30 days",
                "{n} different songs in the last 30 days",
            ),
        ),
        "eclectic": ("Eclectic", f"{count} genres, the biggest at {p}"),
        "throwback": ("Throwback lover", f"{p} of your listening came out before 2000"),
        "freshEars": (
            "Fresh ears",
            f"{p} of your listening came out in the last two years",
        ),
        "onRepeat": ("On repeat", f"{subject}, {count} times in one day"),
        "patient": ("Lets it play", f"{p} of your songs play without a skip"),
        "quickSkipper": ("Quick skipper", f"You skip {p} of your songs"),
    }
    return texts[t]


def traits(
    *,
    all_time: Window,
    month: Window,
    hab: dict,
    snd: dict,
    rh: dict,
    genre_list: list[dict],
    genre_ms: int,
    since: str | None,
    today: date,
    limit: int = 5,
) -> list[dict]:
    """The traits the record bears out, at most `limit`, the most marked
    first: profileTraits of profile_overview_provider.dart, rule by rule.
    A trait holds from its threshold; its rank grows from 1 there to 2 at
    `full`, so traits with different measures compare fairly."""
    found: list[tuple[dict, float]] = []

    def add(
        trait: str,
        value: float,
        *,
        threshold: float,
        full: float,
        share: float = 0.0,
        count: int = 0,
        subject: str = "",
    ) -> None:
        if value < threshold:
            return
        rank = 1 + min(1.0, max(0.0, (value - threshold) / (full - threshold)))
        found.append(
            (
                {
                    "trait": trait,
                    "share": share,
                    "count": int(count),
                    "subject": subject,
                },
                rank,
            )
        )

    add(
        "onAStreak",
        hab["current_streak"],
        threshold=7,
        full=30,
        count=hab["current_streak"],
    )
    if since:
        span = (today - date.fromisoformat(since)).days + 1
        if span >= 30:
            daily = min(all_time.active_days / span, 1.0)
            add("dailyRitual", daily, threshold=0.8, full=1, share=daily)
    _, peak_ms = all_time.peak
    add("marathon", peak_ms, threshold=5 * HOUR, full=12 * HOUR, count=peak_ms)
    change = month.change_percent
    if change is not None and month.milliseconds >= 2 * HOUR:
        add("onTheRise", change, threshold=50, full=200, share=change / 100)

    if snd["quality_ms"] >= HOUR:
        hi = _share(snd["quality"].get("hiRes", 0), snd["quality_ms"])
        lossless = hi + _share(snd["quality"].get("lossless", 0), snd["quality_ms"])
        if hi >= 0.5:
            add("audiophile", hi, threshold=0.5, full=1, share=hi)
        else:
            add("losslessPurist", lossless, threshold=0.95, full=1, share=lossless)

    if rh["enough"] and rh["daypart"]:
        busiest = rh["daypart"]
        share = rh["dayparts"][busiest] / rh["played_through"]
        # Afternoon and evening are long or common, so they need more.
        trait, threshold = {
            "night": ("nightOwl", 0.35),
            "morning": ("earlyBird", 0.35),
            "afternoon": ("daytime", 0.45),
            "evening": ("sunset", 0.45),
        }[busiest]
        add(trait, share, threshold=threshold, full=0.8, share=share)
        through = rh["through_share"]
        add("patient", through, threshold=0.9, full=1, share=through)
        add("quickSkipper", 1 - through, threshold=0.4, full=0.8, share=1 - through)

    week = sum(hab["weekdays"])
    if week >= 2 * HOUR:
        # Two days of seven are 29% of an even week.
        weekend = (hab["weekdays"][5] + hab["weekdays"][6]) / week
        add("weekender", weekend, threshold=0.45, full=0.8, share=weekend)
        add("workday", 1 - weekend, threshold=0.88, full=1, share=1 - weekend)

    artists = all_time.top_artists
    artist_time = sum(v for _, v in artists)
    if len(artists) >= 3 and artist_time > 0:
        share = artists[0][1] / artist_time
        add(
            "loyal", share, threshold=0.25, full=0.7, share=share, subject=artists[0][0]
        )
    if genre_list and genre_ms > 0:
        top = genre_list[0]["milliseconds"] / genre_ms
        add(
            "devotee",
            top,
            threshold=0.6,
            full=1,
            share=top,
            subject=genre_list[0]["name"],
        )
        if top < 0.3:
            add(
                "eclectic",
                len(genre_list),
                threshold=8,
                full=24,
                share=top,
                count=len(genre_list),
            )
    new_artists = len(month.new_artists or [])
    add("explorer", new_artists, threshold=10, full=50, count=new_artists)
    month_songs = len(month.tracks)
    add("songHunter", month_songs, threshold=200, full=800, count=month_songs)

    if snd["decade_ms"] >= HOUR:
        old = _share(
            sum(d["milliseconds"] for d in snd["decades"] if d["decade"] < 2000),
            snd["decade_ms"],
        )
        fresh = _share(snd["recent_ms"], snd["decade_ms"])
        add("throwback", old, threshold=0.4, full=0.9, share=old)
        add("freshEars", fresh, threshold=0.4, full=0.9, share=fresh)

    replay = all_time.replay
    if replay is not None:
        add(
            "onRepeat",
            replay["plays"],
            threshold=8,
            full=40,
            count=replay["plays"],
            subject=replay["title"],
        )

    # Equals keep the trait order.
    found.sort(key=lambda e: (-e[1], TRAITS.index(e[0]["trait"])))
    out = []
    for fact, _ in found[:limit]:
        name, text = trait_text(fact)
        out.append({**fact, "name": name, "text": text})
    return out


# ── Collection ──────────────────────────────────────────────────────────────


def collection(owner: str) -> dict:
    """What the Library holds: songs, albums, artists, genres, playlists,
    total length, and the downloads Mornye made."""
    from . import player_library

    songs = 0
    album_keys: set[tuple[str, str]] = set()
    artist_keys: set[str] = set()
    genre_keys: set[str] = set()
    seconds = 0.0
    for _path, _size, row in player_library.all_rows():
        songs += 1
        seconds += float(row.get("duration_s") or 0)
        artist = (row.get("album_artist") or row.get("artist") or "").strip().lower()
        if row.get("album"):
            album_keys.add((artist, str(row["album"]).strip().lower()))
        for name in split_names(row.get("artist")):
            artist_keys.add(name.lower())
        for g in split_genres(row.get("genre")):
            genre_keys.add(listening.normalize(g))
    playlists = downloads = 0
    try:
        conn = db.connection()
        playlists = int(
            conn.execute(
                "SELECT COUNT(*) FROM player_playlists WHERE owner = ?", (owner or "",)
            ).fetchone()[0]
        )
        sql = "SELECT COUNT(*) FROM downloads WHERE success = 1"
        params: tuple[str, ...] = ()
        if owner:
            sql, params = sql + " AND owner = ?", (owner,)
        downloads = int(conn.execute(sql, params).fetchone()[0])
    except Exception:
        pass
    return {
        "songs": songs,
        "albums": len(album_keys),
        "artists": len(artist_keys),
        "genres": len(genre_keys),
        "playlists": playlists,
        "downloads": downloads,
        "duration_ms": int(seconds * 1000),
    }


# ── Everything ──────────────────────────────────────────────────────────────


def _track_out(t: dict) -> dict:
    return {
        "title": t.get("title", ""),
        "artist": t.get("artist", ""),
        "album": t.get("album", ""),
        "path": t.get("path", ""),
        "milliseconds": t["milliseconds"],
        "plays": t["plays"],
    }


def _summary(w: Window) -> dict:
    return {
        "milliseconds": w.milliseconds,
        "plays": w.plays,
        "songs": len(w.tracks),
        "artists": len([a for a, v in w.artists.items() if v > 0]),
        "active_days": w.active_days,
    }


def profile(
    owner: str, *, now: datetime | None = None, with_collection: bool = True
) -> dict:
    """The whole profile of `owner` for the day of `now`."""
    now = now or datetime.now()
    today = now.date()
    end = (today + timedelta(days=1)).isoformat()
    month_start = (today - timedelta(days=RECENT_DAYS - 1)).isoformat()
    prev_start = (today - timedelta(days=2 * RECENT_DAYS - 1)).isoformat()
    every = [r for r in listening.rows(owner) if r["day"] < end]
    all_w = window(every)
    month_rows = [r for r in every if r["day"] >= month_start]
    before = [r for r in every if r["day"] < month_start]
    month_w = window(
        month_rows, [r for r in before if r["day"] >= prev_start], before or None
    )
    tracks = sorted(all_w.tracks.values(), key=lambda t: -t["milliseconds"])
    # Past these a song adds too little to move a share.
    genre_list, genre_ms = genres(tracks[:800])
    days_heard = sorted(d for d, v in all_w.days.items() if v > 0)
    since = days_heard[0] if days_heard else None
    peak_day, peak_ms = all_w.peak
    hab = habits(all_w.days, today)
    snd = sound(tracks, today)
    rh = rhythm(listening.sessions(owner))
    favorite = (
        max(range(7), key=lambda i: hab["weekdays"][i])
        if any(hab["weekdays"])
        else None
    )
    empty = all_w.milliseconds <= 0
    out: dict[str, Any] = {
        "empty": empty,
        "since": since,
        "all_time": {
            **_summary(all_w),
            "longest_streak": all_w.longest_streak(),
            "current_streak": hab["current_streak"],
            "peak_day": peak_day,
            "peak_day_ms": peak_ms,
            "favorite_weekday": favorite,
            "replay": all_w.replay,
        },
        "last_30_days": {
            **_summary(month_w),
            "previous_ms": month_w.previous_milliseconds,
            "change_percent": month_w.change_percent,
            "new_artists": (month_w.new_artists or [])[:TOP],
            "new_artist_count": len(month_w.new_artists or []),
            "top_artists": [
                {"name": a, "milliseconds": v} for a, v in month_w.top_artists[:5]
            ],
        },
        "habits": hab,
        "genres": genre_list[: TOP * 2],
        "genre_count": len(genre_list),
        "genre_ms": genre_ms,
        "sound": snd,
        "rhythm": rh,
        "top_artists": [
            {"name": a, "milliseconds": v} for a, v in all_w.top_artists[:TOP]
        ],
        "top_albums": all_w.top_albums()[:TOP],
        "top_tracks": [_track_out(t) for t in tracks[:TOP]],
        "traits": (
            []
            if empty
            else traits(
                all_time=all_w,
                month=month_w,
                hab=hab,
                snd=snd,
                rh=rh,
                genre_list=genre_list,
                genre_ms=genre_ms,
                since=since,
                today=today,
            )
        ),
    }
    if with_collection:
        out["collection"] = collection(owner)
    return out
