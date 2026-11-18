"""core/desktop_notify.py — a native notification on the desktop.

The "notify" post-download action. The title and the text come from track
and album names, which come from metadata nobody here wrote, so they are
never written into code:

- macOS: osascript runs a fixed script and receives both as arguments
  (`on run argv`). Interpolating them into the script, as before, broke on a
  quote in a title, and a name crafted for it could run AppleScript, which
  can run shell commands.
- Windows: a toast through PowerShell's Windows.UI.Notifications, with both
  passed in environment variables. Windows had no notification at all.
- Linux and the rest: notify-send, after `--`, so a title starting with "-"
  is not read as an option.

Never raises and never waits long: a notification that cannot be shown is
not worth failing or holding up the batch it reports on.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

logger = logging.getLogger(__name__)

_MACOS_SCRIPT = (
    "on run argv",
    "display notification (item 2 of argv) with title (item 1 of argv)",
    "end run",
)

# PowerShell's own AppUserModelID: a toast from an unregistered id is
# silently dropped by Windows 10 and later.
_WINDOWS_APP_ID = (
    "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe"
)
_WINDOWS_SCRIPT = (
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
    " ContentType = WindowsRuntime] > $null;"
    " $t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
    "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
    " $x = $t.GetElementsByTagName('text');"
    " $x.Item(0).AppendChild($t.CreateTextNode($env:MORNYE_NOTIFY_TITLE)) > $null;"
    " $x.Item(1).AppendChild($t.CreateTextNode($env:MORNYE_NOTIFY_BODY)) > $null;"
    " [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
    f"'{_WINDOWS_APP_ID}').Show([Windows.UI.Notifications.ToastNotification]::new($t))"
)


def command(title: str, body: str) -> tuple[list[str], dict[str, str] | None]:
    """The program to run for this platform, and the environment it needs."""
    if sys.platform == "darwin":
        args = ["osascript"]
        for line in _MACOS_SCRIPT:
            args += ["-e", line]
        return [*args, title, body], None
    if sys.platform == "win32":
        env = {**os.environ, "MORNYE_NOTIFY_TITLE": title, "MORNYE_NOTIFY_BODY": body}
        return [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-Command",
            _WINDOWS_SCRIPT,
        ], env
    return ["notify-send", "--app-name=Mornye", "--", title, body], None


async def notify(title: str, body: str) -> None:
    """Shows a notification. Never raises."""
    args, env = command(str(title or "Mornye"), str(body or ""))
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(process.wait(), timeout=10)
    except Exception as exc:
        logger.debug("[notify] could not show a notification: %s", exc)
