"""core/personal_home.py — the music look's personal Home, as on Mornye mobile.

Home in Mornye mobile 5.4.5 is made from the listener's own record: a
"Recommended for you" shelf of songs outside the Library, mixes of Library
songs (Deep Cuts, the year's songs, a different artist each day…), Quick
Play (nine Library songs one tap from playing), what the listener pinned,
"Finish your collection" and "On repeat". The person can reorder and hide
the sections (Edit Home). This module computes all of it from:

* the Player library (`player_library`, core/player_library.py),
* the listening record (core/listening.py),
* the failed downloads, the playlists' songs not downloaded yet and the
  download log, for "Finish your collection" and the status line.

Everything is per owner (`--web` multi-user) and stored in the `meta` table:
the layout, the pins and the day's recommendations. The same request on the
same day gives the same mixes and Quick Play picks (`stable_hash` seeds
them), so Home holds still while browsing and changes the next day.
"""

from __future__ import annotations

import logging
import random
import zlib
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta
from typing import Any

from . import db, listening

logger = logging.getLogger(__name__)

# ── Layout (Edit Home) ──────────────────────────────────────────────────────

#: Sections in the order Home ships. Saved layouts store the names.
SECTIONS = (
    "topPicks",
    "recentlyPlayed",
    "quickPlay",
    "pins",
    "mixes",
    "recentlyAdded",
    "unfinished",
    "recommended",
    "onRepeat",
)

#: Sections that ship turned off; Edit Home turns them on.
OFF_BY_DEFAULT = frozenset({"unfinished", "onRepeat"})

SECTION_NAMES = {
    "topPicks": "Top picks",
    "recentlyPlayed": "Recently played",
    "quickPlay": "Quick play",
    "pins": "Pinned",
    "mixes": "Mixes for you",
    "recentlyAdded": "Recently added",
    "unfinished": "Finish your collection",
    "recommended": "Recommended for you",
    "onRepeat": "On repeat",
}

SECTION_ABOUT = {
    "topPicks": "Today's mixes and playlists from your recommendations source",
    "recentlyPlayed": "Albums you played lately",
    "quickPlay": "Songs you play most and the day's picks from your Library, one tap from playing",
    "pins": "Albums, playlists, artists, genres and folders you pinned",
    "mixes": "Daily mixes of your own songs",
    "recentlyAdded": "The newest songs in your Library",
    "unfinished": "Playlist songs not downloaded, albums missing songs and failed downloads",
    "recommended": "Songs outside your Library, picked from what you play and add",
    "onRepeat": "Your most played songs, or Library albums while nothing is recorded",
}


def _names(value: Any) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def read_layout(value: Any) -> dict:
    """A saved layout, anything unreadable left out."""
    data = value if isinstance(value, dict) else {}
    return {
        "order": _names(data.get("order")),
        "hidden": sorted(set(_names(data.get("hidden")))),
        "shown": sorted(set(_names(data.get("shown")))),
    }


def layout_sections(layout: dict) -> list[str]:
    """Every section in the person's order. A section the saved order does
    not name goes after the section it follows in the shipped order."""
    ordered: list[str] = []
    for name in layout.get("order") or []:
        if name in SECTIONS and name not in ordered:
            ordered.append(name)
    for index, section in enumerate(SECTIONS):
        if section in ordered:
            continue
        at = 0
        for before in range(index - 1, -1, -1):
            if SECTIONS[before] in ordered:
                at = ordered.index(SECTIONS[before]) + 1
                break
        ordered.insert(at, section)
    return ordered


def layout_shows(layout: dict, section: str) -> bool:
    if section in OFF_BY_DEFAULT:
        return section in (layout.get("shown") or [])
    return section not in (layout.get("hidden") or [])


def layout_with(layout: dict, order: list[str] | None, visible: dict | None) -> dict:
    """`layout` with a new order and/or visibility changes ({name: bool})."""
    out = read_layout(layout)
    if order is not None:
        out["order"] = layout_sections({"order": _names(order)})
    for name, on in (visible or {}).items():
        if name not in SECTIONS:
            continue
        if name in OFF_BY_DEFAULT:
            shown = set(out["shown"])
            if on:
                shown.add(name)
            else:
                shown.discard(name)
            out["shown"] = sorted(shown)
        else:
            hidden = set(out["hidden"])
            if on:
                hidden.discard(name)
            else:
                hidden.add(name)
            out["hidden"] = sorted(hidden)
    return out


def layout_view(layout: dict) -> list[dict]:
    """The sections for Edit Home: name, title, about and whether shown."""
    return [
        {
            "id": s,
            "title": SECTION_NAMES[s],
            "about": SECTION_ABOUT[s],
            "shown": layout_shows(layout, s),
        }
        for s in layout_sections(layout)
    ]


def _meta_key(kind: str, owner: str) -> str:
    return f"home_{kind}:{owner or ''}"


def load_layout(owner: str) -> dict:
    return read_layout(db.loads(db.get_meta(_meta_key("layout", owner)), {}))


def save_layout(owner: str, layout: dict) -> dict:
    clean = read_layout(layout)
    db.set_meta(_meta_key("layout", owner), db.dumps(clean))
    return clean


# ── Pins ────────────────────────────────────────────────────────────────────

PIN_KINDS = ("album", "playlist", "artist", "genre", "folder")

#: How many pins Home holds.
PIN_LIMIT = 12


def pin_key(pin: dict) -> str:
    return f"{pin.get('kind')}:{pin.get('id')}"


