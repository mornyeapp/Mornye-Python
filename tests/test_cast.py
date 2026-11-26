"""Casting to DLNA/UPnP and FCast receivers (core/cast, core/media_server),
ported from Mornye mobile 5.4.5's cast crate."""

from __future__ import annotations

import socket
import struct
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from mornye.core import media_server
from mornye.core.cast import dlna, fcast, ssdp
from mornye.core.cast import status as st
from mornye.core.cast.controller import CastController
from mornye.core.cast.status import Device, Status

# ── SSDP ────────────────────────────────────────────────────────────────────


def test_ssdp_parses_a_search_answer_and_a_byebye():
    answer = (
        b"HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age = 1800\r\n"
        b"LOCATION: http://192.168.1.30:49152/desc.xml\r\n"
        b"ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n"
        b"USN: uuid:tv-1::urn:schemas-upnp-org:device:MediaRenderer:1\r\n\r\n"
    )
    msg = ssdp.parse(answer, "192.168.1.30")
    assert msg is not None and msg.alive and ssdp.is_renderer(msg)
    assert msg.udn == "uuid:tv-1" and msg.max_age == 1800
    assert msg.location == "http://192.168.1.30:49152/desc.xml"
    bye = ssdp.parse(
        b"NOTIFY * HTTP/1.1\r\nNT: urn:schemas-upnp-org:device:MediaRenderer:1\r\n"
        b"NTS: ssdp:byebye\r\nUSN: uuid:tv-1::x\r\n\r\n",
        "192.168.1.30",
    )
    assert bye is not None and not bye.alive
    assert ssdp.parse(b"GET / HTTP/1.1\r\n\r\n", "x") is None


def test_ssdp_search_request_and_locations():
    req = ssdp.search_request().decode()
    assert req.startswith("M-SEARCH * HTTP/1.1\r\n")
    assert (
        'MAN: "ssdp:discover"' in req
        and "ST: urn:schemas-upnp-org:device:MediaRenderer:1" in req
    )
    assert ssdp.valid_location("http://192.168.1.30:1/d.xml", "192.168.1.30")
    assert not ssdp.valid_location("http://8.8.8.8/d.xml", "192.168.1.30")
    assert not ssdp.valid_location("file:///etc/passwd", "192.168.1.30")


# ── DLNA description and SOAP ───────────────────────────────────────────────

DESCRIPTION = """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0"><specVersion><major>1</major></specVersion>
<device><deviceType>urn:schemas-upnp-org:device:Basic:1</deviceType>
  <friendlyName>Hall TV</friendlyName><UDN>uuid:root-1</UDN>
  <deviceList><device>
    <deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>
    <friendlyName>Hall TV &amp; Sound</friendlyName><manufacturer>Maker</manufacturer>
    <UDN>uuid:renderer-1</UDN>
    <serviceList>
      <service><serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType>
        <SCPDURL>avt.xml</SCPDURL><controlURL>/upnp/control/avt</controlURL></service>
      <service><serviceType>urn:schemas-upnp-org:service:RenderingControl:1</serviceType>
        <SCPDURL>/rc.xml</SCPDURL><controlURL>rc/control</controlURL></service>
      <service><serviceType>urn:schemas-upnp-org:service:ConnectionManager:1</serviceType>
        <SCPDURL>/cm.xml</SCPDURL><controlURL>http://8.8.8.8/cm</controlURL></service>
    </serviceList>
  </device></deviceList>
</device></root>"""


def test_an_embedded_renderer_resolves_relative_urls():
    location = "http://192.0.2.30:49152/desc/device.xml"
    renderer = dlna.parse_description(DESCRIPTION, location)
    assert renderer is not None
    assert renderer.udn == "uuid:renderer-1"
    assert renderer.name == "Hall TV & Sound" and renderer.model == "Maker"
    assert renderer.transport.control == "http://192.0.2.30:49152/upnp/control/avt"
    assert renderer.transport.scpd == "http://192.0.2.30:49152/desc/avt.xml"
    assert renderer.rendering.control == "http://192.0.2.30:49152/desc/rc/control"
    # A control URL on another host is not followed.
    assert renderer.connection is None
    assert dlna.parse_description("<root/>", location) is None


