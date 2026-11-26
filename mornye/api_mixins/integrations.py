"""api_mixins/integrations.py — telling other services what the Player plays.

* Discord Rich Presence: "Listening to …" on the Discord profile
  (core/discord_presence.py).

The page reports what plays through `set_now_playing` on every change of
song, play/pause and seek; each service takes what it needs from it.

Desktop only: these talk to the Discord client and accounts of the machine
running Mornye, which over `--web` is the server, not the listener — see the
note under ALLOWED_METHODS in webapp.py.
"""

from __future__ import annotations

from typing import Any


class IntegrationsMixin:
    def set_now_playing(self, track: dict | None = None) -> dict:
        """What the Player plays now: {title, artist, album, duration,
        position, playing}, or None when it stopped."""
        from ..core import discord_presence

        info = track if isinstance(track, dict) else None
        discord_presence.service().now_playing(info)
        return {"status": "ok"}

    # ── Discord ─────────────────────────────────────────────────────────────

    def get_discord_presence(self) -> dict:
        from ..core import discord_presence

        presence = discord_presence.service()
        presence.start()
        return {"status": "ok", **presence.status()}

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
        return {"status": "ok", **presence.status()}
