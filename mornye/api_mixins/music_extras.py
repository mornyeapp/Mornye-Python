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

    def translate_lyrics(self, lyrics: str, language: str) -> dict:
        """Translates `lyrics` (plain or LRC) into `language`, line by line."""
        from ..core.lyrics_translate import translate_lyrics_async

        try:
            text = run_sync(translate_lyrics_async(lyrics, language), timeout=600)
        except Exception as e:
            return {"status": "error", "error": str(e)}
        return {"status": "ok", "lyrics": text}

    def save_translated_lyrics(self, path: str, language: str, lyrics: str) -> dict:
        """Writes `lyrics` next to the track as ``<name>.<language>.lrc``."""
        import re

        if not self._is_approved_path(path):
            return {"status": "error", "error": "Path is outside approved directories"}
        code = re.sub(r"[^A-Za-z0-9-]", "", language or "")[:12] or "translation"
        target = Path(path).with_suffix(f".{code}.lrc")
        try:
            target.write_text(lyrics, encoding="utf-8")
        except OSError as e:
            return {"status": "error", "error": str(e)}
        return {"status": "ok", "path": str(target)}
