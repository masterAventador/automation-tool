from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest

from automation_tool.control_plane.application.device_sessions import ParsedDeviceSession
from automation_tool.control_plane.application.executor_connection_registry import (
    EXECUTOR_CONNECTION_REPLACED_CODE,
    EXECUTOR_CONNECTION_REPLACED_REASON,
    EXECUTOR_CONNECTION_SHUTDOWN_CODE,
    EXECUTOR_CONNECTION_SHUTDOWN_REASON,
    ExecutorConnectionRegistry,
    ExecutorConnectionRegistryRejected,
    ExecutorConnectionUnavailable,
    StaleExecutorConnection,
)
from automation_tool.control_plane.application.executor_connections import (
    AuthorizedExecutorConnection,
    BoundExecutorConnection,
    ExecutorArchitecture,
    ExecutorPlatform,
)
from automation_tool.control_plane.domain import (
    ExecutorConnectionId,
    ExecutorId,
    InstallationId,
)

NOW = datetime(2026, 7, 18, 18, 0, tzinfo=UTC)


@dataclass
class MutableClock:
    value: datetime = NOW

    def now(self) -> datetime:
        return self.value


class FailingClock:
    def now(self) -> datetime:
        raise RuntimeError("private clock failure")


@dataclass
class MemoryConnection:
    closes: list[tuple[int, str]] = field(default_factory=list)
    sent: list[str] = field(default_factory=list)
    fail_close: bool = False
    fail_send: bool = False

    async def close(self, *, code: int, reason: str) -> None:
        self.closes.append((code, reason))
        if self.fail_close:
            raise RuntimeError("private close failure")

    async def send_text(self, data: str) -> None:
        self.sent.append(data)
        if self.fail_send:
            raise RuntimeError("private send failure")


class ReplacingConnection(MemoryConnection):
    def __init__(
        self,
        registry: ExecutorConnectionRegistry,
        replacement: BoundExecutorConnection,
    ) -> None:
        super().__init__()
        self._registry = registry
        self._replacement = replacement

    async def send_text(self, data: str) -> None:
        await super().send_text(data)
        await self._registry.register(self._replacement, MemoryConnection())


def bound_connection(
    *,
    installation_id: InstallationId | None = None,
    executor_id: ExecutorId | None = None,
    connection_id: ExecutorConnectionId | None = None,
    hello_sequence: int = 1,
) -> BoundExecutorConnection:
    target_installation = installation_id or InstallationId.new()
    authorization = AuthorizedExecutorConnection(
        installation_id=target_installation,
        session_id=UUID("123e4567-e89b-42d3-a456-426614174003"),
        credential_id=UUID("123e4567-e89b-42d3-a456-426614174007"),
        credential_version=1,
        session_expires_at=NOW + timedelta(minutes=5),
        _presented_session=ParsedDeviceSession(
            session_id=UUID("123e4567-e89b-42d3-a456-426614174003"),
            secret_digest=b"a" * 32,
        ),
    )
    return BoundExecutorConnection(
        connection_id=connection_id or ExecutorConnectionId.new(),
        installation_id=target_installation,
        executor_id=executor_id or ExecutorId.new(),
        protocol_version="1.0",
        executor_version="0.1.0",
        platform=ExecutorPlatform.MACOS,
        architecture=ExecutorArchitecture.ARM64,
        hello_sequence=hello_sequence,
        _authorization=authorization,
    )


@pytest.mark.asyncio
async def test_registry_projects_one_safe_online_connection_per_installation() -> None:
    clock = MutableClock()
    registry = ExecutorConnectionRegistry(clock=clock)
    bound = bound_connection()
    channel = MemoryConnection()

    online = await registry.register(bound, channel)

    assert online.connection_id == bound.connection_id
    assert online.installation_id == bound.installation_id
    assert online.executor_id == bound.executor_id
    assert online.protocol_version == "1.0"
    assert online.executor_version == "0.1.0"
    assert online.platform is ExecutorPlatform.MACOS
    assert online.architecture is ExecutorArchitecture.ARM64
    assert online.connected_at == NOW
    assert online.last_heartbeat_at == NOW
    assert online.last_sequence == 1
    assert await registry.snapshot(bound.installation_id) == online
    assert await registry.list_online() == (online,)
    assert "MemoryConnection" not in repr(online)
    assert "secret" not in repr(online).lower()


