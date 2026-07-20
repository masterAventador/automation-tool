from enum import StrEnum

from automation_tool.control_plane.domain import (
    TERMINAL_TASK_COMMAND_STATUSES,
    TaskCommandResponseType,
    TaskCommandStatus,
    TaskCommandType,
)


def test_task_command_types_match_the_executor_v1_command_vocabulary() -> None:
    assert issubclass(TaskCommandType, StrEnum)
    assert tuple(command.value for command in TaskCommandType) == (
        "task.offer",
        "task.discover",
        "task.pause",
        "task.resume",
        "task.cancel",
        "task.emergency_stop",
    )
    assert tuple(response.value for response in TaskCommandResponseType) == (
        "task.accept",
        "task.reject",
        "task.control_ack",
    )


def test_task_command_outbox_statuses_and_terminals_are_an_exact_contract() -> None:
    assert tuple(status.value for status in TaskCommandStatus) == (
        "pending",
        "in_flight",
        "delivered",
        "acknowledged",
        "rejected",
        "expired",
    )
    assert (
        frozenset(
            {
                TaskCommandStatus.ACKNOWLEDGED,
                TaskCommandStatus.REJECTED,
                TaskCommandStatus.EXPIRED,
            }
        )
        == TERMINAL_TASK_COMMAND_STATUSES
    )