def read_pin(value: Any) -> dict | None:
    """A saved pin, or None for one this version cannot open."""
    if not isinstance(value, dict):
        return None
    kind, pid, title = value.get("kind"), value.get("id"), value.get("title")
    if kind not in PIN_KINDS or not isinstance(pid, str) or not pid:
        return None
    if not isinstance(title, str):
        return None
    pin = {"kind": kind, "id": pid, "title": title}
    for name in ("subtitle", "cover_path"):
        text = value.get(name)
        if isinstance(text, str) and text:
            pin[name] = text
    return pin


def decode_pins(value: Any) -> list[dict]:
    """The pins in `value`, without unknown kinds, repeats or those past
    PIN_LIMIT."""
    pins: list[dict] = []
    seen: set[str] = set()
    for entry in value if isinstance(value, list) else []:
        pin = read_pin(entry)
        if pin is None or pin_key(pin) in seen:
            continue
        seen.add(pin_key(pin))
        pins.append(pin)
        if len(pins) == PIN_LIMIT:
            break
    return pins


def load_pins(owner: str) -> list[dict]:
    return decode_pins(db.loads(db.get_meta(_meta_key("pins", owner)), []))


def _save_pins(owner: str, pins: list[dict]) -> None:
    db.set_meta(_meta_key("pins", owner), db.dumps(pins))


def add_pin(owner: str, pin: dict) -> tuple[bool, list[dict]]:
    """Pins `pin` after the others. False when Home already holds the limit
    (a pin already there is True)."""
    clean = read_pin(pin)
    pins = load_pins(owner)
    if clean is None:
        return False, pins
    if any(pin_key(p) == pin_key(clean) for p in pins):
        return True, pins
    if len(pins) >= PIN_LIMIT:
        return False, pins
    pins.append(clean)
    _save_pins(owner, pins)
    return True, pins


def remove_pin(owner: str, key: str) -> list[dict]:
    pins = [p for p in load_pins(owner) if pin_key(p) != key]
    _save_pins(owner, pins)
    return pins


def move_pin(owner: str, key: str, to: int = 0) -> list[dict]:
    pins = load_pins(owner)
    found = next((p for p in pins if pin_key(p) == key), None)
    if found is None:
        return pins
    pins.remove(found)
    pins.insert(max(0, min(len(pins), int(to))), found)
    _save_pins(owner, pins)
    return pins


# ── The Library and the record ──────────────────────────────────────────────


def stable_hash(text: str) -> int:
    """The same number for the same text on every run (unlike hash())."""
    return zlib.crc32(text.encode("utf-8"))


def song(row: dict) -> dict:
    """What Home sends of one Library song."""
    return {
        "path": row.get("path", ""),
        "title": row.get("title", "") or "",
        "artist": row.get("artist", "") or "",
        "album": row.get("album", "") or "",
        "album_artist": row.get("album_artist", "") or "",
        "duration_s": row.get("duration_s", 0) or 0,
        "year": row.get("year", "") or "",
        "genre": row.get("genre", "") or "",
        "track": row.get("track", 0) or 0,
        "disc": row.get("disc", 0) or 0,
    }


def library_songs() -> list[dict]:
    """Every Player library row, with its path."""
    from . import player_library

    out = []
    for path, _size, row in player_library.all_rows():
        out.append({**row, "path": row.get("path") or path})
    return out


def artist_of(row: dict) -> str:
    return (
        row.get("album_artist") or row.get("artist") or ""
    ).strip() or "Unknown artist"


def album_key(row: dict) -> str:
    """As the Player's libAlbumKey spells it: album artist, then album."""
    return f"{artist_of(row).lower()} /// {(row.get('album') or '').strip().lower()}"


def first_artist(name: str) -> str:
    return listening.track_key("x", name).split("|", 1)[1] if name else ""


def genres_of(row: dict) -> list[str]:
    raw = str(row.get("genre") or "")
    return [
        p.strip()
        for p in raw.replace("/", ";").replace(",", ";").split(";")
        if p.strip()
    ]


def first_genre(row: dict) -> str:
    found = genres_of(row)
    return found[0] if found else ""


def _year(row: dict) -> int:
    text = str(row.get("year") or "")[:4]
    return int(text) if text.isdigit() else 0


def play_counts(
    owner: str, now: datetime, days: int, before_days: int = 0
) -> dict[str, float]:
    """Plays per track key in the `days` days ending `before_days` ago."""
    end = now.date() - timedelta(days=before_days)
    until = (end + timedelta(days=1)).isoformat()
    since = (end - timedelta(days=days - 1)).isoformat()
    out: dict[str, float] = {}
    for r in listening.rows(owner, since, until):
        weight = (r.get("plays") or 0) or (
            1 if (r.get("milliseconds") or 0) >= 30000 else 0
        )
        if weight:
            out[r["track_key"]] = out.get(r["track_key"], 0) + weight
    return out


# ── Quick Play ──────────────────────────────────────────────────────────────

QUICK_COUNT = 9


def quick_play(
    library: list[dict], played: list[dict], day: str, limit: int = QUICK_COUNT
) -> list[dict]:
    """Up to `limit` songs: `played` first (most played Library songs), then
    the Library in the day's order, one song per album while albums are
    left. Each song has its own place in that order, so a Library change
    swaps a tile, not the grid."""

    def album(s: dict) -> str:
        return album_key(s) if (s.get("album") or "").strip() else s.get("path", "")

    picks: dict[str, dict] = {}
    for s in played:
        if len(picks) == limit:
            break
        picks.setdefault(s["path"], s)
    rest = sorted(
        (s for s in library if s["path"] not in picks),
        key=lambda s: (stable_hash(f"{day}|{s['path']}"), s["path"]),
    )
    albums = {album(s) for s in picks.values()}
    repeats = []
    for s in rest:
        if len(picks) == limit:
            break
        if album(s) not in albums:
            albums.add(album(s))
            picks[s["path"]] = s
        else:
            repeats.append(s)
    for s in repeats:
        if len(picks) == limit:
            break
        picks.setdefault(s["path"], s)
    return list(picks.values())


