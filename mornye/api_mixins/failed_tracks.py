"""api_mixins/failed_tracks.py — the queue panel's list of tracks still missing.

See core/failed_tracks.py for how the list is kept. This mixin is only the
adapter: read it, retry it, clear it. A retry goes through the same path as
any other download (`_start_download_job`), so in --web mode it is written
down first and survives a restart just like the download it is retrying.

Multi-user mode needs nothing extra: each account has its own Api instance
with its own `owner`, and the list is keyed by owner.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


class FailedTracksMixin:
    if TYPE_CHECKING:
        _start_download_job: Any
        log: Any

    def get_failed_tracks(self) -> dict:
        """Every track that failed and has not downloaded since. Never raises."""
        from ..core import failed_tracks

        tracks = failed_tracks.list_for(getattr(self, "owner", "") or "")
        return {"tracks": tracks, "count": len(tracks)}

    def retry_failed_tracks(
        self, config: dict | None = None, keys: list[str] | None = None
    ) -> dict:
        """Downloads the listed failures again — all of them, or only `keys`.

        One job per source collection (see failed_tracks.retry_groups). A row
        stays in the list until its track actually downloads: a retry that
        fails again only raises the attempt count, it does not lose the track.
        """
        from ..core import failed_tracks

        owner = getattr(self, "owner", "") or ""
        groups = failed_tracks.retry_groups(owner, keys)
        queued = 0
        for source_url, playlist_name, tracks in groups:
            if not playlist_name and failed_tracks.is_playlist_url(source_url):
                # Saved before failures kept their playlist (issue #277):
                # ask the playlist its name once, and keep it on the rows.
                playlist_name = self._playlist_name_for(source_url)
                failed_tracks.fill_playlist_name(owner, source_url, playlist_name)
            payload = {
                "indices": list(range(len(tracks))),
                "tracks": tracks,
                "source_url": source_url,
                # A retry is always a hand-picked selection, never "the
                # whole collection": the collection is what already ran.
                "whole": False,
                "config": dict(config or {}),
            }
            if playlist_name:
                # Back into the playlist's folder and onto its M3U, not
                # loose in the download folder (issue #277).
                payload["playlist_name"] = playlist_name
            self._start_download_job(payload)
            queued += len(tracks)
        if queued:
            self.log(f"Retrying {queued} failed track(s)…", "info")
        return {"queued": queued}

    def _playlist_name_for(self, source_url: str) -> str:
        """The playlist's own name, as the tracklist fetch reads it, or ""
        when it cannot be read. Never raises: without a name the retry still
        runs, only loose in the download folder as before."""
        from ..core.loop_runner import run_sync
        from ..core.metadata_fallback import get_url_with_fallback
        from ..core.tracklist import metadata_client_for

        try:
            result = run_sync(
                get_url_with_fallback(
                    source_url, lambda: metadata_client_for(source_url)
                )
            )
            return str(result[0] or "").strip()
        except Exception as exc:
            self.log(f"Could not read the playlist name for a retry: {exc}", "warning")
            return ""

    def clear_failed_tracks(self, keys: list[str] | None = None) -> dict:
        """Drops failures from the list without retrying them."""
        from ..core import failed_tracks

        owner = getattr(self, "owner", "") or ""
        return {"removed": failed_tracks.clear(owner, keys)}
