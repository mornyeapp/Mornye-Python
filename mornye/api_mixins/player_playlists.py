"""Create and manage playlists made from files in the Player's library."""

from __future__ import annotations

import base64
import binascii
import ipaddress
import io
import logging
import math
import re
import socket
from collections.abc import Iterable
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import httpx

from ..core import player_playlists
from ..core.errors import MornyeError
from ..core.m3u_source import read_m3u_text
from ..core.tagger import SUPPORTED_SUFFIXES

_MAX_COVER_BYTES = 5_000_000
_MAX_IMPORT_BYTES = player_playlists.MAX_IMPORT_BYTES
_MAX_PLAYLIST_REDIRECTS = 3
logger = logging.getLogger(__name__)


def _validated_playlist_url(url: str) -> tuple[str, str, str]:
    """`(url, pinned_url, host)` for a public HTTP(S) playlist link.

    `pinned_url` targets the address that was just checked. Requesting the
    hostname instead would let the HTTP client resolve it a second time,
    and a DNS answer that changes in between (DNS rebinding) would reach a
    private address the check never saw.
    """
    if not isinstance(url, str) or not url.strip() or len(url) > 4096:
        raise ValueError("Enter a valid M3U/M3U8 link.")
    try:
        parsed = urlsplit(url.strip())
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Enter a valid M3U/M3U8 link.") from exc
    host = parsed.hostname
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or (port is not None and port not in {80, 443})
    ):
        raise ValueError("Use a public HTTP or HTTPS M3U/M3U8 link.")
    request_port = port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        addresses = {
            ipaddress.ip_address(info[4][0])
            for info in socket.getaddrinfo(host, request_port, type=socket.SOCK_STREAM)
        }
    except (OSError, ValueError) as exc:
        raise ValueError("The playlist link host could not be resolved.") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("Only publicly reachable playlist links are supported.")
    scheme = parsed.scheme.lower()
    path = parsed.path or "/"
    pinned = min(addresses, key=lambda address: (address.version, int(address)))
    pinned_host = f"[{pinned}]" if pinned.version == 6 else str(pinned)
    if port is not None:
        pinned_host = f"{pinned_host}:{port}"
    return (
        urlunsplit((scheme, parsed.netloc, path, parsed.query, "")),
        urlunsplit((scheme, pinned_host, path, parsed.query, "")),
        host,
    )