# ── Mixes ───────────────────────────────────────────────────────────────────

MIX_MIN_LIBRARY = 30
MIX_MAX = 12
MIX_MIN_SONGS = 20
MIX_TARGET = 40
ARTIST_SHARE = 0.2
MAX_OVERLAP = 0.6
MAX_GENRE_MIXES = 5
LEAD_GENRE_MIXES = 3
MAX_DECADE_MIXES = 2
MAX_ARTIST_MIXES = 2
MIN_ANCHOR_SONGS = 5
ANCHOR_CHOICES = 4
DEEP_CUT_ARTISTS = 10
FRESH_DAYS = 30
MIN_GENRE_CORE = 12
MIN_DECADE_CORE = 25
FAVORITES_MIN_PLAYED = 8

DISCOVERIES = frozenset({"fresh", "rediscover", "discover", "deepCuts"})


def mix_title(mix: dict) -> str:
    kind, label = mix["kind"], mix.get("label", "")
    return {
        "favorites": "Favorites Mix",
        "genre": f"{label} Mix",
        "artist": f"{label} Mix",
        "decade": f"{label}s Mix",
        "fresh": "New Additions Mix",
        "rediscover": "Rediscover Mix",
        "discover": "Discover Mix",
        "year": f"{label} Mix",
        "deepCuts": "Deep Cuts Mix",
    }.get(kind, "Mix")


def mix_subtitle(artists: list[str]) -> str:
    if not artists:
        return ""
    if len(artists) == 1:
        return artists[0]
    if len(artists) == 2:
        return f"{artists[0]} and {artists[1]}"
    return f"{artists[0]}, {artists[1]} and more"


