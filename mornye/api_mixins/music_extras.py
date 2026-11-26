"""api_mixins/music_extras.py — features that came over from the mobile app.

* Weekly releases of the artists on the Following list.
* Lyrics translation with the user's own AI provider (config + translate +
  save as a sidecar ``.<lang>.lrc`` next to the track).

Concerts need no API of their own: they ride along with the artist card's
metadata (see core/concerts.py).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core.loop_runner import run_sync


class MusicExtrasMixin:
    if TYPE_CHECKING:
        download_dir: str
        log: Any
        _is_approved_path: Any
        _remember_tracklist: Any

    # ── Artist page sections ────────────────────────────────────────────────

    def get_artist_overview(self, url: str) -> dict:
        """Popular tracks, discography, related artists and the rest of a
        Spotify artist page (core/artist_overview.py)."""
        from ..core.artist_overview import fetch_artist_overview_async

        try:
            data = run_sync(fetch_artist_overview_async(str(url or "")), timeout=60)
        except Exception as e:
            self.log(f"[artist] Could not load the artist page sections: {e}", "debug")
            return {"status": "error", "error": str(e)}
        return {"status": "ok", **data}

    def load_track_for_queue(self, url: str) -> dict:
        """A track that is not in the list on screen, ready for the Queue.

        The artist page's Popular can hold songs from other artists' records
        (a feature), which the artist's own tracklist does not contain. The
        Queue only downloads positions in a tracklist the backend keeps, so
        the track is fetched from its link and kept as a one-track list of
        its own: the same thing pasting its link on Home would do. The page
        queues it under the returned `list_id`, at index 0.
        """
        from ..core.metadata_fallback import get_url_with_fallback
        from ..core.tracklist import metadata_client_for

        link = str(url or "").strip()
        if "/track/" not in link and ":track:" not in link:
            return {"status": "error", "error": "Not a track link"}
        try:
            result = run_sync(
                get_url_with_fallback(link, lambda: metadata_client_for(link)),
                timeout=90,
            )
        except Exception as e:
            self.log(f"Could not load {link}: {e}", "error")
            return {"status": "error", "error": str(e)}
        tracks = list(result[1]) if result and len(result) > 1 else []
        if not tracks:
            return {"status": "error", "error": "No track found at this link"}
        track = tracks[0]
        list_id = self._remember_tracklist([track], link, "")
        return {
            "status": "ok",
            "list_id": list_id,
            "track": {
                "id": getattr(track, "id", "") or "",
                "title": getattr(track, "title", "") or "",
                "artist": getattr(track, "artists", "") or "",
                "album": getattr(track, "album", "") or "",
            },
        }

    # ── Weekly releases ─────────────────────────────────────────────────────

    def get_weekly_releases(self, days: int = 7, refresh: bool = False) -> dict:
        """Releases of the followed artists dated within the last `days`."""
        from ..core.weekly_releases import weekly_releases_async

        try:
            data = run_sync(
                weekly_releases_async(days=int(days), refresh=bool(refresh)),
                timeout=300,
            )
        except Exception as e:
            self.log(f"[weekly] {e}", "error")
            return {"status": "error", "error": str(e)}
        return {"status": "ok", **data}

    # ── Lyrics translation ──────────────────────────────────────────────────

    def get_lyrics_ai_config(self) -> dict:
        """The provider settings. The API key is never sent back: only whether
        one is stored."""
        from ..core.lyrics_translate import LANGUAGES, PROVIDERS, load_config

        cfg = load_config()
        return {
            "provider": cfg.provider,
            "model": cfg.model,
            "base_url": cfg.base_url,
            "has_key": bool(cfg.api_key),
            "configured": cfg.configured,
            "providers": [
                {"id": k, "label": v["label"], "model": v["model"], "key": v["key"]}
                for k, v in PROVIDERS.items()
            ],
            "languages": [{"code": c, "name": n} for c, n in LANGUAGES],
        }

    def save_lyrics_ai_config(
        self,
        provider: str,
        model: str = "",
        base_url: str = "",
        api_key: str | None = None,
    ) -> dict:
        """Stores the provider settings. `api_key` None keeps the stored key;
        an empty string clears it."""
        from ..core.lyrics_translate import save_config

        try:
            cfg = save_config(provider, model or "", base_url or "", api_key)
        except Exception as e:
            return {"status": "error", "error": str(e)}
        return {"status": "ok", "configured": cfg.configured}

    def track_share_links(self, title: str, artist: str = "", isrc: str = "") -> dict:
        """Where a song can be found, as {platform: link}, for "Share Link".

        By its ISRC when the file has one (exact recording), otherwise by
        searching the catalogue for title and artist and keeping a result
        whose title and artist both agree.
        """
        try:
            links = run_sync(_share_links_async(title, artist, isrc), timeout=60)
        except Exception as e:
            return {"status": "error", "error": str(e)}
        if not links:
            return {
                "status": "error",
                "error": "This song was not found on any service.",
            }
        return {"status": "ok", "links": links}

    def translate_lyrics(self, lyrics: str, language: str) -> dict:
        """Translates `lyrics` (plain or LRC) into `language`, line by line."""
        from ..core.lyrics_translate import translate_lyrics_async

        try:
            text = run_sync(translate_lyrics_async(lyrics, language), timeout=600)
        except Exception as e:
            return {"status": "error", "error": str(e)}
        return {"status": "ok", "lyrics": text}

    def save_translated_lyrics(
        self, path: str, language: str, lyrics: str, original: str | None = None
    ) -> dict:
        """Saves a translation next to the track.

        With the `original` lyrics it was made from, as ``<name>.mlrc``: the
        lyrics with each line's translation tagged under it, the file the
        player here and the Mornye app both show line by line. Without, or
        when the lines do not pair up, as ``<name>.<language>.lrc``.
        """
        import re

        from ..core.lyric_tags import MAX_SIDECAR_BYTES, build_mlrc

        if not self._is_approved_path(path):
            return {"status": "error", "error": "Path is outside approved directories"}
        if not isinstance(lyrics, str) or len(lyrics) > MAX_SIDECAR_BYTES:
            return {"status": "error", "error": "The translation is not text."}
        if isinstance(original, str) and original.strip():
            try:
                text = build_mlrc(original, lyrics)
            except ValueError:
                text = None  # hand-edited out of shape: the plain file below
            if text is not None:
                target = Path(path).with_suffix(".mlrc")
                try:
                    target.write_text(text, encoding="utf-8")
                except OSError as e:
                    return {"status": "error", "error": str(e)}
                return {"status": "ok", "path": str(target)}
        code = re.sub(r"[^A-Za-z0-9-]", "", language or "")[:12] or "translation"
        target = Path(path).with_suffix(f".{code}.lrc")
        try:
            target.write_text(lyrics, encoding="utf-8")
        except OSError as e:
            return {"status": "error", "error": str(e)}
        return {"status": "ok", "path": str(target)}


#: How close a search result's title must be to the song's to be shared.
_SHARE_TITLE_RATIO = 0.85


async def _share_links_async(title: str, artist: str, isrc: str) -> dict[str, str]:
    """{platform: link} for one recording; {} when it is not found."""
    from ..core.isrc_utils import normalize_isrc
    from ..core.link_resolver import LinkResolver
    from ..core.text_match import artists_match, ratio
    from .search import search_metadata_async

    title = str(title or "").strip()
    artist = str(artist or "").strip()
    resolver = LinkResolver()
    links: dict[str, str] = {}
    code = normalize_isrc(str(isrc or ""))
    if code:
        deezer = await resolver._get_deezer_url_by_isrc_async(code)
        if deezer:
            links = {
                "deezer": deezer,
                **await resolver._get_resolve_links_by_url_async(deezer),
            }
    if not links and title:
        results = await search_metadata_async(f"{title} {artist}".strip(), limit=5)
        for track in results.get("tracks", []):
            url = str(track.get("external_url") or "")
            found_artist = str(track.get("artists") or track.get("artist") or "")
            if not url.startswith("https://"):
                continue
            if ratio(title, str(track.get("name") or "")) < _SHARE_TITLE_RATIO:
                continue
            if artist and not artists_match(artist, found_artist):
                continue
            links = {
                "spotify": url,
                **await resolver._get_resolve_links_by_url_async(url),
            }
            break
    return {
        k: v for k, v in links.items() if k != "isrc" and str(v).startswith("https://")
    }
