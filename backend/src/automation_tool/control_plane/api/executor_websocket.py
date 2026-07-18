"""Authenticated outbound WebSocket entry for the Local Executor."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Final, cast

from fastapi import APIRouter, WebSocket
from starlette.responses import Response
from starlette.websockets import WebSocketDisconnect

from automation_tool.control_plane.application.executor_connections import (
    EXECUTOR_WEBSOCKET_SUBPROTOCOL,
    ExecutorConnectionRejected,
    ExecutorConnectionService,
)

EXECUTOR_CLOSE_AUTHENTICATION_REJECTED: Final = 4401
EXECUTOR_CLOSE_IDENTITY_REJECTED: Final = 4403
EXECUTOR_CLOSE_PROTOCOL_REJECTED: Final = 4406
EXECUTOR_CLOSE_HELLO_TIMEOUT: Final = 4408
EXECUTOR_CLOSE_INTERNAL_ERROR: Final = 1011

_AUTHENTICATION_REJECTED_REASON: Final = "Executor authentication is rejected"
_IDENTITY_REJECTED_REASON: Final = "Executor identity is rejected"
_PROTOCOL_REJECTED_REASON: Final = "Executor protocol is rejected"
_HELLO_TIMEOUT_REASON: Final = "Executor hello timed out"
_INTERNAL_ERROR_REASON: Final = "Executor connection failed"

logger = logging.getLogger(__name__)
router = APIRouter(tags=["executors"])


class _TextFrameRequired(ValueError):
    pass


def _offered_exact_subprotocol(websocket: WebSocket) -> bool:
    return websocket.scope.get("subprotocols") == [EXECUTOR_WEBSOCKET_SUBPROTOCOL]


def _session_token(websocket: WebSocket) -> str | None:
    headers = cast(list[tuple[bytes, bytes]], websocket.scope["headers"])
    values = [
        value.decode("latin-1") for name, value in headers if name.lower() == b"authorization"
    ]
    if len(values) != 1:
        return None
    scheme, separator, token = values[0].partition(" ")
    if (
        scheme.lower() != "bearer"
        or separator != " "
        or not token
        or any(character.isspace() for character in token)
    ):
        return None
    return token


async def _deny(websocket: WebSocket, status_code: int) -> None:
    response = Response(
        status_code=status_code,
        headers={"cache-control": "no-store"},
    )
    # Uvicorn's WebSocket implementations generate the denial body and its
    # Content-Length. Forwarding Starlette's automatic length would duplicate
    # the header and make the HTTP handshake invalid for strict clients.
    del response.headers["content-length"]
    await websocket.send_denial_response(response)


async def _close(websocket: WebSocket, *, code: int, reason: str) -> None:
    with suppress(RuntimeError):
        await websocket.close(code=code, reason=reason)


async def _receive_text(websocket: WebSocket) -> str:
    message = await websocket.receive()
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(
            code=message.get("code", 1000),
            reason=message.get("reason"),
        )
    source = message.get("text")
    if type(source) is not str:
        raise _TextFrameRequired
    return source


@router.websocket("/api/v1/executors/connect")
async def connect_executor(websocket: WebSocket) -> None:
    service = websocket.app.state.executor_connection_service
    if not isinstance(service, ExecutorConnectionService):
        await _deny(websocket, 503)
        return
    if not _offered_exact_subprotocol(websocket):
        await _deny(websocket, 403)
        return
    token = _session_token(websocket)
    if token is None:
        await _deny(websocket, 403)
        return
    try:
        authorized = await service.authorize(token)
    except ExecutorConnectionRejected:
        await _deny(websocket, 403)
        return
    except Exception:
        logger.error("Executor WebSocket authentication failed")
        await _deny(websocket, 503)
        return
    websocket.scope["headers"] = [
        (name, value)
        for name, value in websocket.scope["headers"]
        if name.lower() != b"authorization"
    ]
    del token

    await websocket.accept(subprotocol=EXECUTOR_WEBSOCKET_SUBPROTOCOL)
    hello_timeout = websocket.app.state.executor_connection_hello_timeout_seconds
    recheck_interval = websocket.app.state.executor_connection_recheck_interval_seconds
    try:
        source = await asyncio.wait_for(_receive_text(websocket), timeout=hello_timeout)
    except TimeoutError:
        await _close(
            websocket,
            code=EXECUTOR_CLOSE_HELLO_TIMEOUT,
            reason=_HELLO_TIMEOUT_REASON,
        )
        return
    except WebSocketDisconnect:
        return
    except _TextFrameRequired:
        await _close(
            websocket,
            code=EXECUTOR_CLOSE_PROTOCOL_REJECTED,
            reason=_PROTOCOL_REJECTED_REASON,
        )
        return

    try:
        bound = service.bind_hello(authorized, source)
    except ExecutorConnectionRejected:
        await _close(
            websocket,
            code=EXECUTOR_CLOSE_IDENTITY_REJECTED,
            reason=_IDENTITY_REJECTED_REASON,
        )
        return
    except Exception:
        logger.error("Executor WebSocket hello binding failed")
        await _close(
            websocket,
            code=EXECUTOR_CLOSE_INTERNAL_ERROR,
            reason=_INTERNAL_ERROR_REASON,
        )
        return

    while True:
        try:
            await service.reauthorize(bound)
        except ExecutorConnectionRejected:
            await _close(
                websocket,
                code=EXECUTOR_CLOSE_AUTHENTICATION_REJECTED,
                reason=_AUTHENTICATION_REJECTED_REASON,
            )
            return
        except Exception:
            logger.error("Executor WebSocket reauthentication failed")
            await _close(
                websocket,
                code=EXECUTOR_CLOSE_INTERNAL_ERROR,
                reason=_INTERNAL_ERROR_REASON,
            )
            return

        try:
            source = await asyncio.wait_for(
                _receive_text(websocket),
                timeout=recheck_interval,
            )
        except TimeoutError:
            continue
        except WebSocketDisconnect:
            return
        except _TextFrameRequired:
            await _close(
                websocket,
                code=EXECUTOR_CLOSE_PROTOCOL_REJECTED,
                reason=_PROTOCOL_REJECTED_REASON,
            )
            return

        try:
            service.validate_lifecycle_message(bound, source)
        except ExecutorConnectionRejected:
            await _close(
                websocket,
                code=EXECUTOR_CLOSE_PROTOCOL_REJECTED,
                reason=_PROTOCOL_REJECTED_REASON,
            )
            return
        except Exception:
            logger.error("Executor WebSocket lifecycle validation failed")
            await _close(
                websocket,
                code=EXECUTOR_CLOSE_INTERNAL_ERROR,
                reason=_INTERNAL_ERROR_REASON,
            )
            return


__all__ = [
    "EXECUTOR_CLOSE_AUTHENTICATION_REJECTED",
    "EXECUTOR_CLOSE_HELLO_TIMEOUT",
    "EXECUTOR_CLOSE_IDENTITY_REJECTED",
    "EXECUTOR_CLOSE_INTERNAL_ERROR",
    "EXECUTOR_CLOSE_PROTOCOL_REJECTED",
    "router",
]
