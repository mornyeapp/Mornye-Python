"""core/connect/headless.py — a Connect device with no page: the server itself.

The page owns the queue when a page plays. When the music comes out of the
machine running Mornye (the server speaker in ``--web``, a cast receiver the
server drives), there is no page to own it, so this player does: the queue
in play order, shuffle and repeat, moving on when a song ends, the next file
queued for a gapless change. Commands reach it through the hub as they reach
a page (it registers with its own ``sender``), and it reports its state the
same way.

The sound comes from an engine with the native player's surface
(``load(path, next_path)``, ``set_next(path)``, ``command(name, value)``,
``close()``) that sends back the native player's events (``play``,
``pause``, ``timeupdate``, ``loadedmetadata``, ``seeked``, ``advanced``,
``ended``, ``error``): ``NativePlayer`` itself (mpv), or the cast adapter.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from .hub import ConnectHub
from .model import CommandError, PlayerState, TrackRef, now_ms, queue_window

logger = logging.getLogger(__name__)


class Engine(Protocol):
    def load(self, path: str, next_path: str | None = None) -> None: ...
    def set_next(self, path: str | None) -> None: ...
    def command(self, name: str, value: Any = None) -> None: ...
    def close(self) -> None: ...


EngineFactory = Callable[[Callable[[dict], None]], Engine]
Resolve = Callable[[list[TrackRef | None]], list[dict | None]]
RefsFor = Callable[[list[dict]], list[TrackRef | None]]


def _start_index(slots: list[Any], index: int) -> int:
    """Where a handed-over queue starts: the requested entry if it resolved,
    else the next one that did, else the last one before it."""
    if 0 <= index < len(slots) and slots[index]:
        return index
    for i in range(index + 1, len(slots)):
        if slots[i]:
            return i
    for i in range(min(index, len(slots)) - 1, -1, -1):
        if slots[i]:
            return i
    return -1


class HeadlessPlayer:
    def __init__(
        self,
        device_id: str,
        name: str,
        kind: str,
        engine_factory: EngineFactory,
        *,
        resolve: Resolve,
        refs_for: RefsFor,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self.id = device_id
        self.name = name
        self.kind = kind
        self._engine_factory = engine_factory
        self._engine: Engine | None = None
        self._resolve = resolve
        self._refs_for = refs_for
        self._clock = clock
        self._rng = rng or random.Random()
        self._lock = threading.RLock()
        self._hubs: list[ConnectHub] = []
        self.items: list[dict] = []
        self.order: list[int] = []
        self.pos = -1
        self.shuffle = False
        self.repeat = "none"
        self.volume = 1.0
        self.playing = False
        self._time = 0.0  # seconds, as read at self._at
        self._at = clock()
        self.duration: float | None = None

    # ── hubs ──
    def attach(self, hub: ConnectHub) -> None:
        with self._lock:
            if hub in self._hubs:
                return
            self._hubs.append(hub)
        hub.register(self.id, self.name, self.kind, sender=self.handle)
        if self.current is not None:
            self._report()

    def detach(self, hub: ConnectHub) -> None:
        with self._lock:
            if hub not in self._hubs:
                return
            self._hubs.remove(hub)
        hub.unregister(self.id)

    @property
    def attached(self) -> bool:
        return bool(self._hubs)

    def close(self) -> None:
        for hub in list(self._hubs):
            self.detach(hub)
        with self._lock:
            engine, self._engine = self._engine, None
            self.playing = False
        if engine is not None:
            try:
                engine.close()
            except Exception:
                logger.debug("engine close failed", exc_info=True)

    # ── position ──
    def position(self) -> float:
        with self._lock:
            t = self._time + (self._clock() - self._at if self.playing else 0.0)
            if self.duration:
                t = min(t, self.duration)
            return max(0.0, t)

    def _settle(self, t: float) -> None:
        self._time = max(0.0, float(t))
        self._at = self._clock()

    # ── queue ──
    @property
    def current(self) -> dict | None:
        if 0 <= self.pos < len(self.order):
            return self.items[self.order[self.pos]]
        return None

    def _build_order(self, first: int | None) -> None:
        idx = list(range(len(self.items)))
        if self.shuffle:
            self._rng.shuffle(idx)
            if first is not None and first in idx:
                at = idx.index(first)
                idx[0], idx[at] = idx[at], idx[0]
        self.order = idx
        self.pos = idx.index(first) if first is not None and first in idx else 0

    def _upcoming(self) -> str | None:
        current = self.current
        if current is None:
            return None
        if self.repeat == "one":
            return current["path"]
        if self.pos + 1 < len(self.order):
            return self.items[self.order[self.pos + 1]]["path"]
        if self.repeat == "all" and not self.shuffle:
            return self.items[self.order[0]]["path"]
        return None

    def _engine_or_start(self) -> Engine:
        if self._engine is None:
            self._engine = self._engine_factory(self.on_event)
        return self._engine

    def _load(self, pos: int, start: float = 0.0, playing: bool = True) -> None:
        self.pos = pos
        item = self.current
        if item is None:
            return
        engine = self._engine_or_start()
        self.duration = float(item["duration_s"]) if item.get("duration_s") else None
        self._settle(start)
        self.playing = playing
        describe = getattr(engine, "describe", None)
        if describe is not None:  # a receiver shows the title, not the file
            describe(item)
        engine.load(item["path"], self._upcoming())
        engine.command("volume", self.volume)
        if start > 0:
            engine.command("seek", start)
        if not playing:
            engine.command("pause")

    def _advance(self, by_user: bool) -> bool:
        """Moves on one song; False at the end of the queue."""
        if self.pos + 1 < len(self.order):
            self._load(self.pos + 1)
            return True
        if self.repeat == "all" and self.order:
            if self.shuffle:
                self._build_order(None)
            self._load(0)
            return True
        if not by_user:
            self._settle(self.duration or self.position())
            self.playing = False
        return False

    def _seek(self, t: float) -> None:
        if self.current is None or self._engine is None:
            return
        self._settle(t)
        self._engine.command("seek", max(0.0, t))

    def _refresh_next(self) -> None:
        if self._engine is not None and self.current is not None:
            self._engine.set_next(self._upcoming())

    def _window_pos(self, index: Any) -> int:
        """A queue-window index (what the state sent) as a play-order position."""
        if not isinstance(index, int) or isinstance(index, bool):
            return -1
        start, end = queue_window(len(self.order), self.pos)
        p = start + index
        return p if start <= p < end else -1

    # ── commands (the hub's sender) ──
    def handle(self, action: str, args: dict[str, Any]) -> None:
        with self._lock:
            self._handle(action, args)
        self._report()

    def _handle(self, action: str, args: dict[str, Any]) -> None:
        if action == "play_queue":
            self._play_queue(args)
        elif action == "queue_add":
            refs = [TrackRef.from_json(q) for q in args.get("queue") or []]
            added = [s for s in self._resolve(refs) if s]
            if not added:
                return
            if self.current is None:
                self.items, self.order = added, list(range(len(added)))
                self._load(0)
                return
            start = len(self.items)
            self.items.extend(added)
            new = list(range(start, start + len(added)))
            if args.get("next"):
                self.order[self.pos + 1 : self.pos + 1] = new
            else:
                self.order.extend(new)
            self._refresh_next()
        elif action in ("play", "pause", "toggle"):
            want = {"play": True, "pause": False}.get(action, not self.playing)
            if self.current is None or want == self.playing:
                return
            if want and self.duration and self.position() >= self.duration - 0.25:
                self._load(self.pos)  # it had ended: from the top
                return
            if self._engine is not None:
                self._settle(self.position())
                self.playing = want
                self._engine.command("play" if want else "pause")
        elif action == "next":
            self._advance(by_user=True)
        elif action == "previous":
            if self.position() > 3 or self.pos <= 0:
                self._seek(0)
            else:
                self._load(self.pos - 1)
        elif action == "seek":
            self._seek(float(args.get("positionMs", 0)) / 1000)
        elif action == "volume":
            self.volume = float(args.get("value", 1.0))
            if self._engine is not None:
                self._engine.command("volume", self.volume)
        elif action == "shuffle":
            self.shuffle = bool(args.get("enabled"))
            if self.items:
                self._build_order(self.order[self.pos] if self.pos >= 0 else None)
                self._refresh_next()
        elif action == "repeat":
            self.repeat = args.get("mode", "none")
            self._refresh_next()
        elif action == "jump":
            p = self._window_pos(args.get("index"))
            if p >= 0:
                self._load(p)
        elif action in ("move", "remove"):
            cur = self.order[self.pos] if self.pos >= 0 else None
            if action == "move":
                a, b = (
                    self._window_pos(args.get("from")),
                    self._window_pos(args.get("to")),
                )
                if a < 0 or b < 0 or a == b:
                    return
                self.order.insert(b, self.order.pop(a))
            else:
                p = self._window_pos(args.get("index"))
                if p < 0 or p == self.pos:
                    return
                self.order.pop(p)
            if cur is not None:
                self.pos = self.order.index(cur)
            self._refresh_next()

    def _play_queue(self, args: dict[str, Any]) -> None:
        refs = [TrackRef.from_json(q) for q in args.get("queue") or []]
        slots = self._resolve(refs)
        wanted = max(0, int(args.get("index") or 0))
        start = _start_index(slots, wanted)
        if start < 0:
            raise CommandError("invalid", "None of those songs is in this library")
        self.items = [s for s in slots if s]
        if isinstance(args.get("shuffle"), bool):
            self.shuffle = args["shuffle"]
        if args.get("repeat") in ("none", "one", "all"):
            self.repeat = args["repeat"]
        # The queue arrives in play order: kept as sent.
        self.order = list(range(len(self.items)))
        first = sum(1 for s in slots[:start] if s)
        position = float(args.get("positionMs") or 0) / 1000 if start == wanted else 0.0
        self._load(first, position, args.get("playing") is not False)

    # ── engine events ──
    def on_event(self, event: dict) -> None:
        kind = event.get("type")
        report = True
        with self._lock:
            if kind == "timeupdate":
                self._settle(float(event.get("time") or 0))
                report = False
            elif kind in ("play", "pause"):
                self.playing = kind == "play"
                if "time" in event:
                    self._settle(float(event.get("time") or 0))
            elif kind == "seeked":
                self._settle(float(event.get("time") or self.position()))
            elif kind == "loadedmetadata":
                if event.get("duration"):
                    self.duration = float(event["duration"])
            elif kind == "advanced":
                # The engine went on to the queued file by itself (gapless).
                if self.repeat != "one":
                    self.pos = self.pos + 1 if self.pos + 1 < len(self.order) else 0
                self._settle(0)
                item = self.current
                self.duration = (
                    float(item["duration_s"])
                    if item and item.get("duration_s")
                    else None
                )
                self._refresh_next()
            elif kind == "ended":
                if self.repeat == "one" and self.current is not None:
                    self._load(self.pos)
                else:
                    self._advance(by_user=False)
            elif kind == "error":
                logger.warning("%s: %s", self.name, event.get("message"))
            else:
                report = False
        if report:
            self._report()

    # ── state ──
    def state(self) -> tuple[PlayerState, list[str]]:
        with self._lock:
            ordered = [self.items[i] for i in self.order]
            start, end = queue_window(len(ordered), self.pos)
            window = ordered[start:end]
            refs = self._refs_for(window)
            keep = [i for i, r in enumerate(refs) if r is not None]
            queue = tuple(r for r in refs if r is not None)
            local = self.pos - start if self.pos >= 0 else -1
            index = keep.index(local) if local in keep else -1
            state = PlayerState(
                track=queue[index] if index >= 0 else None,
                playing=self.playing,
                positionMs=int(self.position() * 1000),
                durationMs=int(self.duration * 1000) if self.duration else None,
                updatedMs=now_ms(),
                queue=queue,
                index=index,
                shuffle=self.shuffle,
                repeat=self.repeat,
                volume=self.volume,
            )
            return state, [str(window[i].get("path") or "") for i in keep]

    def _report(self) -> None:
        if not self._hubs:
            return
        state, paths = self.state()
        for hub in list(self._hubs):
            try:
                hub.report_state(self.id, state, {"paths": paths})
            except CommandError:
                pass
