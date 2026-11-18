"""The "notify" post-download action (core/desktop_notify.py).

Titles come from track and album metadata. On macOS they used to be pasted
into the AppleScript, so a quote broke the notification and a crafted name
could run code; Windows showed nothing at all.
"""

from __future__ import annotations

import asyncio

import pytest

from mornye.core import desktop_notify

HOSTILE = 'Song" & (do shell script "touch /tmp/pwned") & "'


def test_macos_passes_names_as_arguments_not_as_script(monkeypatch):
    monkeypatch.setattr(desktop_notify.sys, "platform", "darwin")
    args, env = desktop_notify.command(HOSTILE, "Album")

    script = [args[i + 1] for i, a in enumerate(args) if a == "-e"]
    assert all(HOSTILE not in line for line in script)
    assert args[-2:] == [HOSTILE, "Album"]
    assert env is None


def test_windows_shows_a_toast_with_names_in_the_environment(monkeypatch):
    monkeypatch.setattr(desktop_notify.sys, "platform", "win32")
    args, env = desktop_notify.command(HOSTILE, "Album")

    assert args[0] == "powershell"
    assert all(HOSTILE not in a for a in args)
    assert env["MORNYE_NOTIFY_TITLE"] == HOSTILE
    assert env["MORNYE_NOTIFY_BODY"] == "Album"


def test_linux_ends_the_options_before_the_names(monkeypatch):
    monkeypatch.setattr(desktop_notify.sys, "platform", "linux")
    args, _ = desktop_notify.command("--help", "Album")

    assert args[0] == "notify-send"
    assert args[args.index("--") + 1 :] == ["--help", "Album"]


def test_a_missing_notifier_is_not_an_error(monkeypatch):
    async def missing(*_a, **_k):
        raise FileNotFoundError("notify-send")

    monkeypatch.setattr(desktop_notify.asyncio, "create_subprocess_exec", missing)
    asyncio.run(desktop_notify.notify("Title", "Body"))


@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
def test_every_platform_runs_one_program(monkeypatch, platform):
    seen: list[tuple] = []

    class _Proc:
        async def wait(self):
            return 0

    async def fake_exec(*args, **kwargs):
        seen.append(args)
        return _Proc()

    monkeypatch.setattr(desktop_notify.sys, "platform", platform)
    monkeypatch.setattr(desktop_notify.asyncio, "create_subprocess_exec", fake_exec)
    asyncio.run(desktop_notify.notify("Title", "Body"))

    assert len(seen) == 1
