"""Authenticated outbound WebSocket entry for the Local Executor."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Final, cast

from fastapi import APIRouter, WebSocket
from starlette.responses import Response
from starlette.websockets import WebSocketDisconnect

from automation_tool.control_plane.application.executor_connection_registry import (
    EXECUTOR_CONNECTION_REPLACED_CODE,
    EXECUTOR_CONNECTION_REPLACED_REASON,
    ExecutorConnectionRegistry,
    ExecutorConnectionRegistryRejected,
    StaleExecutorConnection,
)
from automation_tool.control_plane.application.executor_connections import (
    EXECUTOR_WEBSOCKET_SUBPROTOCOL,
    ExecutorConnectionRejected,
    ExecutorConnectionService,
)
from automation_tool.control_plane.application.task_command_delivery import (
    TaskCommandDeliveryRejected,
    TaskCommandDeliveryService,
    TaskCommandDeliveryUnavailable,
)
from automation_tool.control_plane.application.task_event_convergence import (
    TaskEventConvergenceRejected,
    TaskEventConvergenceService,
    TaskEventConvergenceUnavailable,
)
from automation_tool.protocol import (
    ExecutorLifecycleEnvelope,
    TaskCommandResultEnvelope,
    TaskEventEnvelope,
)

EXECUTOR_CLOSE_AUTHENTICATION_REJECTED: Final = 4401
EXECUTOR_CLOSE_IDENTITY_REJECTED: Final = 4403
EXECUTOR_CLOSE_PROTOCOL_REJECTED: Final = 4406
EXECUTOR_CLOSE_HELLO_TIMEOUT: Final = 4408
EXECUTOR_CLOSE_CONNECTION_REPLACED: Final = EXECUTOR_CONNECTION_REPLACED_CODE
EXECUTOR_CLOSE_INTERNAL_ERROR: Final = 1011

_AUTHENTICATION_REJECTED_REASON: Final = "Executor authentication is rejected"
_IDENTITY_REJECTED_REASON: Final = "Executor identity is rejected"
_PROTOCOL_REJECTED_REASON: Final = "Executor protocol is rejected"
_HELLO_TIMEOUT_REASON: Final = "Executor hello timed out"
_CONNECTION_REPLACED_REASON: Final = EXECUTOR_CONNECTION_REPLACED_REASON
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
    registry = websocket.app.state.executor_connection_registry
    delivery = websocket.app.state.task_command_delivery_service
    event_convergence = websocket.app.state.task_event_convergence_service
    if not isinstance(service, ExecutorConnectionService) or not isinstance(
        registry, ExecutorConnectionRegistry
    ):
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

    try:
        await registry.register(bound, websocket)
    except ExecutorConnectionRegistryRejected:
        await _close(
            websocket,
            code=EXECUTOR_CLOSE_INTERNAL_ERROR,
            reason=_INTERNAL_ERROR_REASON,
        )
        return
    except Exception:
        logger.error("Executor WebSocket registration failed")
        await _close(
            websocket,
            code=EXECUTOR_CLOSE_INTERNAL_ERROR,
            reason=_INTERNAL_ERROR_REASON,
        )
        return

    try:
        recover_delivered = True
        while True:
            try:
                if not await registry.is_current(bound):
                    raise StaleExecutorConnection
            except StaleExecutorConnection:
                await _close(
                    websocket,
                    code=EXECUTOR_CLOSE_CONNECTION_REPLACED,
                    reason=_CONNECTION_REPLACED_REASON,
                )
                return
            except Exception:
                logger.error("Executor WebSocket registry check failed")
                await _close(
                    websocket,
                    code=EXECUTOR_CLOSE_INTERNAL_ERROR,
                    reason=_INTERNAL_ERROR_REASON,
                )
                return

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

            if delivery is not None:
                if not isinstance(delivery, TaskCommandDeliveryService):
                    await _close(
                        websocket,
                        code=EXECUTOR_CLOSE_INTERNAL_ERROR,
                        reason=_INTERNAL_ERROR_REASON,
                    )
                    return
                try:
                    await delivery.dispatch_current(
                        installation_id=bound.installation_id,
                        executor_id=bound.executor_id,
                        connection_id=bound.connection_id,
                        recover_delivered=recover_delivered,
                    )
                    recover_delivered = False
                except (TaskCommandDeliveryRejected, TaskCommandDeliveryUnavailable):
                    logger.error("Executor command dispatch failed")
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
                message = service.validate_inbound_message(bound, source)
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

            if isinstance(message, ExecutorLifecycleEnvelope):
                try:
                    await registry.record_heartbeat(bound, sequence=message.sequence)
                except StaleExecutorConnection:
                    await _close(
                        websocket,
                        code=EXECUTOR_CLOSE_CONNECTION_REPLACED,
                        reason=_CONNECTION_REPLACED_REASON,
                    )
                    return
                except ExecutorConnectionRegistryRejected:
                    await _close(
                        websocket,
                        code=EXECUTOR_CLOSE_PROTOCOL_REJECTED,
                        reason=_PROTOCOL_REJECTED_REASON,
                    )
                    return
                except Exception:
                    logger.error("Executor WebSocket heartbeat projection failed")
                    await _close(
                        websocket,
                        code=EXECUTOR_CLOSE_INTERNAL_ERROR,
                        reason=_INTERNAL_ERROR_REASON,
                    )
                    return
                continue

            if isinstance(message, TaskEventEnvelope):
                if event_convergence is None:
                    await _close(
                        websocket,
                        code=EXECUTOR_CLOSE_PROTOCOL_REJECTED,
                        reason=_PROTOCOL_REJECTED_REASON,
                    )
                    return
                if not isinstance(event_convergence, TaskEventConvergenceService):
                    await _close(
                        websocket,
                        code=EXECUTOR_CLOSE_INTERNAL_ERROR,
                        reason=_INTERNAL_ERROR_REASON,
                    )
                    return
                try:
                    await event_convergence.receive(message)
                except TaskEventConvergenceRejected:
                    await _close(
                        websocket,
                        code=EXECUTOR_CLOSE_PROTOCOL_REJECTED,
                        reason=_PROTOCOL_REJECTED_REASON,
                    )
                    return
                except TaskEventConvergenceUnavailable:
                    logger.error("Executor Task event persistence failed")
                    await _close(
                        websocket,
                        code=EXECUTOR_CLOSE_INTERNAL_ERROR,
                        reason=_INTERNAL_ERROR_REASON,
                    )
                    return
                except Exception:
                    logger.error("Executor Task event convergence failed")
                    await _close(
                        websocket,
                        code=EXECUTOR_CLOSE_INTERNAL_ERROR,
                        reason=_INTERNAL_ERROR_REASON,
                    )
                    return
                continue

            if not isinstance(message, TaskCommandResultEnvelope) or not isinstance(
                delivery, TaskCommandDeliveryService
            ):
                await _close(
                    websocket,
                    code=EXECUTOR_CLOSE_PROTOCOL_REJECTED,
                    reason=_PROTOCOL_REJECTED_REASON,
                )
                return
            try:
                await delivery.acknowledge(message)
            except TaskCommandDeliveryRejected:
                await _close(
                    websocket,
                    code=EXECUTOR_CLOSE_PROTOCOL_REJECTED,
                    reason=_PROTOCOL_REJECTED_REASON,
                )
                return
            except TaskCommandDeliveryUnavailable:
                logger.error("Executor command acknowledgement failed")
                await _close(
                    websocket,
                    code=EXECUTOR_CLOSE_INTERNAL_ERROR,
                    reason=_INTERNAL_ERROR_REASON,
                )
                return
    finally:
        try:
            await registry.unregister(bound)
        except Exception:
            logger.error("Executor WebSocket registry cleanup failed")


__all__ = [
    "EXECUTOR_CLOSE_AUTHENTICATION_REJECTED",
    "EXECUTOR_CLOSE_CONNECTION_REPLACED",
    "EXECUTOR_CLOSE_HELLO_TIMEOUT",
    "EXECUTOR_CLOSE_IDENTITY_REJECTED",
    "EXECUTOR_CLOSE_INTERNAL_ERROR",
    "EXECUTOR_CLOSE_PROTOCOL_REJECTED",
    "router",
]
