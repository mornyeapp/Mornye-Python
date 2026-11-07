"""network_paths.py — let a local-library path point at a network share.

"Fix Local Files" and the duplicate scan work on real files: they read and
rewrite tags in place, so they need a path the operating system can open. A
share on a NAS qualifies once the OS has mounted it, and that is the route
taken here rather than speaking SMB ourselves (``network_destinations`` does
that for uploads, but rewriting tags through it would mean copying every file
down and back up):

* ``smb://nas/Music/Albums`` (also ``cifs://``, ``afp://``, ``nfs://``) is
  looked up in the mount table and turned into the mounted path —
  ``/Volumes/Music/Albums`` on macOS, ``/mnt/music/Albums`` or the GVFS
  folder on Linux, ``\\\\nas\\Music\\Albums`` on Windows (UNC needs no mount);
* ``\\\\nas\\Music`` and ``//nas/Music`` are accepted the same way;
* a share that is not mounted gets an error that says how to mount it.

:func:`network_roots` lists the mounted shares so the scans' approved-roots
check can allow them: mounting a share is the user granting access to it.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

#: Filesystem types that are a network share, as the mount tables name them.
NETWORK_FS_TYPES = frozenset(
    {
        "smbfs",
        "cifs",
        "smb3",
        "smb2",
        "nfs",
        "nfs4",
        "afpfs",
        "webdav",
        "davfs",
        "fuse.davfs2",
        "fuse.sshfs",
        "sshfs",
        "macfuse",
    }
)

SHARE_SCHEMES = ("smb", "cifs", "afp", "nfs")


@dataclass(frozen=True)
class Mount:
    """One row of the mount table that is a network share."""

    host: str  # lowercased server name ("nas.local")
    share: str  # first path component on the server ("Music")
    mount_point: str  # where the OS put it ("/Volumes/Music")
    fs_type: str


@dataclass(frozen=True)
class ShareRef:
    host: str
    share: str
    rest: tuple[str, ...]  # path inside the share


# ---------------------------------------------------------------------------
# Parsing what the user typed
# ---------------------------------------------------------------------------


def parse_share_ref(raw: str) -> ShareRef | None:
    """``smb://user@nas/Music/A`` / ``\\\\nas\\Music\\A`` / ``//nas/Music/A``.

    None for anything that is not a share reference (a plain local path).
    """
    text = (raw or "").strip().strip("'\"")
    if not text:
        return None
    lowered = text.lower()
    if any(lowered.startswith(f"{scheme}://") for scheme in SHARE_SCHEMES):
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
        segments = [unquote(p) for p in parts.path.split("/") if p]
    elif text.startswith("\\\\") or (
        text.startswith("//") and not text.startswith("///")
    ):
        segments = [p for p in re.split(r"[\\/]+", text[2:]) if p]
        if not segments:
            return None
        host = segments.pop(0).lower()
        if "@" in host:
            host = host.rsplit("@", 1)[1]
    else:
        return None
    if not host or not segments:
        return None
    return ShareRef(host=host, share=segments[0], rest=tuple(segments[1:]))


# ---------------------------------------------------------------------------
# Mount tables
# ---------------------------------------------------------------------------


def _source_host_share(source: str) -> tuple[str, str] | None:
    """``//user@nas/Music`` · ``nas:/export/music`` · GVFS folder names."""
    gvfs = re.match(r"smb-share:(.*)", source)
    if gvfs:
        fields = dict(kv.split("=", 1) for kv in gvfs.group(1).split(",") if "=" in kv)
        if fields.get("server") and fields.get("share"):
            return fields["server"].lower(), unquote(fields["share"])
        return None
    match = re.match(r"//(?:[^@/]*@)?([^/]+)/([^/]+)", source)
    if match:
        return match.group(1).lower(), unquote(match.group(2))
    match = re.match(r"([^:/]+):/?(.+)", source)  # nfs  host:/export/path
    if match:
        return match.group(1).lower(), match.group(2).rstrip("/").split("/")[-1]
    return None


def parse_mount_output(text: str) -> list[Mount]:
    """macOS / BSD ``mount``: ``//u@nas/Music on /Volumes/Music (smbfs, …)``."""
    mounts = []
    for line in text.splitlines():
        match = re.match(r"(.+?) on (.+?) \(([^,)]+)", line)
        if not match:
            continue
        source, point, fs_type = match.groups()
        if fs_type.strip().lower() not in NETWORK_FS_TYPES:
            continue
        hs = _source_host_share(source)
        if hs:
            mounts.append(Mount(hs[0], hs[1], point, fs_type.strip().lower()))
    return mounts


