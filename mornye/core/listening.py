"""core/listening.py — what the Player has played, day by day.

Mornye mobile keeps a listening record: one row per song per day with the
time heard and the plays counted. Home (core/personal_home.py) and the
listener profile (core/listener_profile.py) are read from it. This is the
same record for the desktop and `--web` Player.

What counts
-----------
The page measures elapsed playing time, never a jump of the seek bar, and
sends it here in pieces (`record`). A play is counted once per session of a
song, after 30 seconds of it; the page says so with ``play: True`` on the
piece that crossed the line. When a song stops being the current one the
page also says how it ended: ``listened`` (played to the end or past half
of it) or ``skipped``. Those outcomes, with their hour, are what the
profile's time of day and play-through rate read (`sessions`).

Songs are known by their title and artist (`track_key`), as on mobile, so
a song moved to another folder keeps its history.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from datetime import date, datetime
from typing import Any

from . import db

logger = logging.getLogger(__name__)

#: Seconds of one song that count it as played.
PLAY_SECONDS = 30

#: The most one piece may add, so a page left playing with a stuck clock
#: cannot invent a day of listening.
MAX_PIECE_MS = 6 * 3600 * 1000

#: How many played-through or skipped songs the record keeps for the profile.
SESSION_LIMIT = 2000

OUTCOMES = ("listened", "skipped")

_QUALITY_RE = re.compile(r"(\d{1,2})-bit\s*/\s*([\d.]+)\s*kHz", re.IGNORECASE)
_RATE_RE = re.compile(r"([\d.]+)\s*kHz", re.IGNORECASE)


def normalize(text: Any) -> str:
    """Lower case, accents and punctuation folded, spaces collapsed."""
    value = unicodedata.normalize("NFKD", str(text or ""))
    value = "".join(c for c in value if not unicodedata.combining(c)).lower()
    value = re.sub(r"[^\w]+", " ", value)
    return " ".join(value.split())


def track_key(title: Any, artist: Any) -> str:
    """How the record knows a song: its title and first artist, folded.
    Empty without a title."""
    name = normalize(title)
    if not name:
        return ""
    first = re.split(r"\s*[;,/&]\s*|\s+feat\.?\s+|\s+ft\.?\s+", str(artist or ""))[0]
    return f"{name}|{normalize(first)}"


def parse_quality(label: Any) -> tuple[int, int]:
    """(bit depth, sample rate in Hz) from the Player's quality label, e.g.
    ``FLAC 24-bit / 96 kHz``; 0 for what it does not say."""
    text = str(label or "")
    m = _QUALITY_RE.search(text)
    if m:
        return int(m.group(1)), int(round(float(m.group(2)) * 1000))
    m = _RATE_RE.search(text)
    if m:
        return 0, int(round(float(m.group(1)) * 1000))
    return 0, 0


def day_of(at: float) -> str:
    return date.fromtimestamp(at).isoformat()


def _int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _library_row(path: str) -> dict:
    """The Player library's row of `path` ({} when unknown), for the genre,
    year and format the page did not send."""
    if not path:
        return {}
    try:
        row = (
            db.connection()
            .execute("SELECT row FROM player_library WHERE path = ?", (path,))
            .fetchone()
        )
    except Exception:
        return {}
    data = db.loads(row["row"], {}) if row is not None else {}
    return data if isinstance(data, dict) else {}


def record(owner: str, entry: dict, *, at: float | None = None) -> bool:
    """Adds one piece of listening. False when there is nothing to add or
    the song has no title. Never raises."""
    if not isinstance(entry, dict):
        return False
    title = str(entry.get("title") or "").strip()
    artist = str(entry.get("artist") or "").strip()
    key = track_key(title, artist)
    if not key:
        return False
    ms = max(0, min(MAX_PIECE_MS, _int(entry.get("ms"))))
    play = 1 if entry.get("play") else 0
    outcome = str(entry.get("outcome") or "")
    if outcome not in OUTCOMES:
        outcome = ""
    if not ms and not play and not outcome:
        return False
    when = float(at if at is not None else time.time())
    path = str(entry.get("path") or "")
    lib = _library_row(path)
    depth, rate = parse_quality(entry.get("quality"))
    album = str(entry.get("album") or lib.get("album") or "")
    genre = str(entry.get("genre") or lib.get("genre") or "")
    year = _int(str(entry.get("year") or lib.get("year") or "")[:4])
    fmt = str(entry.get("format") or lib.get("format") or "")
    if not fmt and path and "." in path:
        fmt = path.rsplit(".", 1)[-1]
    fmt = fmt.upper()[:8]
    try:
        with db.transaction() as conn:
            if ms or play:
                conn.execute(
                    """
                    INSERT INTO listening (owner, day, track_key, path, title,
                        artist, album, genre, year, format, bit_depth,
                        sample_rate, milliseconds, plays)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(owner, day, track_key) DO UPDATE SET
                        path = excluded.path, title = excluded.title,
                        artist = excluded.artist, album = excluded.album,
                        genre = CASE WHEN excluded.genre != '' THEN excluded.genre ELSE genre END,
                        year = CASE WHEN excluded.year > 0 THEN excluded.year ELSE year END,
                        format = CASE WHEN excluded.format != '' THEN excluded.format ELSE format END,
                        bit_depth = MAX(bit_depth, excluded.bit_depth),
                        sample_rate = MAX(sample_rate, excluded.sample_rate),
                        milliseconds = milliseconds + excluded.milliseconds,
                        plays = plays + excluded.plays
                    """,
                    (
                        owner or "",
                        day_of(when),
                        key,
                        path,
                        title,
                        artist,
                        album,
                        genre,
                        year,
                        fmt,
                        depth,
                        rate,
                        ms,
                        play,
                    ),
                )
            if outcome:
                conn.execute(
                    "INSERT INTO listening_sessions (owner, at, track_key, outcome) "
                    "VALUES (?, ?, ?, ?)",
                    (owner or "", when, key, outcome),
                )
                conn.execute(
                    """
                    DELETE FROM listening_sessions WHERE owner = ? AND at < (
                        SELECT at FROM listening_sessions WHERE owner = ?
                        ORDER BY at DESC LIMIT 1 OFFSET ?)
                    """,
                    (owner or "", owner or "", SESSION_LIMIT - 1),
                )
        return True
    except Exception:
        logger.debug("[listening] could not record", exc_info=True)
        return False


def rows(owner: str, since: str | None = None, until: str | None = None) -> list[dict]:
    """The daily rows of `owner`, `since` (a day, inclusive) to `until` (a
    day, exclusive), oldest first. [] on error."""
    sql = ["SELECT * FROM listening WHERE owner = ?"]
    params: list = [owner or ""]
    if since:
        sql.append("AND day >= ?")
        params.append(since)
    if until:
        sql.append("AND day < ?")
        params.append(until)
    sql.append("ORDER BY day ASC")
    try:
        return [dict(r) for r in db.connection().execute(" ".join(sql), params)]
    except Exception:
        logger.debug("[listening] could not read", exc_info=True)
        return []


def sessions(owner: str) -> list[dict]:
    """The played-through and skipped songs kept, oldest first, each with
    its local `hour`."""
    try:
        found = db.connection().execute(
            "SELECT at, track_key, outcome FROM listening_sessions "
            "WHERE owner = ? ORDER BY at ASC",
            (owner or "",),
        )
        return [
            {**dict(r), "hour": datetime.fromtimestamp(float(r["at"])).hour}
            for r in found
        ]
    except Exception:
        return []


def clear(owner: str) -> None:
    try:
        with db.transaction() as conn:
            conn.execute("DELETE FROM listening WHERE owner = ?", (owner or "",))
            conn.execute(
                "DELETE FROM listening_sessions WHERE owner = ?", (owner or "",)
            )
    except Exception:
        logger.debug("[listening] could not clear", exc_info=True)


def totals(day_rows: list[dict]) -> dict[str, dict]:
    """Per song over `day_rows`: its latest details with the summed
    `milliseconds` and `plays`, by track key."""
    out: dict[str, dict] = {}
    for r in day_rows:
        key = r.get("track_key") or ""
        known = out.get(key)
        if known is None:
            out[key] = {**r}
        else:
            ms, plays = known["milliseconds"], known["plays"]
            known.update({k: v for k, v in r.items() if v not in ("", 0, None)})
            known["milliseconds"] = ms + int(r.get("milliseconds") or 0)
            known["plays"] = plays + int(r.get("plays") or 0)
    return out
