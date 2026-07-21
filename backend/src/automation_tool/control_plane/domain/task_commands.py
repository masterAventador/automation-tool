"""Closed command outbox vocabulary shared with Executor v1."""

from enum import StrEnum
from typing import Final


class TaskCommandType(StrEnum):
    TASK_OFFER = "task.offer"
    TASK_DISCOVER = "task.discover"
    ACTION_EXECUTE = "action.execute"
    TASK_PAUSE = "task.pause"
    TASK_RESUME = "task.resume"
    TASK_CANCEL = "task.cancel"
    TASK_EMERGENCY_STOP = "task.emergency_stop"


class TaskCommandResponseType(StrEnum):
    TASK_ACCEPT = "task.accept"
    TASK_REJECT = "task.reject"
    TASK_CONTROL_ACK = "task.control_ack"
    ACTION_ACCEPT = "action.accept"
    ACTION_REJECT = "action.reject"


class TaskCommandStatus(StrEnum):
    PENDING = "pending"
    IN_FLIGHT = "in_flight"
    DELIVERED = "delivered"
    ACKNOWLEDGED = "acknowledged"
    REJECTED = "rejected"
    EXPIRED = "expired"


TERMINAL_TASK_COMMAND_STATUSES: Final[frozenset[TaskCommandStatus]] = frozenset(
    {
        TaskCommandStatus.ACKNOWLEDGED,
        TaskCommandStatus.REJECTED,
        TaskCommandStatus.EXPIRED,
    }
)


__all__ = [
    "TERMINAL_TASK_COMMAND_STATUSES",
    "TaskCommandResponseType",
    "TaskCommandStatus",
    "TaskCommandType",
]
