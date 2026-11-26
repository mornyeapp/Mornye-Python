"""core/connect/model.py — the shapes Mornye Connect passes around.

Connect speaks the Link protocol of the Mornye app (mornyeapp/Mornye,
``lib/services/link/link_protocol.dart``) everywhere, not only to phones: a
player's state is a ``LinkPlayerState``, a song another device has to find is
a ``TrackRef``, and a remote's request is a ``command`` with an action and its
arguments. The desktop window, a browser on ``--web``, the server speaker and
a paired phone then all describe themselves the same way, and a state a page
reports can go to a phone unchanged.

Field names are the wire names (camelCase) on purpose: ``to_json`` and
``from_json`` are the protocol.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, replace
from typing import Any

#: At most this many queue entries travel in a state: a window around the
#: current one (``linkQueueWindow``).
MAX_QUEUE = 200

#: Two durations name the same recording when they differ by at most this; a
#: position this far from where playback alone would have taken it counts as
#: a change worth sending (``linkDurationToleranceMs``).
DURATION_TOLERANCE_MS = 2000

REPEAT_MODES = ("none", "one", "all")

_FILE_ID = re.compile(r"^[0-9a-f]{32}$")


def now_ms() -> int:
    """Wall-clock milliseconds: the clock ``updatedMs`` is read on. A phone
    estimates its offset from this computer's clock with it, so it is never a
    monotonic clock."""
    return int(time.time() * 1000)


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


# ── Matching keys (link_catalog_index.dart, history_database.dart) ──────────


def normalize_isrc(value: str | None) -> str:
    return re.sub(r"[-\s]", "", (value or "").strip().upper())


def match_key(title: str | None, artist: str | None) -> str:
    """``title|artist``, trimmed and lower-cased; '' for an empty title."""
    track = (title or "").strip().lower()
    if not track:
        return ""
    return f"{track}|{(artist or '').strip().lower()}"


_BRACKETED = re.compile(r"[\(\[][^\)\]]*[\)\]]")
# Dart's [^\p{L}\p{N}]: \W leaves letters, digits and "_" in, so "_" goes too.
_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)
_ARTIST_SEPARATOR = re.compile(
    r"\s*(?:,|;|/|&|\sfeat\.?\s|\sft\.?\s|\sfeaturing\s|\swith\s)\s*", re.IGNORECASE
)


def loose_key(title: str | None, artist: str | None) -> str:
    """A looser key for the last resolution step: case, punctuation,
    bracketed suffixes ("(Remastered)") and featured artists do not count."""

    def compact(value: str) -> str:
        return _NON_WORD.sub("", value.lower())

    title = title or ""
    name = compact(_BRACKETED.sub(" ", title)) or compact(title)
    if not name:
        return ""
    first = next((p for p in _ARTIST_SEPARATOR.split(artist or "") if p.strip()), "")
    return f"{name}|{compact(first)}"


def is_file_id(value: str | None) -> bool:
    return bool(value and _FILE_ID.match(value))


# ── TrackRef ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TrackRef:
    """A song as another device resolves it: by catalog id when ``serverId``
    is the one it knows, else by ISRC, match key, then title and artist with
    the duration within two seconds. ``fileId`` names a copy a phone
    uploaded for a song the library lacks."""

    title: str
    artist: str = ""
    album: str | None = None
    durationMs: int | None = None
    isrc: str | None = None
    matchKey: str | None = None
    songId: str | None = None
    serverId: str | None = None
    fileId: str | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"title": self.title, "artist": self.artist}
        for name in (
            "album",
            "durationMs",
            "isrc",
            "matchKey",
            "songId",
            "serverId",
            "fileId",
        ):
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        return out

    @staticmethod
    def from_json(raw: Any) -> TrackRef | None:
        if not isinstance(raw, dict):
            return None
        title = _text(raw.get("title"))
        if title is None:
            return None
        file_id = _text(raw.get("fileId"))
        return TrackRef(
            title=title,
            artist=_text(raw.get("artist")) or "",
            album=_text(raw.get("album")),
            durationMs=_int(raw.get("durationMs")),
            isrc=_text(raw.get("isrc")),
            matchKey=_text(raw.get("matchKey")),
            songId=_text(raw.get("songId")),
            serverId=_text(raw.get("serverId")),
            fileId=file_id if is_file_id(file_id) else None,
        )


# ── PlayerState ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PlayerState:
    """One player as it is now (``LinkPlayerState``).

    ``queue`` is a window of at most ``MAX_QUEUE`` entries around the current
    song and ``index`` points into it (-1 when the current song lies outside
    it). ``positionMs`` was read at ``updatedMs`` (wall clock)."""

    track: TrackRef | None = None
    playing: bool = False
    positionMs: int = 0
    durationMs: int | None = None
    updatedMs: int = 0
    queue: tuple[TrackRef, ...] = field(default_factory=tuple)
    index: int = -1
    shuffle: bool = False
    repeat: str = "none"
    volume: float = 1.0
    autoplay: bool | None = None
    autoMix: bool | None = None

    def position_at(self, at_ms: int) -> int:
        if not self.playing:
            return self.positionMs
        position = self.positionMs + (at_ms - self.updatedMs)
        if self.durationMs:
            position = min(position, self.durationMs)
        return max(0, position)

    def at(self, at_ms: int | None = None) -> PlayerState:
        """This state as read at ``at_ms``: the position playback alone
        explains, stamped with that time. A phone's ``GET state`` must see a
        fresh ``updatedMs``, never a cached one."""
        at_ms = now_ms() if at_ms is None else at_ms
        return replace(self, positionMs=self.position_at(at_ms), updatedMs=at_ms)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "playing": self.playing,
            "positionMs": int(self.positionMs),
            "updatedMs": int(self.updatedMs),
            "queue": [ref.to_json() for ref in self.queue],
            "index": self.index,
            "shuffle": self.shuffle,
            "repeat": self.repeat,
            "volume": round(min(1.0, max(0.0, self.volume)), 4),
        }
        if self.track is not None:
            out["track"] = self.track.to_json()
        if self.durationMs is not None:
            out["durationMs"] = int(self.durationMs)
        if self.autoplay is not None:
            out["autoplay"] = self.autoplay
        if self.autoMix is not None:
            out["autoMix"] = self.autoMix
        return out

    @staticmethod
    def from_json(raw: Any) -> PlayerState | None:
        if not isinstance(raw, dict):
            return None
        queue = tuple(
            ref
            for ref in (TrackRef.from_json(q) for q in raw.get("queue") or [])
            if ref
        )[:MAX_QUEUE]
        index = _int(raw.get("index"))
        if index is None or not -1 <= index < len(queue):
            index = -1
        repeat = raw.get("repeat")
        volume = raw.get("volume")
        return PlayerState(
            track=TrackRef.from_json(raw.get("track")),
            playing=raw.get("playing") is True,
            positionMs=max(0, _int(raw.get("positionMs")) or 0),
            durationMs=_int(raw.get("durationMs")),
            updatedMs=_int(raw.get("updatedMs")) or now_ms(),
            queue=queue,
            index=index,
            shuffle=raw.get("shuffle") is True,
            repeat=repeat if repeat in REPEAT_MODES else "none",
            volume=(
                float(volume)
                if isinstance(volume, (int, float)) and not isinstance(volume, bool)
                else 1.0
            ),
            autoplay=(
                raw.get("autoplay") if isinstance(raw.get("autoplay"), bool) else None
            ),
            autoMix=(
                raw.get("autoMix") if isinstance(raw.get("autoMix"), bool) else None
            ),
        )

    def handoff_args(
        self, *, at_ms: int | None = None, playing: bool | None = None
    ) -> dict:
        """The ``handoff/play`` arguments (also the ``play_queue`` command)
        that continue this state on another device (``linkHandoffArgs``)."""
        at_ms = now_ms() if at_ms is None else at_ms
        out: dict[str, Any] = {
            "queue": [ref.to_json() for ref in self.queue],
            "index": self.index,
            "positionMs": self.position_at(at_ms),
            "playing": self.playing if playing is None else playing,
            "shuffle": self.shuffle,
            "repeat": self.repeat,
        }
        if self.autoplay is not None:
            out["autoplay"] = self.autoplay
        if self.autoMix is not None:
            out["autoMix"] = self.autoMix
        return out


def state_changed(previous: PlayerState | None, nxt: PlayerState) -> bool:
    """Whether ``nxt`` is worth sending after ``previous``: anything but a
    position that playback alone explains (``linkPlayerStateChanged``)."""
    if previous is None:
        return True
    if (
        previous.playing != nxt.playing
        or previous.index != nxt.index
        or previous.shuffle != nxt.shuffle
        or previous.repeat != nxt.repeat
        or previous.autoplay != nxt.autoplay
        or previous.autoMix != nxt.autoMix
        or previous.durationMs != nxt.durationMs
        or abs(previous.volume - nxt.volume) > 0.005
        or previous.track != nxt.track
        or previous.queue != nxt.queue
    ):
        return True
    expected = previous.position_at(nxt.updatedMs)
    return abs(expected - nxt.positionMs) > DURATION_TOLERANCE_MS


def queue_window(length: int, index: int, maximum: int = MAX_QUEUE) -> tuple[int, int]:
    """``(start, end)`` of the part of a queue of ``length`` sent around
    ``index``: at most ``maximum`` entries, the current one in the middle
    where possible."""
    if length <= maximum:
        return 0, length
    start = min(max(max(index, 0) - maximum // 2, 0), length - maximum)
    return start, start + maximum


# ── Commands (link_protocol.dart:439-462, link_desktop_ops.dart:291-360) ─────


class CommandError(Exception):
    """A request the protocol refuses: ``code`` is one of the Link error
    codes (``invalid``, ``not_found``, ``unavailable``, ``timeout``)."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