def test_volume_maximum_from_the_scpd():
    scpd = (
        '<scpd xmlns="urn:schemas-upnp-org:service-1-0"><serviceStateTable>'
        "<stateVariable><name>Volume</name><allowedValueRange><minimum>0</minimum>"
        "<maximum>60</maximum></allowedValueRange></stateVariable></serviceStateTable></scpd>"
    )
    assert dlna.volume_maximum(scpd) == 60
    assert dlna.volume_maximum("<scpd/>") is None


def test_soap_envelope_answer_and_fault():
    text = dlna.envelope(
        "urn:x:AVTransport:1", "SetAVTransportURI", [("CurrentURIMetaData", "<a>&</a>")]
    )
    assert '<u:SetAVTransportURI xmlns:u="urn:x:AVTransport:1">' in text
    assert "&lt;a&gt;&amp;&lt;/a&gt;" in text
    ok = (
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
        '<u:GetTransportInfoResponse xmlns:u="x"><CurrentTransportState>PLAYING</CurrentTransportState>'
        "</u:GetTransportInfoResponse></s:Body></s:Envelope>"
    )
    assert dlna.answer(200, ok, "GetTransportInfo") == {
        "CurrentTransportState": "PLAYING"
    }
    fault = (
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><s:Fault>'
        '<detail><UPnPError xmlns="urn:schemas-upnp-org:control-1-0"><errorCode>714</errorCode>'
        "<errorDescription>Illegal MIME</errorDescription></UPnPError></detail></s:Fault></s:Body></s:Envelope>"
    )
    with pytest.raises(dlna.Fault) as caught:
        dlna.answer(500, fault, "SetAVTransportURI")
    assert caught.value.code == 714
    with pytest.raises(dlna.Unreachable):
        dlna.answer(500, "", "Play")


def test_times_and_didl():
    assert dlna.seconds("0:03:25") == 205
    assert dlna.seconds("1:00:00.500") == 3600.5
    assert dlna.seconds("0:00:01.1/2") == 1.5
    assert dlna.seconds("NOT_IMPLEMENTED") is None
    assert dlna.time_text(3725.4) == "1:02:05"
    text = dlna.didl(
        {
            "title": "A & B",
            "artist": "X",
            "album": "Y",
            "url": "http://h/x.flac?a=1&b=2",
            "duration": 61500,
        },
        "audio/flac",
    )
    assert "<dc:title>A &amp; B</dc:title>" in text
    assert "<upnp:class>object.item.audioItem.musicTrack</upnp:class>" in text
    assert 'duration="0:01:01.500"' in text
    assert "http-get:*:audio/flac:DLNA.ORG_OP=01" in text
    assert "http://h/x.flac?a=1&amp;b=2" in text


def test_sink_mime_picks_the_spelling_the_renderer_lists():
    sink = "http-get:*:audio/x-flac:*,http-get:*:audio/mpeg:*"
    assert dlna.sink_mime(sink, "audio/flac") == "audio/x-flac"
    assert dlna.sink_mime(sink, "audio/mpeg") == "audio/mpeg"
    assert dlna.sink_mime("", "audio/flac") == "audio/flac"
    assert dlna.sink_mime("http-get:*:*:*", "audio/wav") == "audio/wav"


def _session(duration=200.0):
    device = Device(
        "dlna:uuid:1", "TV", "", "192.0.2.1", 1, "dlna", "http://192.0.2.1/d.xml"
    )
    status = Status()
    status.start(device, 0.5)
    session = dlna.DlnaSession(device, status, lambda snap: None)
    session.item = {"url": "u", "mime": "audio/flac", "token": 1}
    status.begin_item(1, duration)
    return session, status


