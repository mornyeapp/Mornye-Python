"""safe_xml.py — parsing XML that came from somewhere else.

Two places read XML off the network: a WebDAV server's directory listing and
a lyrics service's TTML. Neither format has any use for a document type
declaration, and a DTD is where every classic XML attack lives — entity
expansion ("billion laughs"), external entities, external DTD retrieval.

So the rule is the one ``defusedxml`` applies by default, without the
dependency: a document that declares a DTD or an entity is refused before the
parser sees it.
"""

from __future__ import annotations

import re
from xml.etree import ElementTree

#: ``<!DOCTYPE`` / ``<!ENTITY``, in the encodings a declaration can arrive in.
_FORBIDDEN = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


def fromstring(text: str | bytes) -> ElementTree.Element:
    """``ElementTree.fromstring`` that raises ``ParseError`` on a DTD.

    Raising the parser's own error means callers keep one ``except`` clause:
    a hostile document and a malformed one are both "not usable".
    """
    probe = text
    if isinstance(probe, bytes):
        # UTF-16/32 documents put NULs between the ASCII we are looking for.
        probe = probe.replace(b"\x00", b"").decode("latin-1")
    if _FORBIDDEN.search(probe):
        raise ElementTree.ParseError("document type declarations are not accepted")
    return ElementTree.fromstring(text)
