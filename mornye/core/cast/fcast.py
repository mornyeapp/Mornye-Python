"""FCast receivers (Mornye mobile fcast.rs).

FCast is a TCP protocol: each packet is a little-endian u32 size, an opcode
byte and a JSON body. The sender says its version, reads the receiver's,
introduces itself (v3: INITIAL) and subscribes to the end-of-item event;
then PLAY carries the URL and metadata, and PAUSE / RESUME / STOP / SEEK /
SET_VOLUME drive it. The receiver sends PLAYBACK_UPDATE (state, time,
duration), VOLUME_UPDATE, PLAYBACK_ERROR and EVENT, and PING to keep the
connection alive. Receivers announce themselves over mDNS as
``_fcast._tcp``.
"""

from __future__ import annotations

import json
import logging
import socket
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import status as st
from .status import Device, Status

logger = logging.getLogger(__name__)

VERSION = 3
MAX_PACKET = 32_000
MAX_READ = 64 * 1024
MAX_TEXT = 512
SERVICE_TYPE = "_fcast._tcp.local."

PLAY, PAUSE, RESUME, STOP, SEEK = 1, 2, 3, 4, 5
PLAYBACK_UPDATE, VOLUME_UPDATE, SET_VOLUME, PLAYBACK_ERROR = 6, 7, 8, 9
VERSION_OP, PING, PONG, INITIAL, PLAY_UPDATE = 11, 12, 13, 14, 15
SUBSCRIBE_EVENT, EVENT = 17, 19

STATE_IDLE, STATE_PLAYING, STATE_PAUSED = 0, 1, 2
MEDIA_ITEM_END = 1


def encode(opcode: int, body: dict | None = None) -> bytes:
    """One packet. ValueError when it would be larger than receivers take."""
    data = json.dumps(body, separators=(",", ":")).encode() if body is not None else b""
    size = 1 + len(data)
    if size > MAX_PACKET:
        raise ValueError("packet too large")
    return struct.pack("<I", size) + bytes([opcode]) + data


def decode(buffer: bytearray) -> list[tuple[int, dict]]:
    """The whole packets at the start of `buffer`, taken out of it."""
    out = []
    while len(buffer) >= 4:
        (size,) = struct.unpack("<I", bytes(buffer[:4]))
        if size == 0 or size > MAX_READ:
            raise ValueError("bad packet size")
        if len(buffer) < 4 + size:
            break
        opcode = buffer[4]
        raw = bytes(buffer[5 : 4 + size])
        del buffer[: 4 + size]
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        out.append((opcode, body if isinstance(body, dict) else {}))
    return out


def device_from_service(name: str, host: str, port: int) -> Device | None:
    """A receiver from its mDNS instance name and address."""
    instance = (
        name.removesuffix("." + SERVICE_TYPE).removesuffix(SERVICE_TYPE).rstrip(".")
    )
    if not instance or not host:
        return None
    return Device(f"fcast:{instance}", instance, "FCast", host, port, "fcast")


def _cut(text: str) -> str:
    return (text or "")[:MAX_TEXT]


Emit = Callable[[dict], None]


@dataclass
class _Song:
    started: bool = False
    played_at: float | None = None
    playing_seen: bool = False
    asked_pause: bool = False
    asked_stop: bool = False
    done: bool = False
    start: float = 0.0
    time: float = 0.0


