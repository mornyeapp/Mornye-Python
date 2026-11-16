"""N2: "Copy as CLI command" in the GUI (plan §7.3, §0 item 7)."""

from __future__ import annotations

from pathlib import Path

from mornye.core.cli_preview import gui_command
from mornye.webapp import ALLOWED_METHODS

FRONT = Path(__file__).resolve().parents[1] / "mornye" / "frontend"
URL = "https://open.spotify.com/album/x"


def test_only_choices_that_differ_from_the_cli_defaults_are_printed():
    cmd = gui_command(URL, {"services": ["tidal"], "quality": "LOSSLESS"}, "/m")
    assert cmd.splitlines()[0] == f"mornye {URL} /m \\"
    assert "-s tidal" in cmd
    assert "-q " not in cmd  # LOSSLESS is the CLI default
    assert "--timeout" not in cmd  # the GUI sends none: the engine default


def test_gui_names_and_values_map_onto_flags():
    cmd = gui_command(
        URL,
        {
            "services": ["qobuz", "tidal"],
            "quality": "HI_RES_LOSSLESS",
            "lyrics": False,
            "enrich_metadata": True,
            "enrich_providers": ["deezer"],
            "transcode_to": "mp3",
            "transcode_bitrate": "256k",
            "log_level": "DEBUG",
            "network_destination_id": "abc",  # GUI-only: no flag
            "sound_enabled": True,
        },
        "/m",
    )
    for flag in (
        "-s qobuz tidal",
        "-q HI_RES_LOSSLESS",
        "--no-lyrics",
        "--enrich-providers deezer",
        "--transcode mp3",
        "--transcode-bitrate 256k",
        "--log-level DEBUG",
    ):
        assert flag in cmd, flag
    assert "abc" not in cmd


def test_gui_command_respects_batch_provider_priority_and_fallback():
    cmd = gui_command(
        URL,
        {
            "services": ["tidal", "qobuz", "deezer"],
            "preferred_service": "qobuz",
            "fallback_services": False,
        },
        "/m",
    )
    assert "-s qobuz" in cmd
    assert "-s tidal" not in cmd


def test_no_transcode_and_info_level_print_nothing():
    cmd = gui_command(URL, {"transcode_to": "none", "log_level": "INFO"}, "/m")
    assert "--transcode" not in cmd and "--log-level" not in cmd


def test_wired_to_the_gui_and_the_web_shim():
    assert "get_cli_command" in ALLOWED_METHODS
    assert "'get_cli_command'" in (FRONT / "web-shim.js").read_text(encoding="utf-8")
    app = (FRONT / "app.js").read_text(encoding="utf-8")
    assert 'data-onclick="copyCliCommand()"' in app
    assert "async function copyCliCommand()" in app
    # Both card templates (album and single track) carry the button.
    assert app.count("${CLI_BUTTON_HTML}") == 2