def _fetch_public(url: str, accept: str, max_bytes: int) -> tuple[bytes, str]:
    """`(body, final_url)` of a GET to a public HTTP(S) address.

    Each hop of a redirect is validated and pinned again (see
    `_validated_playlist_url`), and the body is cut off past `max_bytes`.
    """
    current_url = url
    try:
        with httpx.Client(
            timeout=httpx.Timeout(15.0, connect=5.0),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            for redirect_count in range(_MAX_PLAYLIST_REDIRECTS + 1):
                current_url, pinned_url, host = _validated_playlist_url(current_url)
                with client.stream(
                    "GET",
                    pinned_url,
                    headers={"Accept": accept, "Host": urlsplit(current_url).netloc},
                    # TLS still names, and verifies the certificate for,
                    # the original host rather than the pinned address.
                    extensions={"sni_hostname": host},
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location or redirect_count == _MAX_PLAYLIST_REDIRECTS:
                            raise ValueError("The link redirected too many times.")
                        current_url = urljoin(current_url, location)
                        continue
                    response.raise_for_status()
                    too_big = f"The download must be smaller than {max_bytes // 1_000_000} MB."
                    declared_size = response.headers.get("content-length")
                    if declared_size:
                        try:
                            declared_size_bytes = int(declared_size)
                        except ValueError as exc:
                            raise ValueError(
                                "The link returned an invalid size."
                            ) from exc
                        if declared_size_bytes > max_bytes:
                            raise ValueError(too_big)
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > max_bytes:
                            raise ValueError(too_big)
                    return bytes(content), current_url
    except httpx.TimeoutException as exc:
        raise ValueError("The link request timed out.") from exc
    except httpx.HTTPStatusError as exc:
        raise ValueError(f"The link returned HTTP {exc.response.status_code}.") from exc
    except httpx.HTTPError as exc:
        raise ValueError("Could not download the link.") from exc
    raise ValueError("Could not download the link.")


def _download_playlist_url(url: str) -> tuple[str, str]:
    content, final_url = _fetch_public(
        url,
        "audio/x-mpegurl, application/vnd.apple.mpegurl, text/plain, */*",
        _MAX_IMPORT_BYTES,
    )
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Playlist link must return UTF-8 text.") from exc
    filename = Path(unquote(urlsplit(final_url).path)).name
    if not filename.lower().endswith((".m3u", ".m3u8")):
        filename = "playlist.m3u8"
    read_m3u_text(text, name=filename)
    return text, filename


def _download_cover(url: str) -> str:
    """A remote cover image as a data URL, for `_playlist_cover` to check."""
    content, _ = _fetch_public(url, "image/*", _MAX_COVER_BYTES)
    return "data:image/unknown;base64," + base64.b64encode(content).decode("ascii")


def _provider_track_dict(track: Any) -> dict[str, Any]:
    """The fields of a provider track the import needs, as plain data."""

    def field(*names: str) -> Any:
        for name in names:
            value = (
                track.get(name)
                if isinstance(track, dict)
                else getattr(track, name, None)
            )
            if value:
                return value
        return ""

    return {
        "title": str(field("title", "name")),
        "artists": str(field("artists", "artist")),
        "album": str(field("album", "album_name")),
        "duration_ms": field("duration_ms") or 0,
        "isrc": str(field("isrc")),
        "external_url": str(field("external_url", "externalUrl", "link", "url")),
    }


def _source_url(url: Any) -> str:
    """The provider playlist link an import follows, or "" when it is not
    one a later sync could fetch again."""
    url = str(url or "").strip() if isinstance(url, str) else ""
    if not url.startswith(("http://", "https://")) or len(url) > 2048:
        return ""
    return url


def _remote_track(item: dict[str, Any]) -> dict[str, Any] | None:
    """A not-downloaded playlist entry for a provider track."""
    title = str(item.get("title") or item.get("name") or "").strip()[:500]
    if not title:
        return None
    artist = str(item.get("artists") or item.get("artist") or "").strip()[:500]
    album = str(item.get("album") or item.get("album_name") or "").strip()[:500]
    try:
        duration = float(item.get("duration_ms") or 0) / 1000 or float(
            item.get("duration_s") or 0
        )
    except (TypeError, ValueError):
        duration = 0.0
    if not math.isfinite(duration) or duration < 0:
        duration = 0.0
    url = str(item.get("external_url") or item.get("source_url") or "").strip()
    if not url.startswith(("http://", "https://", "spotify:")) or len(url) > 2048:
        url = ""
    track = {
        "title": title,
        "artist": artist,
        "album": album,
        "duration_s": duration,
        "isrc": str(item.get("isrc") or "").strip().upper()[:32],
        "source_url": url,
    }
    track["path"] = player_playlists.remote_key(track)
    return track


_FEATURING = re.compile(r"\s*[\(\[](?:feat|ft|with)\.?\s[^\)\]]*[\)\]]", re.IGNORECASE)
_ARTIST_SPLIT = re.compile(
    r"\s*(?:,|&|;|/|\bfeat\.?|\bft\.?|\bx\b|\band\b)\s*", re.IGNORECASE
)


def _norm_title(text: str) -> str:
    text = _FEATURING.sub("", text or "")
    text = re.sub(r"[^\w\s]", " ", text.casefold())
    return " ".join(text.split())


def _artist_names(text: str) -> set[str]:
    return {
        _norm_title(name)
        for name in _ARTIST_SPLIT.split(text or "")
        if _norm_title(name)
    }


class _LibraryIndex:
    """Finds the library file for a provider track: the same ISRC, else the
    same title with at least one artist in common (the closest duration
    when several files qualify)."""

    def __init__(self, tracks: list[dict[str, Any]]) -> None:
        self.by_isrc: dict[str, dict[str, Any]] = {}
        self.by_title: dict[str, list[dict[str, Any]]] = {}
        for track in tracks:
            if track.get("isrc"):
                self.by_isrc.setdefault(track["isrc"], track)
            self.by_title.setdefault(_norm_title(track["title"]), []).append(track)

    def match(self, remote: dict[str, Any]) -> dict[str, Any] | None:
        if remote["isrc"] and remote["isrc"] in self.by_isrc:
            return self._local(self.by_isrc[remote["isrc"]], remote)
        artists = _artist_names(remote["artist"])
        candidates = [
            track
            for track in self.by_title.get(_norm_title(remote["title"]), [])
            if not artists or artists & _artist_names(track["artist"])
        ]
        if not candidates:
            return None
        if remote["duration_s"]:
            candidates.sort(
                key=lambda t: abs((t["duration_s"] or 0) - remote["duration_s"])
            )
            best = candidates[0]
            if (
                best["duration_s"]
                and abs(best["duration_s"] - remote["duration_s"]) > 10
            ):
                return None  # another version (live, extended…), not this one
            return self._local(best, remote)
        return self._local(candidates[0], remote)

    @staticmethod
    def _local(track: dict[str, Any], remote: dict[str, Any]) -> dict[str, Any]:
        return {
            **track,
            "isrc": track.get("isrc") or remote["isrc"],
            "source_url": remote["source_url"],
        }


def _track_link(url: str) -> str:
    """A track link without the share junk (``?si=…``) two copies differ by."""
    parts = urlsplit(str(url or "").strip())
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.netloc.casefold()}{parts.path.rstrip('/')}"


class _SongIndex:
    """Tells whether a song is already in a playlist, however it is listed.

    The same song can carry an ISRC in one entry (read from the file it was
    linked to) and none in another (a provider that does not send it), so
    no single key works: the same ISRC, the same track link, or the same
    title with an artist in common and a duration within 10 s all count.
    """

    def __init__(self, tracks: Iterable[dict[str, Any]] = ()) -> None:
        self.isrcs: set[str] = set()
        self.links: set[str] = set()
        self.titles: dict[str, list[tuple[set[str], float]]] = {}
        for track in tracks:
            self.add(track)

    def add(self, track: dict[str, Any]) -> None:
        isrc = str(track.get("isrc") or "").strip().upper()
        if isrc:
            self.isrcs.add(isrc)
        link = _track_link(track.get("source_url") or "")
        if link:
            self.links.add(link)
        title = _norm_title(track.get("title") or "")
        if title:
            self.titles.setdefault(title, []).append(
                (
                    _artist_names(track.get("artist") or ""),
                    float(track.get("duration_s") or 0),
                )
            )

    def __contains__(self, track: dict[str, Any]) -> bool:
        isrc = str(track.get("isrc") or "").strip().upper()
        if isrc and isrc in self.isrcs:
            return True
        link = _track_link(track.get("source_url") or "")
        if link and link in self.links:
            return True
        artists = _artist_names(track.get("artist") or "")
        duration = float(track.get("duration_s") or 0)
        for names, seconds in self.titles.get(
            _norm_title(track.get("title") or ""), []
        ):
            if artists and names and not artists & names:
                continue
            if duration and seconds and abs(duration - seconds) > 10:
                continue
            return True
        return False


class PlayerPlaylistsMixin:
    owner: str
    if TYPE_CHECKING:
        _is_approved_path: Any

    def _playlist_cover(self, data_url: str) -> str:
        if not data_url:
            return ""
        if not isinstance(data_url, str) or len(data_url) > _MAX_COVER_BYTES * 2:
            raise ValueError("Cover image is too large.")
        header, separator, encoded = data_url.partition(",")
        if not separator or not header.lower().startswith("data:image/"):
            raise ValueError("Choose a supported image file.")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("The cover image could not be read.") from exc
        if not raw or len(raw) > _MAX_COVER_BYTES:
            raise ValueError("Cover image must be smaller than 5 MB.")
        try:
            from PIL import Image

            with Image.open(io.BytesIO(raw)) as source:
                if source.format not in {"JPEG", "PNG", "WEBP", "GIF"}:
                    raise ValueError("Choose a JPEG, PNG, WebP or GIF image.")
                if source.width * source.height > 25_000_000:
                    raise ValueError("Cover image dimensions are too large.")
                image = source.convert("RGB")
                image.thumbnail((512, 512))
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=86, optimize=True)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(
                "The cover image is invalid or could not be decoded."
            ) from exc
        return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode(
            "ascii"
        )

    def _playlist_tracks(self, tracks: Any) -> list[dict[str, Any]]:
        if not isinstance(tracks, list):
            raise ValueError("Tracks must be a list.")
        if len(tracks) > player_playlists.MAX_TRACKS:
            raise ValueError("The playlist contains too many tracks.")
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in tracks:
            if not isinstance(item, dict):
                raise ValueError("A playlist track has an invalid shape.")
            path = item.get("path")
            if not isinstance(path, str) or not path:
                raise ValueError("A playlist track is missing its file path.")
            try:
                resolved = Path(path).expanduser().resolve(strict=True)
                if (
                    not resolved.is_file()
                    or resolved.suffix.lower() not in SUPPORTED_SUFFIXES
                    or not self._is_approved_path(str(resolved))
                ):
                    continue
            except (OSError, RuntimeError, ValueError):
                continue
            local_path = str(resolved)
            if local_path in seen:
                continue
            seen.add(local_path)
            try:
                duration = float(item.get("duration_s") or 0)
            except (TypeError, ValueError):
                duration = 0.0
            if not math.isfinite(duration) or duration < 0:
                duration = 0.0
            out.append(
                {
                    "path": local_path,
                    "title": str(item.get("title") or resolved.stem)[:500],
                    "artist": str(item.get("artist") or "")[:500],
                    "album": str(item.get("album") or "")[:500],
                    "duration_s": duration,
                    "isrc": str(item.get("isrc") or "").strip().upper()[:32],
                }
            )
        return out

    def get_player_playlists(self) -> dict:
        return {
            "status": "ok",
            "playlists": player_playlists.list_playlists(self.owner),
        }

    def create_player_playlist(
        self,
        name: str,
        description: str = "",
        cover_data_url: str = "",
        tracks: Any = None,
    ) -> dict:
        try:
            playlist = player_playlists.create_playlist(
                name,
                description,
                self._playlist_cover(cover_data_url),
                self._playlist_tracks([] if tracks is None else tracks),
                self.owner,
            )
        except (MornyeError, ValueError, TypeError, OSError) as exc:
            return {"status": "error", "error": str(exc)}
        return {"status": "ok", "playlist": playlist}

    def update_player_playlist(
        self,
        playlist_id: str,
        name: str,
        description: str,
        cover_data_url: str = "",
    ) -> dict:
        try:
            stored = next(
                (
                    item
                    for item in player_playlists.list_playlists(self.owner)
                    if item["id"] == playlist_id
                ),
                None,
            )
            # The editor sends the saved cover back unchanged on every save;
            # re-encoding it would cost JPEG quality each time.
            cover = (
                cover_data_url
                if stored is not None and cover_data_url == stored["cover"]
                else self._playlist_cover(cover_data_url)
            )
            playlist = player_playlists.update_playlist(
                playlist_id,
                self.owner,
                name=name,
                description=description,
                cover=cover,
            )
        except (MornyeError, ValueError, TypeError, OSError) as exc:
            return {"status": "error", "error": str(exc)}
        if playlist is None:
            return {"status": "error", "error": "Playlist not found."}
        return {"status": "ok", "playlist": playlist}

    def delete_player_playlist(self, playlist_id: str) -> dict:
        if not player_playlists.delete_playlist(playlist_id, self.owner):
            return {"status": "error", "error": "Playlist not found."}
        return {"status": "ok"}

    def add_player_playlist_tracks(self, playlist_id: str, tracks: Any) -> dict:
        try:
            playlist = player_playlists.add_tracks(
                playlist_id, self.owner, self._playlist_tracks(tracks)
            )
        except (ValueError, TypeError, OSError) as exc:
            return {"status": "error", "error": str(exc)}
        if playlist is None:
            return {"status": "error", "error": "Playlist not found."}
        return {"status": "ok", "playlist": playlist}

    def remove_player_playlist_track(self, playlist_id: str, path: str) -> dict:
        try:
            resolved = (
                path
                if player_playlists.is_remote(path)
                else str(Path(path).expanduser().resolve(strict=False))
            )
            playlist = player_playlists.remove_track(playlist_id, self.owner, resolved)
        except (ValueError, TypeError, OSError) as exc:
            return {"status": "error", "error": str(exc)}
        if playlist is None:
            return {"status": "error", "error": "Playlist not found."}
        return {"status": "ok", "playlist": playlist}

    def reorder_player_playlist_tracks(self, playlist_id: str, paths: Any) -> dict:
        if not isinstance(paths, list) or not all(
            isinstance(path, str) for path in paths
        ):
            return {"status": "error", "error": "The playlist order is invalid."}
        try:
            playlist = player_playlists.reorder_tracks(playlist_id, self.owner, paths)
        except (ValueError, TypeError, OSError) as exc:
            return {"status": "error", "error": str(exc)}
        if playlist is None:
            return {"status": "error", "error": "Playlist not found."}
        return {"status": "ok", "playlist": playlist}

    def export_player_playlist(self, playlist_id: str) -> dict:
        playlist = next(
            (
                item
                for item in player_playlists.list_playlists(self.owner)
                if item["id"] == playlist_id
            ),
            None,
        )
        if playlist is None:
            return {"status": "error", "error": "Playlist not found."}
        lines = ["#EXTM3U"]
        for track in playlist["tracks"]:
            duration = max(0, round(track["duration_s"]))
            display = " - ".join(
                part for part in (track["artist"], track["title"]) if part
            )
            display = display.replace("\r", " ").replace("\n", " ")
            if track["downloaded"]:
                location = Path(track["path"]).as_uri()
            elif track["source_url"].startswith(("http://", "https://")):
                location = track["source_url"]
            else:
                continue  # not downloaded, and nothing to point at
            lines.extend((f"#EXTINF:{duration},{display}", location))
        safe_name = "".join(
            char if char.isalnum() or char in " -_." else "_"
            for char in playlist["name"]
        ).strip(" .")
        return {
            "status": "ok",
            "name": safe_name or "Playlist",
            "text": "\n".join(lines) + "\n",
        }

    def _resolve_player_playlist_import(
        self, text: str, filename: str, library_tracks: Any
    ) -> dict[str, Any]:
        if not isinstance(text, str) or len(text.encode("utf-8")) > _MAX_IMPORT_BYTES:
            raise ValueError("Playlist file must be smaller than 5 MB.")
        safe_filename = Path(filename).name or "playlist.m3u8"
        document = read_m3u_text(text, name=safe_filename)
        candidates = self._playlist_tracks(
            [] if library_tracks is None else library_tracks
        )
        by_path = {track["path"]: track for track in candidates}
        by_name: dict[str, list[dict[str, Any]]] = {}
        for track in candidates:
            by_name.setdefault(Path(track["path"]).name.casefold(), []).append(track)

        row_by_line = {row.line: row for row in document.rows}
        imported: list[dict[str, Any]] = []
        missing: list[str] = []
        ambiguous: list[str] = []
        for number, raw in enumerate(text.lstrip("\ufeff").splitlines(), start=1):
            location = raw.strip()
            row = row_by_line.get(number)
            if not location or location.startswith("#") or row is None:
                continue
            direct = ""
            if location.lower().startswith("file://"):
                direct = unquote(urlsplit(location).path)
            elif not location.lower().startswith(("http://", "https://", "spotify:")):
                direct = location
            match = None
            is_ambiguous = False
            if direct:
                direct_path = Path(direct).expanduser()
                try:
                    match = by_path.get(str(direct_path.resolve(strict=True)))
                except (OSError, RuntimeError, ValueError):
                    pass
                if match is None:
                    path_type = PureWindowsPath if "\\" in direct else Path
                    same_name = by_name.get(path_type(direct).name.casefold(), [])
                    if len(same_name) == 1:
                        match = same_name[0]
                    elif len(same_name) > 1:
                        narrowed = [
                            track
                            for track in same_name
                            if (
                                not row.title
                                or track["title"].casefold() == row.title.casefold()
                            )
                            and (
                                not row.artist
                                or track["artist"].casefold() == row.artist.casefold()
                            )
                        ]
                        if len(narrowed) == 1:
                            match = narrowed[0]
                        else:
                            is_ambiguous = True
            if match is None and row.title:
                narrowed = [
                    track
                    for track in candidates
                    if track["title"].casefold() == row.title.casefold()
                    and (
                        not row.artist
                        or track["artist"].casefold() == row.artist.casefold()
                    )
                ]
                if len(narrowed) == 1:
                    match = narrowed[0]
                elif len(narrowed) > 1:
                    is_ambiguous = True
            if match is None:
                label = " - ".join(part for part in (row.artist, row.title) if part)
                if is_ambiguous:
                    ambiguous.append(label or location)
                else:
                    missing.append(label or location)
            else:
                imported.append(match)
        return {
            "tracks": imported,
            "missing": missing,
            "ambiguous": ambiguous,
        }

    def preview_player_playlist_import(
        self, text: str, filename: str, library_tracks: Any = None
    ) -> dict:
        try:
            result = self._resolve_player_playlist_import(
                text, filename, library_tracks
            )
        except (MornyeError, ValueError, TypeError, OSError) as exc:
            return {"status": "error", "error": str(exc)}
        return {
            "status": "ok",
            "matched": len(result["tracks"]),
            "skipped": len(result["missing"]),
            "ambiguous": len(result["ambiguous"]),
            "missing_tracks": result["missing"][:20],
            "ambiguous_tracks": result["ambiguous"][:20],
        }

    def fetch_player_playlist_import(self, url: str) -> dict:
        try:
            text, filename = _download_playlist_url(url)
        except (MornyeError, ValueError, TypeError, OSError) as exc:
            return {"status": "error", "error": str(exc)}
        return {"status": "ok", "text": text, "filename": filename}

    def fetch_player_playlist_search_import(self, url: str) -> dict:
        """A playlist found through a provider, ready for
        `import_player_playlist_collection`: name, description, cover and
        every track."""
        from ..core.loop_runner import run_sync
        from ..core.tracklist import resolve_tracklist

        try:
            tracklist = run_sync(resolve_tracklist(url))
        except Exception as exc:
            logger.warning("Could not fetch playlist search result: %s", exc)
            return {
                "status": "error",
                "error": str(exc) or "Could not fetch the playlist.",
            }
        if not tracklist.tracks:
            return {
                "status": "error",
                "error": "The playlist provider returned no tracks.",
            }
        if len(tracklist.tracks) > player_playlists.MAX_TRACKS:
            return {
                "status": "error",
                "error": f"A playlist can contain at most {player_playlists.MAX_TRACKS} tracks.",
            }
        meta = getattr(tracklist, "meta", None)
        meta = meta if isinstance(meta, dict) else {}
        return {
            "status": "ok",
            "name": tracklist.name or "Imported playlist",
            "description": str(meta.get("description") or ""),
            "cover": str(getattr(tracklist, "cover", "") or ""),
            "tracks": [_provider_track_dict(track) for track in tracklist.tracks],
        }

    def import_player_playlist_collection(
        self,
        name: str,
        description: str = "",
        cover_url: str = "",
        tracks: Any = None,
        library_tracks: Any = None,
        playlist_id: str = "",
        source_url: str = "",
    ) -> dict:
        """Import a whole playlist found through a provider.

        Every song is kept, in order. Songs already in the library (same
        ISRC, else same title and artist) point at their file; the others
        are kept as not downloaded, and `link_player_playlist_tracks`
        points them at their file once they are. The cover is fetched and
        re-encoded like an uploaded one; a cover that cannot be fetched
        does not stop the import. A new playlist given the provider link it
        came from as `source_url` follows it: `sync_player_playlist` adds
        the songs added there later.
        """
        try:
            if not isinstance(tracks, list) or not tracks:
                raise ValueError("The playlist has no tracks.")
            if len(tracks) > player_playlists.MAX_TRACKS:
                raise ValueError(
                    f"A playlist can contain at most {player_playlists.MAX_TRACKS} tracks."
                )
            library = _LibraryIndex(
                self._playlist_tracks([] if library_tracks is None else library_tracks)
            )
            entries: list[dict[str, Any]] = []
            source_keys: list[str] = []
            for item in tracks:
                if not isinstance(item, dict):
                    raise ValueError("A playlist track has an invalid shape.")
                remote = _remote_track(item)
                if remote is None:
                    continue
                source_keys.append(remote["path"])
                entries.append(library.match(remote) or remote)
            if not entries:
                raise ValueError("The playlist has no usable tracks.")

            if playlist_id:
                before = player_playlists.get_playlist(playlist_id, self.owner)
                if before is None:
                    return {"status": "error", "error": "Playlist not found."}
                playlist = player_playlists.add_tracks(playlist_id, self.owner, entries)
            else:
                cover = ""
                if isinstance(cover_url, str) and cover_url.startswith(
                    ("http://", "https://")
                ):
                    try:
                        cover = self._playlist_cover(_download_cover(cover_url))
                    except ValueError as exc:
                        logger.info("Playlist cover not imported: %s", exc)
                playlist = player_playlists.create_playlist(
                    str(name or "").strip()[: player_playlists.MAX_NAME_LENGTH]
                    or "Imported playlist",
                    str(description or "")[: player_playlists.MAX_DESCRIPTION_LENGTH],
                    cover,
                    entries,
                    self.owner,
                    source_url=_source_url(source_url),
                    source_keys=source_keys,
                )
        except (MornyeError, ValueError, TypeError, OSError) as exc:
            return {"status": "error", "error": str(exc)}
        if playlist is None:
            return {"status": "error", "error": "Playlist not found."}
        imported = [
            t for t in playlist["tracks"] if t["path"] in {e["path"] for e in entries}
        ]
        downloaded = sum(1 for t in imported if t["downloaded"])
        return {
            "status": "ok",
            "playlist": playlist,
            "imported": len(imported),
            "downloaded": downloaded,
            "not_downloaded": len(imported) - downloaded,
        }

    def set_player_playlist_source(self, playlist_id: str, url: str = "") -> dict:
        """Make a playlist follow a provider playlist link, then sync it.

        For playlists imported before they could follow their source: the
        songs listed now count as seen, so the first sync adds only what
        the source has beyond them. An empty `url` stops following. A link
        that cannot be fetched is not kept.
        """
        url = str(url or "").strip() if isinstance(url, str) else ""
        followed = _source_url(url)
        if url and not followed:
            return {
                "status": "error",
                "error": "Paste the link of a playlist (https://…).",
            }
        playlist = player_playlists.get_playlist(playlist_id, self.owner)
        if playlist is None:
            return {"status": "error", "error": "Playlist not found."}
        previous = player_playlists.playlist_source(playlist_id, self.owner)
        seen = {player_playlists.remote_key(t) for t in playlist["tracks"]}
        player_playlists.set_playlist_source(playlist_id, self.owner, followed, seen)
        if not followed:
            return {
                "status": "ok",
                "playlist": player_playlists.get_playlist(playlist_id, self.owner),
                "added": 0,
            }
        result = self.sync_player_playlist(playlist_id)
        if result.get("status") != "ok" and previous is not None:
            old_url, old_keys = previous
            player_playlists.set_playlist_source(
                playlist_id, self.owner, old_url, old_keys
            )
        return result

    def sync_player_playlist(self, playlist_id: str) -> dict:
        """Add the songs added to the provider playlist this one follows.

        Only additions travel: a song the source dropped stays here, and
        one the user removed here is not brought back, since the source
        listed it before. New songs are appended as not downloaded; the
        Player points them at their file when the library has one.
        """
        source = player_playlists.playlist_source(playlist_id, self.owner)
        if source is None:
            return {"status": "error", "error": "Playlist not found."}
        url, known = source
        if not url:
            return {
                "status": "error",
                "error": "This playlist does not follow a provider playlist.",
            }
        fetched = self.fetch_player_playlist_search_import(url)
        if fetched.get("status") != "ok":
            return fetched
        playlist = player_playlists.get_playlist(playlist_id, self.owner)
        if playlist is None:
            return {"status": "error", "error": "Playlist not found."}
        # Not-downloaded entries that repeat a song already listed (a file,
        # or an earlier entry) go: they are what an earlier sync that
        # missed the match left behind. Files are never removed.
        present = _SongIndex([t for t in playlist["tracks"] if t["downloaded"]])
        drop: list[str] = []
        for track in playlist["tracks"]:
            if track["downloaded"]:
                continue
            if track in present:
                drop.append(track["path"])
            else:
                present.add(track)
        keys: list[str] = []
        added: list[dict[str, Any]] = []
        for item in fetched["tracks"]:
            remote = _remote_track(item)
            if remote is None:
                continue
            keys.append(remote["path"])
            if remote["path"] in known or remote in present:
                continue
            present.add(remote)
            added.append(remote)
        try:
            playlist = player_playlists.record_source_sync(
                playlist_id, self.owner, keys, added, drop
            )
        except (ValueError, TypeError, OSError) as exc:
            return {"status": "error", "error": str(exc)}
        if playlist is None:
            return {"status": "error", "error": "Playlist not found."}
        return {
            "status": "ok",
            "playlist": playlist,
            "added": len(added),
            "removed": len(drop),
        }

    def link_player_playlist_tracks(self, links: Any) -> dict:
        """Point not-downloaded playlist entries at files now in the library.

        `links` is a list of `{"key": <remote key>, "track": <library
        track>}`; each track is checked like any other playlist file.
        """
        if not isinstance(links, list):
            return {"status": "error", "error": "Links must be a list."}
        mapping: dict[str, dict[str, Any]] = {}
        for link in links[: player_playlists.MAX_TRACKS]:
            if not isinstance(link, dict):
                continue
            key = link.get("key")
            if not isinstance(key, str) or not player_playlists.is_remote(key):
                continue
            checked = (
                self._playlist_tracks([link.get("track")])
                if isinstance(link.get("track"), dict)
                else []
            )
            if checked:
                mapping[key] = checked[0]
        try:
            changed = player_playlists.link_tracks(self.owner, mapping)
        except (ValueError, TypeError, OSError) as exc:
            return {"status": "error", "error": str(exc)}
        return {
            "status": "ok",
            "linked": changed,
            "playlists": (
                player_playlists.list_playlists(self.owner) if changed else None
            ),
        }

    def import_player_playlist(
        self,
        text: str,
        filename: str,
        name: str,
        description: str = "",
        cover_data_url: str = "",
        library_tracks: Any = None,
        playlist_id: str = "",
    ) -> dict:
        try:
            result = self._resolve_player_playlist_import(
                text, filename, library_tracks
            )
            if playlist_id:
                before = next(
                    (
                        item
                        for item in player_playlists.list_playlists(self.owner)
                        if item["id"] == playlist_id
                    ),
                    None,
                )
                if before is None:
                    return {"status": "error", "error": "Playlist not found."}
                playlist = player_playlists.add_tracks(
                    playlist_id, self.owner, result["tracks"]
                )
                if playlist is None:
                    return {"status": "error", "error": "Playlist not found."}
                imported_count = len(playlist["tracks"]) - len(before["tracks"])
            else:
                playlist = player_playlists.create_playlist(
                    name,
                    description,
                    self._playlist_cover(cover_data_url),
                    result["tracks"],
                    self.owner,
                )
                imported_count = len(playlist["tracks"])
        except (MornyeError, ValueError, TypeError, OSError) as exc:
            return {"status": "error", "error": str(exc)}
        return {
            "status": "ok",
            "playlist": playlist,
            "imported": imported_count,
            "skipped": len(result["missing"]),
            "ambiguous": len(result["ambiguous"]),
        }
