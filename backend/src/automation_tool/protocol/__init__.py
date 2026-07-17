"""Versioned public protocol shared by Control Plane and Local Executor."""

from automation_tool.protocol.version import (
    API_VERSION,
    CURRENT_EXECUTOR_PROTOCOL,
    MAXIMUM_COMPATIBLE_EXECUTOR_PROTOCOL,
    MINIMUM_COMPATIBLE_EXECUTOR_PROTOCOL,
)

__all__ = [
    "API_VERSION",
    "CURRENT_EXECUTOR_PROTOCOL",
    "MAXIMUM_COMPATIBLE_EXECUTOR_PROTOCOL",
    "MINIMUM_COMPATIBLE_EXECUTOR_PROTOCOL",
]