def test_dlna_follows_a_song_to_its_end():
    session, status = _session()
    session.play_state.asked_play = time.monotonic()
    session.play_state.held_autoplay = True
    assert session.observe("TRANSITIONING", True, None, None) is None
    assert status.player_state == st.BUFFERING
    session.observe("PLAYING", True, 10.0, 200.0)
    assert status.player_state == st.PLAYING
    session.play_state.clock = 195.0
    session.observe("STOPPED", True, None, None)
    assert status.idle_reason == st.FINISHED and status.snapshot()["finished"]


def test_dlna_a_stop_mid_song_is_not_an_end_and_an_error_fails():
    session, status = _session()
    session.play_state.asked_play = time.monotonic()
    session.play_state.held_autoplay = True
    session.observe("PLAYING", True, 30.0, 200.0)
    session.observe("STOPPED", True, None, None)
    assert status.idle_reason is None and status.player_state == st.IDLE
    session, status = _session()
    session.play_state.asked_play = time.monotonic()
    session.observe("STOPPED", False, None, None)
    assert status.snapshot()["failed"]


def test_dlna_holds_a_renderer_that_starts_by_itself():
    session, _ = _session()
    assert session.observe("PLAYING", True, 1.0, 200.0) == "pause"
    session2, _ = _session()
    session2.play_state.asked_play = time.monotonic()
    session2.play_state.pending_seek = 42.0
    assert session2.observe("PLAYING", True, 1.0, 200.0) == 42.0


# ── The media server's answers to renderers ────────────────────────────────


@pytest.fixture()
def served(tmp_path):
    song = tmp_path / "song.flac"
    song.write_bytes(bytes(range(256)) * 4)
    server = media_server.MediaServer(lambda p: True, "127.0.0.1", dlna=True)
    yield server, song
    server.close()


def _request(url: str, method: str = "GET", headers: dict | None = None):
    req = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), b""


def test_renderers_get_head_ranges_and_dlna_headers(served):
    server, song = served
    url = server.url_for(song)
    code, headers, body = _request(url, "HEAD", {"getcontentFeatures.dlna.org": "1"})
    assert code == 200 and body == b""
    assert headers["Content-Length"] == "1024" and headers["Accept-Ranges"] == "bytes"
    assert headers["contentFeatures.dlna.org"].startswith(
        "DLNA.ORG_OP=01;DLNA.ORG_CI=0"
    )
    assert headers["transferMode.dlna.org"] == "Streaming"
    code, headers, body = _request(
        url, headers={"Range": "bytes=10-19", "transferMode.dlna.org": "Interactive"}
    )
    assert code == 206 and body == bytes(range(10, 20))
    assert headers["Content-Range"] == "bytes 10-19/1024"
    assert headers["transferMode.dlna.org"] == "Interactive"
    assert _request(url, headers={"TimeSeekRange.dlna.org": "npt=10-"})[0] == 406
    assert _request(url, "OPTIONS")[0] == 204


def test_the_local_server_stays_loopback_without_dlna_headers(tmp_path):
    song = tmp_path / "a.mp3"
    song.write_bytes(b"x" * 10)
    server = media_server.MediaServer(lambda p: True)
    try:
        assert server.url_for(song).startswith("http://127.0.0.1:")
        code, headers, _ = _request(server.url_for(song), "HEAD")
        assert code == 200 and "contentFeatures.dlna.org" not in headers
    finally:
        server.close()


# ── FCast ───────────────────────────────────────────────────────────────────


def test_fcast_packets_follow_the_v3_framing():
    assert fcast.encode(fcast.PING) == bytes([1, 0, 0, 0, 12])
    packet = fcast.encode(fcast.VERSION_OP, {"version": 3})
    body = b'{"version":3}'
    assert (
        packet[:4] == struct.pack("<I", len(body) + 1)
        and packet[4] == 11
        and packet[5:] == body
    )
    with pytest.raises(ValueError):
        fcast.encode(fcast.PLAY, {"url": "x" * fcast.MAX_PACKET})
    buf = bytearray(packet + fcast.encode(fcast.PING) + b"\x05\x00")
    assert fcast.decode(buf) == [(11, {"version": 3}), (12, {})]
    assert bytes(buf) == b"\x05\x00"


