"""webapi/link.py — the Mornye Link routes (see link_server.py).

Answers as the Rust desktop's `crates/link/src/api.rs` does, so the phone
app cannot tell the two apart: the same paths, status codes and error
bodies (`{"error": "<code>"}`), and the same WebSocket close codes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from ..core import link
from ..core import subsonic as sub
from .subsonic import build_subsonic_router

if TYPE_CHECKING:
    from ..link_server import LinkServer

logger = logging.getLogger(__name__)

HELLO_TIMEOUT_S = 60.0
#: Link error codes as HTTP statuses (rust_backend/crates/link api.rs).
_STATUS = {"invalid": 400, "not_found": 404, "unavailable": 503, "timeout": 504}
CLOSE_UNAUTHORIZED = 4401
CLOSE_REPLACED = 4409
MAX_BODY = 8 << 20


def _error(status: int, code: str, message: str = "") -> JSONResponse:
    body: dict[str, Any] = {"error": code}
    if message:
        body["message"] = message
    return JSONResponse(body, status_code=status)


def _peer(request: Request) -> str:
    return request.client.host if request.client else ""


async def _receive_file(
    server: LinkServer, request: Request, name: str
) -> JSONResponse:
    """``PUT files/<32 hex>.<ext>``: a song the phone sends for a hand-off,
    kept in ``received/`` where hand-offs find it by its file id."""
    import os
    import tempfile

    from ..core.connect.link_bridge import MAX_UPLOAD

    target = server.connect.upload_path(name)
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_UPLOAD:
        return _error(413, "too_large")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".upload-")
    size = 0
    try:
        with os.fdopen(fd, "wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise OverflowError
                out.write(chunk)
        os.replace(tmp, target)
    except OverflowError:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        return _error(413, "too_large")
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return JSONResponse({"name": target.name, "size": size})


def build_link_app(server: LinkServer) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    # -- Subsonic, opened by the paired devices' keys ------------------------

    def authenticate(params: dict[str, str]) -> str:
        try:
            device = link.authenticate_subsonic(server.store, params)
        except link.LinkAuthError as exc:
            raise sub.AuthError(exc.code, str(exc)) from None
        server.store.touch(device.deviceId)
        # The phone shows this computer's library, so it sees the same
        # stars and plays as the person using it.
        return sub.username()

    app.include_router(build_subsonic_router(lambda: server.roots(), authenticate))

    # -- /mornye/v1 ------------------------------------------------------------

    @app.get("/mornye/v1/info")
    async def info() -> JSONResponse:
        return JSONResponse(server.info())

    @app.post("/mornye/v1/pair")
    async def pair(request: Request) -> JSONResponse:
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_BODY:
            return _error(413, "too_large")
        try:
            body = json.loads(await request.body() or b"null")
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return _error(400, "invalid", "The body must be a JSON object")

        def text(key: str) -> str:
            value = body.get(key)
            return value.strip() if isinstance(value, str) else ""

        device_id, proof = text("deviceId"), text("proof")
        if not device_id or not proof:
            return _error(400, "invalid", "deviceId and proof are required")
        if len(device_id) > link.MAX_DEVICE_ID:
            return _error(400, "invalid", "deviceId is too long")
        name = text("name") or device_id
        platform = text("platform") or "unknown"
        try:
            record = server.pairing.attempt(
                _peer(request),
                server.fingerprint,
                device_id,
                proof,
                lambda: server.store.upsert(device_id, name, platform),
            )
        except link.PairError as exc:
            status = {"rate_limited": 429, "pairing_closed": 403, "bad_proof": 403}.get(
                exc.code, 500
            )
            if status == 500:
                # Unexpected: the detail goes to the log, not to the client.
                logger.warning("[link] pairing failed: %s", exc)
                return _error(500, exc.code, "Pairing failed on this computer")
            return _error(status, exc.code)
        logger.info("[link] paired %s (%s)", name, platform)
        return JSONResponse(
            {
                "apiKey": record.apiKey,
                "serverId": server.store.server_id,
                "name": server.name,
                "fingerprint": server.fingerprint,
            }
        )

    @app.websocket("/mornye/v1/ws")
    async def socket(ws: WebSocket) -> None:
        device = link.authenticate_bearer(server.store, ws.headers.get("authorization"))
        if device is None:
            # Refused before the upgrade, as the Rust server does (401).
            try:
                await ws.send_denial_response(_error(401, "unauthorized"))
            except Exception:
                with contextlib.suppress(Exception):
                    await ws.close(code=CLOSE_UNAUTHORIZED)
            return
        await ws.accept()
        try:
            hello = json.loads(
                await asyncio.wait_for(ws.receive_text(), HELLO_TIMEOUT_S)
            )
        except (TimeoutError, ValueError, WebSocketDisconnect, RuntimeError):
            hello = None
        if (
            not isinstance(hello, dict)
            or hello.get("type") != "hello"
            or hello.get("deviceId") != device.deviceId
        ):
            with contextlib.suppress(Exception):
                await ws.close(code=CLOSE_UNAUTHORIZED, reason="hello required")
            return
        client = {
            "deviceId": device.deviceId,
            "name": str(hello.get("name") or device.name),
            "platform": str(hello.get("platform") or device.platform),
            "appVersion": str(hello.get("appVersion") or ""),
            "connectedMs": link.now_ms(),
        }
        # One connection per device: the newer one takes over.
        replaced = server.register_client(device.deviceId, ws, client)
        if replaced is not None:
            with contextlib.suppress(Exception):
                await replaced.close(code=CLOSE_REPLACED, reason="replaced")
        server.store.touch(device.deviceId)
        try:
            welcome = {
                "type": "welcome",
                "serverId": server.store.server_id,
                "name": server.name,
                "protocol": link.PROTOCOL,
            }
            await ws.send_text(json.dumps(server.announce(welcome)))
            # The phone is a Connect device from here on, and is sent the
            # player's state at once (link_desktop_host.dart).
            await run_in_threadpool(
                server.connect.phone_connected, device.deviceId, str(client["name"])
            )
            while True:
                text = await ws.receive_text()
                server.store.touch(device.deviceId)
                try:
                    message = json.loads(text)
                except ValueError:
                    continue
                await run_in_threadpool(
                    server.connect.phone_message, device.deviceId, message
                )
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            server.unregister_client(device.deviceId, ws)
            if device.deviceId not in server.clients:
                await run_in_threadpool(
                    server.connect.phone_disconnected, device.deviceId
                )

    @app.api_route("/mornye/v1/{op:path}", methods=["GET", "POST", "PUT"])
    async def forward(request: Request, op: str) -> JSONResponse:
        device = link.authenticate_bearer(
            server.store, request.headers.get("authorization")
        )
        if device is None:
            return _error(401, "unauthorized")
        if op in ("info", "pair", "ws"):
            return _error(405, "method_not_allowed")
        server.store.touch(device.deviceId)
        from ..core.connect.model import CommandError

        method = request.method
        try:
            if method == "PUT" and op.startswith("files/"):
                return await _receive_file(server, request, op.removeprefix("files/"))
            if method == "GET":
                args: dict[str, Any] = dict(request.query_params)
            else:
                declared = request.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > MAX_BODY:
                    return _error(413, "too_large")
                raw = await request.body()
                try:
                    args = json.loads(raw) if raw.strip() else {}
                except ValueError:
                    return _error(400, "invalid", "The body must be a JSON object")
                if not isinstance(args, dict):
                    return _error(400, "invalid", "The body must be a JSON object")
            args["_method"] = method
            result = await run_in_threadpool(
                server.connect.op, op, method, args, device.deviceId
            )
            return JSONResponse(result)
        except CommandError as exc:
            return _error(_STATUS.get(exc.code, 500), exc.code, exc.message)
        except Exception:
            logger.warning("[link] %s %s failed", method, op, exc_info=True)
            return _error(503, "unavailable", "Something went wrong on this computer")

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def not_found(path: str) -> JSONResponse:
        return _error(404, "not_found")

    return app


__all__ = ["build_link_app"]
