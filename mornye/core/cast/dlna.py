"""DLNA/UPnP MediaRenderer control (Mornye mobile dlna.rs, dlna/*.rs).

A renderer is driven over SOAP: AVTransport loads (SetAVTransportURI with a
DIDL-Lite description), plays, pauses, stops and seeks; RenderingControl
sets the volume (scaled to the maximum its SCPD declares);
ConnectionManager's GetProtocolInfo says which MIME spelling it accepts.
Renderers send no events this app listens to, so a thread polls
GetTransportInfo / GetPositionInfo every second while something plays
(every third second when idle) and works out from the transport state when
a song has finished, failed or been stopped on the device.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit
from xml.sax.saxutils import escape

import httpx

from ..media_server import dlna_features
from ..safe_xml import fromstring
from . import status as st
from .status import Device, Status

logger = logging.getLogger(__name__)

INSTANCE = ("InstanceID", "0")
MAX_ANSWER = 256 * 1024
USER_AGENT = "UPnP/1.0 DLNADOC/1.50 Mornye/1.0"


# ── Description ─────────────────────────────────────────────────────────────


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el: Any, name: str) -> Any:
    for c in list(el):
        if _local(c.tag) == name:
            return c
    return None


def _text(el: Any, name: str) -> str:
    c = _child(el, name)
    return (c.text or "").strip() if c is not None and c.text else ""


@dataclass
class Service:
    type: str
    control: str
    scpd: str = ""


@dataclass
class Renderer:
    udn: str
    name: str
    model: str
    transport: Service
    rendering: Service | None = None
    connection: Service | None = None


def _same_host(url: str, location: str) -> bool:
    return urlsplit(url).hostname == urlsplit(location).hostname


def _service(device: Any, kind: str, base: str, location: str) -> Service | None:
    services = _child(device, "serviceList")
    if services is None:
        return None
    wanted = f":service:{kind.lower()}:"
    for entry in list(services):
        if (
            _local(entry.tag) != "service"
            or wanted not in _text(entry, "serviceType").lower()
        ):
            continue

        def resolve(name: str, entry: Any = entry) -> str:
            path = _text(entry, name)
            if not path:
                return ""
            url = urljoin(base, path)
            return (
                url if url.startswith("http://") and _same_host(url, location) else ""
            )

        control = resolve("controlURL")
        if not control:
            return None
        return Service(_text(entry, "serviceType"), control, resolve("SCPDURL"))
    return None


def parse_description(
    text: str, location: str, udn: str | None = None
) -> Renderer | None:
    """The renderer in a device description: the device with that UDN, else
    the first MediaRenderer, else the first device with an AVTransport."""
    try:
        root = fromstring(text)
    except Exception:
        return None
    base = location
    for el in root.iter():
        if _local(el.tag) == "URLBase" and el.text:
            if _same_host(el.text.strip(), location):
                base = el.text.strip()
            break
    devices = [el for el in root.iter() if _local(el.tag) == "device"]
    candidates = [d for d in devices if _service(d, "AVTransport", base, location)]
    chosen = next(
        (d for d in candidates if udn and _text(d, "UDN").lower() == udn.lower()), None
    )
    if chosen is None:
        chosen = next(
            (
                d
                for d in candidates
                if ":device:mediarenderer:" in _text(d, "deviceType").lower()
            ),
            None,
        )
    if chosen is None and candidates:
        chosen = candidates[0]
    if chosen is None:
        return None
    name = _text(chosen, "friendlyName") or _text(chosen, "modelName") or "DLNA device"
    model = _text(chosen, "modelName") or _text(chosen, "manufacturer")
    transport = _service(chosen, "AVTransport", base, location)
    assert transport is not None
    return Renderer(
        _text(chosen, "UDN"),
        name,
        model,
        transport,
        _service(chosen, "RenderingControl", base, location),
        _service(chosen, "ConnectionManager", base, location),
    )


def volume_maximum(scpd: str) -> int | None:
    try:
        root = fromstring(scpd)
    except Exception:
        return None
    for var in root.iter():
        if _local(var.tag) != "stateVariable" or _text(var, "name").lower() != "volume":
            continue
        rng = _child(var, "allowedValueRange")
        if rng is None:
            return None
        try:
            top = int(_text(rng, "maximum"))
        except ValueError:
            return None
        return top if top > 0 else None
    return None


# ── SOAP ────────────────────────────────────────────────────────────────────


class Fault(Exception):
    def __init__(self, code: int, description: str = "") -> None:
        super().__init__(f"UPnP error {code}: {description}")
        self.code = code


class Unreachable(Exception):
    pass


def envelope(service: str, action: str, arguments: list[tuple[str, str]]) -> str:
    body = "".join(f"<{n}>{escape(str(v))}</{n}>" for n, v in arguments)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\r\n'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
        f'<u:{action} xmlns:u="{escape(service)}">{body}</u:{action}></s:Body></s:Envelope>'
    )


def answer(status: int, body: str, action: str) -> dict[str, str]:
    """The arguments of `action`'s response; a UPnP fault raises Fault."""
    try:
        root = fromstring(body) if body.strip() else None
    except Exception:
        root = None
    if root is not None:
        for el in root.iter():
            if _local(el.tag) == f"{action}Response" and 200 <= status < 300:
                return {_local(c.tag): (c.text or "").strip() for c in list(el)}
        for el in root.iter():
            if _local(el.tag) == "UPnPError":
                try:
                    code = int(_text(el, "errorCode") or 0)
                except ValueError:
                    code = 0
                raise Fault(code, _text(el, "errorDescription"))
    if 200 <= status < 300 and not body.strip():
        return {}
    raise Unreachable(f"HTTP {status}")


