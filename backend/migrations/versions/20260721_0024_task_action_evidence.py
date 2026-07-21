"""Persist privacy-safe evidence for terminal Task actions.

Revision ID: 20260721_0024
Revises: 20260721_0023
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_0024"
down_revision: str | None = "20260721_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SUCCESS = (
    "'profile_visible', 'comment_confirmed', 'message_confirmed', 'executor_reported_success'"
)
_FAILED = (
    "'admission_rejected', 'local_safety_limit', 'login_required', 'dialog_blocked', "
    "'messaging_not_allowed', 'follow_required', 'timed_out', 'page_version_unknown', "
    "'conflicting_anchors', 'page_unavailable', 'verification_unavailable', "
    "'executor_reported_failure'"
)
_UNCERTAIN = (
    "'dispatch_timed_out', 'dispatch_unavailable', 'final_state_unconfirmed', "
    "'recovery_unconfirmed'"
)


def upgrade() -> None:
    op.add_column(
        "task_actions",
        sa.Column("evidence_code", sa.String(length=64), nullable=True),
    )
    op.execute(
        "update task_actions set evidence_code = case "
        "when status = 'verified' and outcome = 'succeeded' then 'executor_reported_success' "
        "when status = 'verified' and outcome = 'failed' then 'executor_reported_failure' "
        "when status = 'cancelled' then 'action_cancelled' "
        "when status = 'outcome_uncertain' then 'final_state_unconfirmed' "
        "else null end"
    )
    op.drop_constraint("ck_task_actions_result_coherence", "task_actions", type_="check")
    op.create_check_constraint(
        "ck_task_actions_result_coherence",
        "task_actions",
        "(status in ('planned', 'authorized', 'prepared', 'dispatched') "
        "and outcome = 'pending' and evidence_code is null and finished_at is null) or "
        "(status = 'verified' and outcome in ('succeeded', 'failed') "
        "and evidence_code is not null and finished_at is not null) or "
        "(status = 'cancelled' and outcome = 'cancelled' "
        "and evidence_code = 'action_cancelled' and finished_at is not null) or "
        "(status = 'outcome_uncertain' and outcome = 'outcome_uncertain' "
        "and evidence_code is not null and finished_at is not null)",
    )
    op.create_check_constraint(
        "ck_task_actions_evidence_coherence",
        "task_actions",
        "evidence_code is null or "
        f"(outcome = 'succeeded' and evidence_code in ({_SUCCESS})) or "
        f"(outcome = 'failed' and evidence_code in ({_FAILED})) or "
        "(outcome = 'cancelled' and evidence_code = 'action_cancelled') or "
        f"(outcome = 'outcome_uncertain' and evidence_code in ({_UNCERTAIN}))",
    )


def downgrade() -> None:
    op.drop_constraint("ck_task_actions_evidence_coherence", "task_actions", type_="check")
    op.drop_constraint("ck_task_actions_result_coherence", "task_actions", type_="check")
    op.create_check_constraint(
        "ck_task_actions_result_coherence",
        "task_actions",
        "(status in ('planned', 'authorized', 'prepared', 'dispatched') "
        "and outcome = 'pending' and finished_at is null) or "
        "(status = 'verified' and outcome in ('succeeded', 'failed') "
        "and finished_at is not null) or "
        "(status = 'cancelled' and outcome = 'cancelled' and finished_at is not null) or "
        "(status = 'outcome_uncertain' and outcome = 'outcome_uncertain' "
        "and finished_at is not null)",
    )
    op.drop_column("task_actions", "evidence_code")
