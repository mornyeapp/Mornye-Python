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

        One job per source collection (see failed_tracks.retry_batches). A row
        stays in the list until its track actually downloads: a retry that
        fails again only raises the attempt count, it does not lose the track.
        """
        from ..core import failed_tracks

        owner = getattr(self, "owner", "") or ""
        busy = self._retry_keys_in_hand()
        queued = already = 0
        for (
            source_url,
            playlist_name,
            tracks,
            track_keys,
        ) in failed_tracks.retry_batches(owner, keys):
            # A track another retry already has queued or running is not
            # queued again. Each click on Retry used to add a full copy of
            # the list to the queue, which is persisted in --web mode: the
            # copies outlived restarts and kept retrying the same tracks for
            # hours (issue #284).
            fresh = [
                (track, key)
                for track, key in zip(tracks, track_keys, strict=True)
                if key not in busy
            ]
            already += len(tracks) - len(fresh)
            if not fresh:
                continue
            tracks = [track for track, _ in fresh]
            track_keys = [key for _, key in fresh]
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
                # Marks the job as a retry: run_download_job() drops the
                # tracks cleared from the list meanwhile, and never adds a
                # failure back to it (issue #284).
                "retry_keys": track_keys,
            }
            if playlist_name:
                # Back into the playlist's folder and onto its M3U, not
                # loose in the download folder (issue #277).
                payload["playlist_name"] = playlist_name
            if getattr(self, "_download_queue", None) is None:
                # No queue to read back from: remembered here until the
                # job's thread is done with them (see run_download_job).
                self._retries_in_flight().update(track_keys)
            self._start_download_job(payload)
            queued += len(tracks)
        if queued:
            self.log(f"Retrying {queued} failed track(s)…", "info")
        return {"queued": queued, "already_queued": already}

    def _retries_in_flight(self) -> set[str]:
        """Failure keys a retry started without a queue is working on."""
        keys = getattr(self, "_retry_keys_running", None)
        if keys is None:
            keys = set()
            self._retry_keys_running = keys
        return keys

    def _retry_keys_in_hand(self) -> set[str]:
        """Failure keys some retry has queued or is running right now."""
        from ..core.job_queue import JobStatus

        keys = set(self._retries_in_flight())
        queue = getattr(self, "_download_queue", None)
        list_for = getattr(queue, "list_for", None)
        if callable(list_for):
            owner = getattr(self, "owner", "") or ""
            for job in list_for(owner):
                if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
                    keys.update(job.payload.get("retry_keys") or ())
        return keys

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
