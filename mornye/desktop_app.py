"""Entry point of the packaged desktop app (macOS .app, Windows installer, AppImage).

The release's single-file binaries are command-line tools: started with no
arguments they print the help and exit, which is what a terminal user wants
and what someone who double-clicked a download does not. The desktop
packages start here instead:

* With no arguments — a double-click, the Dock, the Start menu, a .desktop
  launcher — the GUI opens, as if ``--gui`` had been passed.
* With arguments, they reach the normal command line unchanged, so the
  binary inside the app still works from a terminal.

The packages are windowed: on Windows a windowed executable has no console,
so ``sys.stdout`` and ``sys.stderr`` are ``None``, and anything the app
prints or logs would otherwise fail or vanish. They are pointed at
``~/.mornye/.cache/desktop.log`` instead, which is also where a crash at
start-up leaves its traceback, since there is no terminal to show it.
"""

from __future__ import annotations

import contextlib
import sys
import traceback
from pathlib import Path
from typing import TextIO

#: Arguments the OS may add to a GUI launch that are not Mornye's: older
#: macOS versions pass the process serial number (``-psn_0_12345``) to an
#: app opened from Finder.
_OS_LAUNCH_ARGS_PREFIXES = ("-psn_",)


def desktop_argv(argv: list[str]) -> list[str]:
    """``argv`` with ``--gui`` added when it names no command of its own."""
    args = [a for a in argv[1:] if not a.startswith(_OS_LAUNCH_ARGS_PREFIXES)]
    return [argv[0], *(args or ["--gui"])]


def desktop_log_path() -> Path:
    from .core.paths import cache_path

    return cache_path("desktop.log")


def _open_log() -> TextIO | None:
    try:
        path = desktop_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Line-buffered and overwritten on every launch: the file is for
        # the run that just went wrong, not a history.
        return open(path, "w", encoding="utf-8", errors="replace", buffering=1)
    except OSError:
        return None


def redirect_missing_streams() -> TextIO | None:
    """Point a missing stdout/stderr at the desktop log; return the log, if opened."""
    if sys.stdout is not None and sys.stderr is not None:
        return None
    log = _open_log()
    if log is None:
        return None
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log
    return log


def main() -> None:
    log = redirect_missing_streams()
    sys.argv = desktop_argv(sys.argv)

    from dotenv import load_dotenv

    from .launcher import main as launcher_main

    load_dotenv()
    try:
        launcher_main()
    except SystemExit:
        raise
    except BaseException:
        # Without a console this is the only record of why the window never
        # appeared.
        target = log or _open_log()
        if target is not None:
            with contextlib.suppress(Exception):
                traceback.print_exc(file=target)
                target.flush()
        raise


if __name__ == "__main__":
    main()
