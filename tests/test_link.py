"""Mornye Link (core/link.py, webapi/link.py, link_server.py): the pairing a
phone does, against a real TLS server on loopback, checked the way the
phone app's LinkPairingClient checks it."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import ssl
from pathlib import Path

import httpx
import pytest

from mornye.core import link
from mornye.link_server import FEATURES, LinkServer


def _client() -> httpx.Client:
    # The phone trusts the certificate by its pinned fingerprint, not a CA.
    return httpx.Client(verify=False, timeout=10)


def _observed_fingerprint(port: int) -> str:
    pem = ssl.get_server_certificate(("127.0.0.1", port))
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()


@pytest.fixture()
def server(tmp_path: Path):
    srv = LinkServer(
        tmp_path / "link", bind="127.0.0.1", advertise=False, pair_interval_s=0
    )
    status = srv.start()
    assert status["running"], status["error"]
    yield srv
    srv.stop()


def _pair(srv: LinkServer, pin: str, device_id: str = "phone-1") -> httpx.Response:
    base = f"https://127.0.0.1:{srv.port}"
    with _client() as http:
        info = http.get(f"{base}/mornye/v1/info").json()
        proof = link.pairing_proof(pin, info["fingerprint"], device_id)
        return http.post(
            f"{base}/mornye/v1/pair",
            json={
                "deviceId": device_id,
                "name": "Pixel",
                "platform": "android",
                "proof": proof,
            },
        )


def _wrong(pin: str) -> str:
    return "000000" if pin != "000000" else "111111"


def test_proof_matches_the_reference_hmac():
    expected = hmac.new(
        b"123456", b"mornye-link-pair-v1:ab:dev", hashlib.sha256
    ).hexdigest()
    assert link.pairing_proof("123456", "ab", "dev") == expected


def test_identity_is_created_once(tmp_path: Path):
    first = link.load_or_create_identity(tmp_path)
    assert len(first.fingerprint) == 64
    assert link.load_or_create_identity(tmp_path).fingerprint == first.fingerprint
    (tmp_path / link.KEY_FILE).write_text("broken")
    assert link.load_or_create_identity(tmp_path).fingerprint != first.fingerprint


def test_info_matches_the_certificate(server: LinkServer):
    with _client() as http:
        info = http.get(f"https://127.0.0.1:{server.port}/mornye/v1/info").json()
    assert info["protocol"] == 1
    assert info["pairing"] is False
    assert info["features"] == list(FEATURES)
    assert info["serverId"] == server.store.server_id
    # What the phone checks before sending a PIN.
    assert info["fingerprint"] == _observed_fingerprint(server.port)


def test_pairs_with_the_pin_and_the_key_opens_subsonic(server: LinkServer):
    assert _pair(server, "000000").json() == {"error": "pairing_closed"}
    pin = server.begin_pairing()["pairing"]["pin"]
    assert _pair(server, _wrong(pin)).json() == {"error": "bad_proof"}

    reply = _pair(server, pin)
    assert reply.status_code == 200
    body = reply.json()
    assert body["serverId"] == server.store.server_id
    assert body["fingerprint"] == server.fingerprint
    key = body["apiKey"]
    assert server.status()["pairing"] is None
    assert server.pairing.last_ended == link.PAIRED
    assert [d["deviceId"] for d in server.status()["devices"]] == ["phone-1"]

    origin = f"https://127.0.0.1:{server.port}"
    ping = f"{origin}/rest/ping.view"
    salt = "abc"
    token = hashlib.md5((key + salt).encode()).hexdigest()
    with _client() as http:
        for params in (
            {"apiKey": key},
            {"u": "anyone", "t": token, "s": salt},
            {"u": "anyone", "p": "enc:" + key.encode().hex()},
        ):
            ok = http.get(
                ping, params={**params, "f": "json", "v": "1.16.1", "c": "t"}
            ).json()
            assert ok["subsonic-response"]["status"] == "ok", params
        bad = http.get(ping, params={"apiKey": "nope", "f": "json"}).json()
        assert bad["subsonic-response"]["error"]["code"] == 44
        # Without credentials only the extension list answers.
        ext = http.get(
            f"{origin}/rest/getOpenSubsonicExtensions", params={"f": "json"}
        ).json()
        assert ext["subsonic-response"]["status"] == "ok"
        # Forwarded ops need the key. The player's (Mornye Connect) answer;
        # with nothing open the state is an idle one. Others are not here.
        op = f"{origin}/mornye/v1/state"
        assert http.get(op).status_code == 401
        bearer = {"Authorization": f"Bearer {key}"}
        state = http.get(op, headers=bearer)
        assert state.status_code == 200 and state.json()["playing"] is False
        assert state.json()["updatedMs"] > 0
        other = http.get(f"{origin}/mornye/v1/downloads", headers=bearer)
        assert (other.status_code, other.json()["error"]) == (404, "not_found")
        bad = http.post(
            f"{origin}/mornye/v1/command", headers=bearer, json={"action": ""}
        )
        assert (bad.status_code, bad.json()["error"]) == (400, "invalid")

    # Revoking takes the key away.
    server.revoke("phone-1")
    with _client() as http:
        gone = http.get(ping, params={"apiKey": key, "f": "json"}).json()
    assert gone["subsonic-response"]["error"]["code"] == 44


def test_five_bad_proofs_close_the_window(server: LinkServer):
    pin = server.begin_pairing()["pairing"]["pin"]
    for _ in range(5):
        assert _pair(server, _wrong(pin)).json()["error"] == "bad_proof"
    assert _pair(server, pin).json()["error"] == "pairing_closed"
    assert server.pairing.last_ended == link.ATTEMPTS


def test_rate_limits_one_address():
    window = link.PairingWindow(ttl_s=60, interval_s=60)
    window.begin()
    with pytest.raises(link.PairError) as first:
        window.attempt("1.2.3.4", "fp", "d", "bad", lambda: None)
    assert first.value.code == "bad_proof"
    with pytest.raises(link.PairError) as second:
        window.attempt("1.2.3.4", "fp", "d", "bad", lambda: None)
    assert second.value.code == "rate_limited"


def test_a_failed_save_keeps_the_window_open():
    window = link.PairingWindow(ttl_s=60, interval_s=0)
    pin = window.begin()["pin"]

    def fail() -> None:
        raise OSError("disk full")

    with pytest.raises(link.PairError) as error:
        window.attempt("p", "fp", "d", link.pairing_proof(pin, "fp", "d"), fail)
    assert error.value.code == "internal"
    assert window.is_open()


def test_websocket_hello_welcome(server: LinkServer):
    import websockets

    pin = server.begin_pairing()["pairing"]["pin"]
    key = _pair(server, pin).json()["apiKey"]
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    url = f"wss://127.0.0.1:{server.port}/mornye/v1/ws"
    auth = {"Authorization": f"Bearer {key}"}

    async def scenario() -> tuple[dict, bool, int | None]:
        async with websockets.connect(url, ssl=ctx, additional_headers=auth) as ws:
            hello = {
                "type": "hello",
                "deviceId": "phone-1",
                "name": "Pixel",
                "platform": "android",
                "appVersion": "1",
                "protocol": 1,
            }
            await ws.send(json.dumps(hello))
            welcome = json.loads(await ws.recv())
            connected = [d["connected"] for d in server.status()["devices"]] == [True]
        # A hello for another device is closed with 4401.
        async with websockets.connect(url, ssl=ctx, additional_headers=auth) as ws:
            await ws.send(json.dumps({"type": "hello", "deviceId": "someone-else"}))
            with pytest.raises(websockets.ConnectionClosed) as closed:
                await ws.recv()
        return welcome, connected, closed.value.rcvd.code if closed.value.rcvd else None

    welcome, connected, code = asyncio.run(scenario())
    assert welcome["type"] == "welcome"
    assert welcome["protocol"] == 1
    assert welcome["serverId"] == server.store.server_id
    assert connected
    assert code == 4401


def test_store_survives_a_restart(tmp_path: Path):
    store = link.LinkStore(tmp_path)
    record = store.upsert("phone", "Phone", "ios")
    again = link.LinkStore(tmp_path)
    assert again.server_id == store.server_id
    assert again.by_key(record.apiKey).deviceId == "phone"
    # A re-pair replaces the key.
    newer = again.upsert("phone", "Phone", "ios")
    assert again.by_key(record.apiKey) is None
    assert again.by_key(newer.apiKey) is not None


def test_connect_over_link_upload_and_state_frame(server: LinkServer):
    """Mornye Connect through the real server: a song uploaded for a hand-off
    lands in received/, and the socket sends the player's state after the
    welcome (link_desktop_host.dart)."""
    import websockets

    from mornye.core.connect.hub import reset_connect_hubs

    reset_connect_hubs()
    pin = server.begin_pairing()["pairing"]["pin"]
    key = _pair(server, pin).json()["apiKey"]
    bearer = {"Authorization": f"Bearer {key}"}
    origin = f"https://127.0.0.1:{server.port}"
    name = "cd" * 16 + ".flac"
    with _client() as http:
        put = http.put(
            f"{origin}/mornye/v1/files/{name}", headers=bearer, content=b"fLaC" * 10
        )
        assert put.json() == {"name": name, "size": 40}
        bad = http.put(
            f"{origin}/mornye/v1/files/notes.txt", headers=bearer, content=b"x"
        )
        assert (bad.status_code, bad.json()["error"]) == (400, "invalid")
    assert (server.dir / "received" / name).read_bytes() == b"fLaC" * 10

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    async def scenario() -> dict:
        url = f"wss://127.0.0.1:{server.port}/mornye/v1/ws"
        async with websockets.connect(url, ssl=ctx, additional_headers=bearer) as ws:
            await ws.send(
                json.dumps({"type": "hello", "deviceId": "phone-1", "name": "Pixel"})
            )
            assert json.loads(await ws.recv())["type"] == "welcome"
            return json.loads(await asyncio.wait_for(ws.recv(), 5))

    frame = asyncio.run(scenario())
    assert frame["type"] == "player.state" and frame["state"]["playing"] is False
    reset_connect_hubs()


# ── pairing without a PIN (pair/request, pair/wait) ───────────────────────


def _ask(srv: LinkServer, device_id: str = "phone-9") -> httpx.Response:
    with _client() as http:
        return http.post(
            f"https://127.0.0.1:{srv.port}/mornye/v1/pair/request",
            json={"deviceId": device_id, "name": "Pixel", "platform": "android"},
        )


def _wait(srv: LinkServer, request_id: str, device_id: str = "phone-9") -> dict:
    with httpx.Client(verify=False, timeout=40) as http:
        return http.post(
            f"https://127.0.0.1:{srv.port}/mornye/v1/pair/wait",
            json={"requestId": request_id, "deviceId": device_id},
        ).json()


def test_a_trusted_network_pairs_at_once(server: LinkServer):
    server.set_trust_network(True)
    answer = _ask(server)
    assert answer.status_code == 200
    body = answer.json()
    assert body["status"] == "approved" and body["serverId"] == server.store.server_id
    assert server.store.by_key(body["apiKey"]).deviceId == "phone-9"
    assert link.LinkStore(server.dir).trust_network is True  # kept on disk


def test_a_request_waits_for_a_click_and_is_shown(server: LinkServer):
    from mornye.core.connect.hub import get_connect_hub, reset_connect_hubs

    reset_connect_hubs()
    shown: list[tuple[str, dict]] = []
    get_connect_hub(None).set_broadcast(lambda fn, p: shown.append((fn, p)))
    answer = _ask(server)
    assert answer.status_code == 202
    request_id = answer.json()["requestId"]
    assert shown == [
        (
            "app_link_pair_request",
            {
                "id": request_id,
                "deviceId": "phone-9",
                "name": "Pixel",
                "platform": "android",
                "address": "127.0.0.1",
            },
        )
    ]
    assert [r["id"] for r in server.status()["requests"]] == [request_id]
    # Asking again is the same request; another address would wait its turn.
    assert _ask(server).json()["requestId"] == request_id
    import threading

    threading.Timer(0.3, server.answer_request, (request_id, True)).start()
    body = _wait(server, request_id)
    assert body["status"] == "approved"
    assert server.store.by_key(body["apiKey"]).name == "Pixel"
    assert server.status()["requests"] == []
    reset_connect_hubs()


def test_a_denied_request_gets_no_key(server: LinkServer):
    request_id = _ask(server).json()["requestId"]
    server.answer_request(request_id, False)
    assert _wait(server, request_id) == {"status": "denied"}
    assert [d.deviceId for d in server.store.devices()] == []
    with httpx.Client(verify=False) as http:
        missing = http.post(
            f"https://127.0.0.1:{server.port}/mornye/v1/pair/wait",
            json={"requestId": request_id, "deviceId": "someone-else"},
        )
    assert missing.status_code == 404


def test_requests_expire_and_are_limited_per_address():
    requests = link.PairRequests(ttl_s=0.05)
    first = requests.open("a", "A", "android", "10.0.0.2")
    with pytest.raises(link.PairError):
        requests.open("b", "B", "android", "10.0.0.2")
    assert requests.wait(first.id, "a", 1.0).status == "expired"
    assert requests.pending() == []
    assert not requests.answer(first.id, True, lambda r: None)


def test_only_local_addresses_count_as_the_network():
    for address in (
        "192.168.1.5",
        "10.1.2.3",
        "127.0.0.1",
        "::1",
        "fe80::1",
        "::ffff:192.168.0.9",
    ):
        assert link.is_local_address(address), address
    for address in ("8.8.8.8", "2a00:1450::1", "", "nonsense"):
        assert not link.is_local_address(address), address
