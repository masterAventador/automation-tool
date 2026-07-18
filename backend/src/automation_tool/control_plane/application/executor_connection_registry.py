"""Single-process live Executor registry and safe online projections."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from automation_tool.control_plane.application.executor_connections import (
    BoundExecutorConnection,
    ExecutorArchitecture,
    ExecutorPlatform,
)
from automation_tool.control_plane.domain import (
    ExecutorConnectionId,
    ExecutorId,
    InstallationId,
)
from automation_tool.protocol import MAX_EXECUTOR_MESSAGE_BYTES
from automation_tool.protocol.limits import MAX_CROSS_RUNTIME_SEQUENCE

EXECUTOR_CONNECTION_REPLACED_CODE = 4409
EXECUTOR_CONNECTION_REPLACED_REASON = "Executor connection was replaced"
EXECUTOR_CONNECTION_SHUTDOWN_CODE = 1012
EXECUTOR_CONNECTION_SHUTDOWN_REASON = "Executor service is restarting"


class ExecutorConnectionRegistryRejected(ValueError):
    def __init__(self) -> None:
        super().__init__("Executor connection registry operation is rejected")


class StaleExecutorConnection(LookupError):
    def __init__(self) -> None:
        super().__init__("Executor connection is no longer current")


class ExecutorConnectionUnavailable(ConnectionError):
    def __init__(self) -> None:
        super().__init__("Executor connection is unavailable")


class ExecutorRegistryClock(Protocol):
    def now(self) -> datetime: ...


class SystemExecutorRegistryClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@runtime_checkable
class ExecutorConnectionChannel(Protocol):
    async def close(self, *, code: int, reason: str) -> None: ...

    async def send_text(self, data: str) -> None: ...


@dataclass(frozen=True, slots=True)
class OnlineExecutorConnection:
    connection_id: ExecutorConnectionId
    installation_id: InstallationId
    executor_id: ExecutorId
    protocol_version: str
    executor_version: str
    platform: ExecutorPlatform
    architecture: ExecutorArchitecture
    connected_at: datetime
    last_heartbeat_at: datetime
    last_sequence: int


@dataclass(frozen=True, slots=True)
class _RegisteredConnection:
    online: OnlineExecutorConnection
    channel: ExecutorConnectionChannel


def _valid_bound(bound: object) -> bool:
    return (
        isinstance(bound, BoundExecutorConnection)
        and type(bound.connection_id) is ExecutorConnectionId
        and type(bound.installation_id) is InstallationId
        and type(bound.executor_id) is ExecutorId
        and type(bound.protocol_version) is str
        and type(bound.executor_version) is str
        and isinstance(bound.platform, ExecutorPlatform)
        and isinstance(bound.architecture, ExecutorArchitecture)
        and type(bound.hello_sequence) is int
        and 1 <= bound.hello_sequence <= MAX_CROSS_RUNTIME_SEQUENCE
    )


class ExecutorConnectionRegistry:
    """Own exactly one current live Executor connection for each Installation."""

    def __init__(self, *, clock: ExecutorRegistryClock | None = None) -> None:
        self._clock = clock or SystemExecutorRegistryClock()
        self._lock = asyncio.Lock()
        self._connections: dict[InstallationId, _RegisteredConnection] = {}
        self._closed = False

    def _now(self) -> datetime:
        value: datetime | None = None
        try:
            candidate = self._clock.now()
            if isinstance(candidate, datetime) and candidate.utcoffset() is not None:
                value = candidate.astimezone(UTC)
        except Exception:
            value = None
        if value is None:
            raise ExecutorConnectionRegistryRejected
        return value

    async def register(
        self,
        bound: BoundExecutorConnection,
        channel: ExecutorConnectionChannel,
    ) -> OnlineExecutorConnection:
        if not _valid_bound(bound) or not isinstance(channel, ExecutorConnectionChannel):
            raise ExecutorConnectionRegistryRejected
        now = self._now()
        online = OnlineExecutorConnection(
            connection_id=bound.connection_id,
            installation_id=bound.installation_id,
            executor_id=bound.executor_id,
            protocol_version=bound.protocol_version,
            executor_version=bound.executor_version,
            platform=bound.platform,
            architecture=bound.architecture,
            connected_at=now,
            last_heartbeat_at=now,
            last_sequence=bound.hello_sequence,
        )
        previous: _RegisteredConnection | None
        async with self._lock:
            if self._closed:
                raise ExecutorConnectionRegistryRejected
            previous = self._connections.get(bound.installation_id)
            if previous is not None and previous.online.connection_id == bound.connection_id:
                raise ExecutorConnectionRegistryRejected
            self._connections[bound.installation_id] = _RegisteredConnection(
                online=online,
                channel=channel,
            )
        if previous is not None:
            with suppress(Exception):
                await previous.channel.close(
                    code=EXECUTOR_CONNECTION_REPLACED_CODE,
                    reason=EXECUTOR_CONNECTION_REPLACED_REASON,
                )
        return online

    async def record_heartbeat(
        self,
        bound: BoundExecutorConnection,
        *,
        sequence: int,
    ) -> OnlineExecutorConnection:
        if (
            not _valid_bound(bound)
            or type(sequence) is not int
            or not 1 <= sequence <= MAX_CROSS_RUNTIME_SEQUENCE
        ):
            raise ExecutorConnectionRegistryRejected
        now = self._now()
        async with self._lock:
            registered = self._connections.get(bound.installation_id)
            if registered is None or registered.online.connection_id != bound.connection_id:
                raise StaleExecutorConnection
            if (
                sequence <= registered.online.last_sequence
                or now < registered.online.last_heartbeat_at
            ):
                raise ExecutorConnectionRegistryRejected
            online = replace(
                registered.online,
                last_heartbeat_at=now,
                last_sequence=sequence,
            )
            self._connections[bound.installation_id] = replace(registered, online=online)
            return online

    async def snapshot(
        self,
        installation_id: InstallationId,
    ) -> OnlineExecutorConnection | None:
        if type(installation_id) is not InstallationId:
            raise ExecutorConnectionRegistryRejected
        async with self._lock:
            registered = self._connections.get(installation_id)
            return None if registered is None else registered.online

    async def list_online(self) -> tuple[OnlineExecutorConnection, ...]:
        async with self._lock:
            return tuple(
                self._connections[installation_id].online
                for installation_id in sorted(
                    self._connections,
                    key=lambda value: value.uuid,
                )
            )

    async def is_current(self, bound: BoundExecutorConnection) -> bool:
        if not _valid_bound(bound):
            raise ExecutorConnectionRegistryRejected
        async with self._lock:
            registered = self._connections.get(bound.installation_id)
            return registered is not None and registered.online.connection_id == bound.connection_id

    async def unregister(self, bound: BoundExecutorConnection) -> bool:
        if not _valid_bound(bound):
            raise ExecutorConnectionRegistryRejected
        async with self._lock:
            registered = self._connections.get(bound.installation_id)
            if registered is None or registered.online.connection_id != bound.connection_id:
                return False
            del self._connections[bound.installation_id]
            return True

    async def send_current(
        self,
        *,
        installation_id: InstallationId,
        connection_id: ExecutorConnectionId,
        source: str,
    ) -> None:
        encoded_length: int | None = None
        if type(source) is str:
            try:
                encoded_length = len(source.encode("utf-8"))
            except UnicodeEncodeError:
                encoded_length = None
        if (
            type(installation_id) is not InstallationId
            or type(connection_id) is not ExecutorConnectionId
            or encoded_length is None
            or not 1 <= encoded_length <= MAX_EXECUTOR_MESSAGE_BYTES
        ):
            raise ExecutorConnectionRegistryRejected
        async with self._lock:
            registered = self._connections.get(installation_id)
            if registered is None or registered.online.connection_id != connection_id:
                raise StaleExecutorConnection
            channel = registered.channel
        failed = False
        try:
            await channel.send_text(source)
        except Exception:
            failed = True
        if failed:
            raise ExecutorConnectionUnavailable
        async with self._lock:
            current = self._connections.get(installation_id)
            if current is None or current.online.connection_id != connection_id:
                raise StaleExecutorConnection

    async def shutdown(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            registered = tuple(self._connections.values())
            self._connections.clear()
        for connection in registered:
            with suppress(Exception):
                await connection.channel.close(
                    code=EXECUTOR_CONNECTION_SHUTDOWN_CODE,
                    reason=EXECUTOR_CONNECTION_SHUTDOWN_REASON,
                )


__all__ = [
    "EXECUTOR_CONNECTION_REPLACED_CODE",
    "EXECUTOR_CONNECTION_REPLACED_REASON",
    "EXECUTOR_CONNECTION_SHUTDOWN_CODE",
    "EXECUTOR_CONNECTION_SHUTDOWN_REASON",
    "ExecutorConnectionChannel",
    "ExecutorConnectionRegistry",
    "ExecutorConnectionRegistryRejected",
    "ExecutorConnectionUnavailable",
    "OnlineExecutorConnection",
    "StaleExecutorConnection",
]