class _Mixes:
    """Builds the day's mixes, as library_mixes.dart does, with a simpler
    song picker: the mix opens on its best seed, then takes the pool in an
    order of plays and the day, spacing artists and capping each at a fifth
    of the mix."""

    def __init__(
        self,
        library: list[dict],
        plays: dict[str, float],
        past_plays: dict[str, float],
        now: datetime,
    ) -> None:
        self.now = now
        self.day = now.date().isoformat()
        self.plays_by_key = plays
        self.past_by_key = past_plays
        self.songs: list[dict] = []
        seen: set[str] = set()
        for row in library:
            if not row.get("path") or row["path"] in seen:
                continue
            seen.add(row["path"])
            self.songs.append(row)
        self.keys = {
            s["path"]: listening.track_key(s.get("title"), s.get("artist"))
            for s in self.songs
        }
        self.genre_songs: dict[str, list[dict]] = {}
        self.genre_labels: dict[str, Counter] = {}
        self.artist_songs: dict[str, list[dict]] = {}
        self.artist_names: dict[str, str] = {}
        self.decade_songs: dict[int, list[dict]] = {}
        for s in self.songs:
            g = first_genre(s)
            if g:
                key = listening.normalize(g)
                self.genre_songs.setdefault(key, []).append(s)
                self.genre_labels.setdefault(key, Counter())[g] += 1
            a = first_artist(s.get("artist") or "")
            if a:
                self.artist_songs.setdefault(a, []).append(s)
                self.artist_names.setdefault(a, (s.get("artist") or "").strip())
            y = _year(s)
            if 1900 <= y <= now.year:
                self.decade_songs.setdefault(y // 10 * 10, []).append(s)
        self.used: set[str] = set()

    def plays(self, s: dict) -> float:
        return self.plays_by_key.get(self.keys.get(s["path"], ""), 0)

    def past(self, s: dict) -> float:
        return self.past_by_key.get(self.keys.get(s["path"], ""), 0)

    def rng(self, kind: str, key: str) -> random.Random:
        return random.Random(stable_hash(f"{self.day}|{kind}|{key}"))

    def by_plays(self, songs: Iterable[dict], rng: random.Random) -> list[dict]:
        ordered = list(songs)
        rng.shuffle(ordered)
        if any(self.plays(s) > 0 for s in ordered):
            ordered.sort(key=lambda s: -self.plays(s))
        return ordered

    def seeds(self, core: list[dict], rng: random.Random) -> list[dict]:
        ordered = self.by_plays(core, rng)
        best: dict[str, dict] = {}
        for s in ordered:
            if self.plays(s) <= 0:
                break
            best.setdefault(first_artist(s.get("artist") or ""), s)
        found = list(best.values())[:4]
        return found or ordered

    def compose(
        self, kind: str, key: str, label: str, pool: list[dict], seeds: list[dict]
    ) -> dict | None:
        unique = list({s["path"]: s for s in pool}.values())
        if len(unique) < MIX_MIN_SONGS or not seeds:
            return None
        rng = self.rng(kind, key)
        target = min(MIX_TARGET, len(unique))
        cap = max(2, -(-target * 2 // 10))  # ceil(target * ARTIST_SHARE)
        top = max([self.plays(s) for s in unique] + [1])
        noise = {s["path"]: rng.random() for s in unique}
        scored = sorted(
            unique,
            key=lambda s: (
                -(
                    0.6 * self.plays(s) / top
                    + noise[s["path"]]
                    - (0.3 if s["path"] in self.used else 0)
                )
            ),
        )
        mix = [seeds[0]]
        counts = Counter({first_artist(seeds[0].get("artist") or ""): 1})
        for s in [*seeds[1:4], *scored]:
            if len(mix) >= target:
                break
            if any(m["path"] == s["path"] for m in mix):
                continue
            artist = first_artist(s.get("artist") or "")
            if counts[artist] >= cap:
                continue
            mix.append(s)
            counts[artist] += 1
        # Space the artists: no two songs of one artist in a row where
        # another song can go between them.
        spaced: list[dict] = []
        waiting = list(mix)
        while waiting:
            last = first_artist(spaced[-1].get("artist") or "") if spaced else None
            pick = next(
                (s for s in waiting if first_artist(s.get("artist") or "") != last),
                waiting[0],
            )
            waiting.remove(pick)
            spaced.append(pick)
        if len(spaced) < MIX_MIN_SONGS:
            return None
        heard = Counter(
            (s.get("artist") or "").strip() for s in spaced if s.get("artist")
        )
        return {
            "kind": kind,
            "key": key,
            "label": label,
            "artists": [a for a, _ in heard.most_common(3)],
            "songs": [song(s) for s in spaced],
        }

    def add(self, made: list[dict], mix: dict | None) -> bool:
        if mix is None:
            return False
        paths = {s["path"] for s in mix["songs"]}
        discovery = mix["kind"] in DISCOVERIES
        for other in made:
            if (other["kind"] in DISCOVERIES) != discovery:
                continue
            shared = sum(1 for s in other["songs"] if s["path"] in paths)
            if shared > MAX_OVERLAP * min(len(paths), len(other["songs"])):
                return False
        made.append(mix)
        self.used |= paths
        return True

    # -- kinds --

    def favorites(self) -> dict | None:
        played = sorted(
            (s for s in self.songs if self.plays(s) > 0), key=lambda s: -self.plays(s)
        )
        if len(played) < FAVORITES_MIN_PLAYED:
            return None
        # Plays are weighted, so the counts are not whole numbers.
        weights: dict[str, float] = {}
        for s in played[:50]:
            g = listening.normalize(first_genre(s))
            if g:
                weights[g] = weights.get(g, 0.0) + self.plays(s)
        pool = [*played[:100]]
        for g, _ in sorted(weights.items(), key=lambda e: -e[1])[:3]:
            pool += self.genre_songs.get(g, [])
        if not weights:
            pool += self.songs
        return self.compose("favorites", "favorites", "", pool, played[:12])

    def genre_order(self) -> list[str]:
        def score(g: str) -> float:
            return sum(self.plays(s) for s in self.genre_songs[g]) + 0.01 * len(
                self.genre_songs[g]
            )

        return sorted(
            (
                g
                for g, songs in self.genre_songs.items()
                if len(songs) >= MIN_GENRE_CORE
            ),
            key=lambda g: (-score(g), g),
        )

    def genre(self, g: str) -> dict | None:
        core = self.genre_songs[g]
        pool = list(core)
        if len(core) < MIX_MIN_SONGS:
            # A small genre adds its own artists' other songs only.
            for s in core:
                pool += self.artist_songs.get(first_artist(s.get("artist") or ""), [])
        label = self.genre_labels[g].most_common(1)[0][0]
        return self.compose(
            "genre", g, label, pool, self.seeds(core, self.rng("genre", g))
        )

    def fresh(self) -> dict | None:
        since = (self.now - timedelta(days=FRESH_DAYS)).timestamp()
        fresh = [s for s in self.songs if (s.get("added") or 0) > since]
        if len(fresh) < MIX_MIN_SONGS or len(fresh) * 2 > len(self.songs):
            return None
        newest = sorted(fresh, key=lambda s: (-(s.get("added") or 0), s["path"]))
        return self.compose("fresh", "fresh", "", fresh, newest)

    def rediscover(self) -> dict | None:
        forgotten = [s for s in self.songs if self.past(s) > 0 and self.plays(s) <= 0]
        if len(forgotten) < MIX_MIN_SONGS:
            return None
        forgotten.sort(key=lambda s: (-self.past(s), s["path"]))
        return self.compose("rediscover", "rediscover", "", forgotten, forgotten)

    def discover(self) -> dict | None:
        def heard(g: str) -> float:
            return sum(self.plays(s) for s in self.genre_songs[g])

        genres = sorted(
            (g for g in self.genre_songs if heard(g) > 0), key=lambda g: (-heard(g), g)
        )
        pool = list(
            {
                s["path"]: s
                for g in genres[:3]
                for s in self.genre_songs[g]
                if self.plays(s) <= 0 and self.past(s) <= 0
            }.values()
        )
        if len(pool) < MIX_MIN_SONGS:
            return None
        seeds = self.by_plays(pool, self.rng("discover", "discover"))
        return self.compose("discover", "discover", "", pool, seeds)

    def deep_cuts(self) -> dict | None:
        """Songs not played lately by the artists played most."""
        heard = {
            a: sum(self.plays(s) for s in songs)
            for a, songs in self.artist_songs.items()
        }
        artists = sorted(
            (a for a in heard if heard[a] > 0), key=lambda a: (-heard[a], a)
        )
        pool = [
            s
            for a in artists[:DEEP_CUT_ARTISTS]
            for s in self.artist_songs[a]
            if self.plays(s) <= 0
        ]
        if len(pool) < MIX_MIN_SONGS:
            return None
        seeds = self.by_plays(pool, self.rng("deepCuts", "deepCuts"))
        return self.compose("deepCuts", "deepCuts", "", pool, seeds)

    def year(self) -> dict | None:
        """Songs released this year, or last year when this one has too
        few; a Library mostly of that year has nothing to set them apart."""
        for y in (self.now.year, self.now.year - 1):
            core = [s for s in self.songs if _year(s) == y]
            if len(core) < MIX_MIN_SONGS or len(core) * 2 > len(self.songs):
                continue
            return self.compose(
                "year", str(y), str(y), core, self.seeds(core, self.rng("year", str(y)))
            )
        return None

    def decade_core(self, decade: int) -> list[dict]:
        weights: dict[str, float] = {}
        for s in self.decade_songs[decade]:
            g = listening.normalize(first_genre(s))
            if g:
                weights[g] = weights.get(g, 0.0) + 1 + self.plays(s)
        top = {g for g, _ in sorted(weights.items(), key=lambda e: (-e[1], e[0]))[:2]}
        return [
            s
            for s in self.decade_songs[decade]
            if listening.normalize(first_genre(s)) in top
        ]

    def decades(self) -> list[int]:
        def heard(d: int) -> float:
            return sum(self.plays(s) for s in self.decade_songs[d])

        return sorted(
            (
                d
                for d in self.decade_songs
                if len(self.decade_core(d)) >= MIN_DECADE_CORE
            ),
            key=lambda d: (-heard(d), -len(self.decade_songs[d]), -d),
        )

    def decade(self, d: int) -> dict | None:
        core = self.decade_core(d)
        return self.compose(
            "decade", str(d), str(d), core, self.seeds(core, self.rng("decade", str(d)))
        )

    def anchor_artists(self) -> list[str]:
        heard = {
            a: sum(self.plays(s) for s in songs)
            for a, songs in self.artist_songs.items()
        }
        return sorted(
            (a for a, songs in self.artist_songs.items() if a and len(songs) >= 3),
            key=lambda a: (-heard[a], -len(self.artist_songs[a]), a),
        )

    def mix_artists(self, sparse: bool) -> list[str]:
        """A sparse shelf takes the artists played most; otherwise the
        played ones with enough songs, from a different one of the leading
        few each day."""
        anchors = self.anchor_artists()
        if sparse:
            return anchors
        heard = [
            a
            for a in anchors
            if len(self.artist_songs[a]) >= MIN_ANCHOR_SONGS
            and any(self.plays(s) > 0 for s in self.artist_songs[a])
        ][:ANCHOR_CHOICES]
        if not heard:
            return heard
        start = self.rng("artist", "rotation").randrange(len(heard))
        return heard[start:] + heard[:start]

    def artist(self, a: str) -> dict | None:
        own = self.artist_songs[a]
        genres = {listening.normalize(first_genre(s)) for s in own} - {""}
        pool = list(own)
        for g in genres:
            pool += self.genre_songs.get(g, [])
        if not genres:
            pool += [s for s in self.songs if not first_genre(s)]
        seeds = self.by_plays(own, self.rng("artist", a))
        return self.compose("artist", a, self.artist_names.get(a, a), pool, seeds)

    def build(self) -> list[dict]:
        if len(self.songs) < MIX_MIN_LIBRARY:
            return []
        made: list[dict] = []

        def make(
            keys: Iterable, build: Callable[[Any], dict | None], count: int
        ) -> list[dict]:
            added: list[dict] = []
            for k in keys:
                if len(added) == count:
                    break
                mix = build(k)
                if mix is not None and self.add(made, mix):
                    added.append(mix)
            return added

        favorites = make([self.favorites], lambda f: f(), 1)
        ranked = self.genre_order()
        tried: set[str] = set()

        def genre(g: str) -> dict | None:
            tried.add(g)
            return self.genre(g)

        genres = make(ranked, genre, MAX_GENRE_MIXES)
        found = make(
            [self.fresh, self.rediscover, self.discover, self.deep_cuts, self.year],
            lambda f: f(),
            5,
        )
        decades = make(self.decades(), self.decade, MAX_DECADE_MIXES)
        tagged = sum(1 for s in self.songs if first_genre(s))
        sparse = len(made) < 3 or tagged * 2 < len(self.songs)
        artists = make(
            self.mix_artists(sparse),
            lambda a: None if any(m["key"] == a for m in made) else self.artist(a),
            3 if sparse else MAX_ARTIST_MIXES,
        )
        others = [g for g in ranked if g not in tried]
        self.rng("genre", "rotation").shuffle(others)
        extra = make(others, genre, 1)
        mixes = [
            *favorites,
            *genres[:LEAD_GENRE_MIXES],
            *found,
            *decades[:1],
            *artists[:1],
            *genres[LEAD_GENRE_MIXES:],
            *decades[1:],
            *artists[1:],
            *extra,
        ][:MIX_MAX]

        def weight(m: dict) -> float:
            return sum(
                self.plays_by_key.get(listening.track_key(s["title"], s["artist"]), 0)
                for s in m["songs"]
            )

        # Favorites lead and the two mixes played most follow; discoveries
        # then take turns with the rest by plays.
        rest = sorted(
            [
                (i, m)
                for i, m in enumerate(mixes)
                if m["kind"] != "favorites" and m["kind"] not in DISCOVERIES
            ],
            key=lambda e: (-weight(e[1]), e[0]),
        )
        discoveries = [m for m in mixes if m["kind"] in DISCOVERIES]
        later = [m for _, m in rest[2:]]
        ordered = [m for m in mixes if m["kind"] == "favorites"] + [
            m for _, m in rest[:2]
        ]
        for i in range(max(len(discoveries), len(later))):
            if i < len(discoveries):
                ordered.append(discoveries[i])
            if i < len(later):
                ordered.append(later[i])
        for m in ordered:
            m["title"] = mix_title(m)
            m["subtitle"] = mix_subtitle(m["artists"])
        return ordered


def build_mixes(
    library: list[dict],
    plays: dict[str, float],
    past_plays: dict[str, float] | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """The day's mixes of `library` (up to MIX_MAX). `plays` are the plays
    of the last 90 days by track key, `past_plays` those of the months
    before. The same input on the same day gives the same mixes."""
    return _Mixes(library, plays, past_plays or {}, now or datetime.now()).build()


# ── Recently played, On repeat ──────────────────────────────────────────────


def _album_card(s: dict) -> dict:
    return {
        "key": album_key(s),
        "title": (s.get("album") or "").strip(),
        "artist": artist_of(s),
        "cover_path": s["path"],
    }


def recently_played(
    owner: str, library: list[dict], now: datetime, limit: int = 12
) -> list[dict]:
    """Albums played lately, the latest first, as Library albums."""
    by_key: dict[str, dict] = {}
    for s in library:
        if (s.get("album") or "").strip():
            by_key.setdefault(listening.track_key(s.get("title"), s.get("artist")), s)
    since = (now.date() - timedelta(days=60)).isoformat()
    albums: dict[str, dict] = {}
    for r in reversed(listening.rows(owner, since)):
        song = by_key.get(r["track_key"])
        if song is not None:
            albums.setdefault(album_key(song), _album_card(song))
        if len(albums) >= limit:
            break
    return list(albums.values())


def on_repeat(owner: str, library: list[dict], now: datetime, limit: int = 12) -> dict:
    """The most played Library songs of the last 30 days; Library albums
    (the newest first) while nothing is recorded."""
    counts = play_counts(owner, now, 30)
    if counts:
        by_key: dict[str, dict] = {}
        for s in library:
            by_key.setdefault(listening.track_key(s.get("title"), s.get("artist")), s)
        top = sorted(counts.items(), key=lambda e: (-e[1], e[0]))
        songs = [{**song(by_key[k]), "plays": int(n)} for k, n in top if k in by_key][
            :limit
        ]
        if songs:
            return {"kind": "songs", "songs": songs}
    albums: dict[str, dict] = {}
    for s in sorted(library, key=lambda s: -(s.get("added") or 0)):
        if (s.get("album") or "").strip():
            albums.setdefault(album_key(s), _album_card(s))
        if len(albums) >= limit:
            break
    return {"kind": "albums", "albums": list(albums.values())}


# ── Finish your collection ──────────────────────────────────────────────────


def incomplete_albums(library: list[dict]) -> list[dict]:
    """Albums whose track numbers leave gaps: fewer songs than the highest
    track number on a disc."""
    discs: dict[str, dict[int, set[int]]] = {}
    cards: dict[str, dict] = {}
    for s in library:
        if not (s.get("album") or "").strip() or not s.get("track"):
            continue
        key = album_key(s)
        discs.setdefault(key, {}).setdefault(int(s.get("disc") or 1), set()).add(
            int(s["track"])
        )
        cards.setdefault(key, _album_card(s))
    out = []
    for key, by_disc in discs.items():
        present = sum(len(t) for t in by_disc.values())
        expected = sum(max(t) for t in by_disc.values())
        if present >= 2 and expected > present:
            out.append({**cards[key], "present": present, "expected": expected})
    out.sort(key=lambda a: (a["expected"] - a["present"], a["title"].lower()))
    return out


def _plural(n: int, one: str, many: str) -> str:
    return one.format(n=n) if n == 1 else many.format(n=n)


def unfinished(owner: str, library: list[dict]) -> dict:
    """Loose ends of the collection, as counts, with the rows to show. The
    mobile's wishlist is, here, the playlist songs not downloaded yet."""
    from . import failed_tracks

    wishlist = 0
    try:
        row = (
            db.connection()
            .execute(
                "SELECT COUNT(DISTINCT t.path) FROM player_playlist_tracks t "
                "JOIN player_playlists p ON p.id = t.playlist_id "
                "WHERE t.path LIKE 'remote:%' AND p.owner = ?",
                (owner or "",),
            )
            .fetchone()
        )
        wishlist = int(row[0] or 0)
    except Exception:
        logger.debug("[home] could not count playlist songs", exc_info=True)
    albums = incomplete_albums(library)
    failed = len(failed_tracks.list_for(owner or ""))
    rows = []
    if wishlist:
        rows.append(
            {
                "id": "wishlist",
                "count": wishlist,
                "text": _plural(
                    wishlist,
                    "1 playlist song is not in your Library",
                    "{n} playlist songs are not in your Library",
                ),
            }
        )
    if albums:
        n = len(albums)
        rows.append(
            {
                "id": "incompleteAlbums",
                "count": n,
                "text": _plural(
                    n, "1 album is missing songs", "{n} albums are missing songs"
                ),
            }
        )
    if failed:
        rows.append(
            {
                "id": "failedDownloads",
                "count": failed,
                "text": _plural(failed, "1 download failed", "{n} downloads failed"),
            }
        )
    return {"rows": rows, "albums": albums[:12]}


# ── Recommended for you ─────────────────────────────────────────────────────

RECOMMENDED_COUNT = 12
RECOMMENDED_KEPT = 20
RECOMMENDED_PER_ARTIST = 3
SEEDS_PER_KIND = 15
POOL_SIZE = 45

FetchJson = Callable[[str, dict], Any]


def interleave(*lists: list[dict]) -> list[dict]:
    out = []
    for i in range(max((len(x) for x in lists), default=0)):
        for x in lists:
            if i < len(x):
                out.append(x[i])
    return out


def recommendation_pool(most_played: list[dict], added: list[dict]) -> list[dict]:
    """The songs recommendations start from: the most played and the newest
    in the Library in turn, each once, without songs lacking an artist."""
    seen: set[str] = set()
    pool = []
    for s in interleave(most_played, added):
        key = listening.track_key(s.get("title"), s.get("artist"))
        if not key or not (s.get("artist") or "").strip() or key in seen:
            continue
        seen.add(key)
        pool.append(s)
        if len(pool) >= POOL_SIZE:
            break
    return pool


def order_recommendations(
    tracks: list[dict], per_artist: int = RECOMMENDED_PER_ARTIST
) -> list[dict]:
    """Past `per_artist` songs of one artist, the next waits until every
    artist has had as many turns (orderHomeRecommendations' rounds)."""
    turns: Counter = Counter()
    rounds = []
    for t in tracks:
        artist = first_artist(t.get("artist") or "")
        rounds.append(0 if not artist else turns[artist] // per_artist)
        turns[artist] += 1
    order = sorted(range(len(tracks)), key=lambda i: (rounds[i], i))
    return [tracks[i] for i in order]


def _deezer_get(url: str, params: dict) -> Any:
    import httpx

    resp = httpx.get(url, params=params, timeout=12, headers={"User-Agent": "Mornye"})
    resp.raise_for_status()
    return resp.json()


def _deezer_track(t: Any) -> dict | None:
    if not isinstance(t, dict) or not t.get("title"):
        return None
    album = t.get("album") or {}
    return {
        "title": t.get("title_short") or t.get("title"),
        "artist": (t.get("artist") or {}).get("name", ""),
        "album": album.get("title", ""),
        "cover": album.get("cover_medium") or album.get("cover") or "",
        "duration_s": int(t.get("duration") or 0),
        "url": t.get("link", ""),
    }


def ask_recommendations(
    seeds: list[dict], fetch: FetchJson | None = None, limit: int = RECOMMENDED_KEPT
) -> list[dict]:
    """Songs like `seeds` from Deezer's public catalogue: the top songs of
    the artists Deezer relates to each seed artist, two per artist."""
    get = fetch or _deezer_get
    base = "https://api.deezer.com"
    artists: list[str] = []
    for s in seeds:
        name = (s.get("artist") or "").strip()
        if name and first_artist(name) not in {first_artist(a) for a in artists}:
            artists.append(name)
        if len(artists) >= 6:
            break
    seed_names = {first_artist(a) for a in artists}
    found: list[dict] = []
    keys: set[str] = set()
    for name in artists:
        try:
            hit = (get(f"{base}/search/artist", {"q": name, "limit": 1}) or {}).get(
                "data"
            ) or []
            if not hit:
                continue
            related = (
                get(f"{base}/artist/{hit[0]['id']}/related", {"limit": 4}) or {}
            ).get("data") or []
        except Exception:
            logger.debug("[home] related artists failed", exc_info=True)
            continue
        for other in related[:4]:
            if first_artist(other.get("name") or "") in seed_names:
                continue
            try:
                top = (get(f"{base}/artist/{other['id']}/top", {"limit": 3}) or {}).get(
                    "data"
                ) or []
            except Exception:
                continue
            for raw in top[:2]:
                t = _deezer_track(raw)
                if t is None:
                    continue
                key = listening.track_key(t["title"], t["artist"])
                if key and key not in keys:
                    keys.add(key)
                    found.append(t)
        if len(found) >= limit * 2:
            break
    return found


def outside_library(
    tracks: list[dict], library: list[dict], downloaded: Iterable[tuple[str, str]] = ()
) -> list[dict]:
    have = {listening.track_key(s.get("title"), s.get("artist")) for s in library}
    have |= {listening.track_key(t, a) for t, a in downloaded}
    return [
        t
        for t in tracks
        if listening.track_key(t.get("title"), t.get("artist")) not in have
    ]


def _downloaded_names(owner: str) -> list[tuple[str, str]]:
    try:
        sql = "SELECT title, artist FROM downloads"
        params: tuple = ()
        if owner:
            sql += " WHERE owner = ?"
            params = (owner,)
        return [
            (r["title"] or "", r["artist"] or "")
            for r in db.connection().execute(sql, params)
        ]
    except Exception:
        return []


def recommended(
    owner: str,
    library: list[dict],
    now: datetime,
    *,
    fetch: FetchJson | None = None,
    refresh: bool = False,
) -> list[dict]:
    """The day's "Recommended for you": asked once a day and kept for that
    day, in the order chosen then. A failed request falls back on the last
    day's songs."""
    day = now.date().isoformat()
    key = _meta_key("recommended", owner)
    cached = db.loads(db.get_meta(key), {}) or {}
    stored = cached.get("tracks")
    tracks: list[dict] = stored if isinstance(stored, list) else []
    if refresh or cached.get("day") != day:
        counts = play_counts(owner, now, 28)
        by_key = {
            listening.track_key(s.get("title"), s.get("artist")): s for s in library
        }
        top = sorted(counts.items(), key=lambda e: (-e[1], e[0]))
        played = [by_key[k] for k, _ in top if k in by_key][:SEEDS_PER_KIND]
        if not played:
            # Songs heard but not in the Library seed too.
            since = (now.date() - timedelta(days=27)).isoformat()
            heard = listening.totals(listening.rows(owner, since))
            played = sorted(heard.values(), key=lambda r: -(r.get("plays") or 0))[
                :SEEDS_PER_KIND
            ]
        added = sorted(library, key=lambda s: -(s.get("added") or 0))[:SEEDS_PER_KIND]
        pool = recommendation_pool(played, added)
        if pool:
            # Each day starts from other songs of the pool.
            shift = (date.fromisoformat(day) - date(2020, 1, 1)).days % len(pool)
            pool = pool[shift:] + pool[:shift]
        fresh: list[dict] = []
        if pool:
            try:
                fresh = ask_recommendations(pool, fetch)
            except Exception:
                logger.debug("[home] recommendations failed", exc_info=True)
        fresh = outside_library(fresh, library, _downloaded_names(owner))
        random.Random(stable_hash(day)).shuffle(fresh)
        if fresh:
            tracks = order_recommendations(fresh)[:RECOMMENDED_KEPT]
            db.set_meta(key, db.dumps({"day": day, "tracks": tracks}))
    # Songs leave the shelf as they join the Library.
    return outside_library(tracks, library)[:RECOMMENDED_COUNT]


# ── Status line and greeting ────────────────────────────────────────────────


def greeting(now: datetime, variant: int = 0) -> str:
    h = now.hour
    if 5 <= h < 12:
        options = ("Good morning", "Rise and shine", "Fresh start")
    elif 12 <= h < 17:
        options = ("Good afternoon", "Afternoon groove", "Keep the vibe going")
    elif 17 <= h < 21:
        options = ("Good evening", "Time to unwind", "Golden hour")
    else:
        options = ("Good night", "Late-night session", "Quiet hours")
    return options[variant % len(options)]


def arrivals(owner: str, since: float, until: float) -> int:
    """Songs that finished downloading after `since`, up to `until`."""
    try:
        sql = (
            "SELECT COUNT(*) FROM downloads WHERE success = 1 "
            "AND downloaded_at > ? AND downloaded_at <= ?"
        )
        params: list = [since, until]
        if owner:
            sql += " AND owner = ?"
            params.append(owner)
        return int(db.connection().execute(sql, params).fetchone()[0] or 0)
    except Exception:
        return 0


def visit_window(owner: str, now: float, session: str) -> tuple[float | None, float]:
    """The previous session's Home visit and this session's first one,
    recorded once per app session so the counts hold while browsing."""
    key = _meta_key("visit", owner)
    saved = db.loads(db.get_meta(key), {}) or {}
    if saved.get("session") == session:
        return saved.get("previous"), float(saved.get("current") or now)
    previous = saved.get("current")
    db.set_meta(
        key, db.dumps({"session": session, "previous": previous, "current": now})
    )
    return previous, now


def status(owner: str, now: datetime, session: str) -> dict:
    """Songs downloaded between the previous session's Home visit and this
    session's first; `since_yesterday` when that visit was yesterday."""
    previous, current = visit_window(owner, now.timestamp(), session)
    if previous is None:
        return {"added": 0, "since_yesterday": False}
    count = arrivals(owner, float(previous), current)
    before = datetime.fromtimestamp(current).date() - timedelta(days=1)
    return {
        "added": count,
        "since_yesterday": datetime.fromtimestamp(float(previous)).date() == before,
    }


# ── Pins' songs ─────────────────────────────────────────────────────────────


def pin_songs(owner: str, pin: dict, library: list[dict]) -> list[dict]:
    """The songs a pin plays, in Library order."""
    kind, pid = pin.get("kind"), pin.get("id", "")
    if kind == "album":
        found = [s for s in library if album_key(s) == pid]
        found.sort(
            key=lambda s: (
                int(s.get("disc") or 0),
                int(s.get("track") or 0),
                s.get("title", ""),
            )
        )
    elif kind == "artist":
        found = [
            s
            for s in library
            if artist_of(s).lower() == pid
            or first_artist(s.get("artist") or "") == first_artist(pid)
        ]
        found.sort(
            key=lambda s: (
                s.get("album", "").lower(),
                int(s.get("disc") or 0),
                int(s.get("track") or 0),
            )
        )
    elif kind == "genre":
        found = [
            s for s in library if pid in {listening.normalize(g) for g in genres_of(s)}
        ]
    elif kind == "folder":
        root = pid.rstrip("/\\")
        found = [s for s in library if s["path"].startswith((root + "/", root + "\\"))]
        found.sort(key=lambda s: s["path"].lower())
    elif kind == "playlist":
        from . import player_playlists

        try:
            tracks = (player_playlists.get_playlist(pid, owner) or {}).get("tracks", [])
        except Exception:
            tracks = []
        return [
            song(t) for t in tracks if not str(t.get("path", "")).startswith("remote:")
        ]
    else:
        found = []
    return [song(s) for s in found]


# ── Everything at once ──────────────────────────────────────────────────────


def home(
    owner: str,
    *,
    now: datetime | None = None,
    session: str = "",
    fetch: FetchJson | None = None,
    recommend: bool = True,
) -> dict:
    """Every section Home shows, in the person's order, with the layout.
    `recommend=False` leaves out the one section that asks the network."""
    now = now or datetime.now()
    layout = load_layout(owner)
    library = library_songs()
    day = now.date().isoformat()
    shows = {s: layout_shows(layout, s) for s in SECTIONS}
    sections: dict[str, Any] = {}
    if shows["quickPlay"]:
        by_key: dict[str, dict] = {}
        for s in library:
            by_key.setdefault(listening.track_key(s.get("title"), s.get("artist")), s)
        recent = play_counts(owner, now, 28)
        top = sorted(recent.items(), key=lambda e: (-e[1], e[0]))
        played = [by_key[k] for k, _ in top if k in by_key][: 2 * QUICK_COUNT]
        sections["quickPlay"] = [song(s) for s in quick_play(library, played, day)]
    if shows["mixes"]:
        plays = play_counts(owner, now, 90)
        past = play_counts(owner, now, 275, before_days=90)
        sections["mixes"] = build_mixes(library, plays, past, now)
    if shows["recentlyPlayed"]:
        sections["recentlyPlayed"] = recently_played(owner, library, now)
    if shows["onRepeat"]:
        sections["onRepeat"] = on_repeat(owner, library, now)
    if shows["unfinished"]:
        sections["unfinished"] = unfinished(owner, library)
    if shows["recommended"] and recommend:
        sections["recommended"] = recommended(owner, library, now, fetch=fetch)
    return {
        "layout": layout_view(layout),
        "order": [s for s in layout_sections(layout) if shows[s]],
        "pins": load_pins(owner) if shows["pins"] else [],
        "sections": sections,
        "greeting": greeting(now, stable_hash(session) if session else 0),
        # Not "status": the API answer's own "status" is "ok".
        "arrivals": (
            status(owner, now, session)
            if session
            else {"added": 0, "since_yesterday": False}
        ),
        "library_size": len(library),
    }