SIMPLE_ACTIONS = frozenset({"play", "pause", "toggle", "next", "previous"})
ACTIONS = SIMPLE_ACTIONS | {
    "seek",
    "volume",
    "shuffle",
    "repeat",
    "autoplay",
    "auto_mix",
    "jump",
    "move",
    "remove",
    "play_queue",
    # Not a Link command: how ``queue/add`` (and a remote page) reaches the
    # device that plays. ``{queue: [TrackRef], next: bool}``.
    "queue_add",
}


def _index_arg(args: dict, name: str) -> int:
    value = args.get(name)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value != int(value)
    ):
        raise CommandError("invalid", f"{name} is required")
    return int(value)


def validate_command(
    action: Any, args: Any, state: PlayerState | None = None
) -> tuple[str, dict]:
    """The command normalised, or ``CommandError("invalid")``. With the
    player's ``state``, queue indexes are checked against the window it sent:
    out of range, or removing the current song, is refused."""
    if not isinstance(action, str) or action not in ACTIONS:
        raise CommandError("invalid", f"unknown action {action!r}")
    args = dict(args) if isinstance(args, dict) else {}
    out: dict[str, Any] = {}
    if action == "pause" and args.get("continueOnPhone") is True:
        out["continueOnPhone"] = True
    elif action == "seek":
        position = _int(args.get("positionMs"))
        if position is None:
            raise CommandError("invalid", "positionMs is required")
        out["positionMs"] = max(0, position)
    elif action == "volume":
        value = args.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CommandError("invalid", "value is required")
        out["value"] = min(1.0, max(0.0, float(value)))
    elif action in ("shuffle", "autoplay", "auto_mix"):
        if not isinstance(args.get("enabled"), bool):
            raise CommandError("invalid", "enabled is required")
        out["enabled"] = args["enabled"]
    elif action == "repeat":
        mode = args.get("mode")
        if mode not in REPEAT_MODES:
            raise CommandError("invalid", "mode must be none, one or all")
        out["mode"] = mode
    elif action in ("jump", "remove"):
        out["index"] = _index_arg(args, "index")
    elif action == "move":
        out["from"] = _index_arg(args, "from")
        out["to"] = _index_arg(args, "to")
    elif action == "play_queue":
        out = validate_handoff(args)
    elif action == "queue_add":
        if not isinstance(args.get("queue"), list) or not args["queue"]:
            raise CommandError("invalid", "queue is required")
        out = {"queue": args["queue"], "next": args.get("next") is True}

    if state is not None and action in ("jump", "remove", "move"):
        size = len(state.queue)
        for name in ("index", "from", "to"):
            if name in out and not 0 <= out[name] < size:
                raise CommandError("invalid", f"{name} is out of range")
        if action == "remove" and out["index"] == state.index:
            raise CommandError("invalid", "the current song cannot be removed")
    return action, out


