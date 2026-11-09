"""Download settings from the environment: ``MORNYE_<SETTING>``.

A container is configured with environment variables, not with a command
line or a GUI click: every setting below can be given as ``MORNYE_`` plus its
name in capitals — ``MORNYE_SERVICES=tidal,qobuz``, ``MORNYE_QUALITY=
HI_RES_LOSSLESS``, ``MORNYE_SAVE_LRC=true`` (issue #254).

They are *defaults*, never overrides:

* CLI: below ``--profile`` and the flags, above the config file;
* GUI / ``--web``: below what has been saved from the page — the page shows
  the environment's value until somebody changes it there.

Value syntax: booleans accept ``1/true/yes/on`` and ``0/false/no/off``;
lists are comma-separated; ``MORNYE_EXTENSION_QUALITY`` is
``service=quality-id`` pairs (``amazon=ac4,tidal=LOSSLESS``). A value that
does not parse is ignored with a warning rather than failing the start-up.

Deliberately not here: ``tidal_custom_api`` and ``qobuz_local_api_url``
(set tidal-web's / qobuz-web's ``customEndpoint`` setting instead), and
the post-download *command* still needs ``MORNYE_ALLOW_POST_COMMAND=1`` to
run from the GUI, exactly as when it is typed into the page.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

PREFIX = "MORNYE_"

#: setting name (as profiles and the CLI spell it) -> value type.
SETTINGS: dict[str, str] = {
    # Container configuration
    "services": "list",
    "timeout_s": "int",
    "max_concurrent_downloads": "int",
    "loop": "int",
    "track_max_retries": "int",
    "lyrics_providers": "list",
    "apple_lyrics_word_by_word": "bool",
    "save_lrc": "bool",
    "lrc_library_dir": "str",
    "save_canvas": "bool",
    "canvas_library_dir": "str",
    "canvas_providers": "list",
    "enrich_metadata": "bool",
    "enrich_providers": "list",
    "post_download_action": "str",
    "post_download_command": "str",
    "extension_quality": "map",
    # Profile-style defaults
    "filename_format": "str",
    "use_track_numbers": "bool",
    "use_album_track_numbers": "bool",
    "use_artist_subfolders": "bool",
    "use_album_subfolders": "bool",
    "create_playlist_subfolders": "bool",
    "first_artist_only": "bool",
    "artist_separator": "str",
    "include_featuring": "bool",
    "quality": "str",
    "allow_fallback": "bool",
    "embed_lyrics": "bool",
    "transcode_to": "str",
    "transcode_bitrate": "str",
    "transcode_keep_original": "bool",
    "transcode_max_sample_rate": "int",
    "transcode_max_bit_depth": "int",
    "verify_hires": "bool",
    "redownload_fake_hires": "bool",
}

#: Where the GUI's saved settings spell a setting differently.
GUI_KEYS = {"embed_lyrics": "lyrics"}

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def env_name(setting: str) -> str:
    return PREFIX + setting.upper()


def _parse(kind: str, raw: str) -> Any:
    text = raw.strip()
    if kind == "bool":
        lowered = text.lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        raise ValueError("expected true or false")
    if kind == "int":
        numeric_value = int(text)
        if numeric_value < 0:
            raise ValueError("expected a number of 0 or more")
        return numeric_value
    if kind == "list":
        return [part.strip() for part in text.split(",") if part.strip()]
    if kind == "map":
        pairs: dict[str, str] = {}
        for item in text.split(","):
            if not item.strip():
                continue
            key, sep, parsed_value = item.partition("=")
            if not sep or not key.strip() or not parsed_value.strip():
                raise ValueError("expected service=id pairs")
            pairs[key.strip()] = parsed_value.strip()
        return pairs
    return text


def env_defaults(environ: dict[str, str] | None = None) -> dict[str, Any]:
    """Every setting given in the environment, parsed, by its profile name."""
    source = os.environ if environ is None else environ
    found: dict[str, Any] = {}
    for setting, kind in SETTINGS.items():
        raw = source.get(env_name(setting))
        if raw is None or (kind != "str" and not raw.strip()):
            continue
        try:
            found[setting] = _parse(kind, raw)
        except ValueError as exc:
            logger.warning("Ignoring %s=%r: %s.", env_name(setting), raw, exc)
    return found


def gui_env_defaults(environ: dict[str, str] | None = None) -> dict[str, Any]:
    """`env_defaults()` with the GUI's own key names."""
    return {
        GUI_KEYS.get(setting, setting): value
        for setting, value in env_defaults(environ).items()
    }
