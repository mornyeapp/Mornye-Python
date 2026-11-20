# PyInstaller spec of the desktop app: the GUI packaged as an application,
# next to the single-file command-line binaries the release also ships.
#
#     python packaging/make_icons.py          # build/icons/mornye.icns, .png
#     pyinstaller packaging/mornye-desktop.spec
#
# Output (dist/):
#   macOS    Mornye.app            → packed into Mornye-macOS.dmg
#   Windows  Mornye/Mornye.exe     → wrapped by packaging/windows/mornye.iss
#   Linux    Mornye/Mornye         → wrapped into an AppImage
#
# One folder rather than one file: a --onefile build unpacks itself into a
# temporary directory on every launch, which is seconds of nothing after a
# double-click, and on macOS a one-file .app is deprecated by PyInstaller.
# Windowed: no console on Windows, no Terminal on macOS. desktop_launcher.py
# opens the GUI when there are no arguments (see mornye/desktop_app.py).
#
# The bundled data and hidden imports match the CLI build in
# .github/workflows/build-release.yml; keep the two in step.

import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH is set by PyInstaller
ICONS = ROOT / "build" / "icons"

with open(ROOT / "pyproject.toml", "rb") as f:
    VERSION = tomllib.load(f)["project"]["version"]

datas, binaries, hiddenimports = collect_all("mornye")
for sub in ("frontend", "extensions", "tui"):
    datas.append((str(ROOT / "mornye" / sub), f"mornye/{sub}"))
hiddenimports += [
    "mornye.core.signed_session_desktop",
    "mornye.core.signed_session_mobile",
    "mornye.core.signed_session_mono",
    "mpv",
]

if sys.platform == "win32":
    # Same libmpv as the CLI build (fetched by the workflow), found by
    # native_player next to the executable's _internal directory.
    libmpv = ROOT / "build" / "libmpv" / "libmpv-2.dll"
    if libmpv.exists():
        binaries.append((str(libmpv), "."))
    icon = str(ROOT / "mornye" / "frontend" / "assets" / "icons" / "logo.ico")
elif sys.platform == "darwin":
    icon = str(ICONS / "mornye.icns")
else:
    # pywebview's Qt backend: wheels that carry Qt and QtWebEngine with
    # them, where GTK would need the user's own WebKitGTK to match the one
    # the AppImage was built against.
    hiddenimports += ["webview.platforms.qt", "qtpy"]
    icon = None

a = Analysis(  # noqa: F821
    [str(ROOT / "desktop_launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Mornye",
    console=False,
    icon=icon,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    name="Mornye",
)

if sys.platform == "darwin":
    app = BUNDLE(  # noqa: F821
        coll,
        name="Mornye.app",
        icon=icon,
        bundle_identifier="app.mornye.desktop",
        version=VERSION,
        info_plist={
            "CFBundleName": "Mornye",
            "CFBundleDisplayName": "Mornye",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.music",
            "NSHumanReadableCopyright": "MIT License",
        },
    )