def test_fcast_device_from_mdns():
    d = fcast.device_from_service(
        "Living Room._fcast._tcp.local.", "192.168.1.9", 46899
    )
    assert (
        d is not None
        and d.id == "fcast:Living Room"
        and d.port == 46899
        and d.kind == "fcast"
    )


class _Receiver:
    """A minimal FCast receiver: answers the version, records packets."""

    def __init__(self) -> None:
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.got: list[tuple[int, dict]] = []
        self.conn: socket.socket | None = None
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        self.conn, _ = self.server.accept()
        buf = bytearray()
        while True:
            try:
                data = self.conn.recv(65536)
            except OSError:
                return
            if not data:
                return
            buf.extend(data)
            for opcode, body in fcast.decode(buf):
                self.got.append((opcode, body))
                if opcode == fcast.VERSION_OP:
                    self.send(fcast.VERSION_OP, {"version": 3})

    def send(self, opcode: int, body: dict | None = None) -> None:
        assert self.conn is not None
        self.conn.sendall(fcast.encode(opcode, body))

    def wait_for(self, opcode: int, timeout: float = 3.0) -> dict:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for op, body in self.got:
                if op == opcode:
                    return body
            time.sleep(0.02)
        raise AssertionError(f"no packet {opcode}: {self.got}")


def test_fcast_session_plays_and_hears_the_end():
    receiver = _Receiver()
    device = Device("fcast:R", "R", "FCast", "127.0.0.1", receiver.port, "fcast")
    snaps: list[dict] = []
    session = fcast.FcastSession(device, Status(), snaps.append)
    session.connect()
    try:
        receiver.wait_for(fcast.INITIAL)
        assert receiver.wait_for(fcast.SUBSCRIBE_EVENT) == {"event": {"type": 1}}
        item = {
            "url": "http://h/a.flac",
            "mime": "audio/flac",
            "token": 7,
            "title": "T",
            "artist": "A",
            "album": "B",
        }
        session.load(item)
        session.play()
        play = receiver.wait_for(fcast.PLAY)
        assert play["url"] == "http://h/a.flac" and play["container"] == "audio/flac"
        assert (
            play["metadata"]["title"] == "T"
            and play["metadata"]["custom"]["artist"] == "A"
        )
        receiver.send(
            fcast.PLAYBACK_UPDATE, {"state": 1, "time": 1.0, "duration": 100.0}
        )
        receiver.send(
            fcast.EVENT, {"event": {"type": 1, "item": {"url": "http://h/a.flac"}}}
        )
        end = time.monotonic() + 3
        while time.monotonic() < end and not session.status.snapshot().get("finished"):
            time.sleep(0.02)
        snap = session.status.snapshot()
        assert snap["finished"] and snap["token"] == 7
        session.set_volume(0.4)
        assert receiver.wait_for(fcast.SET_VOLUME) == {"volume": 0.4}
    finally:
        session.disconnect()


# ── The controller ──────────────────────────────────────────────────────────


def test_controller_lists_renderers_once_and_drops_them_on_byebye():
    pushed: list = []
    controller = CastController(
        lambda p: True, lambda name, payload: pushed.append(name)
    )
    location = "http://192.168.1.30:49152/desc/device.xml"
    alive = ssdp.Message(
        True, ssdp.RENDERER, "uuid:renderer-1::x", location, 1800, "192.168.1.30"
    )
    asked: list[str] = []

    def describe(url: str) -> str:
        asked.append(url)
        return DESCRIPTION

    controller.handle_ssdp([alive, alive], describe)
    assert asked == [location]
    (device,) = controller.devices()
    assert device == {
        "id": "dlna:uuid:renderer-1",
        "name": "Hall TV & Sound",
        "model": "Maker",
        "host": "192.168.1.30",
        "port": 49152,
        "kind": "dlna",
    }
    bye = ssdp.Message(
        False, ssdp.RENDERER, "uuid:renderer-1::x", None, None, "192.168.1.30"
    )
    controller.handle_ssdp([bye], describe)
    assert controller.devices() == [] and pushed.count("app_cast_devices") == 2


