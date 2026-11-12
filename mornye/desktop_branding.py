"""Give the desktop window Mornye's icon (and, on macOS, name) everywhere.

Run from source (``python -m mornye`` / ``uv run``), the process is the
Python interpreter, so by default every OS shows Python's icon for it:

* macOS — the rocket and the name "Python" in the Dock, Cmd+Tab and the
  menu bar, because the main bundle is the framework's ``Python.app``.
* Windows — python.exe's icon on the window and the taskbar, and the
  taskbar groups the window under "Python".
* Linux — a generic or Python icon on the window and in the task switcher.

:func:`apply_desktop_branding` fixes what can be fixed at runtime and
returns the icon file to hand to ``webview.start(icon=...)``, which
pywebview applies on its Windows (WinForms), GTK and Qt backends.

macOS details
-------------
* Dock / Cmd+Tab icon — ``NSApplication.setApplicationIconImage_``.
  pywebview's own ``icon=`` would do this too, but with the full-bleed
  square logo; we draw it on the macOS icon grid instead, and therefore
  return no icon for webview.start() on macOS.
* App name — ``CFBundleName`` in the main bundle's in-memory info
  dictionary. The process checks in with LaunchServices only when
  NSApplication is created, and the name it registers (menu bar, Cmd+Tab,
  ``lsappinfo``) is read from that dictionary; pywebview's "About/Hide/Quit
  Mornye" items read it too. So it must be set before NSApplication exists,
  which is why this runs before pywebview is started.

What stays Python's on macOS: the bundle identifier (``org.python.python``),
the Dock tile of a *not-running* app, and anything Finder shows for the
interpreter on disk. Those come from ``Python.app``'s Info.plist and only a
bundled build (py2app / PyInstaller with ``CFBundleName = "Mornye"`` and a
``.icns``) changes them. Likewise on Windows the icon of python.exe itself
(Explorer, the pinned taskbar shortcut) only changes with a packaged .exe.

Everything here is best-effort: a failure, or PyObjC missing, leaves the
default icon and never stops the application from starting.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

APP_NAME = "Mornye"

#: Windows taskbar identity. Without an explicit AppUserModelID the window
#: is grouped under python.exe and the taskbar shows python.exe's icon
#: instead of the window's.
WINDOWS_APP_USER_MODEL_ID = "Mornye.Desktop"

_ICONS_DIR = Path(__file__).resolve().parent / "frontend" / "assets" / "icons"

# macOS icon grid (Big Sur and later): a 1024 pt canvas holds an 824 pt
# rounded square inset by 100 pt, corner radius ~185 pt. logo.png is a
# full-bleed square, so drawn as-is it would look larger and sharper than
# every other icon in the Dock.
_CANVAS = 1024.0
_INSET = 100.0
_CORNER_RADIUS = 185.4


def app_icon_path() -> Path:
    """The square, full-bleed 1024x1024 Mornye logo shipped with the frontend."""
    return _ICONS_DIR / "logo.png"


def windows_icon_path() -> Path:
    """Multi-size (16-256 px) .ico of the same logo; WinForms reads only .ico."""
    return _ICONS_DIR / "logo.ico"


def window_icon_path(platform: str | None = None) -> str | None:
    """The icon file to pass to ``webview.start(icon=...)`` on ``platform``.

    None on macOS (see :func:`apply_macos_branding`) or if the file is missing.
    """
    platform = platform or sys.platform
    if platform == "darwin":
        return None
    path = windows_icon_path() if platform == "win32" else app_icon_path()
    return str(path) if path.is_file() else None


def apply_windows_app_id(platform: str | None = None) -> bool:
    """Gives the process its own taskbar identity on Windows. Never raises."""
    if (platform or sys.platform) != "win32":
        return False
    try:
        import ctypes

        # `windll` only exists in the Windows build of ctypes (and its stubs).
        windll = getattr(ctypes, "windll")
        result = windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            WINDOWS_APP_USER_MODEL_ID
        )
        return result == 0  # S_OK
    except Exception:
        logger.debug("Could not set the AppUserModelID", exc_info=True)
        return False


def _dock_icon(appkit, source):
    """``source`` redrawn on the macOS icon grid, or ``source`` itself if that fails."""
    try:
        body = _CANVAS - 2 * _INSET
        rect = ((_INSET, _INSET), (body, body))
        image = appkit.NSImage.alloc().initWithSize_((_CANVAS, _CANVAS))
        image.lockFocus()
        try:
            appkit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                rect, _CORNER_RADIUS, _CORNER_RADIUS
            ).addClip()
            source.drawInRect_fromRect_operation_fraction_(
                rect,
                ((0.0, 0.0), (0.0, 0.0)),  # NSZeroRect: the whole source image
                appkit.NSCompositingOperationSourceOver,
                1.0,
            )
        finally:
            image.unlockFocus()
        return image
    except Exception:
        logger.debug("Could not round the Dock icon; using it as-is", exc_info=True)
        return source


def apply_macos_branding(
    name: str = APP_NAME,
    icon_path: str | Path | None = None,
    *,
    platform: str | None = None,
) -> bool:
    """Sets the app name and Dock icon for a from-source run on macOS.

    Call it before ``webview.start()``. Returns True if anything was applied,
    False when it was a no-op (not macOS, PyObjC missing, or every step
    failed). Never raises.
    """
    if (platform or sys.platform) != "darwin":
        return False

    try:
        import AppKit
        from Foundation import NSBundle
    except Exception:
        logger.debug("PyObjC not available; keeping the default app name and icon")
        return False

    applied = False

    # Name first: it has to be in place before NSApplication is created.
    try:
        bundle = NSBundle.mainBundle()
        for info in (bundle.localizedInfoDictionary(), bundle.infoDictionary()):
            if info is not None:
                info["CFBundleName"] = name
                applied = True
    except Exception:
        logger.debug("Could not set CFBundleName", exc_info=True)

    try:
        path = Path(icon_path) if icon_path is not None else app_icon_path()
        if path.is_file():
            source = AppKit.NSImage.alloc().initWithContentsOfFile_(str(path))
            if source is not None:
                app = AppKit.NSApplication.sharedApplication()
                app.setApplicationIconImage_(_dock_icon(AppKit, source))
                applied = True
        else:
            logger.debug("Dock icon not found at %s", path)
    except Exception:
        logger.debug("Could not set the Dock icon", exc_info=True)

    return applied


def apply_desktop_branding(platform: str | None = None) -> str | None:
    """Applies the per-OS runtime branding; returns the icon for webview.start().

    Call it before ``webview.start()``. Never raises.
    """
    platform = platform or sys.platform
    apply_macos_branding(platform=platform)
    apply_windows_app_id(platform=platform)
    return window_icon_path(platform)
