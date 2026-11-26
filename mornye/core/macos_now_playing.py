"""core/macos_now_playing.py — the keyboard's media keys and the Now Playing
widget on macOS, for the native (mpv) player.

libmpv's `media-controls` does nothing on macOS when mpv is embedded: the
remote command center is wired up by the mpv application, not the library.
So with the native engine the play/pause and skip keys reached nobody — the
page had handed them to mpv (its own Media Session is switched off then,
or every key would act twice) and mpv never registered for them.

This registers Mornye itself with MPRemoteCommandCenter and keeps
MPNowPlayingInfoCenter current, and hands every command to `on_action` with
the same action names the page uses (toggle, play, pause, nexttrack, …).
Everything is optional: without PyObjC's MediaPlayer bindings, or off
macOS, `create()` returns None and nothing changes.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

_PLAYING, _PAUSED, _STOPPED = 1, 2, 3  # MPNowPlayingPlaybackState


class MacNowPlaying:
    def __init__(self, mp: Any, on_action: Callable[[str, dict], None]) -> None:
        self._mp = mp
        self._on_action = on_action
        self._center = mp.MPRemoteCommandCenter.sharedCommandCenter()
        self._info = mp.MPNowPlayingInfoCenter.defaultCenter()
        self._targets: list[tuple[Any, Any]] = []
        self._meta: dict[str, Any] = {}
        c = self._center
        for command, action in (
            (c.togglePlayPauseCommand(), "toggle"),
            (c.playCommand(), "play"),
            (c.pauseCommand(), "pause"),
            (c.stopCommand(), "stop"),
            (c.nextTrackCommand(), "nexttrack"),
            (c.previousTrackCommand(), "previoustrack"),
        ):
            self._add(command, action)
        self._add(c.changePlaybackPositionCommand(), "seekto")

    def _add(self, command: Any, action: str) -> None:
        def handler(event: Any) -> int:
            detail: dict = {}
            if action == "seekto":
                try:
                    detail["seekTime"] = float(event.positionTime())
                except Exception:
                    return 1  # MPRemoteCommandHandlerStatusNoSuchContent
            try:
                self._on_action(action, detail)
            except Exception:
                logger.debug("[now-playing] %s handler failed", action, exc_info=True)
            return 0  # MPRemoteCommandHandlerStatusSuccess

        command.setEnabled_(True)
        target = command.addTargetWithHandler_(handler)
        self._targets.append((command, target))

    def update(
        self,
        *,
        paused: bool | None = None,
        position: float | None = None,
        duration: float | None = None,
        title: str | None = None,
        artist: str | None = None,
        album: str | None = None,
    ) -> None:
        mp = self._mp
        meta = self._meta
        if title is not None:
            meta[mp.MPMediaItemPropertyTitle] = title
        if artist is not None:
            meta[mp.MPMediaItemPropertyArtist] = artist
        if album is not None:
            meta[mp.MPMediaItemPropertyAlbumTitle] = album
        if duration is not None:
            meta[mp.MPMediaItemPropertyPlaybackDuration] = float(duration)
        if position is not None:
            meta[mp.MPNowPlayingInfoPropertyElapsedPlaybackTime] = float(position)
        if paused is not None:
            meta[mp.MPNowPlayingInfoPropertyPlaybackRate] = 0.0 if paused else 1.0
        try:
            self._info.setNowPlayingInfo_(dict(meta))
            if paused is not None:
                self._info.setPlaybackState_(_PAUSED if paused else _PLAYING)
        except Exception:
            logger.debug("[now-playing] update failed", exc_info=True)

    def clear(self) -> None:
        self._meta.clear()
        try:
            self._info.setNowPlayingInfo_(None)
            self._info.setPlaybackState_(_STOPPED)
        except Exception:
            pass

    def close(self) -> None:
        self.clear()
        for command, target in self._targets:
            try:
                command.removeTarget_(target)
            except Exception:
                pass
        self._targets.clear()


def create(on_action: Callable[[str, dict], None]) -> MacNowPlaying | None:
    if sys.platform != "darwin":
        return None
    try:
        import MediaPlayer  # pyobjc-framework-MediaPlayer
    except Exception:
        logger.debug("[now-playing] MediaPlayer bindings unavailable", exc_info=True)
        return None
    try:
        return MacNowPlaying(MediaPlayer, on_action)
    except Exception:
        logger.debug("[now-playing] could not register", exc_info=True)
        return None
