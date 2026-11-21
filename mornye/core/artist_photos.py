"""artist_photos.py — An artist's own photo for the Player's library.

The library's artist cards used to show one of the artist's album covers.
The photo they show now is the one the text search shows next to the artist
(Home → search): Spotify's artist search, `artists[].cover_url`, read from
the artist's `visualIdentity` / `avatarImage`.

A library can hold thousands of artists and the cards ask as they scroll
into view, so:

* the answer is kept on disk (`<cache>/artist-photos.json`), "nothing found"
  included, so a card that scrolls back asks nobody;
* at most `_MAX_LOOKUPS` searches run at once, whatever the number of
  windows or batches asking;
* a search that fails (offline, rate limited) is not recorded, so it is
  tried again later; one that answers without a matching artist is, and
  is retried after `_MISS_TTL`.

The best match is picked by name, not the first result: searching "Low"
returns a dozen artists, and the photo must be the one called Low.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import threading
import time
import unicodedata
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .atomic_io import write_json_atomic
from .paths import cache_path

logger = logging.getLogger(__name__)

_CACHE_NAME = "artist-photos.json"
#: A found photo is kept a month: Spotify's image URLs outlive that.
_HIT_TTL = 30 * 86400
#: "No such artist" is asked again after a week: the artist may appear.
_MISS_TTL = 7 * 86400
#: Searches running at once, across every caller.
_MAX_LOOKUPS = 3
#: Names one call answers; the rest of a batch is the caller's next call.
_MAX_BATCH = 24
#: Below this similarity no result is the artist.
_MIN_RATIO = 0.88
#: Names that stand for no one: the library's own placeholder and the usual
#: tag fillers. Spotify has artists called this, and their photo is wrong.
_PLACEHOLDERS = frozenset(
    {"unknown", "unknown artist", "various", "various artists", "va", "artist"}
)

_lock = threading.Lock()
_lookups = threading.BoundedSemaphore(_MAX_LOOKUPS)
_cache: dict[str, dict] | None = None

#: (name) -> list of artist dicts with "name" and "cover_url". Replaced in tests.
Searcher = Callable[[str], list]


def normalize(name: str) -> str:
    """The form two spellings of one artist share: no accents, no case, no
    punctuation, "&" as "and", no leading "The"."""
    text = unicodedata.normalize("NFKD", str(name or ""))
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    text = text.replace("&", " and ")
    text = re.sub(r"[^\w]+", " ", text).strip()
    text = re.sub(r"^the\s+", "", text)
    return re.sub(r"\s+", " ", text)


def best_match(name: str, artists: Iterable[Any]) -> str:
    """The photo of the result that is `name`, "" if none is.

    An exact match (after `normalize`) wins over a close one, and among
    equals the search's own order decides — it ranks the more popular
    artist first. A result with no photo is skipped, so a same-named
    artist with one can still be used.
    """
    want = normalize(name)
    if not want or want in _PLACEHOLDERS:
        return ""
    best_url, best_ratio = "", 0.0
    for art in artists or []:
        if not isinstance(art, dict):
            continue
        url = str(art.get("cover_url") or art.get("images") or "")
        if not url.startswith(("https://", "http://")):
            continue
        got = normalize(art.get("name") or art.get("title") or "")
        if not got:
            continue
        if got == want:
            return url
        ratio = difflib.SequenceMatcher(None, want, got).ratio()
        if ratio > best_ratio:
            best_url, best_ratio = url, ratio
    return best_url if best_ratio >= _MIN_RATIO else ""


def _spotify_search(name: str) -> list:
    """The artists the text search would list for `name`."""
    from .loop_runner import run_sync
    from .spotify_metadata import SpotifyMetadataClient

    results = run_sync(SpotifyMetadataClient().search_async(name, limit=10), 20)
    return list((results or {}).get("artists") or [])


def _path():
    return cache_path(_CACHE_NAME)


def _load() -> dict[str, dict]:
    """The cache, read once. Call with `_lock` held."""
    global _cache
    if _cache is None:
        try:
            data = json.loads(_path().read_text(encoding="utf-8"))
            _cache = data if isinstance(data, dict) else {}
        except FileNotFoundError:
            _cache = {}
        except Exception as exc:
            logger.warning("[artist_photos] Could not read the cache: %s", exc)
            _cache = {}
    return _cache


def cached(name: str, now: float | None = None) -> str | None:
    """The stored photo URL ("" = none found), or None if it must be asked."""
    key = normalize(name)
    if not key or key in _PLACEHOLDERS:
        return ""
    with _lock:
        entry = _load().get(key)
    if not isinstance(entry, dict):
        return None
    url = str(entry.get("url") or "")
    try:
        ts = float(entry.get("ts") or 0)
    except (TypeError, ValueError):
        return None
    age = (now if now is not None else time.time()) - ts
    if age > (_HIT_TTL if url else _MISS_TTL):
        return None
    return url


def _store(results: dict[str, str]) -> None:
    if not results:
        return
    ts = time.time()
    with _lock:
        cache = _load()
        for key, url in results.items():
            cache[key] = {"url": url, "ts": ts}
        try:
            write_json_atomic(_path(), cache)
        except Exception as exc:
            logger.warning("[artist_photos] Could not write the cache: %s", exc)


def _lookup(name: str, search: Searcher) -> str | None:
    """Searches for `name`; None when the search itself failed."""
    with _lookups:
        try:
            return best_match(name, search(name))
        except Exception as exc:
            logger.debug("[artist_photos] Search for %r failed: %s", name, exc)
            return None


def artist_photos(names: Iterable[Any], search: Searcher | None = None) -> dict:
    """{name: photo URL or ""} for up to `_MAX_BATCH` names.

    Stored answers come back at once; the others are searched, at most
    `_MAX_LOOKUPS` at a time, and stored. A name whose search failed
    comes back as "" without being stored.
    """
    wanted = [
        n for n in dict.fromkeys(names or []) if isinstance(n, str) and n.strip()
    ][:_MAX_BATCH]
    out: dict[str, str] = {}
    missing: dict[str, list[str]] = {}
    for name in wanted:
        hit = cached(name)
        if hit is None:
            missing.setdefault(normalize(name), []).append(name)
        else:
            out[name] = hit
    if missing:
        searcher = search or _spotify_search
        # Spellings that normalize alike are one search.
        firsts = [spellings[0] for spellings in missing.values()]
        with ThreadPoolExecutor(max_workers=min(_MAX_LOOKUPS, len(firsts))) as pool:
            found = list(pool.map(lambda n: _lookup(n, searcher), firsts))
        fresh: dict[str, str] = {}
        for (key, spellings), url in zip(missing.items(), found, strict=True):
            if url is not None:
                fresh[key] = url
            for name in spellings:
                out[name] = url or ""
        _store(fresh)
    return out


def reset_for_tests() -> None:
    global _cache
    with _lock:
        _cache = None
