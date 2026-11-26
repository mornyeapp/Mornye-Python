"""SSDP: finding DLNA/UPnP MediaRenderers on the LAN (Mornye mobile ssdp.rs).

An M-SEARCH for ``urn:schemas-upnp-org:device:MediaRenderer:1`` goes to
239.255.255.250:1900 from every LAN address, twice; renderers answer with
their description's LOCATION. A renderer's ``NOTIFY ssdp:byebye`` drops it.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

GROUP = ("239.255.255.250", 1900)
RENDERER = "urn:schemas-upnp-org:device:MediaRenderer:1"
RENDERER_PREFIX = "urn:schemas-upnp-org:device:mediarenderer:"
MX = 2
TTL = 2
#: How long one search listens for answers.
WINDOW = MX + 2
MAX_DATAGRAM = 4096


@dataclass
class Message:
    alive: bool
    target: str
    usn: str
    location: str | None
    max_age: int | None
    sender: str

    @property
    def udn(self) -> str:
        return self.usn.split("::", 1)[0].strip()


def _max_age(value: str) -> int | None:
    lower = value.lower()
    if "max-age" not in lower:
        return None
    after = lower.split("max-age", 1)[1].lstrip()
    if not after.startswith("="):
        return None
    digits = ""
    for ch in after[1:].lstrip():
        if not ch.isdigit():
            break
        digits += ch
    return int(digits) if digits else None


def parse(datagram: bytes, sender: str) -> Message | None:
    """A search answer (``HTTP/1.1 200``) or an announcement (``NOTIFY``)."""
    lines = datagram.decode("utf-8", "replace").splitlines()
    if not lines:
        return None
    start = lines[0].strip()
    if start.upper() == "NOTIFY * HTTP/1.1":
        notify = True
    elif start.startswith("HTTP/1.") and start.split()[1:2] == ["200"]:
        notify = False
    else:
        return None
    msg = Message(True, "", "", None, None, sender)
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if not sep:
            continue
        name, value = name.strip().upper(), value.strip()
        if name == "ST" and not notify or name == "NT" and notify:
            msg.target = value
        elif name == "NTS" and notify and value.lower() == "ssdp:byebye":
            msg.alive = False
        elif name == "USN":
            msg.usn = value
        elif name == "LOCATION":
            msg.location = value
        elif name == "CACHE-CONTROL":
            msg.max_age = _max_age(value)
    return msg if msg.usn and msg.target else None


def search_request(target: str = RENDERER) -> bytes:
    return (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {GROUP[0]}:{GROUP[1]}\r\n"
        'MAN: "ssdp:discover"\r\n'
        f"MX: {MX}\r\n"
        f"ST: {target}\r\n"
        "USER-AGENT: Python/1.0 UPnP/1.1 Mornye/1.0\r\n\r\n"
    ).encode()


def is_renderer(msg: Message) -> bool:
    return msg.target.lower().startswith(RENDERER_PREFIX)


def valid_location(location: str, sender: str) -> bool:
    """A description address on the sender itself or on the private LAN."""
    try:
        host = urlsplit(location).hostname or ""
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if urlsplit(location).scheme != "http":
        return False
    return host == sender or (ip.version == 4 and (ip.is_private or ip.is_link_local))


def lan_addresses() -> list[str]:
    from ...link_server import lan_addresses as link_lan

    return [a for a in link_lan() if a and not a.startswith("127.")]


def search(
    window: float = WINDOW,
    addresses: list[str] | None = None,
    destination: tuple[str, int] = GROUP,
    target: str = RENDERER,
) -> list[Message]:
    """One round: the search from every LAN address, the answers heard."""
    sockets: list[socket.socket] = []
    for address in addresses if addresses is not None else lan_addresses():
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.bind((address, 0))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, TTL)
            sock.setsockopt(
                socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(address)
            )
            sock.setblocking(False)
            sockets.append(sock)
        except OSError:
            continue
    request = search_request(target)
    found: list[Message] = []
    try:
        for attempt in range(2):
            for sock in sockets:
                try:
                    sock.sendto(request, destination)
                except OSError:
                    pass
            if attempt == 0:
                time.sleep(0.3)
        import selectors

        selector = selectors.DefaultSelector()
        for sock in sockets:
            selector.register(sock, selectors.EVENT_READ)
        end = time.monotonic() + window
        while sockets and (left := end - time.monotonic()) > 0:
            for key, _ in selector.select(timeout=left):
                try:
                    data, (sender, _port) = key.fileobj.recvfrom(MAX_DATAGRAM)  # type: ignore[union-attr]
                except OSError:
                    continue
                msg = parse(data, sender)
                if msg is not None:
                    found.append(msg)
        selector.close()
    finally:
        for sock in sockets:
            sock.close()
    return found


def local_address_for(host: str, port: int = 9) -> str:
    """This computer's address on the network that reaches `host`."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect((host, port))
            return sock.getsockname()[0]
        except OSError:
            addresses = lan_addresses()
            return addresses[0] if addresses else "127.0.0.1"
