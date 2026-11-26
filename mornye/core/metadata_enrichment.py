"""Metadata Enrichment — Phase 2 asyncio migration.

Adds `enrich_metadata_async` that uses `asyncio.gather` with a global timeout instead of `ThreadPoolExecutor`. The original sync version is retained for backward compatibility.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .http import NetworkManager
from .isrc_utils import normalize_isrc
from .loop_runner import run_sync
from .response_cache import get as get_cached_response
from .response_cache import put as put_cached_response
from .text_match import fold, is_latin_script, ratio, score_track_match, titles_match

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)

_HTTP_TIMEOUT = 4
_GLOBAL_TIMEOUT = 6.0
_ENRICHMENT_CACHE_TTL = 3600.0
#: A lookup that found nothing is remembered too, but only briefly: the
#: providers do add releases, and an hour of "not there" for a track that
#: appeared five minutes ago is worse than the repeat request costs.
#: Without this a miss was simply never cached, so a failing ISRC paid the
#: full four-provider fan-out again on every single pass over a library.
_NEGATIVE_CACHE_TTL = 300.0
_ENRICHMENT_CACHE_MAX = 2000
#: The on-disk cache's name. "-v2" since enrichment began reading credits,
#: composer and ℗ from Qobuz and the extensions: a day-old entry written
#: before that has none of those fields, and would have kept them out of
#: every track it matched until it expired.
_CACHE_NAMESPACE = "metadata-enrichment-v2"

#: Below this, a search result is not the track we asked for. iTunes
#: always answers *something* — searching an ISRC it does not know
#: returns whatever the digits look like — so taking results[0] on faith
#: tagged tracks with a stranger's genre and cover art.
_APPLE_MATCH_MIN = 0.55

#: The services enrichment can ask, in their default order.
ENRICH_PROVIDERS: tuple[str, ...] = ("deezer", "apple")


def _run_async_sync(coro):
    """Thin alias for loop_runner.run_sync() — see that function for why the
    old three-branch shim (which created a fresh loop in every branch) went
    away. Kept as a name so the many call sites below read unchanged.
    """
    return run_sync(coro)


# ---------------------------------------------------------------------------
# EnrichedMetadata (invariato)
# ---------------------------------------------------------------------------


#: Everything merge() carries across, in the order the tag names are built
#: from. `explicit` is handled apart because it is a flag: absent and False
#: are the same value, so "already set" cannot mean "do not replace".
_MERGE_ATTRS = (
    "genre",
    "label",
    "bpm",
    "upc",
    "isrc",
    "cover_url_hd",
    "composer",
    "copyright",
    "release_date",
    "album_type",
    "total_tracks",
    "total_discs",
    "lyricist",
    "producer",
    "mixer",
    "engineer",
)


@dataclass
class EnrichedMetadata:
    genre: str = ""
    label: str = ""
    bpm: int = 0
    explicit: bool = False
    upc: str = ""
    isrc: str = ""
    cover_url_hd: str = ""
    composer: str = ""
    copyright: str = ""
    release_date: str = ""
    album_type: str = ""
    #: The release's own totals. These matter more than they look: nothing
    #: else in the pipeline knows the disc count, so a two-disc album was
    #: tagged DISCTOTAL=1 — the model's default — on every track of it.
    total_tracks: int = 0
    total_discs: int = 0
    #: Credits, from services that publish them (Qobuz's performer list,
    #: Tidal's credits). Several names are joined with "; ".
    lyricist: str = ""
    producer: str = ""
    mixer: str = ""
    engineer: str = ""
    _sources: dict[str, str] = field(default_factory=dict, repr=False)

    def as_tags(self) -> dict[str, str]:
        tags: dict[str, str] = {}
        if self.genre:
            tags["GENRE"] = self.genre
        if self.label:
            tags["ORGANIZATION"] = self.label
        if self.bpm:
            tags["BPM"] = str(self.bpm)
        if self.upc:
            tags["UPC"] = self.upc
        if self.isrc:
            isrc_n = normalize_isrc(self.isrc)
            if isrc_n:
                tags["ISRC"] = isrc_n
        if self.composer:
            tags["COMPOSER"] = self.composer
        if self.copyright:
            tags["COPYRIGHT"] = self.copyright
        if self.release_date:
            tags["DATE"] = self.release_date
        if self.album_type:
            tags["RELEASETYPE"] = self.album_type
        if self.total_tracks > 0:
            tags["TRACKTOTAL"] = str(self.total_tracks)
        if self.total_discs > 0:
            tags["DISCTOTAL"] = str(self.total_discs)
        if self.explicit:
            tags["ITUNESADVISORY"] = "1"
        for attr, tag in (
            ("lyricist", "LYRICIST"),
            ("producer", "PRODUCER"),
            ("mixer", "MIXER"),
            ("engineer", "ENGINEER"),
        ):
            if getattr(self, attr):
                tags[tag] = getattr(self, attr)
        return tags

    def merge(self, other: EnrichedMetadata, source: str) -> None:
        for attr in _MERGE_ATTRS:
            if not getattr(self, attr) and getattr(other, attr):
                setattr(self, attr, getattr(other, attr))
                self._sources[attr] = source
        if not self.explicit and other.explicit:
            self.explicit = True
            self._sources["explicit"] = source

    def is_complete(self) -> bool:
        return bool(self.genre and self.label and self.cover_url_hd)


# ---------------------------------------------------------------------------
# In-memory cache (invariata)
# ---------------------------------------------------------------------------

#: (value, stored_at, ttl_s) — the TTL is per entry so a negative result can
#: expire sooner than a real one. See _NEGATIVE_CACHE_TTL.
_enrichment_cache: dict[str, tuple[EnrichedMetadata, float, float]] = {}
_cache_lock = threading.Lock()


def _get_cached(isrc: str) -> EnrichedMetadata | None:
    import dataclasses

    if not isrc:
        return None
    with _cache_lock:
        entry = _enrichment_cache.get(isrc.upper())
        if entry and (time.time() - entry[1]) < entry[2]:
            return entry[0]
    persisted = get_cached_response(_CACHE_NAMESPACE, isrc.upper(), 24 * 60 * 60)
    if isinstance(persisted, dict):
        valid_fields = {f.name for f in dataclasses.fields(EnrichedMetadata)}
        filtered = {k: v for k, v in persisted.items() if k in valid_fields}
        result = EnrichedMetadata(**filtered)
        _put_cached_memory(isrc, result)
        return result
    return None


def _put_cached_memory(
    isrc: str,
    data: EnrichedMetadata,
    ttl_s: float = _ENRICHMENT_CACHE_TTL,
) -> None:
    if not isrc:
        return
    with _cache_lock:
        key = isrc.upper()
        _enrichment_cache[key] = (data, time.time(), ttl_s)
        if len(_enrichment_cache) > _ENRICHMENT_CACHE_MAX:
            oldest_key = min(_enrichment_cache.items(), key=lambda kv: kv[1][1])[0]
            with contextlib.suppress(Exception):
                _enrichment_cache.pop(oldest_key, None)


def _put_cached(isrc: str, data: EnrichedMetadata) -> None:
    if not isrc:
        return
    if not (
        data.genre
        or data.label
        or data.cover_url_hd
        or data.upc
        or data.composer
        or data.copyright
    ):
        # Nothing usable came back. Remember that, in memory only and on the
        # short TTL — persisting a miss to disk would keep a track starved of
        # metadata across restarts long after the provider learned about it.
        _put_cached_memory(isrc, data, _NEGATIVE_CACHE_TTL)
        return
    _put_cached_memory(isrc, data)
    put_cached_response(
        _CACHE_NAMESPACE,
        isrc.upper(),
        {
            "genre": data.genre,
            "label": data.label,
            "bpm": data.bpm,
            "explicit": data.explicit,
            "upc": data.upc,
            "isrc": data.isrc,
            "cover_url_hd": data.cover_url_hd,
            "composer": data.composer,
            "copyright": data.copyright,
            "release_date": data.release_date,
            "album_type": data.album_type,
            "total_tracks": data.total_tracks,
            "total_discs": data.total_discs,
            "lyricist": data.lyricist,
            "producer": data.producer,
            "mixer": data.mixer,
            "engineer": data.engineer,
        },
    )


# ---------------------------------------------------------------------------
# Sync provider classes (invariate)
# ---------------------------------------------------------------------------


#: Deezer labels every credited person with a role. Most are "Main" or
#: "Featured"; these are the ones that mean "wrote it".
_DEEZER_COMPOSER_ROLES = frozenset(
    {"composer", "writer", "songwriter", "author", "lyricist", "compositor"},
)


def _same_release(expected: str, found: str) -> bool:
    """Whether two album titles name the same release.

    Deliberately loose — "Abbey Road" and "Abbey Road (Remastered)" are the
    same record for tagging purposes — but not blind: fold() already strips
    case, accents and punctuation, and the containment test catches the
    edition suffixes that folding leaves behind.
    """
    left, right = fold(expected), fold(found)
    if not left or not right:
        return False
    if left == right or left in right or right in left:
        return True
    return ratio(expected, found) >= 0.85


#: Deezer names genres in the language the request asks for, and without
#: asking, in the one its servers guess from the address: "Musica Asiatica"
#: from Italy for what is "Asian Music" everywhere else.
_DEEZER_HEADERS = {"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"}

#: A credited role, reduced to letters, and the credit field(s) it fills.
#: Covers Qobuz's performer roles ("MixingEngineer", "ComposerLyricist")
#: and Tidal's credit types ("Mixing Engineer").
_CREDIT_ROLES: dict[str, tuple[str, ...]] = {
    "composer": ("composer",),
    "composerlyricist": ("composer", "lyricist"),
    "songwriter": ("composer", "lyricist"),
    "writer": ("lyricist",),
    "lyricist": ("lyricist",),
    "producer": ("producer",),
    "coproducer": ("producer",),
    "mixer": ("mixer",),
    "mixingengineer": ("mixer",),
    "mixengineer": ("mixer",),
    "engineer": ("engineer",),
    "masteringengineer": ("engineer",),
    "recordingengineer": ("engineer",),
}


def _apply_credits(out: EnrichedMetadata, pairs) -> None:
    """Fills the credit fields of `out` from (name, role) pairs, leaving any
    field already set alone."""
    buckets: dict[str, list[str]] = {}
    for name, role in pairs:
        name = str(name or "").strip()
        fields = _CREDIT_ROLES.get(re.sub(r"[^a-z]", "", str(role or "").lower()))
        if not name or not fields:
            continue
        for field_name in fields:
            buckets.setdefault(field_name, []).append(name)
    for field_name, names in buckets.items():
        if not getattr(out, field_name):
            setattr(out, field_name, "; ".join(dict.fromkeys(names)))


def _other_recording(expected: str, found: str) -> bool:
    """Whether a lookup's title shows it answered about another song.

    An ISRC lookup is an identity lookup, but only as good as the ISRC: a
    wrong one — a typo, a reissue's code reused — returns a stranger's
    track, and its credits and ℗ line would be written into the file. So a
    title that is plainly a different song discards the answer.

    Plainly: both titles in Latin script and nowhere near each other. A
    Hangul title against its romanisation shares no letters at all, and
    that is the same song.
    """
    if not expected or not found:
        return False
    if not (is_latin_script(expected) and is_latin_script(found)):
        return False
    return not titles_match(expected, found) and ratio(expected, found) < 0.5


#: How far apart two running times may be and still be the same recording,
#: and what counts as a preview clip: Mornye's own re-enrich rules.
_LENGTH_SLACK_S = 10
_PREVIEW_MAX_S = 35
_FULL_TRACK_MIN_S = 45


def _other_length(
    expected_ms: int, found_ms: int, expected_title: str, found_title: str
) -> bool:
    """Whether an ISRC answer's running time shows it is another take.

    An ISRC match is not proof on its own: catalogs file previews, and now
    and then a different recording, under the code. So, as in Mornye, a
    length more than 10s off is accepted only when the title is the same
    word for word, and a clip of 35s or less never stands in for a full
    track. Unknown lengths prove nothing.
    """
    expected_s, found_s = expected_ms // 1000, found_ms // 1000
    if expected_s <= 0 or found_s <= 0 or abs(expected_s - found_s) <= _LENGTH_SLACK_S:
        return False
    if found_s <= _PREVIEW_MAX_S and expected_s > _FULL_TRACK_MIN_S:
        return True
    if not expected_title or not found_title:
        return False
    return fold(expected_title) != fold(found_title)


def _other_isrc(wanted: Any, found: Any) -> bool:
    """Whether a lookup by ISRC answered with a different one. Qobuz's is a
    search whose term is the ISRC, and a search returns its nearest hit when
    there is no exact one. Absent on either side proves nothing."""
    wanted_n = normalize_isrc(str(wanted or ""))
    found_n = normalize_isrc(str(found or ""))
    return bool(wanted_n and found_n and wanted_n != found_n)


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_dict(value: Any) -> dict[str, Any]:
    """`value` if it is a JSON object, else an empty one to read from."""
    return value if isinstance(value, dict) else {}


class _DeezerMeta:
    BASE = "https://api.deezer.com/2.0"

    def __init__(self) -> None:
        self._client = None

    def fetch(self, isrc: str, album_name: str = "") -> EnrichedMetadata:
        return _run_async_sync(self.fetch_async(isrc, album_name))

    async def fetch_async(
        self,
        isrc: str,
        album_name: str = "",
        track_name: str = "",
        duration_ms: int = 0,
    ) -> EnrichedMetadata:
        """Deezer's view of one recording, looked up by ISRC.

        `album_name` guards the release-scoped fields. An ISRC identifies a
        *recording*, not a release, so Deezer routinely answers with the
        compilation or the deluxe edition it happens to file that recording
        under. Its label, barcode, date and track count then describe a
        record the user is not tagging — a UPC pointing at a greatest-hits
        disc is worse than no UPC at all. When the caller tells us which
        album it wanted, those fields are taken only if Deezer agrees; when
        it does not, we cannot check and the previous behaviour stands.
        """
        out = EnrichedMetadata()
        if not isrc:
            return out
        try:
            client = await NetworkManager.get_async_client_safe()
            r = await client.get(
                f"{self.BASE}/track/isrc:{isrc}",
                timeout=_HTTP_TIMEOUT,
                headers=_DEEZER_HEADERS,
            )
            if r.status_code != 200:
                return out
            d = r.json()
            if "error" in d:
                return out
            if _other_recording(track_name, str(d.get("title") or "")):
                logger.debug(
                    "[meta/deezer] ISRC %s is %r, not %r — ignored",
                    isrc,
                    d.get("title"),
                    track_name,
                )
                return out
            if _other_isrc(isrc, d.get("isrc")):
                logger.debug(
                    "[meta/deezer] asked for ISRC %s, answered with %s — ignored",
                    isrc,
                    d.get("isrc"),
                )
                return out
            if _other_length(
                duration_ms,
                _as_int(d.get("duration")) * 1000,
                track_name,
                str(d.get("title") or ""),
            ):
                logger.debug(
                    "[meta/deezer] ISRC %s runs %ss, not ~%ss — ignored",
                    isrc,
                    d.get("duration"),
                    duration_ms // 1000,
                )
                return out

            composers = []
            for contributor in d.get("contributors") or []:
                role = str(contributor.get("role") or "").strip().lower()
                name = str(contributor.get("name") or "").strip()
                if name and role in _DEEZER_COMPOSER_ROLES:
                    composers.append(name)
            out.composer = "; ".join(dict.fromkeys(composers))

            album_id = (d.get("album") or {}).get("id")
            if album_id:
                ar = await client.get(
                    f"{self.BASE}/album/{album_id}",
                    timeout=_HTTP_TIMEOUT,
                    headers=_DEEZER_HEADERS,
                )
                if ar.is_success:
                    ad = ar.json()
                    # Genre describes the music, not the pressing, so it is
                    # taken whichever release this turned out to be. All of
                    # them, not just the first: Deezer files "Rock" and
                    # "Classic Rock" side by side and picking [0] threw away
                    # a genre the user could have had for free.
                    genres = [
                        str(g.get("name") or "").strip()
                        for g in (ad.get("genres") or {}).get("data") or []
                    ]
                    out.genre = "; ".join(
                        dict.fromkeys(name for name in genres if name),
                    )

                    found_title = str(ad.get("title") or "")
                    release_ok = not album_name or _same_release(
                        album_name,
                        found_title,
                    )
                    if release_ok:
                        out.label = ad.get("label", "")
                        out.upc = ad.get("upc", "")
                        out.release_date = str(ad.get("release_date") or "")
                        out.album_type = str(ad.get("record_type") or "")
                        out.total_tracks = int(ad.get("nb_tracks") or 0)
                        out.cover_url_hd = ad.get("cover_xl") or ad.get(
                            "cover_big",
                            "",
                        )
                    else:
                        logger.debug(
                            "[meta/deezer] release mismatch: wanted %r, got %r "
                            "— keeping genre only",
                            album_name,
                            found_title,
                        )
            out.bpm = int(d.get("bpm") or 0)
            out.explicit = bool(d.get("explicit_lyrics"))
            out.isrc = d.get("isrc", "")
        except Exception as exc:
            logger.debug("[meta/deezer] async %s", exc)
        return out


@dataclass
class _ItunesCandidate:
    """One iTunes search hit, in the shape score_track_match() reads.

    The scorer speaks in title/artists/album/duration_ms and is already
    tuned and tested against the download providers; adapting to it beats
    writing a second, subtly different comparison here.
    """

    title: str
    artists: str
    first_artist: str
    album: str
    duration_ms: int

    @classmethod
    def of(cls, item: dict[str, Any]) -> _ItunesCandidate:
        name = str(item.get("artistName") or "")
        return cls(
            title=str(item.get("trackName") or ""),
            artists=name,
            first_artist=name,
            album=str(item.get("collectionName") or ""),
            duration_ms=int(item.get("trackTimeMillis") or 0),
        )


class _AppleMusicMeta:
    SEARCH = "https://itunes.apple.com/search"

    def __init__(self) -> None:
        self._client = None

    def fetch(
        self,
        track_name: str,
        artist_name: str,
        isrc: str = "",
        album_name: str = "",
        duration_ms: int = 0,
    ) -> EnrichedMetadata:
        return _run_async_sync(
            self.fetch_async(track_name, artist_name, isrc, album_name, duration_ms),
        )

    def _search(
        self,
        title: str,
        artist: str,
        isrc: str,
        album: str = "",
        duration_ms: int = 0,
    ) -> dict[str, Any] | None:
        return _run_async_sync(
            self._search_async(title, artist, isrc, album, duration_ms),
        )

    def _best(
        self,
        results: list[dict[str, Any]],
        title: str,
        artist: str,
        album: str,
        duration_ms: int,
    ) -> dict[str, Any] | None:
        """The best-scoring hit, or None if none of them is good enough.

        None is a real answer here. iTunes has no "no match" response: an
        unknown ISRC or a misspelt title still comes back with songs, and
        the old code returned results[0] regardless — so a track iTunes had
        never heard of was tagged with the genre, the explicit flag and the
        cover art of whatever happened to rank first.
        """
        if not results:
            return None
        if not title:
            return results[0]

        best: dict[str, Any] | None = None
        best_score = 0.0
        for item in results:
            score = score_track_match(
                title=title,
                artist=artist,
                album=album,
                duration_ms=duration_ms,
                candidate=_ItunesCandidate.of(item),
            )
            if score > best_score:
                best, best_score = item, score

        if best is None or best_score < _APPLE_MATCH_MIN:
            logger.debug(
                "[meta/apple] no result above %.2f for %r / %r (best %.2f)",
                _APPLE_MATCH_MIN,
                title,
                artist,
                best_score,
            )
            return None
        return best

    async def _search_async(
        self,
        title: str,
        artist: str,
        isrc: str,
        album: str = "",
        duration_ms: int = 0,
    ) -> dict[str, Any] | None:
        try:
            client = await NetworkManager.get_async_client_safe()
            if isrc:
                r = await client.get(
                    self.SEARCH,
                    params={
                        "term": isrc,
                        "media": "music",
                        "entity": "song",
                        "limit": 5,
                        "country": "US",
                    },
                    headers={"User-Agent": _UA},
                    timeout=_HTTP_TIMEOUT,
                )
                if r.is_success:
                    # Not a lookup by identifier — iTunes has no such
                    # endpoint publicly, this is a full-text search whose
                    # term happens to be an ISRC — so the hits are checked
                    # like any others rather than trusted for their query.
                    item = self._best(
                        r.json().get("results", []),
                        title,
                        artist,
                        album,
                        duration_ms,
                    )
                    if item:
                        return item
            r = await client.get(
                self.SEARCH,
                params={
                    "term": f"{title} {artist}",
                    "media": "music",
                    "entity": "song",
                    "limit": 5,
                    "country": "US",
                },
                headers={"User-Agent": _UA},
                timeout=_HTTP_TIMEOUT,
            )
            if not r.is_success:
                return None
            return self._best(
                r.json().get("results", []),
                title,
                artist,
                album,
                duration_ms,
            )
        except Exception as exc:
            logger.debug("[meta/apple] async %s", exc)
            return None

    async def fetch_async(
        self,
        track_name: str,
        artist_name: str,
        isrc: str = "",
        album_name: str = "",
        duration_ms: int = 0,
    ) -> EnrichedMetadata:
        out = EnrichedMetadata()
        item = await self._search_async(
            track_name,
            artist_name,
            isrc,
            album_name,
            duration_ms,
        )
        if not item:
            return out
        out.genre = item.get("primaryGenreName", "")
        out.explicit = item.get("trackExplicitness") == "explicit"
        raw_art = item.get("artworkUrl100", "")
        found_album = str(item.get("collectionName") or "")
        # The cover is not the only field that belongs to the release: the
        # date, the track and disc counts and the kind of record do too. A
        # song found on a compilation used to give the file that
        # compilation's 40 tracks and its year, as long as the cover was
        # held back. Mornye applies release fields only from the same
        # release, and so does this now.
        if not album_name or _same_release(album_name, found_album):
            out.cover_url_hd = raw_art.replace("100x100", "600x600")
            # ISO-8601 with a time part; only the date half is a release date.
            out.release_date = str(item.get("releaseDate") or "")[:10]
            out.total_tracks = int(item.get("trackCount") or 0)
            out.total_discs = int(item.get("discCount") or 0)
            # A song result carries no collectionType — that only appears on
            # album lookups — so the release kind is inferred from its size,
            # which is all iTunes actually tells us here.
            if out.total_tracks:
                out.album_type = "single" if out.total_tracks == 1 else "album"
        else:
            logger.debug(
                "[meta/apple] release mismatch: wanted %r, got %r — keeping "
                "track metadata only",
                album_name,
                found_album,
            )
        return out


# ---------------------------------------------------------------------------
# Singleton provider instances
# ---------------------------------------------------------------------------

_singleton_lock = threading.Lock()
_deezer_inst: _DeezerMeta | None = None
_apple_inst: _AppleMusicMeta | None = None


def _get_deezer() -> _DeezerMeta:
    global _deezer_inst
    if _deezer_inst is None:
        with _singleton_lock:
            if _deezer_inst is None:
                _deezer_inst = _DeezerMeta()
    return _deezer_inst


def _get_apple() -> _AppleMusicMeta:
    global _apple_inst
    if _apple_inst is None:
        with _singleton_lock:
            if _apple_inst is None:
                _apple_inst = _AppleMusicMeta()
    return _apple_inst


# ---------------------------------------------------------------------------
# Async fetch wrappers per i provider sync (Phase 2)
# ---------------------------------------------------------------------------


async def _deezer_fetch_async(
    isrc: str, album_name: str = "", track_name: str = "", duration_ms: int = 0
) -> EnrichedMetadata:
    return await _get_deezer().fetch_async(isrc, album_name, track_name, duration_ms)


async def _apple_fetch_async(
    track_name: str,
    artist_name: str,
    isrc: str,
    album_name: str = "",
    duration_ms: int = 0,
) -> EnrichedMetadata:
    return await _get_apple().fetch_async(
        track_name,
        artist_name,
        isrc,
        album_name,
        duration_ms,
    )


# ---------------------------------------------------------------------------
# Async enrich_metadata — Phase 2 (new)
# ---------------------------------------------------------------------------


async def enrich_metadata_async(
    track_name: str,
    artist_name: str,
    isrc: str = "",
    providers: list[str] | None = None,
    timeout_s: float = _GLOBAL_TIMEOUT,
    qobuz_token: str | None = None,
    album_name: str = "",
    duration_ms: int = 0,
    skip_extensions: tuple[str, ...] | list[str] = (),
) -> EnrichedMetadata:
    """Queries providers in parallel with asyncio.gather + global timeout.
    Replaces the sync version's ThreadPoolExecutor.

    `album_name` and `duration_ms` are what the caller already knows about
    the track, and they are what lets a provider's answer be *checked*
    rather than accepted: they gate Deezer's release-scoped fields and feed
    the iTunes match score. Both are optional and both default to the old,
    unverified behaviour when the caller has nothing to offer.
    """
    if providers is None:
        providers = list(ENRICH_PROVIDERS)
    # Only Deezer and Apple are enrichment sources now. SoundCloud had no
    # ISRC, label or release data to add, only a title search. Qobuz and
    # Tidal had no built-in lookup that worked without a Python extension,
    # and asking their JavaScript extensions cost a signed session (a
    # captcha in a hidden browser) plus a search and a lookup per track.
    # A saved setting or profile that still names any of them is read
    # without it.
    providers = [p for p in dict.fromkeys(providers) if p in ENRICH_PROVIDERS]

    if isrc:
        cached = _get_cached(isrc)
        if cached is not None:
            return cached

    async def run_provider(name: str) -> tuple[str, EnrichedMetadata]:
        try:
            if name == "deezer":
                return name, await _deezer_fetch_async(
                    isrc, album_name, track_name=track_name, duration_ms=duration_ms
                )
            if name == "apple":
                return name, await _apple_fetch_async(
                    track_name,
                    artist_name,
                    isrc,
                    album_name,
                    duration_ms,
                )
            logger.warning("[meta/enrich] provider sconosciuto: %s", name)
            return name, EnrichedMetadata()
        except Exception as exc:
            logger.debug("[meta/enrich] %s failed: %s", name, exc)
            return name, EnrichedMetadata()

    # Each service in the list is asked twice where it can be: the built-in
    # lookup above, then its own JavaScript extensions (deezer,
    # apple-music). Only the services in the list: every other
    # installed extension used to be asked too, whatever its service, and
    # for the zarz-backed ones each such ask is a signed session of its own —
    # a captcha solved in a hidden browser, then a search and a lookup — on
    # every track, for services the user had not chosen. A Tidal download
    # woke Qobuz that way. `skip_extensions` names the extension the file
    # came from: what it knows is already in the caller's source_tags.
    # See core/extension_enrichment.py.
    from . import extension_enrichment

    skip = {str(e).removeprefix("ext:") for e in (skip_extensions or ()) if e}
    ext_sources: dict[str, list[str]] = {}
    if extension_enrichment.extension_enrichment_enabled():
        try:
            for name in providers:
                ext_sources[name] = [
                    ext
                    for ext in extension_enrichment.extensions_for_service(name)
                    if ext not in skip
                ]
        except Exception as exc:
            logger.debug("[meta/enrich] could not list enriching extensions: %s", exc)

    async def bounded(coro, limit: float, label: str):
        # A timeout per source, not one for the lot: a slow extension used
        # to be able to cost every provider's answer, and the extensions'
        # first call also starts their runtime.
        try:
            return await asyncio.wait_for(coro, timeout=limit)
        except TimeoutError:
            logger.warning("[meta/enrich] %s timed out after %.1fs", label, limit)
            return None

    ext_keys = [(name, ext) for name in providers for ext in ext_sources.get(name, [])]
    outcomes = await asyncio.gather(
        *[bounded(run_provider(p), timeout_s, p) for p in providers],
        *[
            bounded(
                extension_enrichment.fetch_extension_enrichment(
                    ext, track_name, artist_name, isrc, album_name, duration_ms
                ),
                max(timeout_s, extension_enrichment.EXTENSION_TIMEOUT_S),
                f"ext:{ext}",
            )
            for _name, ext in ext_keys
        ],
    )
    results = {
        outcome[0]: outcome[1]
        for outcome in outcomes[: len(providers)]
        if isinstance(outcome, tuple)
    }
    ext_results = dict(zip(ext_keys, outcomes[len(providers) :], strict=False))

    # Every provider has already answered — gather() above waits for all of
    # them — so this loop spends nothing but the merge. It used to stop at
    # the first is_complete(), which saved no request and threw away fields
    # already in hand: the disc count only Apple reports was discarded
    # whenever Deezer had happened to supply genre, label and cover.
    # merge() only ever fills blanks, in the caller's provider order, so
    # reading them all cannot change which provider wins a field.
    merged = EnrichedMetadata()
    for name in providers:
        data = results.get(name)
        if isinstance(data, EnrichedMetadata):
            merged.merge(data, name)
        for ext in ext_sources.get(name, []):
            data = ext_results.get((name, ext))
            if isinstance(data, EnrichedMetadata):
                merged.merge(data, f"ext:{ext}")
    if merged._sources:
        logger.debug("[meta/enrich] async enriched: %s", merged._sources)

    if isrc:
        # Unconditional now: _put_cached() decides for itself whether this
        # is worth persisting, and stores a miss in memory on the short TTL
        # so the same barren ISRC does not re-run four providers per pass.
        _put_cached(isrc, merged)

    return merged
