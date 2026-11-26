"""api_mixins/personal.py — the listening record, the personal Home and the
listener profile (ported from Mornye mobile 5.4.5).

Everything here is scoped to the caller (`owner` in multi-user `--web`), so
it is safe to expose over HTTP: see ALLOWED_METHODS in webapp.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


class PersonalMixin:
    if TYPE_CHECKING:
        log: Any

    def _personal_owner(self) -> str:
        return getattr(self, "owner", "") or ""

    # ── Listening record ────────────────────────────────────────────────────

    def record_listening(self, entry: dict) -> dict:
        """One piece of Player listening (core/listening.py `record`)."""
        from ..core import listening

        entry = entry if isinstance(entry, dict) else {}
        ok = listening.record(self._personal_owner(), entry)
        # The piece sent when a song stops being the current one may make a
        # Last.fm scrobble. Desktop only: over --web (where _ws_broadcast is
        # set) the account would be the server's, not the listener's.
        if entry.get("outcome") and getattr(self, "_ws_broadcast", None) is None:
            try:
                from ..core import scrobbler

                scrobbler.service().listened(entry)
            except Exception:
                pass
        return {"status": "ok" if ok else "ignored"}

    def clear_listening_record(self) -> dict:
        from ..core import listening

        listening.clear(self._personal_owner())
        return {"status": "ok"}

    # ── Home ────────────────────────────────────────────────────────────────

    def get_personal_home(self, session: str = "", recommend: bool = False) -> dict:
        """Home's sections in the person's order (core/personal_home.py).
        The network-bound "Recommended for you" comes from
        `get_home_recommendations` unless `recommend` is set, so the rest
        shows at once."""
        from ..core import personal_home

        try:
            return {
                "status": "ok",
                **personal_home.home(
                    self._personal_owner(),
                    session=str(session or ""),
                    recommend=bool(recommend),
                ),
            }
        except Exception as e:
            self.log(f"[home] could not build Home: {e}", "error")
            return {"status": "error", "error": str(e)}

    def get_home_recommendations(self, refresh: bool = False) -> dict:
        """The day's "Recommended for you", asked once a day and cached."""
        from datetime import datetime

        from ..core import personal_home

        try:
            tracks = personal_home.recommended(
                self._personal_owner(),
                personal_home.library_songs(),
                datetime.now(),
                refresh=bool(refresh),
            )
            return {"status": "ok", "tracks": tracks}
        except Exception as e:
            return {"status": "error", "error": str(e), "tracks": []}

    def save_home_layout(
        self, order: list | None = None, visible: dict | None = None
    ) -> dict:
        """Edit Home: a new section order and/or {section: shown}."""
        from ..core import personal_home

        owner = self._personal_owner()
        layout = personal_home.layout_with(
            personal_home.load_layout(owner),
            list(order) if isinstance(order, list) else None,
            visible if isinstance(visible, dict) else None,
        )
        personal_home.save_layout(owner, layout)
        return {"status": "ok", "layout": personal_home.layout_view(layout)}

    def reset_home_layout(self) -> dict:
        from ..core import personal_home

        layout = personal_home.save_layout(self._personal_owner(), {})
        return {"status": "ok", "layout": personal_home.layout_view(layout)}

    def pin_to_home(self, pin: dict) -> dict:
        """Pins a Library album, playlist, artist, genre or folder (up to 12)."""
        from ..core import personal_home

        ok, pins = personal_home.add_pin(
            self._personal_owner(), pin if isinstance(pin, dict) else {}
        )
        if not ok:
            full = len(pins) >= personal_home.PIN_LIMIT
            return {
                "status": "error",
                "error": (
                    f"Home holds {personal_home.PIN_LIMIT} pins. Unpin one first."
                    if full
                    else "This can't be pinned."
                ),
                "pins": pins,
            }
        return {"status": "ok", "pins": pins}

    def unpin_from_home(self, key: str) -> dict:
        from ..core import personal_home

        return {
            "status": "ok",
            "pins": personal_home.remove_pin(self._personal_owner(), str(key or "")),
        }

    def move_home_pin(self, key: str, to: int = 0) -> dict:
        from ..core import personal_home

        return {
            "status": "ok",
            "pins": personal_home.move_pin(
                self._personal_owner(), str(key or ""), int(to or 0)
            ),
        }

    def get_home_pin_songs(self, key: str) -> dict:
        """The songs a pin plays (genres and folders have no page of their
        own in the Library: they play)."""
        from ..core import personal_home

        owner = self._personal_owner()
        pin = next(
            (
                p
                for p in personal_home.load_pins(owner)
                if personal_home.pin_key(p) == key
            ),
            None,
        )
        if pin is None:
            return {"status": "error", "error": "This pin is gone.", "songs": []}
        songs = personal_home.pin_songs(owner, pin, personal_home.library_songs())
        return {"status": "ok", "songs": songs}

    # ── Listener profile ────────────────────────────────────────────────────

    def get_listener_profile(self) -> dict:
        """Totals and streaks, the last 30 days and 12 months, taste,
        time of day, habits, tops, the collection and the listener types
        (core/listener_profile.py)."""
        from ..core import listener_profile

        try:
            return {"status": "ok", **listener_profile.profile(self._personal_owner())}
        except Exception as e:
            self.log(f"[profile] could not read the listening record: {e}", "error")
            return {"status": "error", "error": str(e)}
