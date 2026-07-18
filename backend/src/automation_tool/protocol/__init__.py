"""Versioned public protocol shared by Control Plane and Local Executor."""

from automation_tool.protocol.executor_envelope import (
    EXECUTOR_PROTOCOL_VERSION,
    CorrelationId,
    ExecutorEnvelope,
    ExecutorLifecycleEnvelope,
    ExecutorMessage,
    ExecutorProtocolError,
    IdempotencyKey,
    MessageId,
    ProtocolExecutionAttemptId,
    ProtocolExecutorId,
    ProtocolInstallationId,
    ProtocolTaskId,
    TaskCommandEnvelope,
    TaskCommandResultEnvelope,
    TaskEventEnvelope,
    parse_executor_message,
)
from automation_tool.protocol.version import (
    API_VERSION,
    CURRENT_EXECUTOR_PROTOCOL,
    MAXIMUM_COMPATIBLE_EXECUTOR_PROTOCOL,
    MINIMUM_COMPATIBLE_EXECUTOR_PROTOCOL,
)

__all__ = [
    "API_VERSION",
    "CURRENT_EXECUTOR_PROTOCOL",
    "EXECUTOR_PROTOCOL_VERSION",
    "MAXIMUM_COMPATIBLE_EXECUTOR_PROTOCOL",
    "MINIMUM_COMPATIBLE_EXECUTOR_PROTOCOL",
    "CorrelationId",
    "ExecutorEnvelope",
    "ExecutorLifecycleEnvelope",
    "ExecutorMessage",
    "ExecutorProtocolError",
    "IdempotencyKey",
    "MessageId",
    "ProtocolExecutionAttemptId",
    "ProtocolExecutorId",
    "ProtocolInstallationId",
    "ProtocolTaskId",
    "TaskCommandEnvelope",
    "TaskCommandResultEnvelope",
    "TaskEventEnvelope",
    "parse_executor_message",
]
