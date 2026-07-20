"""Extend durable Task commands and events for target discovery.

Revision ID: 20260720_0017
Revises: 20260718_0016
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260720_0017"
down_revision: str | None = "20260718_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COMMAND_TYPES_WITH_DISCOVERY = (
    "command_type in ('task.offer', 'task.discover', 'task.pause', "
    "'task.resume', 'task.cancel', 'task.emergency_stop')"
)
_COMMAND_TYPES_WITHOUT_DISCOVERY = (
    "command_type in ('task.offer', 'task.pause', 'task.resume', "
    "'task.cancel', 'task.emergency_stop')"
)
_RESPONSES_WITH_DISCOVERY = (
    "response_type is null or "
    "(command_type in ('task.offer', 'task.discover') "
    "and response_type in ('task.accept', 'task.reject')) or "
    "(command_type in ('task.pause', 'task.resume', 'task.cancel', "
    "'task.emergency_stop') and response_type = 'task.control_ack')"
)
_RESPONSES_WITHOUT_DISCOVERY = (
    "response_type is null or "
    "(command_type = 'task.offer' and response_type in ('task.accept', 'task.reject')) or "
    "(command_type in ('task.pause', 'task.resume', 'task.cancel', "
    "'task.emergency_stop') and response_type = 'task.control_ack')"
)
_EVENT_TYPES_WITH_DISCOVERY = (
    "event_type in ('task.created', 'task.validation_started', "
    "'task.validation_failed', 'task.awaiting_platform_login', "
    "'task.discovery_started', 'task.awaiting_confirmation', 'task.started', "
    "'step.started', 'step.progress', 'step.completed', 'step.failed', "
    "'task.awaiting_human', 'task.paused', 'task.resumed', 'task.cancelling', "
    "'task.cancelled', 'task.completed', 'task.partially_completed', "
    "'task.failed', 'task.outcome_uncertain')"
)
_EVENT_TYPES_WITHOUT_DISCOVERY = (
    "event_type in ('task.created', 'task.validation_started', "
    "'task.validation_failed', 'task.awaiting_platform_login', "
    "'task.awaiting_confirmation', 'task.started', 'step.started', "
    "'step.progress', 'step.completed', 'step.failed', "
    "'task.awaiting_human', 'task.paused', 'task.resumed', 'task.cancelling', "
    "'task.cancelled', 'task.completed', 'task.partially_completed', "
    "'task.failed', 'task.outcome_uncertain')"
)


def _replace_constraints(*, with_discovery: bool) -> None:
    op.drop_constraint(
        "ck_task_commands_response_coherence",
        "task_commands",
        type_="check",
    )
    op.drop_constraint("ck_task_commands_type", "task_commands", type_="check")
    op.drop_constraint("ck_task_events_type", "task_events", type_="check")
    op.create_check_constraint(
        "ck_task_commands_type",
        "task_commands",
        _COMMAND_TYPES_WITH_DISCOVERY if with_discovery else _COMMAND_TYPES_WITHOUT_DISCOVERY,
    )
    op.create_check_constraint(
        "ck_task_commands_response_coherence",
        "task_commands",
        _RESPONSES_WITH_DISCOVERY if with_discovery else _RESPONSES_WITHOUT_DISCOVERY,
    )
    op.create_check_constraint(
        "ck_task_events_type",
        "task_events",
        _EVENT_TYPES_WITH_DISCOVERY if with_discovery else _EVENT_TYPES_WITHOUT_DISCOVERY,
    )


def upgrade() -> None:
    """Allow discovery delivery and its authoritative start event."""
    _replace_constraints(with_discovery=True)


def downgrade() -> None:
    """Restore the pre-discovery closed vocabularies."""
    _replace_constraints(with_discovery=False)
