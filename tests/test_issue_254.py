"""Issue #254: muting sounds, the real download folder, notifications and
download settings from the environment."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mornye.core import notifiers
from mornye.core.env_settings import env_defaults, env_name, gui_env_defaults

FRONTEND = Path(__file__).resolve().parent.parent / "mornye" / "frontend"


# ---------------------------------------------------------------------------
# Notification sounds
# ---------------------------------------------------------------------------


def test_the_settings_reach_the_toast_manager() -> None:
    """`const toastMgr` is not `window.toastMgr`; the sound toggle and saved
    settings talk to the latter, so it has to be the same object."""
    source = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert re.search(r"const toastMgr = new ToastManager\(\);", source)
    assert "window.toastMgr = toastMgr;" in source
    assert source.index("window.toastMgr = toastMgr;") < source.index(
        "window.toastMgr.setSoundEnabled"
    )


# ---------------------------------------------------------------------------
# Download folder
# ---------------------------------------------------------------------------


def test_the_page_can_ask_for_the_real_download_folder() -> None:
    from mornye.app import MornyeAPI
    from mornye.webapp import ALLOWED_METHODS

    assert "get_download_dir" in ALLOWED_METHODS
    api = MornyeAPI.__new__(MornyeAPI)
    api.download_dir = "/app/downloads"
    assert api.get_download_dir() == {"download_dir": "/app/downloads"}

    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert 'id="folder-path">~/Music/Mornye<' not in html
    shim = (FRONTEND / "web-shim.js").read_text(encoding="utf-8")
    assert "get_download_dir" in shim


# ---------------------------------------------------------------------------
# Notifications from the environment
# ---------------------------------------------------------------------------


def test_no_notifier_unless_mornye_notify_is_set(monkeypatch) -> None:
    for name in ("MORNYE_NOTIFY", "MORNYE_NOTIFY_URL", "MORNYE_NOTIFY_ON"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MORNYE_NOTIFY_URL", "https://ntfy.sh/topic")
    assert notifiers.target_from_env() is None


def test_every_flag_has_its_environment_variable(monkeypatch) -> None:
    monkeypatch.setenv("MORNYE_NOTIFY", "telegram")
    monkeypatch.setenv("MORNYE_NOTIFY_TOKEN", "123:abc")
    monkeypatch.setenv("MORNYE_NOTIFY_CHAT_ID", "42")
    monkeypatch.setenv("MORNYE_NOTIFY_ON", "failure")

    target = notifiers.target_from_env()
    assert target is not None
    assert (target.kind, target.chat_id, target.on) == ("telegram", "42", "failure")
    assert target.url == "https://api.telegram.org/bot123:abc/sendMessage"

    # A flag still wins over its variable.
    assert notifiers.resolve_kind("ntfy") == "ntfy"
    assert notifiers.resolve_chat_id("7") == "7"
    assert notifiers.resolve_on("both") == "both"
    assert notifiers.resolve_on(None) == "failure"


def test_gui_downloads_use_the_environment_notifier(monkeypatch) -> None:
    from mornye.app import MornyeAPI
    from mornye.core.report import RunReport

    api = MornyeAPI.__new__(MornyeAPI)
    logged = []
    api.log = lambda message, level="info": logged.append((level, message))

    monkeypatch.delenv("MORNYE_NOTIFY", raising=False)
    assert api._notify_target_for_run() == (None, None)

    monkeypatch.setenv("MORNYE_NOTIFY", "ntfy")
    monkeypatch.setenv("MORNYE_NOTIFY_URL", "https://ntfy.sh/topic")
    monkeypatch.delenv("MORNYE_NOTIFY_ON", raising=False)
    target, report = api._notify_target_for_run()
    assert target.kind == "ntfy" and isinstance(report, RunReport)

    monkeypatch.setenv("MORNYE_NOTIFY_ON", "both")
    target, report = api._notify_target_for_run()
    assert target.on == "both" and report is None

    # Misconfigured: logged, never raised into the download.
    monkeypatch.delenv("MORNYE_NOTIFY_URL")
    assert api._notify_target_for_run() == (None, None)
    assert logged and logged[-1][0] == "warn"


# ---------------------------------------------------------------------------
# Settings from the environment
# ---------------------------------------------------------------------------


def test_settings_parse_by_type() -> None:
    env = {
        env_name("services"): "tidal, qobuz ,deezer",
        env_name("save_lrc"): "YES",
        env_name("verify_hires"): "off",
        env_name("max_concurrent_downloads"): "4",
        env_name("filename_format"): "{track} - {title}",
        env_name("extension_quality"): "amazon=ac4, tidal=LOSSLESS",
        env_name("embed_lyrics"): "false",
    }
    assert env_defaults(env) == {
        "services": ["tidal", "qobuz", "deezer"],
        "save_lrc": True,
        "verify_hires": False,
        "max_concurrent_downloads": 4,
        "filename_format": "{track} - {title}",
        "extension_quality": {"amazon": "ac4", "tidal": "LOSSLESS"},
        "embed_lyrics": False,
    }
    # The GUI spells embed_lyrics "lyrics".
    assert gui_env_defaults(env)["lyrics"] is False


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("save_lrc", "maybe"),
        ("timeout_s", "soon"),
        ("timeout_s", "-1"),
        ("extension_quality", "amazon"),
    ],
)
def test_a_bad_value_is_ignored_not_fatal(name, value, caplog) -> None:
    assert env_defaults({env_name(name): value}) == {}
    assert env_name(name) in caplog.text


def test_unset_and_unknown_variables_change_nothing() -> None:
    assert env_defaults({"MORNYE_NOT_A_SETTING": "1", "HOME": "/x"}) == {}


def test_the_page_shows_the_environment_until_something_is_saved(
    monkeypatch, tmp_path
) -> None:
    from mornye import app as app_module
    from mornye.app import MornyeAPI

    settings_file = tmp_path / "gui-settings.json"
    monkeypatch.setattr(app_module, "cache_path", lambda name: settings_file)
    monkeypatch.setenv("MORNYE_QUALITY", "HI_RES_LOSSLESS")
    monkeypatch.setenv("MORNYE_EMBED_LYRICS", "false")

    api = MornyeAPI.__new__(MornyeAPI)
    api.log = lambda *a, **k: None
    settings = api.load_settings()
    assert settings["quality"] == "HI_RES_LOSSLESS"
    assert settings["lyrics"] is False

    settings_file.write_text('{"quality": "LOSSLESS"}', encoding="utf-8")
    assert api.load_settings()["quality"] == "LOSSLESS"


def test_the_cli_reads_the_environment_below_its_flags(monkeypatch) -> None:
    from mornye import launcher

    monkeypatch.setenv("MORNYE_QUALITY", "HI_RES_LOSSLESS")
    monkeypatch.setenv("MORNYE_SAVE_LRC", "true")
    url = "https://open.spotify.com/album/x"
    monkeypatch.setattr("sys.argv", ["mornye", url])
    args = launcher.parse_args(profile_defaults={**env_defaults()})
    assert args.quality == "HI_RES_LOSSLESS"
    assert args.save_lrc is True

    monkeypatch.setattr("sys.argv", ["mornye", url, "-q", "LOSSLESS"])
    flagged = launcher.parse_args(profile_defaults={**env_defaults()})
    assert flagged.quality == "LOSSLESS"