def parse_proc_mounts(text: str) -> list[Mount]:
    """Linux ``/proc/mounts``: ``//nas/Music /mnt/music cifs rw,… 0 0``."""
    mounts = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        source, point, fs_type = (f.replace("\\040", " ") for f in fields[:3])
        if fs_type.lower() not in NETWORK_FS_TYPES:
            continue
        hs = _source_host_share(source)
        if hs:
            mounts.append(Mount(hs[0], hs[1], point, fs_type.lower()))
    return mounts


def _gvfs_mounts() -> list[Mount]:
    """GNOME/KDE mounts made from the file manager live under GVFS."""
    base = Path(f"/run/user/{os.getuid()}/gvfs") if hasattr(os, "getuid") else None
    if base is None or not base.is_dir():
        return []
    mounts = []
    try:
        for entry in base.iterdir():
            hs = _source_host_share(entry.name)
            if hs:
                mounts.append(Mount(hs[0], hs[1], str(entry), "gvfs"))
    except OSError:
        pass
    return mounts


def current_mounts() -> list[Mount]:
    """The network shares this machine has mounted right now."""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(
                ["mount"], capture_output=True, text=True, timeout=5, check=False
            ).stdout
            return parse_mount_output(out)
        if sys.platform.startswith("linux"):
            try:
                text = Path("/proc/mounts").read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                text = ""
            return parse_proc_mounts(text) + _gvfs_mounts()
    except Exception:
        return []
    return []  # Windows: UNC paths are opened directly, nothing to look up


def network_roots(mounts: list[Mount] | None = None) -> list[Path]:
    """Mount points of the shares, for an approved-roots check."""
    return [
        Path(m.mount_point) for m in (current_mounts() if mounts is None else mounts)
    ]


def is_unc_path(path: str) -> bool:
    return sys.platform == "win32" and str(path).startswith("\\\\")


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _not_mounted(ref: ShareRef, raw: str) -> str:
    how = {
        "darwin": "in Finder use Go ▸ Connect to Server (⌘K) and open it",
        "win32": "open it once in File Explorer so Windows has the credentials",
    }.get(sys.platform, "mount it from your file manager or with mount -t cifs")
    return (
        f"The network share '{ref.share}' on {ref.host} is not mounted on this "
        f"computer, so its files cannot be opened yet. To use {raw}, {how}, then try again."
    )


def resolve_library_path(
    raw: str,
    *,
    mounts: list[Mount] | None = None,
    platform: str | None = None,
) -> tuple[str, str]:
    """(local path, error). Plain paths pass through, shares are mapped.

    Quotes are stripped and ``~`` is expanded, as the scans already did.
    """
    text = (raw or "").strip().strip("'\"")
    if not text:
        return "", "No path given"
    if text.lower().startswith("file://"):
        text = unquote(urlsplit(text).path)
        if re.match(r"/[A-Za-z]:/", text):  # file:///C:/Music
            text = text[1:]
    ref = parse_share_ref(text)
    if ref is None:
        return os.path.expanduser(text), ""

    platform = platform or sys.platform
    table = current_mounts() if mounts is None else mounts
    host_short = ref.host.split(".")[0]
    for mount in table:
        same_host = mount.host == ref.host or mount.host.split(".")[0] == host_short
        if same_host and mount.share.lower() == ref.share.lower():
            return str(Path(mount.mount_point, *ref.rest)), ""
    # Same share name mounted from an address we cannot match (an IP for a
    # .local name, say): one unambiguous candidate is good enough.
    by_share = [m for m in table if m.share.lower() == ref.share.lower()]
    if len(by_share) == 1:
        return str(Path(by_share[0].mount_point, *ref.rest)), ""
    if platform == "win32":
        return "\\\\" + "\\".join((ref.host, ref.share, *ref.rest)), ""
    return "", _not_mounted(ref, text)


def describe_shares(mounts: list[Mount] | None = None) -> list[dict]:
    """For the UI: the mounted shares, as quick picks."""
    table = current_mounts() if mounts is None else mounts
    return [
        {
            "label": f"{m.share} on {m.host}",
            "url": (
                f"smb://{m.host}/{m.share}"
                if m.fs_type in {"smbfs", "cifs", "smb3", "smb2", "gvfs"}
                else ""
            ),
            "path": m.mount_point,
            "type": m.fs_type,
        }
        for m in table
    ]