def call(
    service: Service,
    action: str,
    arguments: list[tuple[str, str]],
    timeout: float = 8.0,
    client: httpx.Client | None = None,
) -> dict[str, str]:
    headers = {
        "Content-Type": 'text/xml; charset="utf-8"',
        "SOAPAction": f'"{service.type}#{action}"',
        "User-Agent": USER_AGENT,
    }
    try:
        resp = (client or httpx).post(  # type: ignore[union-attr]
            service.control,
            content=envelope(service.type, action, arguments).encode("utf-8"),
            headers=headers,
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise Unreachable(str(exc)) from exc
    return answer(resp.status_code, resp.text[:MAX_ANSWER], action)


_TIME_RE = re.compile(r"^\+?(\d+):(\d{1,2}):(\d{1,2})(?:\.(\d+)(?:/(\d+))?)?$")


def seconds(text: str) -> float | None:
    """``H:MM:SS[.F]`` or ``H:MM:SS.F0/F1`` as seconds; None otherwise."""
    m = _TIME_RE.match((text or "").strip())
    if not m:
        return None
    hours, minutes, secs, frac, bottom = m.groups()
    total: float = int(hours) * 3600 + int(minutes) * 60 + int(secs)
    if frac:
        total += (
            int(frac) / int(bottom)
            if bottom and int(bottom) > 0
            else float(f"0.{frac}")
        )
    return float(total)


def time_text(value: float) -> str:
    total = max(0, round(value))
    return f"{total // 3600}:{total // 60 % 60:02}:{total % 60:02}"


def _duration_text(value: float) -> str:
    millis = round(max(0.0, value) * 1000)
    return f"{time_text(millis // 1000)}.{millis % 1000:03}"


def protocol_info(mime: str) -> str:
    return f"http-get:*:{mime}:{dlna_features(mime)}"


def didl(item: dict, mime: str) -> str:
    """The DIDL-Lite description of one song, as Mornye mobile writes it."""
    out = (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        '<item id="0" parentID="-1" restricted="1">'
    )
    out += f"<dc:title>{escape(item.get('title') or 'Unknown')}</dc:title>"
    if item.get("artist"):
        artist = escape(item["artist"])
        out += f"<dc:creator>{artist}</dc:creator><upnp:artist>{artist}</upnp:artist>"
    if item.get("album"):
        out += f"<upnp:album>{escape(item['album'])}</upnp:album>"
    if item.get("artwork"):
        out += f"<upnp:albumArtURI>{escape(item['artwork'])}</upnp:albumArtURI>"
    out += "<upnp:class>object.item.audioItem.musicTrack</upnp:class>"
    length = ""
    if item.get("duration"):
        length = f' duration="{_duration_text(item["duration"] / 1000)}"'
    out += (
        f'<res protocolInfo="{escape(protocol_info(mime))}"{length}>'
        f"{escape(item['url'])}</res></item></DIDL-Lite>"
    )
    return out


_ALIASES = (
    ("audio/flac", "audio/x-flac"),
    ("audio/mp4", "audio/x-m4a", "audio/m4a"),
    ("audio/mpeg", "audio/mp3", "audio/x-mpeg"),
    ("audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave"),
    ("audio/aac", "audio/x-aac", "audio/aacp"),
    ("audio/ogg", "application/ogg", "audio/x-ogg"),
)


def sink_mime(sink: str, mime: str) -> str:
    """The spelling of `mime` the renderer's Sink lists, if it lists
    another one of the same type."""
    listed = [
        parts[2].strip().lower()
        for parts in (entry.strip().split(":") for entry in (sink or "").split(","))
        if len(parts) > 2
    ]
    wanted = mime.lower()
    if not listed or wanted in listed or "*" in listed:
        return mime
    for names in _ALIASES:
        if wanted in names:
            for name in names:
                if name in listed:
                    return name
    return mime


# ── The session ─────────────────────────────────────────────────────────────

Emit = Callable[[dict], None]


@dataclass
class _Playback:
    asked_play: float | None = None
    asked_pause: bool = False
    asked_stop: bool = False
    played: bool = False
    done: bool = False
    held_autoplay: bool = False
    pending_seek: float | None = None
    clock: float = 0.0
    clock_at: float | None = None


class DlnaSession:
    """One connected renderer, driven by SOAP and followed by polling."""

    POLL = 1.0
    IDLE_EVERY = 3
    CALL = 8.0
    POLL_TIMEOUT = 3.0
    LOAD = 20.0
    GIVE_UP = 3
    START_LIMIT = 20.0

    def __init__(self, device: Device, status: Status, emit: Emit) -> None:
        self.device = device
        self.status = status
        self.emit = emit
        self.client = httpx.Client(headers={"User-Agent": USER_AGENT})
        self.renderer: Renderer | None = None
        self.volume_max = 100.0
        self.sink = ""
        self.item: dict | None = None
        self.transport = ""
        self.play_state = _Playback()
        self.failures = 0
        self.lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- connecting --

    def connect(self) -> None:
        name = self.device.name
        try:
            resp = self.client.get(self.device.location, timeout=self.CALL)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Could not reach {name}") from exc
        renderer = parse_description(
            resp.text, self.device.location, self.device.id.removeprefix("dlna:")
        )
        if renderer is None:
            raise RuntimeError(f"{name} cannot play audio from this app")
        self.renderer = renderer
        try:
            info = self._call(renderer.transport, "GetTransportInfo", [INSTANCE])
        except Fault:
            info = {}
        except Unreachable as exc:
            raise RuntimeError(f"{name} did not answer") from exc
        self.transport = info.get("CurrentTransportState", "").upper()
        if renderer.rendering and renderer.rendering.scpd:
            try:
                scpd = self.client.get(renderer.rendering.scpd, timeout=self.CALL)
                self.volume_max = float(volume_maximum(scpd.text) or 100)
            except httpx.HTTPError:
                pass
        if renderer.connection:
            try:
                self.sink = self._call(renderer.connection, "GetProtocolInfo", []).get(
                    "Sink", ""
                )
            except (Fault, Unreachable):
                pass
        self.status.start(self.device, self._read_volume())
        self._emit()
        self._thread = threading.Thread(
            target=self._run, name="mornye-cast-dlna", daemon=True
        )
        self._thread.start()

    def _call(
        self, service: Service, action: str, args: list, timeout: float | None = None
    ) -> dict:
        return call(service, action, args, timeout or self.CALL, self.client)

    def _emit(self) -> None:
        self.emit(self.status.snapshot())

    def _refused(self, exc: Exception) -> str:
        if isinstance(exc, Fault):
            return f"{self.device.name} refused the request (error {exc.code})"
        return f"{self.device.name} did not answer"

    # -- following --

    def _run(self) -> None:
        ticks = 0
        while not self._stop.wait(self.POLL):
            ticks += 1
            busy = self.transport in ("PLAYING", "TRANSITIONING") or (
                self.play_state.asked_play is not None and not self.play_state.played
            )
            if (busy or ticks % self.IDLE_EVERY == 0) and not self._poll(ticks):
                break
        with self.status.lock:
            self.status.connected = False
        self._emit()

    def _poll(self, ticks: int) -> bool:
        assert self.renderer is not None
        with self.lock:
            try:
                info = self._call(
                    self.renderer.transport,
                    "GetTransportInfo",
                    [INSTANCE],
                    self.POLL_TIMEOUT,
                )
            except Fault:
                self.failures = 0
                return True
            except Unreachable:
                self.failures += 1
                return self.failures < self.GIVE_UP
            self.failures = 0
            transport = info.get("CurrentTransportState", "").upper()
            healthy = info.get("CurrentTransportStatus", "").upper() != "ERROR_OCCURRED"
            position = length = None
            if self.item is not None:
                try:
                    pos = self._call(
                        self.renderer.transport,
                        "GetPositionInfo",
                        [INSTANCE],
                        self.POLL_TIMEOUT,
                    )
                    position = seconds(pos.get("RelTime", ""))
                    length = seconds(pos.get("TrackDuration", ""))
                    if length is not None and length <= 0:
                        length = None
                except (Fault, Unreachable):
                    pass
            if ticks % 5 == 0:
                volume = self._read_volume()
                if volume is not None:
                    self.status.volume = volume
            follow = self.observe(transport, healthy, position, length)
            self.transport = transport
            if follow == "pause":
                try:
                    self._call(
                        self.renderer.transport, "Pause", [INSTANCE], self.POLL_TIMEOUT
                    )
                    self.status.player_state = st.PAUSED
                except Fault:
                    try:
                        self._call(
                            self.renderer.transport,
                            "Stop",
                            [INSTANCE],
                            self.POLL_TIMEOUT,
                        )
                        self.status.player_state = st.PAUSED
                    except (Fault, Unreachable):
                        pass
                except Unreachable:
                    pass
            elif isinstance(follow, float):
                try:
                    self._send_seek(follow)
                except RuntimeError:
                    pass
        self._emit()
        return True

    def observe(
        self,
        transport: str,
        healthy: bool,
        position: float | None,
        length: float | None,
    ) -> str | float | None:
        """What a poll's transport state means for the song (dlna.rs
        `observe`): playing, buffering, paused, finished, failed or stopped
        on the device; "pause" holds a renderer that started by itself, a
        number is a seek still to send."""
        status = self.status
        with status.lock:
            if length is not None:
                status.duration = length
            if self.item is None or self.play_state.done:
                return None
            play = self.play_state
            trusted = position if position is not None and position > 0.5 else None
            reached = play.clock + (
                time.monotonic() - play.clock_at if play.clock_at else 0.0
            )
            duration = status.duration

            def near_end(at: float, slack: float) -> bool:
                return duration <= 0 or at >= duration - slack

            if transport == "PLAYING":
                if play.asked_play is None and not play.held_autoplay:
                    play.held_autoplay = True
                    return "pause"
                play.clock = trusted if trusted is not None else reached
                play.clock_at = time.monotonic()
                play.played = True
                status.player_state = st.PLAYING
                status.report(position if position is not None else play.clock)
                if play.pending_seek is not None:
                    seek, play.pending_seek = play.pending_seek, None
                    return float(seek)
            elif transport == "TRANSITIONING":
                play.clock = trusted if trusted is not None else reached
                play.clock_at = None
                status.player_state = st.BUFFERING
            elif transport in ("PAUSED_PLAYBACK", "PAUSED_RECORDING"):
                play.clock = trusted if trusted is not None else reached
                play.clock_at = None
                if (
                    play.played
                    and not play.asked_pause
                    and duration > 0
                    and near_end(play.clock, 2.0)
                ):
                    self._finish(st.FINISHED)
                else:
                    status.player_state = st.PAUSED
                    status.report(position if position is not None else play.clock)
            elif transport in ("STOPPED", "NO_MEDIA_PRESENT"):
                play.clock_at = None
                if play.asked_stop:
                    status.player_state = (
                        st.PAUSED if play.pending_seek is not None else st.IDLE
                    )
                elif not healthy and (play.played or play.asked_play is not None):
                    self._finish(st.ERROR)
                elif play.played:
                    if near_end(reached, max(duration * 0.1, 10.0)):
                        self._finish(st.FINISHED)
                    else:
                        play.played = False
                        play.asked_play = None
                        play.asked_stop = True
                        status.player_state = st.IDLE
                elif (
                    play.asked_play is not None
                    and time.monotonic() - play.asked_play > self.START_LIMIT
                ):
                    self._finish(st.ERROR)
                else:
                    status.player_state = (
                        st.BUFFERING if play.asked_play is not None else st.PAUSED
                    )
                    status.report(
                        play.pending_seek
                        if play.pending_seek is not None
                        else play.clock
                    )
            return None

    def _finish(self, reason: str) -> None:
        self.play_state.done = True
        with self.status.lock:
            self.status.player_state = st.IDLE
            self.status.idle_reason = reason
            if reason == st.FINISHED and self.status.duration > 0:
                self.status.report(self.status.duration)

    def _read_volume(self) -> float | None:
        if self.renderer is None or self.renderer.rendering is None:
            return None
        try:
            got = self._call(
                self.renderer.rendering,
                "GetVolume",
                [INSTANCE, ("Channel", "Master")],
                self.POLL_TIMEOUT,
            )
            return max(
                0.0, min(1.0, float(got.get("CurrentVolume", "")) / self.volume_max)
            )
        except (Fault, Unreachable, ValueError):
            return None

    # -- commands --

    def _loaded(self) -> None:
        if self.item is None or self.play_state.done:
            raise RuntimeError("Nothing is loaded on the device")

    def load(self, item: dict) -> None:
        assert self.renderer is not None
        with self.lock:
            transport = self.renderer.transport
            if self.transport in ("PLAYING", "PAUSED_PLAYBACK", "TRANSITIONING"):
                self.play_state.asked_stop = True
                try:
                    self._call(transport, "Stop", [INSTANCE])
                except (Fault, Unreachable):
                    pass
            mime = sink_mime(self.sink, item["mime"])
            try:
                try:
                    self._set_uri(item["url"], didl(item, mime))
                except Fault as exc:
                    if exc.code in (714, 716):
                        raise
                    # Some renderers refuse the metadata, not the song.
                    self._set_uri(item["url"], "")
            except Fault as exc:
                name = self.device.name
                raise RuntimeError(
                    {
                        714: f"{name} cannot play this audio format",
                        716: f"{name} could not open the song",
                    }.get(
                        exc.code, f"{name} could not load the song (error {exc.code})"
                    )
                ) from exc
            except Unreachable as exc:
                raise RuntimeError(f"{self.device.name} did not answer") from exc
            self.status.begin_item(item["token"], (item.get("duration") or 0) / 1000)
            self.item = item
            self.play_state = _Playback()
            self.transport = "STOPPED"
        self._emit()

    def _set_uri(self, url: str, metadata: str) -> None:
        assert self.renderer is not None
        self._call(
            self.renderer.transport,
            "SetAVTransportURI",
            [INSTANCE, ("CurrentURI", url), ("CurrentURIMetaData", metadata)],
            self.LOAD,
        )

    def play(self) -> None:
        assert self.renderer is not None
        with self.lock:
            self._loaded()
            try:
                self._call(self.renderer.transport, "Play", [INSTANCE, ("Speed", "1")])
            except (Fault, Unreachable) as exc:
                raise RuntimeError(self._refused(exc)) from exc
            p = self.play_state
            if self.transport != "PAUSED_PLAYBACK":
                p.played = False
                if p.pending_seek is None and p.asked_stop:
                    p.clock = 0.0
            p.asked_play = time.monotonic()
            p.asked_pause = p.asked_stop = False
            p.held_autoplay = True

    def pause(self) -> None:
        assert self.renderer is not None
        with self.lock:
            self._loaded()
            p = self.play_state
            if not p.played and self.transport != "PLAYING":
                p.asked_play = None
                return
            position = self.status.position_seconds()
            try:
                self._call(self.renderer.transport, "Pause", [INSTANCE])
            except Fault as exc:
                if exc.code not in (401, 602, 701):
                    raise RuntimeError(self._refused(exc)) from exc
                # No pause on this renderer: stop, and seek back on play.
                try:
                    self._call(self.renderer.transport, "Stop", [INSTANCE])
                except (Fault, Unreachable) as err:
                    raise RuntimeError(self._refused(err)) from err
                p.asked_stop = True
                p.pending_seek = position
                p.clock = position
            except Unreachable as exc:
                raise RuntimeError(self._refused(exc)) from exc
            p.asked_pause = True
            p.asked_play = None
            self.status.player_state = st.PAUSED
            self.status.report(position)
        self._emit()

    def stop(self) -> None:
        assert self.renderer is not None
        with self.lock:
            if self.item is None:
                return
            try:
                self._call(self.renderer.transport, "Stop", [INSTANCE])
            except Fault as exc:
                if exc.code != 701:
                    raise RuntimeError(self._refused(exc)) from exc
            except Unreachable as exc:
                raise RuntimeError(self._refused(exc)) from exc
            p = self.play_state
            p.asked_stop = True
            p.asked_play = None
            p.pending_seek = None
            if not p.done:
                self.status.player_state = st.IDLE
        self._emit()

    def seek(self, value: float) -> None:
        with self.lock:
            self._loaded()
            value = max(0.0, float(value))
            p = self.play_state
            if p.played or self.transport in ("PLAYING", "PAUSED_PLAYBACK"):
                self._send_seek(value)
            else:
                p.pending_seek = value
            p.clock = value
            if p.clock_at is not None:
                p.clock_at = time.monotonic()
            self.status.report(value)
        self._emit()

    def _send_seek(self, value: float) -> None:
        assert self.renderer is not None
        target = time_text(value)
        try:
            try:
                self._call(
                    self.renderer.transport,
                    "Seek",
                    [INSTANCE, ("Unit", "REL_TIME"), ("Target", target)],
                )
            except Fault as exc:
                if exc.code != 710:
                    raise
                self._call(
                    self.renderer.transport,
                    "Seek",
                    [INSTANCE, ("Unit", "ABS_TIME"), ("Target", target)],
                )
        except Fault as exc:
            if exc.code == 701:
                self.play_state.pending_seek = value
                return
            raise RuntimeError(self._refused(exc)) from exc
        except Unreachable as exc:
            raise RuntimeError(self._refused(exc)) from exc

    def set_volume(self, level: float) -> None:
        if self.renderer is None or self.renderer.rendering is None:
            raise RuntimeError(
                f"{self.device.name} does not let the app change its volume"
            )
        level = max(0.0, min(1.0, float(level)))
        desired = str(round(level * self.volume_max))
        try:
            self._call(
                self.renderer.rendering,
                "SetVolume",
                [INSTANCE, ("Channel", "Master"), ("DesiredVolume", desired)],
            )
        except (Fault, Unreachable) as exc:
            raise RuntimeError(self._refused(exc)) from exc
        self.status.volume = level
        self._emit()

    def disconnect(self) -> None:
        self._stop.set()
        if self.item is not None and self.renderer is not None:
            try:
                self._call(self.renderer.transport, "Stop", [INSTANCE], 3.0)
            except (Fault, Unreachable):
                pass
        with self.status.lock:
            self.status.connected = False
        self.client.close()