def test_controller_refuses_files_outside_the_library(tmp_path: Path):
    controller = CastController(lambda p: False, lambda *a: None)
    with pytest.raises(RuntimeError):
        controller.load(str(tmp_path / "x.flac"))


def test_cast_methods_stay_desktop_only():
    from mornye.webapp import ALLOWED_METHODS

    assert not {m for m in ALLOWED_METHODS if m.startswith("cast_")}
    shim = (
        Path(__file__).parent.parent / "mornye" / "frontend" / "web-shim.js"
    ).read_text()
    assert "cast_connect" not in shim


class _Renderer:
    """A fake DLNA renderer: its description and SOAP control on loopback."""

    def __init__(self) -> None:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        outer = self
        self.actions: list[tuple[str, str]] = []
        self.state = "NO_MEDIA_PRESENT"

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                return

            def do_GET(self):
                body = DESCRIPTION.replace("http://8.8.8.8/cm", "/cm").encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                text = self.rfile.read(length).decode()
                action = self.headers["SOAPAction"].strip('"').split("#")[1]
                outer.actions.append((action, text))
                args = ""
                if action == "GetTransportInfo":
                    args = (
                        f"<CurrentTransportState>{outer.state}</CurrentTransportState>"
                    )
                elif action == "GetVolume":
                    args = "<CurrentVolume>50</CurrentVolume>"
                elif action == "GetProtocolInfo":
                    args = "<Sink>http-get:*:audio/x-flac:*</Sink>"
                elif action == "Play":
                    outer.state = "PLAYING"
                body = (
                    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
                    f'<u:{action}Response xmlns:u="x">{args}</u:{action}Response></s:Body></s:Envelope>'
                ).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()


def test_casting_a_file_to_a_dlna_renderer_end_to_end(tmp_path):
    renderer = _Renderer()
    song = tmp_path / "song.flac"
    song.write_bytes(b"fLaC" + b"\0" * 2000)
    pushed: list = []
    controller = CastController(
        lambda p: True, lambda name, payload: pushed.append((name, payload))
    )
    location = f"http://127.0.0.1:{renderer.port}/desc/device.xml"
    controller.upsert(
        Device(
            "dlna:uuid:renderer-1",
            "Hall TV",
            "",
            "127.0.0.1",
            renderer.port,
            "dlna",
            location,
        )
    )
    try:
        snap = controller.connect("dlna:uuid:renderer-1")
        assert snap["connected"] and snap["volume"] == 0.5
        out = controller.load(
            str(song), {"title": "Song", "artist": "A", "duration": 61}, autoplay=True
        )
        assert out["token"] == 1
        names = [a for a, _ in renderer.actions]
        assert names[-2:] == ["SetAVTransportURI", "Play"]
        uri_call = dict(renderer.actions)["SetAVTransportURI"]
        # The renderer's own spelling of the type, from its Sink.
        assert (
            "audio/x-flac" in uri_call
            and "&lt;dc:title&gt;Song&lt;/dc:title&gt;" in uri_call
        )
        url = uri_call.split("<CurrentURI>")[1].split("</CurrentURI>")[0]
        code, headers, _ = _request(url, "HEAD")
        assert code == 200 and headers["Content-Length"] == str(song.stat().st_size)
        assert headers["contentFeatures.dlna.org"].startswith("DLNA.ORG_OP=01")
        controller.command("volume", 0.2)
        assert "<DesiredVolume>20</DesiredVolume>" in renderer.actions[-1][1]
    finally:
        controller.disconnect()
        renderer.close()
    assert any(name == "app_cast_status" for name, _ in pushed)
