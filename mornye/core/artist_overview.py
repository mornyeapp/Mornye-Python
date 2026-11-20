"""The sections of a Spotify artist page, beyond the track list.

Spotify's own artist page is built from one `queryArtistOverview` answer:
popular tracks, the artist's pick, the discography split into albums,
singles and compilations, the playlists that feature the artist, related
artists and the releases the artist appears on. The metadata client already
sends that query for the profile (name, listeners, biography) and throws the
rest away; this module asks for it again and keeps those sections, in a flat
shape the frontend can draw without knowing Spotify's GraphQL.

Everything is best effort: a section Spotify leaves out, or sends as an
error node, comes back as an empty list.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

_OVERVIEW_HASH = "446130b4a0aa6522a686aafccddb0ae849165b5e0436fd802f96e0243617b5d8"
_ARTIST_ID = re.compile(
    r"(?:open\.spotify\.com/(?:intl-[a-z-]+/)?artist/|spotify:artist:)([A-Za-z0-9]{22})"
)


def artist_id_from_url(url: str) -> str:
    m = _ARTIST_ID.search(url or "")
    return m.group(1) if m else ""


def uri_to_url(uri: str) -> str:
    """`spotify:album:ID` → `https://open.spotify.com/album/ID`."""
    parts = (uri or "").split(":")
    if len(parts) == 3 and parts[0] == "spotify" and parts[2]:
        return f"https://open.spotify.com/{parts[1]}/{parts[2]}"
    return ""


def _get(node: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _items(node: Any) -> list:
    items = _get(node, "items")
    return items if isinstance(items, list) else []


def _image(sources: Any) -> str:
    """The widest source; Spotify lists them in no fixed order."""
    if not isinstance(sources, list):
        return ""
    best, best_w = "", -1
    for s in sources:
        if not isinstance(s, dict) or not s.get("url"):
            continue
        w = s.get("width") or 0
        if w > best_w:
            best, best_w = s["url"], w
    return best


def _names(artists: Any) -> list[str]:
    return [n for n in (_get(a, "profile", "name") for a in _items(artists)) if n]


def _release(node: Any) -> dict | None:
    if not isinstance(node, dict) or not node.get("uri"):
        return None
    return {
        "name": node.get("name") or "",
        "url": uri_to_url(node["uri"]),
        "cover": _image(_get(node, "coverArt", "sources")),
        "year": _get(node, "date", "year") or "",
        "type": (node.get("type") or "").lower(),
        "tracks": _get(node, "tracks", "totalCount") or 0,
        "artists": _names(node.get("artists")),
    }


def _releases(group: Any) -> list[dict]:
    """A discography group: each item wraps its releases (an album and its
    re-issues); the first is the one Spotify shows."""
    out = []
    for item in _items(group):
        if not isinstance(item, dict):
            continue
        rel = _release((_items(item.get("releases")) or [item])[0])
        if rel:
            out.append(rel)
    return out


def _playlist(node: Any) -> dict | None:
    data = _get(node, "data")
    if (
        not isinstance(data, dict)
        or data.get("__typename") != "Playlist"
        or not data.get("uri")
    ):
        return None
    images = _items(data.get("images"))
    return {
        "name": data.get("name") or "",
        "url": uri_to_url(data["uri"]),
        "cover": _image(_get(images[0], "sources")) if images else "",
        "description": re.sub(r"<[^>]+>", "", data.get("description") or ""),
        "owner": _get(data, "ownerV2", "data", "name") or "",
    }


def _playlists(group: Any) -> list[dict]:
    return [p for p in (_playlist(i) for i in _items(group)) if p]


def _top_track(item: Any) -> dict | None:
    t = _get(item, "track")
    if not isinstance(t, dict) or not t.get("uri"):
        return None
    try:
        plays = int(t.get("playcount") or 0)
    except (TypeError, ValueError):
        plays = 0
    return {
        "id": t.get("id") or t["uri"].rsplit(":", 1)[-1],
        "name": t.get("name") or "",
        "url": uri_to_url(t["uri"]),
        "album_url": uri_to_url(_get(t, "albumOfTrack", "uri") or ""),
        "cover": _image(_get(t, "albumOfTrack", "coverArt", "sources")),
        "artists": _names(t.get("artists")),
        "plays": plays,
        "duration_ms": _get(t, "duration", "totalMilliseconds") or 0,
        "explicit": _get(t, "contentRating", "label") == "EXPLICIT",
    }


def _related_artist(node: Any) -> dict | None:
    if not isinstance(node, dict) or not node.get("uri"):
        return None
    return {
        "name": _get(node, "profile", "name") or "",
        "url": uri_to_url(node["uri"]),
        "avatar": _image(_get(node, "visuals", "avatarImage", "sources")),
    }


def _pick(profile: Any) -> dict | None:
    pinned = _get(profile, "pinnedItem")
    data = _get(pinned, "itemV2", "data")
    if not isinstance(data, dict) or not data.get("uri"):
        return None
    images = _items(data.get("images"))
    cover = _image(_get(data, "coverArt", "sources")) or (
        _image(_get(images[0], "sources")) if images else ""
    )
    return {
        "comment": _get(pinned, "comment") or "",
        "name": data.get("name") or "",
        "url": uri_to_url(data["uri"]),
        "cover": cover,
        "type": (data.get("__typename") or "").lower(),
    }


def normalize_overview(artist: dict) -> dict:
    """`data.artistUnion` of a queryArtistOverview answer → page sections."""
    disc = artist.get("discography") or {}
    related = artist.get("relatedContent") or {}
    profile = artist.get("profile") or {}
    return {
        "top_tracks": [
            t for t in (_top_track(i) for i in _items(disc.get("topTracks"))) if t
        ],
        "pick": _pick(profile),
        "discography": {
            "popular": [
                r
                for r in (
                    _release(i) for i in _items(disc.get("popularReleasesAlbums"))
                )
                if r
            ],
            "albums": _releases(disc.get("albums")),
            "singles": _releases(disc.get("singles")),
            "compilations": _releases(disc.get("compilations")),
        },
        "featuring": _playlists(related.get("featuringV2")),
        "discovered_on": _playlists(related.get("discoveredOnV2")),
        "playlists": _playlists(profile.get("playlistsV2")),
        "related_artists": [
            a
            for a in (_related_artist(i) for i in _items(related.get("relatedArtists")))
            if a
        ],
        "appears_on": _releases(related.get("appearsOn")),
    }


async def fetch_artist_overview_async(url: str) -> dict:
    """The sections for a Spotify artist link; raises on a non-artist link."""
    artist_id = artist_id_from_url(url)
    if not artist_id:
        raise ValueError("Not a Spotify artist link")
    from .spotify_metadata import SpotifyMetadataClient

    client = SpotifyMetadataClient()
    payload = {
        "operationName": "queryArtistOverview",
        "variables": {"uri": f"spotify:artist:{artist_id}", "locale": ""},
        "extensions": {"persistedQuery": {"version": 1, "sha256Hash": _OVERVIEW_HASH}},
    }
    response = await asyncio.to_thread(client.web_client.query, payload)
    artist = _get(response, "data", "artistUnion")
    if not isinstance(artist, dict):
        raise ValueError("Spotify sent no artist for this link")
    return normalize_overview(artist)
