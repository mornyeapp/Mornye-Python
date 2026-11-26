"""Translation and pronunciation tags in LRC, and the ``.mlrc`` sidecar.

The same format the Mornye app writes (``lyric_supplement_tags.dart`` and
``lyrics_sidecar.dart`` there), so a file saved by one shows in the other.
A tag follows the line it belongs to, in ID tag form so other players skip
it::

    [x-mlrc:2]
    [00:12.34]僕らは
    [x-romanized:12340:bokura wa]
    [x-translated:12340:We are]

- The key is the line's time in milliseconds; in lyrics without timing it
  is the line's index, counted from 0.
- A value runs to the row's last ``]``. ``\\n`` is a line break and ``\\\\``
  a backslash.
- ``-words`` tags carry word timing; they are recognised (and kept or
  skipped) but their timing is not used here.

Earlier versions wrote ``x-translation``, ``x-romaji`` and their ``-words``
forms with base64 values; they are still read.

``.mlrc`` is version 2 of Mornye's lyrics file: ``[x-mlrc:2]``, then the
lyrics as LRC with these tags. It sits next to the song, which keeps its
own tags untouched.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from pathlib import Path

MLRC_EXTENSION = ".mlrc"
MLRC_HEADER = "[x-mlrc:2]"
#: Sidecars larger than this are not lyrics.
MAX_SIDECAR_BYTES = 8 * 1024 * 1024

_KIND_BY_TAG = {
    "romanized": "romaji",
    "romanized-words": "romaji-words",
    "translated": "translation",
    "translated-words": "translation-words",
}
_TAG_BY_KIND = {kind: tag for tag, kind in _KIND_BY_TAG.items()}

_ROW_RE = re.compile(
    r"^\[x-(romanized-words|romanized|translated-words|translated|"
    r"romaji-words|romaji|translation-words|translation):(\d+):(.*)\]$",
    re.I,
)
#: Any Mornye ``x-`` ID tag row (the tags above, ``[x-mlrc:2]``, …).
X_TAG_ROW_RE = re.compile(r"^\[x-[a-z0-9_-]+:.*\]$", re.I)
_STAMP_RE = re.compile(r"^\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_ID_TAG_RE = re.compile(
    r"^\[(ti|ar|al|by|offset|length|re|ve|tool|au|la|encoder|instrumental):.*\]$",
    re.I,
)
_BG_RE = re.compile(r"^\[bg:.*\]$", re.I)
_ANY_STAMP_RE = re.compile(r"\[\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?\]")
_WORD_STAMP_RE = re.compile(r"<\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?>")
_VOICE_RE = re.compile(r"^\s*v[1-9]\d*:\s*", re.I)


@dataclass(frozen=True)
class SupplementTag:
    """One tag: ``kind`` is romaji, romaji-words, translation or
    translation-words whichever way the file spells it; ``text`` is None for
    word timing and for a value that cannot be read."""

    kind: str
    key: int
    text: str | None


def read_tag(row: str) -> SupplementTag | None:
    """The translation or pronunciation tag `row` holds, or None."""
    m = _ROW_RE.match(row.strip())
    if m is None:
        return None
    written = m.group(1).lower()
    legacy = written not in _KIND_BY_TAG
    kind = _KIND_BY_TAG.get(written, written)
    key = int(m.group(2))
    value = m.group(3)
    if kind.endswith("-words"):
        return SupplementTag(kind, key, None)
    if not legacy:
        return SupplementTag(kind, key, unescape(value).strip())
    try:
        text = base64.b64decode(value, validate=True).decode("utf-8").strip()
    except (binascii.Error, ValueError):
        text = None  # damaged optional metadata must not hide the lyrics
    return SupplementTag(kind, key, text)


def escape(text: str) -> str:
    """`text` as a tag value."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.replace("\\", "\\\\").replace("\n", "\\n")