@pytest.mark.asyncio
async def test_registry_default_clock_projects_utc_server_time() -> None:
    registry = ExecutorConnectionRegistry()
    bound = bound_connection()

    online = await registry.register(bound, MemoryConnection())

    assert online.connected_at.utcoffset() == timedelta(0)
    assert online.last_heartbeat_at == online.connected_at


@pytest.mark.asyncio
async def test_new_connection_atomically_replaces_old_and_stale_cleanup_cannot_remove_it() -> None:
    clock = MutableClock()
    registry = ExecutorConnectionRegistry(clock=clock)
    installation_id = InstallationId.new()
    first = bound_connection(installation_id=installation_id)
    second = bound_connection(installation_id=installation_id)
    first_channel = MemoryConnection()

    await registry.register(first, first_channel)
    replacement = await registry.register(second, MemoryConnection())

    assert replacement.connection_id == second.connection_id
    assert first_channel.closes == [
        (EXECUTOR_CONNECTION_REPLACED_CODE, EXECUTOR_CONNECTION_REPLACED_REASON)
    ]
    assert await registry.snapshot(installation_id) == replacement
    assert await registry.is_current(first) is False
    assert await registry.is_current(second) is True
    with pytest.raises(StaleExecutorConnection) as captured:
        await registry.record_heartbeat(first, sequence=2)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert await registry.unregister(first) is False
    assert await registry.snapshot(installation_id) == replacement


@pytest.mark.asyncio
async def test_heartbeat_advances_server_time_and_sequence_only_for_current_connection() -> None:
    clock = MutableClock()
    registry = ExecutorConnectionRegistry(clock=clock)
    bound = bound_connection(hello_sequence=7)
    await registry.register(bound, MemoryConnection())
    clock.value = NOW + timedelta(seconds=3)

    heartbeat = await registry.record_heartbeat(bound, sequence=8)

    assert heartbeat.connected_at == NOW
    assert heartbeat.last_heartbeat_at == NOW + timedelta(seconds=3)
    assert heartbeat.last_sequence == 8
    for invalid in (8, 7, cast(int, True), 2**53):
        with pytest.raises(ExecutorConnectionRegistryRejected):
            await registry.record_heartbeat(bound, sequence=invalid)
    assert await registry.snapshot(bound.installation_id) == heartbeat
    assert await registry.unregister(bound) is True
    assert await registry.snapshot(bound.installation_id) is None


@pytest.mark.asyncio
async def test_distinct_installations_coexist_and_shutdown_closes_every_connection() -> None:
    registry = ExecutorConnectionRegistry(clock=MutableClock())
    first = bound_connection()
    second = bound_connection()
    first_channel = MemoryConnection(fail_close=True)
    second_channel = MemoryConnection()
    await registry.register(first, first_channel)
    await registry.register(second, second_channel)

    listed = await registry.list_online()
    assert {item.installation_id for item in listed} == {
        first.installation_id,
        second.installation_id,
    }

    await registry.shutdown()

    expected_close = (EXECUTOR_CONNECTION_SHUTDOWN_CODE, EXECUTOR_CONNECTION_SHUTDOWN_REASON)
    assert first_channel.closes == [expected_close]
    assert second_channel.closes == [expected_close]
    assert await registry.list_online() == ()