def validate_handoff(args: Any) -> dict[str, Any]:
    """``handoff/play`` and ``play_queue`` arguments, normalised. Entries that
    are not a TrackRef stay as ``None`` so the indexes the sender knows
    still line up; resolving them reports them missing."""
    if not isinstance(args, dict) or not isinstance(args.get("queue"), list):
        raise CommandError("invalid", "queue is required")
    out: dict[str, Any] = {
        "queue": [TrackRef.from_json(q) for q in args["queue"]],
        "index": max(0, _int(args.get("index")) or 0),
        "positionMs": max(0, _int(args.get("positionMs")) or 0),
        "playing": args.get("playing") is not False,
    }
    if isinstance(args.get("shuffle"), bool):
        out["shuffle"] = args["shuffle"]
    if args.get("repeat") in REPEAT_MODES:
        out["repeat"] = args["repeat"]
    for name in ("autoplay", "autoMix", "check"):
        if isinstance(args.get(name), bool):
            out[name] = args[name]
    if isinstance(args.get("arriving"), list):
        out["arriving"] = [
            {"name": a["name"], "size": a["size"]}
            for a in args["arriving"]
            if isinstance(a, dict)
            and isinstance(a.get("name"), str)
            and isinstance(a.get("size"), int)
            and not isinstance(a.get("size"), bool)
        ]
    return out