def unescape(value: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(value):
        char = value[i]
        if char == "\\" and i + 1 < len(value):
            following = value[i + 1]
            if following == "n":
                out.append("\n")
            elif following in "\\<":
                out.append(following)
            else:
                out.append("\\" + following)
            i += 2
        else:
            out.append(char)
            i += 1
    return "".join(out)


def tag_row(kind: str, key: int, text: str) -> str:
    """The row that writes `text` as a `kind` tag under `key`."""
    return f"[x-{_TAG_BY_KIND.get(kind, kind)}:{key}:{escape(text)}]"


def strip_tags(lyrics: str) -> str:
    """`lyrics` without the ``x-`` rows, as a plain LRC reader expects."""
    return "\n".join(
        line for line in lyrics.splitlines() if not X_TAG_ROW_RE.match(line.strip())
    )


def _stamp_ms(m: re.Match[str]) -> int:
    fraction = m.group(3) or "0"
    scale = {1: 100, 2: 10}.get(len(fraction), 1)
    return (int(m.group(1)) * 60 + int(m.group(2))) * 1000 + int(fraction) * scale


def _lyric_text(line: str) -> str:
    body = _ANY_STAMP_RE.sub("", line)
    body = _VOICE_RE.sub("", _WORD_STAMP_RE.sub("", body))
    return re.sub(r"\s+", " ", body).strip()


def _is_lyric(line: str) -> bool:
    s = line.strip()
    return bool(s) and not (
        X_TAG_ROW_RE.match(s) or _ID_TAG_RE.match(s) or _BG_RE.match(s)
    )


def _is_translation_or_header(line: str) -> bool:
    s = line.strip().lower()
    if s.startswith("[x-mlrc:"):
        return True
    tag = read_tag(line)
    return tag is not None and tag.kind.startswith("translation")


def build_mlrc(original: str, translation: str) -> str:
    """The ``.mlrc`` text: `original` with each line's translation tagged
    under it.

    `translation` has `original`'s shape, line for line (what
    lyrics_translate.join_lyrics gives back, or the same text edited by
    hand); its timestamps are ignored. Translation tags `original` already
    carries are replaced, other tags kept. A line whose translation is
    empty or the same as the line gets none. ValueError when the two do not
    have the same number of lines, or nothing is translated.
    """
    orig_lines = original.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kept = [line for line in orig_lines if not _is_translation_or_header(line)]
    trans_lines = translation.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lyric_rows = [i for i, line in enumerate(kept) if _is_lyric(line)]
    trans_rows = [line for line in trans_lines if _is_lyric(line)]
    if len(lyric_rows) != len(trans_rows):
        raise ValueError("The translation does not have the same lines as the lyrics.")
    synced = any(_STAMP_RE.match(kept[i].strip()) for i in lyric_rows)
    tags: dict[int, str] = {}
    pairs = zip(lyric_rows, trans_rows, strict=True)
    for index, (row, translated) in enumerate(pairs):
        text = _lyric_text(translated)
        if not text or text == _lyric_text(kept[row]):
            continue
        if synced:
            stamp = _STAMP_RE.match(kept[row].strip())
            if stamp is None:
                continue  # an untimed line in synced lyrics has no key
            key = _stamp_ms(stamp)
        else:
            key = index
        tags[row] = tag_row("translation", key, text)
    if not tags:
        raise ValueError("There is no translation to save.")
    out = [MLRC_HEADER]
    for i, line in enumerate(kept):
        out.append(line)
        if i in tags:
            out.append(tags[i])
    while len(out) > 1 and not out[-1].strip():
        out.pop()
    return "\n".join(out) + "\n"


def sidecar_lyrics(audio: str | Path) -> str:
    """The lyrics in the sidecar next to `audio`: the ``.mlrc`` when there
    is one (it holds the translations too), else the ``.lrc``; "" if none."""
    base = Path(audio)
    for suffix in (MLRC_EXTENSION, ".lrc"):
        candidate = base.with_suffix(suffix)
        try:
            size = candidate.stat().st_size if candidate.is_file() else 0
            if 0 < size <= MAX_SIDECAR_BYTES:
                return candidate.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
    return ""