class FcastSession:
    CONNECT = 8.0
    VERSION_WAIT = 5.0
    PING_AFTER = 4.0
    SILENCE = 12.0
    START_LIMIT = 20.0

    def __init__(self, device: Device, status: Status, emit: Emit) -> None:
        self.device = device
        self.status = status
        self.emit = emit
        self.version = VERSION
        self.sock: socket.socket | None = None
        self.item: dict | None = None
        self.song = _Song()
        self.last_seen = time.monotonic()
        self.pinged: float | None = None
        self.volume: float | None = None
        self.lock = threading.RLock()
        self.write_lock = threading.Lock()
        self._closed = threading.Event()
        self._buffer = bytearray()

    # -- wire --

    def _send(self, opcode: int, body: dict | None = None) -> None:
        packet = encode(opcode, body)
        if self.sock is None:
            raise OSError("not connected")
        with self.write_lock:
            self.sock.sendall(packet)

    def _order(self, opcode: int, body: dict | None = None) -> None:
        try:
            self._send(opcode, body)
        except ValueError as exc:
            raise RuntimeError(
                "The song details are too long for this receiver"
            ) from exc
        except OSError as exc:
            self._closed.set()
            raise RuntimeError("Device is disconnected") from exc

    def _read(self, timeout: float) -> list[tuple[int, dict]]:
        assert self.sock is not None
        self.sock.settimeout(timeout)
        try:
            data = self.sock.recv(MAX_READ)
        except TimeoutError:
            return []
        if not data:
            raise ConnectionError("closed")
        self._buffer.extend(data)
        return decode(self._buffer)

    # -- connecting --

    def connect(self) -> None:
        name = self.device.name
        try:
            self.sock = socket.create_connection(
                (self.device.host, self.device.port), timeout=self.CONNECT
            )
        except OSError as exc:
            raise RuntimeError(f"Could not reach {name}") from exc
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            self._send(VERSION_OP, {"version": VERSION})
            theirs = None
            end = time.monotonic() + self.VERSION_WAIT
            while theirs is None and time.monotonic() < end:
                for opcode, body in self._read(max(0.1, end - time.monotonic())):
                    if opcode == VERSION_OP:
                        v = body.get("version")
                        theirs = (
                            int(v) if isinstance(v, (int, float)) and v > 0 else None
                        )
                    elif opcode == PING:
                        self._send(PONG)
                    elif opcode == VOLUME_UPDATE and isinstance(
                        body.get("volume"), (int, float)
                    ):
                        self.volume = float(body["volume"])
        except (OSError, ConnectionError, ValueError) as exc:
            self.sock.close()
            raise RuntimeError(f"Could not reach {name}") from exc
        if theirs is None:
            self.sock.close()
            raise RuntimeError(f"{name} does not speak a supported FCast version")
        self.version = min(theirs, VERSION)
        if self.version >= 3:
            self._send(
                INITIAL,
                {
                    "displayName": "Mornye",
                    "appName": "Mornye",
                    "appVersion": _app_version(),
                },
            )
            self._send(SUBSCRIBE_EVENT, {"event": {"type": MEDIA_ITEM_END}})
        self.status.start(self.device, self.volume if self.volume is not None else 1.0)
        self._emit()
        threading.Thread(
            target=self._run, name="mornye-cast-fcast", daemon=True
        ).start()

    def _emit(self) -> None:
        self.emit(self.status.snapshot())

    # -- following --

    def _run(self) -> None:
        tick = min(self.PING_AFTER, self.SILENCE) / 4
        try:
            while not self._closed.is_set():
                try:
                    packets = self._read(tick)
                except (OSError, ConnectionError, ValueError):
                    break
                for opcode, body in packets:
                    if not self.receive(opcode, body):
                        self._closed.set()
                        break
                if not self.keepalive():
                    break
                with self.status.lock:
                    playing = self.status.player_state == st.PLAYING
                if playing:
                    self._emit()
        finally:
            with self.status.lock:
                self.status.connected = False
            self._emit()
            if self.sock is not None:
                try:
                    self.sock.close()
                except OSError:
                    pass

    def keepalive(self) -> bool:
        silent = time.monotonic() - self.last_seen
        if silent > self.SILENCE:
            return False
        due = self.pinged is None or time.monotonic() - self.pinged >= self.PING_AFTER
        if self.version >= 2 and silent >= self.PING_AFTER and due:
            self.pinged = time.monotonic()
            try:
                self._send(PING)
            except OSError:
                return False
        s = self.song
        if (
            s.started
            and not s.playing_seen
            and not s.done
            and s.played_at is not None
            and time.monotonic() - s.played_at > self.START_LIMIT
        ):
            self._end_song(st.ERROR)
        return True

    def _end_song(self, reason: str) -> None:
        self.song.done = True
        with self.status.lock:
            self.status.player_state = st.IDLE
            self.status.idle_reason = reason
            if reason == st.FINISHED and self.status.duration > 0:
                self.status.report(self.status.duration)
        self._emit()

    def receive(self, opcode: int, body: dict) -> bool:
        """Handles one packet from the receiver; False ends the session."""
        self.last_seen = time.monotonic()
        self.pinged = None
        with self.lock:
            if opcode == PING:
                try:
                    self._send(PONG)
                except OSError:
                    return False
            elif opcode == VOLUME_UPDATE:
                volume = body.get("volume")
                if isinstance(volume, (int, float)):
                    self.status.volume = max(0.0, min(1.0, float(volume)))
                    self._emit()
            elif opcode == PLAYBACK_UPDATE:
                self.playback(body)
            elif opcode == PLAYBACK_ERROR:
                if self.song.started and not self.song.done:
                    self._end_song(st.ERROR)
            elif opcode == EVENT:
                event = body.get("event") or {}
                url = (
                    ((event.get("item") or {}).get("url"))
                    if isinstance(event, dict)
                    else None
                )
                ours = url is None or (
                    self.item is not None and self.item["url"] == url
                )
                if (
                    isinstance(event, dict)
                    and event.get("type") == MEDIA_ITEM_END
                    and ours
                    and self.song.playing_seen
                    and not self.song.done
                ):
                    self._end_song(st.FINISHED)
            elif opcode == PLAY_UPDATE:
                url = (body.get("playData") or {}).get("url")
                theirs = isinstance(url, str) and (
                    self.item is None or self.item["url"] != url
                )
                # Someone else started something on the receiver.
                if theirs and self.song.started and not self.song.done:
                    return False
        return True

    def playback(self, update: dict) -> None:
        song = self.song
        if not song.started or song.done:
            return
        status = self.status
        with status.lock:
            duration_in = update.get("duration")
            if isinstance(duration_in, (int, float)) and duration_in > 0:
                status.duration = float(duration_in)
            t = update.get("time")
            if isinstance(t, (int, float)):
                song.time = max(0.0, float(t))
            duration, at = status.duration, song.time

            def near_end(slack: float) -> bool:
                return duration <= 0 or at >= duration - slack

            state = update.get("state")
            if state == STATE_PLAYING:
                song.playing_seen = True
                status.player_state = st.PLAYING
                status.report(song.time)
            elif state == STATE_PAUSED:
                if (
                    song.playing_seen
                    and not song.asked_pause
                    and duration > 0
                    and near_end(1.5)
                ):
                    self._end_song(st.FINISHED)
                    return
                status.player_state = st.PAUSED
                status.report(song.time)
            elif state == STATE_IDLE:
                if song.playing_seen and not song.asked_stop:
                    if near_end(max(duration * 0.1, 10.0)):
                        self._end_song(st.FINISHED)
                        return
                    song.asked_stop = True
                    status.player_state = st.IDLE
                elif song.asked_stop:
                    status.player_state = st.IDLE
                else:
                    status.player_state = st.BUFFERING
        self._emit()

    # -- commands --

    def _loaded(self) -> dict:
        if self.item is None or self.song.done:
            raise RuntimeError("Nothing is loaded on the device")
        return self.item

    def play_message(self, item: dict) -> dict:
        play: dict[str, Any] = {
            "container": item["mime"],
            "url": item["url"],
            "time": self.song.start,
        }
        if self.version >= 3:
            metadata: dict[str, Any] = {"type": 0, "title": _cut(item.get("title", ""))}
            if item.get("artwork"):
                metadata["thumbnailUrl"] = item["artwork"]
            metadata["custom"] = {
                "artist": _cut(item.get("artist", "")),
                "album": _cut(item.get("album", "")),
            }
            play["metadata"] = metadata
        return play

    def load(self, item: dict) -> None:
        with self.lock:
            if self.song.started and not self.song.done:
                self._order(STOP)
            self.status.begin_item(item["token"], (item.get("duration") or 0) / 1000)
            self.item = item
            self.song = _Song()
        self._emit()

    def play(self) -> None:
        with self.lock:
            item = self._loaded()
            if self.song.started and not self.song.asked_stop:
                self._order(RESUME)
            else:
                self._order(PLAY, self.play_message(item))
                self.song.started = True
                self.song.playing_seen = False
                self.song.played_at = time.monotonic()
                self.status.player_state = st.BUFFERING
            self.song.asked_pause = False
            self.song.asked_stop = False
        self._emit()

    def pause(self) -> None:
        with self.lock:
            self._loaded()
            if self.song.started:
                self._order(PAUSE)
                self.song.asked_pause = True
            self.status.player_state = st.PAUSED
        self._emit()

    def stop(self) -> None:
        with self.lock:
            if self.song.started and not self.song.done:
                self._order(STOP)
                self.song.asked_stop = True
                self.song.start = 0.0
                self.status.player_state = st.IDLE
        self._emit()

    def seek(self, value: float) -> None:
        with self.lock:
            self._loaded()
            value = max(0.0, float(value))
            if self.song.started:
                self._order(SEEK, {"time": value})
            else:
                self.song.start = value
            self.song.time = value
            self.status.report(value)
        self._emit()

    def set_volume(self, level: float) -> None:
        level = max(0.0, min(1.0, float(level)))
        self._order(SET_VOLUME, {"volume": level})
        self.status.volume = level
        self._emit()

    def disconnect(self) -> None:
        if self.song.started and not self.song.done:
            try:
                self._send(STOP)
            except OSError:
                pass
        self._closed.set()
        if self.sock is not None:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _app_version() -> str:
    try:
        from ... import __version__

        return str(__version__)
    except Exception:
        return ""
