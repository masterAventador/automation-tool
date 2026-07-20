"""Bind the exact action intent to target confirmations.

Revision ID: 20260720_0022
Revises: 20260720_0021
"""

from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa
from alembic import op

revision: str = "20260720_0022"
down_revision: str | None = "20260720_0021"
branch_labels: str | None = None
depends_on: str | None = None

_INTENT_VERSION = "task-target-confirmation-intent.v1"

_MESSAGE_SAFETY_SQL = (
    "message_template is null or ("
    "char_length(message_template) between 1 and 500 "
    "and octet_length(message_template) <= 2000 "
    "and btrim(message_template) = message_template "
    "and message_template !~ '[[:cntrl:]]' "
    "and lower(message_template) not like '%bearer %' "
    "and lower(message_template) not like '%file://%' "
    "and lower(message_template) !~ "
    "'(access[_-]?token|api[_-]?key|authorization|cookie|credential|password|"
    "private[_-]?key|refresh[_-]?token|secret|session[_-]?cookie|token)"
    "[[:space:]]*[:=]' "
    "and btrim(replace(message_template, '{{target_display_name}}', '')) <> '' "
    "and replace(message_template, '{{target_display_name}}', '') !~ '[{}]')"
)


def _intent_fingerprint(
    *,
    installation_id: object,
    task_id: object,
    page_revision: int,
    confirmation_revision: int,
    action: str,
    message_template: str | None,
    selected_target_ids: list[object],
) -> bytes:
    encoded = json.dumps(
        {
            "action": action,
            "confirmationRevision": confirmation_revision,
            "installationId": str(installation_id),
            "messageTemplate": message_template,
            "pageRevision": page_revision,
            "selectedTargetIds": [str(value) for value in selected_target_ids],
            "taskId": str(task_id),
            "version": _INTENT_VERSION,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).digest()


def upgrade() -> None:
    op.add_column(
        "task_target_confirmations",
        sa.Column("action", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "task_target_confirmations",
        sa.Column("message_template", sa.String(), nullable=True),
    )
    op.add_column(
        "task_target_confirmations",
        sa.Column("intent_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "task_target_confirmations",
        sa.Column("intent_fingerprint", sa.LargeBinary(length=32), nullable=True),
    )

    bind = op.get_bind()
    confirmations = bind.execute(
        sa.text(
            "select c.task_id, c.installation_id, c.page_revision, "
            "c.selection_task_revision, c.selected_target_count, d.action, "
            "d.message_template from task_target_confirmations c "
            "join douyin_search_exposure_definitions d "
            "on d.task_id = c.task_id and d.installation_id = c.installation_id"
        )
    ).mappings()
    for confirmation in confirmations:
        selected_target_ids = list(
            bind.execute(
                sa.text(
                    "select t.id from task_targets t where t.task_id = :task_id "
                    "and t.installation_id = :installation_id "
                    "and t.page_revision = :page_revision and t.disposition = 'eligible' "
                    "and not exists (select 1 from task_target_exclusions e "
                    "where e.target_id = t.id and e.task_id = t.task_id "
                    "and e.installation_id = t.installation_id "
                    "and e.page_revision = t.page_revision) "
                    "order by t.ordinal, t.id"
                ),
                {
                    "task_id": confirmation["task_id"],
                    "installation_id": confirmation["installation_id"],
                    "page_revision": confirmation["page_revision"],
                },
            ).scalars()
        )
        if len(selected_target_ids) != confirmation["selected_target_count"]:
            raise RuntimeError("Existing target confirmation intent is inconsistent")
        fingerprint = _intent_fingerprint(
            installation_id=confirmation["installation_id"],
            task_id=confirmation["task_id"],
            page_revision=confirmation["page_revision"],
            confirmation_revision=confirmation["selection_task_revision"],
            action=confirmation["action"],
            message_template=confirmation["message_template"],
            selected_target_ids=selected_target_ids,
        )
        bind.execute(
            sa.text(
                "update task_target_confirmations set action = :action, "
                "message_template = :message_template, intent_version = :intent_version, "
                "intent_fingerprint = :intent_fingerprint where task_id = :task_id"
            ),
            {
                "action": confirmation["action"],
                "message_template": confirmation["message_template"],
                "intent_version": _INTENT_VERSION,
                "intent_fingerprint": fingerprint,
                "task_id": confirmation["task_id"],
            },
        )

    op.alter_column("task_target_confirmations", "action", nullable=False)
    op.alter_column("task_target_confirmations", "intent_version", nullable=False)
    op.alter_column("task_target_confirmations", "intent_fingerprint", nullable=False)
    op.create_check_constraint(
        "ck_task_target_confirmations_action",
        "task_target_confirmations",
        "action in ('browse', 'comment', 'direct_message')",
    )
    op.create_check_constraint(
        "ck_task_target_confirmations_message_presence",
        "task_target_confirmations",
        "(action = 'browse' and message_template is null) or "
        "(action in ('comment', 'direct_message') and message_template is not null)",
    )
    op.create_check_constraint(
        "ck_task_target_confirmations_message_safe",
        "task_target_confirmations",
        _MESSAGE_SAFETY_SQL,
    )
    op.create_check_constraint(
        "ck_task_target_confirmations_intent_version",
        "task_target_confirmations",
        f"intent_version = '{_INTENT_VERSION}'",
    )
    op.create_check_constraint(
        "ck_task_target_confirmations_intent_fingerprint_length",
        "task_target_confirmations",
        "octet_length(intent_fingerprint) = 32",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_task_target_confirmations_intent_fingerprint_length",
        "task_target_confirmations",
        type_="check",
    )
    op.drop_constraint(
        "ck_task_target_confirmations_intent_version",
        "task_target_confirmations",
        type_="check",
    )
    op.drop_constraint(
        "ck_task_target_confirmations_message_safe",
        "task_target_confirmations",
        type_="check",
    )
    op.drop_constraint(
        "ck_task_target_confirmations_message_presence",
        "task_target_confirmations",
        type_="check",
    )
    op.drop_constraint(
        "ck_task_target_confirmations_action",
        "task_target_confirmations",
        type_="check",
    )
    op.drop_column("task_target_confirmations", "intent_fingerprint")
    op.drop_column("task_target_confirmations", "intent_version")
    op.drop_column("task_target_confirmations", "message_template")
    op.drop_column("task_target_confirmations", "action")