@pytest.mark.asyncio
async def test_send_current_requires_an_exact_live_connection_and_safe_wire() -> None:
    registry = ExecutorConnectionRegistry(clock=MutableClock())
    bound = bound_connection()
    channel = MemoryConnection()
    await registry.register(bound, channel)

    await registry.send_current(
        installation_id=bound.installation_id,
        connection_id=bound.connection_id,
        source='{"message":"bounded"}',
    )

    assert channel.sent == ['{"message":"bounded"}']

    invalid_calls = (
        registry.send_current(
            installation_id=cast(InstallationId, "private-installation"),
            connection_id=bound.connection_id,
            source='{"message":"invalid"}',
        ),
        registry.send_current(
            installation_id=bound.installation_id,
            connection_id=cast(ExecutorConnectionId, "private-connection"),
            source='{"message":"invalid"}',
        ),
        registry.send_current(
            installation_id=bound.installation_id,
            connection_id=bound.connection_id,
            source=cast(str, object()),
        ),
    )
    for call in invalid_calls:
        with pytest.raises(ExecutorConnectionRegistryRejected):
            await call
    with pytest.raises(StaleExecutorConnection):
        await registry.send_current(
            installation_id=bound.installation_id,
            connection_id=ExecutorConnectionId.new(),
            source='{"message":"stale"}',
        )
    for invalid in ("", "x" * (32 * 1024 + 1), "\ud800"):
        with pytest.raises(ExecutorConnectionRegistryRejected):
            await registry.send_current(
                installation_id=bound.installation_id,
                connection_id=bound.connection_id,
                source=invalid,
            )
    assert channel.sent == ['{"message":"bounded"}']


@pytest.mark.asyncio
async def test_send_current_maps_channel_failure_without_private_details() -> None:
    registry = ExecutorConnectionRegistry(clock=MutableClock())
    bound = bound_connection()
    await registry.register(bound, MemoryConnection(fail_send=True))

    with pytest.raises(ExecutorConnectionUnavailable) as captured:
        await registry.send_current(
            installation_id=bound.installation_id,
            connection_id=bound.connection_id,
            source='{"message":"private-wire"}',
        )

    assert "private" not in repr(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.asyncio
async def test_send_current_reports_replacement_that_races_with_socket_write() -> None:
    registry = ExecutorConnectionRegistry(clock=MutableClock())
    installation_id = InstallationId.new()
    bound = bound_connection(installation_id=installation_id)
    replacement = bound_connection(installation_id=installation_id)
    await registry.register(bound, ReplacingConnection(registry, replacement))

    with pytest.raises(StaleExecutorConnection):
        await registry.send_current(
            installation_id=installation_id,
            connection_id=bound.connection_id,
            source='{"message":"racing"}',
        )

    online = await registry.snapshot(installation_id)
    assert online is not None
    assert online.connection_id == replacement.connection_id


@pytest.mark.asyncio
async def test_registry_rejects_untyped_inputs_and_non_monotonic_server_time() -> None:
    clock = MutableClock()
    registry = ExecutorConnectionRegistry(clock=clock)
    bound = bound_connection()
    invalid_operations = (
        registry.register(cast(BoundExecutorConnection, object()), MemoryConnection()),
        registry.register(bound, cast(MemoryConnection, object())),
        registry.snapshot(cast(InstallationId, "private-installation")),
        registry.is_current(cast(BoundExecutorConnection, object())),
        registry.unregister(cast(BoundExecutorConnection, object())),
    )
    for operation in invalid_operations:
        with pytest.raises(ExecutorConnectionRegistryRejected) as captured:
            await operation
        assert "private" not in repr(captured.value)
        assert captured.value.__cause__ is None
        assert captured.value.__context__ is None

    await registry.register(bound, MemoryConnection())
    clock.value = NOW - timedelta(microseconds=1)
    with pytest.raises(ExecutorConnectionRegistryRejected):
        await registry.record_heartbeat(bound, sequence=2)


@pytest.mark.asyncio
async def test_registry_rejects_invalid_clock_duplicate_registration_and_use_after_shutdown() -> (
    None
):
    bound = bound_connection()
    for clock in (
        MutableClock(cast(datetime, object())),
        MutableClock(datetime(2026, 7, 18, 18, 0)),
        FailingClock(),
    ):
        registry = ExecutorConnectionRegistry(clock=clock)
        with pytest.raises(ExecutorConnectionRegistryRejected) as captured:
            await registry.register(bound, MemoryConnection())
        assert captured.value.__cause__ is None
        assert captured.value.__context__ is None

    registry = ExecutorConnectionRegistry(clock=MutableClock())
    await registry.register(bound, MemoryConnection())
    with pytest.raises(ExecutorConnectionRegistryRejected):
        await registry.register(bound, MemoryConnection())
    await registry.shutdown()
    await registry.shutdown()
    with pytest.raises(ExecutorConnectionRegistryRejected):
        await registry.register(bound_connection(), MemoryConnection())
