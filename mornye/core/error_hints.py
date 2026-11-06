"""error_hints.py — turn a backend error into a sentence someone can act on.

The Python twin of describeError() in frontend/app.js, for the TUI. The
commonest error on a fresh install is a missing extension ("The optional
utilities extension is not installed; '…' requires 'utilities.sflx' …"):
that is a setup step, not a network fault, and saying "check your
connection" sent people the wrong way.
"""

from __future__ import annotations

import re

_MISSING = (
    re.compile(r"optional (\S+) extension is not installed", re.I),
    re.compile(r"requires '([\w-]+)\.", re.I),
)
_NO_EXTENSIONS = re.compile(r"no (provider|extension)s? (is |are )?installed", re.I)
_NETWORK = re.compile(
    r"timed? ?out|network|connection|ENOTFOUND|getaddrinfo|unreachable", re.I
)


def kind(message: str) -> str:
    """"setup", "network" or "error"."""
    text = str(message or "")
    if any(p.search(text) for p in _MISSING) or _NO_EXTENSIONS.search(text):
        return "setup"
    if _NETWORK.search(text):
        return "network"
    return "error"


def missing_extension(message: str) -> str:
    """The extension a message says is missing ("utilities"), or ""."""
    for pattern in _MISSING:
        match = pattern.search(str(message or ""))
        if match:
            return match.group(1)
    return ""


def explain(message: str, what: str = "This") -> str:
    """One line: what happened and what to do about it."""
    text = str(message or "").strip()
    k = kind(text)
    if k == "setup":
        name = missing_extension(text)
        pretty = name.capitalize() if name else ""
        needed = f"the {pretty} extension" if pretty else "an extension"
        return (
            f"{what} needs {needed}, which is not installed yet. "
            "Add it on the Extensions panel, then try again."
        )
    if k == "network":
        return f"{what} could not reach the service. Check that you are online, then retry."
    return f"{what} failed — {text}" if text else f"{what} failed."
