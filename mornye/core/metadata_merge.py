"""metadata_merge.py — the most complete metadata out of two readers.

A Spotify, Apple Music or Tidal link can be read by Mornye's built-in client
and by the JS extension for that catalogue (spotify-web, apple-music,
tidal-web). Neither is a superset of the other: the built-in clients carry
play counts, composer and the real artist lists; the extensions often carry
the label, UPC, copyright or a full release date the built-in reader left
empty. Instead of trusting one and discarding the other, the two answers are
merged field by field:

* the built-in answer is the base — its track ids, order and non-empty
  values stay, so the ISRC lookup, duplicate checks and the download log
  see the same track as before;
* an empty (or placeholder) field is filled from the extension;
* where both have a value, the more complete one wins: the longer artist
  list, the more precise release date, the union of genres, the larger
  track/disc totals.

Tracks are paired by ISRC, then by id, then by title (and disc/track number
when both know it). The track list stays the built-in reader's; when that
reader fails or finds nothing, the extension's answer is used on its own.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .models import TrackMetadata

#: Values readers use to mean "nothing here".
_PLACEHOLDERS = {"", "unknown", "0", "none", "null"}

#: Fields merged with the generic "fill if empty" rule.
_FILL_FIELDS = (
    "title",
    "album",
    "isrc",
    "cover_url",
    "external_url",
    "copyright",
    "publisher",
    "composer",
    "upc",
    "album_type",
    "preview_url",
    "album_id",
    "album_url",
    "artist_id",
    "artist_url",
    "status",
    "rank",
    "description",
    "avatar_url",
    "header_url",
)

_INT_FILL_FIELDS = ("track_number", "duration_ms", "bpm")
_INT_MAX_FIELDS = ("total_tracks", "disc_number", "total_discs")
_LIST_FIELDS = ("artist_names", "album_artist_names", "artists_data")


def _empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in _PLACEHOLDERS
    if isinstance(value, (list, dict, tuple, set)):
        return not value
    return False


def _split_names(value: str) -> list[str]:
    return [p.strip() for p in re.split(r"[,;]", value or "") if p.strip()]


def _more_artists(base: str, other: str) -> str:
    if _empty(base):
        return other
    if _empty(other):
        return base
    return other if len(_split_names(other)) > len(_split_names(base)) else base


def _more_precise_date(base: str, other: str) -> str:
    """``2017-04-21`` over ``2017``, when both describe the same year."""
    if _empty(base):
        return other
    if _empty(other):
        return base
    if len(other) > len(base) and other.startswith(base[:4]):
        return other
    return base


def _union_genres(base: str, other: str) -> str:
    if _empty(base):
        return other
    if _empty(other):
        return base
    seen: dict[str, str] = {}
    for name in _split_names(base) + _split_names(other):
        seen.setdefault(name.lower(), name)
    return ", ".join(seen.values())


def merge_track(base: TrackMetadata, other: TrackMetadata) -> TrackMetadata:
    """``base`` with every gap filled, and every value improved, from ``other``."""
    update: dict[str, Any] = {}

    for name in _FILL_FIELDS:
        mine, theirs = getattr(base, name), getattr(other, name)
        if _empty(mine) and not _empty(theirs):
            update[name] = theirs

    for name in _INT_FILL_FIELDS:
        if not getattr(base, name) and getattr(other, name):
            update[name] = getattr(other, name)

    for name in _INT_MAX_FIELDS:
        mine, theirs = getattr(base, name) or 0, getattr(other, name) or 0
        if theirs > mine:
            update[name] = theirs

    for name in _LIST_FIELDS:
        mine, theirs = getattr(base, name) or [], getattr(other, name) or []
        if len(theirs) > len(mine):
            update[name] = list(theirs)

    artists = _more_artists(base.artists, other.artists)
    if artists != base.artists:
        update["artists"] = artists
    album_artist = _more_artists(base.album_artist, other.album_artist)
    if album_artist != base.album_artist:
        update["album_artist"] = album_artist

    release_date = _more_precise_date(base.release_date, other.release_date)
    if release_date != base.release_date:
        update["release_date"] = release_date

    genre = _union_genres(base.genre, other.genre)
    if genre != base.genre:
        update["genre"] = genre

    if _empty(base.plays) and not _empty(other.plays):
        update["plays"] = other.plays

    if other.is_explicit and not base.is_explicit:
        update["is_explicit"] = True

    if other.extra_info:
        update["extra_info"] = {**other.extra_info, **(base.extra_info or {})}

    if not update:
        return base
    return base.model_copy(update=update)


def _norm_title(title: str) -> str:
    text = unicodedata.normalize("NFKD", title or "").casefold()
    text = re.sub(r"\(.*?\)|\[.*?\]", " ", text)
    return re.sub(r"[^\w]+", " ", text).strip()


def _bare_id(track_id: str) -> str:
    return re.sub(r"^[a-z]+_", "", str(track_id or ""))


def _pair(
    base: TrackMetadata, candidates: list[TrackMetadata], used: set[int]
) -> int | None:
    if base.isrc:
        for idx, cand in enumerate(candidates):
            if idx not in used and cand.isrc and cand.isrc.upper() == base.isrc.upper():
                return idx
    base_id = _bare_id(base.id)
    if base_id:
        for idx, cand in enumerate(candidates):
            if idx not in used and _bare_id(cand.id) == base_id:
                return idx
    title = _norm_title(base.title)
    if not title:
        return None
    for idx, cand in enumerate(candidates):
        if idx in used or _norm_title(cand.title) != title:
            continue
        if not base.track_number or not cand.track_number:
            return idx
        if cand.track_number == base.track_number and (cand.disc_number or 1) == (
            base.disc_number or 1
        ):
            return idx
    return None


def merge_track_lists(base: list[Any], other: list[Any]) -> list[Any]:
    """``base``'s tracks, each merged with its counterpart in ``other``.

    The track list itself is ``base``'s: a track only ``other`` returned is
    not added, because one the pairing merely failed to recognise would be
    downloaded twice. Anything that is not a TrackMetadata passes through
    untouched.
    """
    if not other:
        return list(base)
    if not base:
        return list(other)
    candidates = [t for t in other if isinstance(t, TrackMetadata)]
    used: set[int] = set()
    merged: list[Any] = []
    for track in base:
        if not isinstance(track, TrackMetadata):
            merged.append(track)
            continue
        idx = _pair(track, candidates, used)
        if idx is None:
            merged.append(track)
            continue
        used.add(idx)
        merged.append(merge_track(track, candidates[idx]))
    return merged


def merge_url_results(base: Any, other: Any) -> Any:
    """Merge two ``get_url_async`` answers, ``(name, tracks, cover, meta)``."""
    if not other or len(other) < 2 or not other[1]:
        return base
    if not base or len(base) < 2 or not base[1]:
        return other
    name = base[0] if not _empty(base[0]) else other[0]
    tracks = merge_track_lists(list(base[1]), list(other[1]))
    rest = list(base[2:])
    other_rest = list(other[2:])
    # The collection cover, then the meta dict.
    if rest and _empty(rest[0]) and other_rest and not _empty(other_rest[0]):
        rest[0] = other_rest[0]
    if (
        len(rest) > 1
        and len(other_rest) > 1
        and isinstance(rest[1], dict)
        and isinstance(other_rest[1], dict)
    ):
        rest[1] = {**other_rest[1], **rest[1]}
    return (name, tracks, *rest)
