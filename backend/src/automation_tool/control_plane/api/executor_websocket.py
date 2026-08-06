"""Authenticated outbound WebSocket entry for the Local Executor."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Final, cast

from fastapi import APIRouter, WebSocket
from starlette.responses import Response
from starlette.websockets import WebSocketDisconnect

from automation_tool.control_plane.application.action_execution_orchestration import (
    ActionExecutionOrchestrationRejected,
    ActionExecutionOrchestrationService,
    ActionExecutionOrchestrationUnavailable,
)
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
from automation_tool.control_plane.application.platform_session_health import (
    PlatformSessionHealthRejected,
    PlatformSessionHealthService,
    PlatformSessionHealthUnavailable,
)
from automation_tool.control_plane.application.task_command_delivery import (
    TaskCommandDeliveryRejected,
    TaskCommandDeliveryService,
    TaskCommandDeliveryUnavailable,
)
from automation_tool.control_plane.application.task_discovery import (
    TaskDiscoveryConvergenceService,
    TaskDiscoveryRejected,
    TaskDiscoveryUnavailable,
)
from automation_tool.control_plane.application.task_event_convergence import (
    TaskEventConvergenceRejected,
    TaskEventConvergenceService,
    TaskEventConvergenceUnavailable,
)
from automation_tool.protocol import (
    ExecutorLifecycleEnvelope,
    PlatformSessionHealthEnvelope,
    TaskCommandResultEnvelope,
    TaskDiscoveryBatchEnvelope,
    TaskDiscoveryCompletedEnvelope,
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



def _log_session_health_refusal(reason: str) -> None:
    """Say which rule refused, in whole literal lines.

    The Executor is told only "protocol rejected", by design, so the server log
    is the one place the reason can exist — and one message per reason is how it
    gets there without a formatted argument. Control Plane logging accepts only
    literal messages (`test_control_plane_production_logging_calls_accept_only_literal_messages`)
    because a formatted one carries whatever was interpolated into it; the
    redaction layer would replace this reason with `[REDACTED]` and the line
    would say nothing at all.
    """

    if reason == "sent_at_is_in_the_future":
        logger.error("Executor Session health refused: sent_at is ahead of this server")
    elif reason == "deadline_has_passed":
        logger.error("Executor Session health refused: the message deadline had passed")
    elif reason == "observed_after_sent":
        logger.error("Executor Session health refused: observed_at is after sent_at")
    elif reason == "not_a_session_health_envelope":
        logger.error("Executor Session health refused: the message was not a health envelope")
    elif reason == "installation_is_not_active":
        logger.error("Executor Session health refused: the Installation is not active")
    elif reason == "revision_went_backwards":
        logger.error("Executor Session health refused: the session revision went backwards")
    elif reason == "same_revision_observed_earlier":
        logger.error("Executor Session health refused: same revision, observed earlier")
    elif reason == "same_observation_different_state":
        logger.error("Executor Session health refused: same observation, different state")
    elif reason == "circuit_reopened_without_cause":
        logger.error("Executor Session health refused: the circuit reopened without cause")
    elif reason == "newer_revision_observed_no_later":
        logger.error("Executor Session health refused: newer revision observed no later")
    elif reason == "received_before_last_update":
        logger.error("Executor Session health refused: received before the last update")
    elif reason == "not_a_pending_health_record":
        logger.error("Executor Session health refused: the pending record was malformed")
    else:
        logger.error("Executor Session health refused for a reason with no message")


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
    action_execution = websocket.app.state.action_execution_orchestration_service
    event_convergence = websocket.app.state.task_event_convergence_service
    discovery_convergence = websocket.app.state.task_discovery_convergence_service
    session_health = websocket.app.state.platform_session_health_service
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

            if action_execution is not None:
                if not isinstance(action_execution, ActionExecutionOrchestrationService):
                    await _close(
                        websocket,
                        code=EXECUTOR_CLOSE_INTERNAL_ERROR,
                        reason=_INTERNAL_ERROR_REASON,
                    )
                    return
                try:
                    await action_execution.advance(
                        bound.installation_id,
                        bound.executor_id,
                    )
                except (
                    ActionExecutionOrchestrationRejected,
                    ActionExecutionOrchestrationUnavailable,
                ):
                    logger.error("Action execution orchestration failed")
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
                logger.error("Executor sent a non-text frame")
                await _close(
                    websocket,
                    code=EXECUTOR_CLOSE_PROTOCOL_REJECTED,
                    reason=_PROTOCOL_REJECTED_REASON,
                )
                return

            try:
                message = service.validate_inbound_message(bound, source)
            except ExecutorConnectionRejected:
                logger.error("Executor sent a message this connection cannot accept")
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
                    logger.error("Executor heartbeat sequence was refused")
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

            if isinstance(message, PlatformSessionHealthEnvelope):
                if session_health is None:
                    logger.error("Executor sent Session health but the service is not wired")
                    await _close(
                        websocket,
                        code=EXECUTOR_CLOSE_PROTOCOL_REJECTED,
                        reason=_PROTOCOL_REJECTED_REASON,
                    )
                    return
                if not isinstance(session_health, PlatformSessionHealthService):
                    await _close(
                        websocket,
                        code=EXECUTOR_CLOSE_INTERNAL_ERROR,
                        reason=_INTERNAL_ERROR_REASON,
                    )
                    return
                try:
                    await session_health.receive(message)
                except PlatformSessionHealthRejected as rejection:
                    _log_session_health_refusal(rejection.reason)
                    await _close(
                        websocket,
                        code=EXECUTOR_CLOSE_PROTOCOL_REJECTED,
                        reason=_PROTOCOL_REJECTED_REASON,
                    )
                    return
                except PlatformSessionHealthUnavailable:
                    logger.error("Executor platform Session health persistence failed")
                    await _close(
                        websocket,
                        code=EXECUTOR_CLOSE_INTERNAL_ERROR,
                        reason=_INTERNAL_ERROR_REASON,
                    )
                    return
                except Exception:
                    logger.error("Executor platform Session health convergence failed")
                    await _close(
                        websocket,
                        code=EXECUTOR_CLOSE_INTERNAL_ERROR,
                        reason=_INTERNAL_ERROR_REASON,
                    )
                    return
                continue

            if isinstance(
                message,
                (TaskDiscoveryBatchEnvelope, TaskDiscoveryCompletedEnvelope),
            ):
                if discovery_convergence is None:
                    await _close(
                        websocket,
                        code=EXECUTOR_CLOSE_PROTOCOL_REJECTED,
                        reason=_PROTOCOL_REJECTED_REASON,
                    )
                    return
                if not isinstance(discovery_convergence, TaskDiscoveryConvergenceService):
                    await _close(
                        websocket,
                        code=EXECUTOR_CLOSE_INTERNAL_ERROR,
                        reason=_INTERNAL_ERROR_REASON,
                    )
                    return
                try:
                    if isinstance(message, TaskDiscoveryBatchEnvelope):
                        await discovery_convergence.receive_batch(message)
                    else:
                        await discovery_convergence.receive_completed(message)
                except TaskDiscoveryRejected:
                    await _close(
                        websocket,
                        code=EXECUTOR_CLOSE_PROTOCOL_REJECTED,
                        reason=_PROTOCOL_REJECTED_REASON,
                    )
                    return
                except TaskDiscoveryUnavailable:
                    logger.error("Executor Task discovery persistence failed")
                    await _close(
                        websocket,
                        code=EXECUTOR_CLOSE_INTERNAL_ERROR,
                        reason=_INTERNAL_ERROR_REASON,
                    )
                    return
                except Exception:
                    logger.error("Executor Task discovery convergence failed")
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
