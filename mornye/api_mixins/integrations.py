"""api_mixins/integrations.py — telling other services what the Player plays.

* Discord Rich Presence: "Listening to …" on the Discord profile
  (core/discord_presence.py).
* Last.fm: "Scrobbling now" while a song plays, and a scrobble once enough
  of it was heard (core/scrobbler.py; the scrobble itself is sent from
  `record_listening` in api_mixins/personal.py).

The page reports what plays through `set_now_playing` on every change of
song, play/pause and seek; each service takes what it needs from it.

Desktop only: these talk to the Discord client and accounts of the machine
running Mornye, which over `--web` is the server, not the listener — see the
note under ALLOWED_METHODS in webapp.py.
"""

from __future__ import annotations

from typing import Any


def _service_response(status: dict) -> dict:
    response = dict(status)
    response["service_status"] = response.pop("status", "")
    response["status"] = "ok"
    return response


class IntegrationsMixin:
    def set_now_playing(self, track: dict | None = None) -> dict:
        """What the Player plays now: {title, artist, album, duration,
        position, playing}, or None when it stopped."""
        from ..core import discord_presence, scrobbler

        info = track if isinstance(track, dict) else None
        discord_presence.service().now_playing(info)
        scrobbler.service().now_playing(info)
        return {"status": "ok"}

    # ── Discord ─────────────────────────────────────────────────────────────

    def get_discord_presence(self) -> dict:
        from ..core import discord_presence

        presence = discord_presence.service()
        presence.start()
        return _service_response(presence.status())

    def save_discord_presence(
        self,
        enabled: Any = None,
        application_id: Any = None,
        show_artwork: Any = None,
    ) -> dict:
        """Changes what is given; None leaves a setting as it is."""
        from ..core import discord_presence

        presence = discord_presence.service()
        try:
            presence.configure(
                enabled=enabled,
                application_id=application_id,
                show_artwork=show_artwork,
            )
        except Exception as e:
            return {"status": "error", "error": str(e)}
        return _service_response(presence.status())

    # ── Last.fm ─────────────────────────────────────────────────────────────
    # The shared secret and session key are never sent back to the page:
    # only whether a secret is stored.

    def get_lastfm(self) -> dict:
        from ..core import scrobbler

        return _service_response(scrobbler.service().status())

    def save_lastfm(
        self, enabled: Any = None, api_key: Any = None, secret: Any = None
    ) -> dict:
        """Changes what is given; None leaves a setting as it is (the form
        never receives the secret back, so an untouched field keeps it)."""
        from ..core import scrobbler

        service = scrobbler.service()
        try:
            service.configure(enabled=enabled, api_key=api_key, secret=secret)
        except Exception as e:
            return {"status": "error", "error": str(e)}
        return _service_response(service.status())

    def connect_lastfm(self) -> dict:
        """Opens Last.fm's approval page in the browser; the status says
        when it was approved."""
        from ..core import scrobbler

        service = scrobbler.service()
        try:
            url = service.begin_auth()
        except Exception as e:
            return {"status": "error", "error": str(e)}
        opener = getattr(self, "open_url", None)
        if callable(opener):
            opener(url)
        return {"url": url, **_service_response(service.status())}

    def disconnect_lastfm(self) -> dict:
        from ..core import scrobbler

        service = scrobbler.service()
        service.disconnect()
        return _service_response(service.status())

    def retry_lastfm(self) -> dict:
        """Sends the scrobbles still waiting now."""
        from ..core import scrobbler

        service = scrobbler.service()
        service.flush()
        return _service_response(service.status())
