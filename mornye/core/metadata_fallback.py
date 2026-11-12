"""metadata_fallback.py — Built-in clients and JS extensions, read together.

Since the metadata merge (core/metadata_merge.py) both readers are asked for
every Spotify, Apple Music and Tidal link and their answers combined, so the
result is the most complete either could give; the history below is why the
built-in client stays the base of that merge.

The built-in Spotify client depends on things Spotify changes without
notice: its web-player secrets, the TOTP that turns them into a token, the
GraphQL hashes. When one of them moves, every Spotify link stops resolving
until the client is fixed. The mobile app's `spotify-web` and `apple-music`
extensions read the same catalogues by their own route and are maintained
separately, so the day one breaks is rarely the day the other does.

So the built-in client stays first, always — it is faster, and it carries
what the extensions do not (play counts, composer, Canvas, TTML lyrics).
Only when it raises, or finds nothing, is an installed metadata extension
whose `urlHandler` claims the link asked instead, and the log says so.

A track resolved this way gets the id the built-in client would have given
it — the bare Spotify id, `apple_<id>` for Apple Music — so the ISRC lookup,
duplicate checks and the download log see the same track either way.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from .errors import ErrorKind, MornyeError
from .extension_metadata import _first_image, _int, _text
from .models import TrackMetadata
from .url_utils import url_host_matches

logger = logging.getLogger(__name__)

#: Services whose built-in client an extension reads alongside, with the id
#: prefix that client gives its tracks.
_ID_PREFIX = {"spotify": "", "apple": "apple_", "tidal": "tidal_"}

_LABELS = {"spotify": "Spotify", "apple": "Apple Music", "tidal": "Tidal"}

#: How long one extension call may take.
EXTENSION_TIMEOUT_S = 90


def native_service_for(url: str) -> str | None:
    if (url or "").startswith("spotify:") or url_host_matches(
        url, "open.spotify.com", "spotify.com"
    ):
        return "spotify"
    if url_host_matches(url, "music.apple.com"):
        return "apple"
    if (url or "").startswith("tidal:") or url_host_matches(
        url, "tidal.com", "listen.tidal.com"
    ):
        return "tidal"
    return None


def fallback_extension_for(url: str, manager: Any = None) -> str | None:
    """The installed metadata extension whose urlHandler claims `url`."""
    if manager is None:
        from ..extensions.manager import ExtensionManager

        manager = ExtensionManager(auto_install_downloads=False)
    try:
        installed = manager.list_installed()
    except Exception as exc:
        logger.debug("[metadata] could not list extensions: %s", exc)
        return None
    # Lazy: importing mornye.extensions from core at module load would make
    # the two packages import each other.
    from ..extensions.url_match import handler_matches

    for ext in installed:
        if "metadata_provider" not in (ext.types or []):
            continue
        if handler_matches(ext.manifest, url or ""):
            return ext.name
    return None


def _external_url(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("spotify", "apple", "appleMusic"):
            if value.get(key):
                return str(value[key])
        return str(next((v for v in value.values() if v), ""))
    return str(value or "")


def track_from_extension(
    item: dict, service: str, collection: dict | None = None
) -> TrackMetadata | None:
    """One track as spotify-web or apple-music describe it."""
    if not isinstance(item, dict):
        return None
    raw_id = str(item.get("spotify_id") or item.get("id") or "")
    title = _text(item.get("name") or item.get("title"))
    if not raw_id or not title:
        return None

    # A source with no built-in client (Jellyfin, a self-hosted server) keeps
    # the extension's own ids: there is no other client for them to match.
    prefix = _ID_PREFIX.get(service, "")
    collection = collection or {}
    artists = _text(item.get("artists") or item.get("artist"))
    album = _text(item.get("album_name"))
    if not album and collection.get("type") == "album":
        album = _text(collection.get("name"))

    return TrackMetadata(
        id=raw_id if raw_id.startswith(prefix) else f"{prefix}{raw_id}",
        title=title,
        artists=artists,
        album=album,
        album_artist=_text(item.get("album_artist")) or artists,
        isrc=str(item.get("isrc") or "").strip().upper(),
        track_number=_int(item.get("track_number")),
        disc_number=_int(item.get("disc_number")) or 1,
        total_tracks=_int(item.get("total_tracks")),
        total_discs=_int(item.get("total_discs")) or 1,
        duration_ms=_int(item.get("duration_ms")),
        release_date=str(item.get("release_date") or ""),
        cover_url=_first_image(item) or _first_image(collection),
        external_url=_external_url(
            item.get("external_urls") or item.get("external_url")
        ),
        copyright=str(item.get("copyright") or ""),
        publisher=str(item.get("label") or ""),
        composer=_text(item.get("composer")),
        genre=_text(item.get("genre")),
        upc=str(item.get("upc") or ""),
        album_type=str(item.get("album_type") or ""),
        preview_url=str(item.get("preview_url") or ""),
        album_id=str(item.get("album_id") or ""),
        album_url=str(item.get("album_url") or ""),
        artist_id=str(item.get("artist_id") or ""),
        artist_url=str(item.get("artist_url") or ""),
        is_explicit=bool(item.get("explicit")),
    )


class ExtensionUrlClient:
    """`get_url_async` over an extension's `handleUrl`, in the shape the
    built-in clients answer: `(name, tracks, cover, meta)`."""

    def __init__(
        self, ext_name: str, service: str, *, provider_factory: Any = None
    ) -> None:
        self.ext_name = ext_name
        self.service = service
        self._provider_factory = provider_factory

    def _call_sync(self, method: str, *args: Any) -> Any:
        if self._provider_factory is not None:
            provider = self._provider_factory(self.ext_name)
        else:
            from ..extensions.provider import JSExtensionProvider

            provider = JSExtensionProvider(self.ext_name, timeout_s=EXTENSION_TIMEOUT_S)
        try:
            return provider._call(method, *args)
        finally:
            close = getattr(provider, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:
                    pass

    async def get_url_async(self, url: str, include_featuring: bool = True, **_: Any):
        response = await asyncio.to_thread(self._call_sync, "handleUrl", url)
        if not isinstance(response, dict) or response.get("success") is False:
            reason = response.get("error") if isinstance(response, dict) else ""
            raise MornyeError(
                ErrorKind.TRACK_NOT_FOUND,
                f"The '{self.ext_name}' extension could not read this link"
                + (f": {reason}" if reason else "."),
                provider=f"ext:{self.ext_name}",
            )

        kind = str(response.get("type") or "")
        if kind == "track" and isinstance(response.get("track"), dict):
            track = track_from_extension(response["track"], self.service)
            tracks = [track] if track else []
            name = track.title if track else ""
            cover = track.cover_url if track else ""
        else:
            node_value = response.get(kind)
            node: dict[str, Any] = node_value if isinstance(node_value, dict) else {}
            collection = {
                **node,
                "type": kind,
                "name": response.get("name") or node.get("name"),
                "cover_url": response.get("cover_url") or node.get("cover_url"),
            }
            items = response.get("tracks") or node.get("tracks") or []
            tracks = [
                t
                for item in items
                if (t := track_from_extension(item, self.service, collection))
            ]
            name = _text(collection["name"])
            cover = _first_image(collection)

        if not tracks:
            raise MornyeError(
                ErrorKind.TRACK_NOT_FOUND,
                f"The '{self.ext_name}' extension found no tracks behind this {kind or 'link'}.",
                provider=f"ext:{self.ext_name}",
            )
        # Which extension the tracks came from, so the download can honour
        # what its manifest asks (skipBuiltInFallback,
        # skipMetadataEnrichment — application/download_engine.py).
        for track in tracks:
            track.extra_info = {
                **(track.extra_info or {}),
                "source_extension": self.ext_name,
            }
        return name, tracks, cover, {"link_type": kind or "track"}


class MergedSpotifySearch:
    """Spotify search through the built-in client and spotify-web at once.

    The built-in results are the base: their order, ids and values stay,
    and each track is completed from the extension's copy of it (see
    core/metadata_merge.py). Tracks only the extension found are added at
    the end, up to the limit — a search result is only browsed, so an extra
    row costs nothing the way an extra download would. With no spotify-web
    installed, or ``MORNYE_METADATA_MERGE=0``, this is the built-in client.
    """

    def __init__(self, *, manager: Any = None, provider_factory: Any = None) -> None:
        from .spotify_metadata import SpotifyMetadataClient

        self._builtin = SpotifyMetadataClient()
        self._manager = manager
        self._provider_factory = provider_factory

    def _extension_name(self) -> str | None:
        if not _merge_enabled():
            return None
        return fallback_extension_for("https://open.spotify.com/", self._manager)

    async def _extension_tracks(self, ext_name: str, query: str, limit: int) -> list:
        client = ExtensionUrlClient(
            ext_name, "spotify", provider_factory=self._provider_factory
        )
        response = await asyncio.to_thread(
            client._call_sync, "searchTracks", query, limit
        )
        if isinstance(response, dict):
            items = response.get("tracks") or response.get("items") or []
        else:
            items = response or []
        return [t for item in items if (t := track_from_extension(item, "spotify"))]

    async def search_async(self, query: str, limit: int = 50) -> dict:
        ext_name = self._extension_name()
        if not ext_name:
            return await self._builtin.search_async(query, limit=limit)

        base, extra = await asyncio.gather(
            self._builtin.search_async(query, limit=limit),
            self._extension_tracks(ext_name, query, limit),
            return_exceptions=True,
        )
        if isinstance(extra, BaseException) or not extra:
            if isinstance(extra, BaseException):
                logger.debug(
                    "[search] '%s' could not add to the search: %s", ext_name, extra
                )
            if isinstance(base, BaseException):
                raise base
            return base
        if isinstance(base, BaseException):
            logger.warning(
                "[search] The built-in Spotify search failed (%s); showing the '%s' extension's results.",
                base,
                ext_name,
            )
            return {
                "tracks": extra[:limit],
                "albums": [],
                "artists": [],
                "playlists": [],
            }

        from .metadata_merge import merge_track_lists

        results = dict(base)
        tracks = merge_track_lists(list(base.get("tracks") or []), extra)
        known = {getattr(t, "id", None) for t in tracks}
        tracks.extend(t for t in extra if t.id not in known)
        results["tracks"] = tracks[:limit]
        return results

    def search(self, query: str, limit: int = 50) -> dict:
        from .loop_runner import run_sync

        return run_sync(self.search_async(query, limit=limit))


def _merge_enabled() -> bool:
    """``MORNYE_METADATA_MERGE=0`` restores extension-only-on-failure."""
    from .env import env_value

    return (env_value("MORNYE_METADATA_MERGE") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _has_tracks(result: Any) -> bool:
    return (
        not isinstance(result, BaseException)
        and bool(result)
        and len(result) > 1
        and bool(result[1])
    )


async def get_url_with_fallback(
    url: str,
    client_factory: Callable[[], Any],
    *,
    manager: Any = None,
    provider_factory: Any = None,
    **kwargs: Any,
):
    """The most complete answer for `url` from the built-in client and the
    JS extension that reads the same catalogue.

    Both are asked at once and their answers merged field by field (see
    core/metadata_merge.py): the built-in answer is the base — its ids,
    order and values stay — and the extension fills every gap and improves
    what it knows better. When one of them fails or finds nothing, the
    other's answer is used alone. With ``MORNYE_METADATA_MERGE=0`` the
    extension is only asked when the built-in client fails.

    `client_factory` rather than a client: building the Spotify client is
    itself a network round trip (a session, a token), and that is exactly
    where a change on Spotify's side shows up first.

    A malformed link is never answered by the extension — no other reader
    will make sense of it. When both fail, the built-in client's own error
    is what the caller sees.
    """
    service = native_service_for(url)
    if service is None or not _merge_enabled():
        return await _fallback_only(
            url,
            client_factory,
            manager=manager,
            provider_factory=provider_factory,
            **kwargs,
        )

    ext_name = fallback_extension_for(url, manager)
    if not ext_name:
        return await _fallback_only(
            url,
            client_factory,
            manager=manager,
            provider_factory=provider_factory,
            _ext_name=None,
            **kwargs,
        )

    from ..application.download_engine import _call_metadata_get_url
    from .metadata_merge import merge_url_results

    async def _builtin() -> Any:
        client = client_factory()
        return await _call_metadata_get_url(client, url, **kwargs)

    extension = ExtensionUrlClient(ext_name, service, provider_factory=provider_factory)
    base: Any
    other: Any
    base, other = await asyncio.gather(
        _builtin(), extension.get_url_async(url), return_exceptions=True
    )

    if isinstance(base, MornyeError) and base.kind == ErrorKind.INVALID_URL:
        raise base

    if _has_tracks(base) and _has_tracks(other):
        logger.debug(
            "[metadata] Merged the built-in %s answer with the '%s' extension's for %s",
            _LABELS[service],
            ext_name,
            url,
        )
        return merge_url_results(base, other)
    if _has_tracks(base):
        if isinstance(other, BaseException):
            logger.debug(
                "[metadata] The '%s' extension could not add to %s: %s",
                ext_name,
                url,
                other,
            )
        return base
    if _has_tracks(other):
        logger.warning(
            "[metadata] The built-in %s client %s; using the '%s' extension's answer for %s.",
            _LABELS[service],
            (
                f"failed ({base})"
                if isinstance(base, BaseException)
                else "found no tracks"
            ),
            ext_name,
            url,
        )
        return other
    if isinstance(other, BaseException):
        logger.warning(
            "[metadata] The '%s' extension could not resolve %s either: %s",
            ext_name,
            url,
            other,
        )
    if isinstance(base, BaseException):
        raise base
    return base


async def _fallback_only(
    url: str,
    client_factory: Callable[[], Any],
    *,
    manager: Any = None,
    provider_factory: Any = None,
    _ext_name: Any = ...,
    **kwargs: Any,
):
    """The built-in client's answer, or an extension's when it fails."""
    from ..application.download_engine import _call_metadata_get_url

    failure: BaseException | None = None
    result: Any = None
    try:
        client = client_factory()
        result = await _call_metadata_get_url(client, url, **kwargs)
        if result and len(result) > 1 and result[1]:
            return result
    except MornyeError as exc:
        if exc.kind == ErrorKind.INVALID_URL:
            raise
        failure = exc
    except Exception as exc:
        failure = exc

    service = native_service_for(url)
    if _ext_name is ...:
        ext_name = fallback_extension_for(url, manager) if service else None
    else:
        ext_name = _ext_name
    if not ext_name or service is None:
        if failure is not None:
            raise failure
        return result

    logger.warning(
        "[metadata] The built-in %s client %s; resolving %s with the '%s' extension instead.",
        _LABELS[service],
        f"failed ({failure})" if failure is not None else "found no tracks",
        url,
        ext_name,
    )
    try:
        return await ExtensionUrlClient(
            ext_name, service, provider_factory=provider_factory
        ).get_url_async(url)
    except Exception as ext_exc:
        logger.warning(
            "[metadata] The '%s' extension could not resolve %s either: %s",
            ext_name,
            url,
            ext_exc,
        )
        if failure is not None:
            raise failure from None
        return result
