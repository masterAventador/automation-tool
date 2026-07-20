"""enforce the closed action message template policy

Revision ID: 20260720_0021
Revises: 20260720_0020
Create Date: 2026-07-20 17:17:00
"""

from __future__ import annotations

from alembic import op

revision: str = "20260720_0021"
down_revision: str | None = "20260720_0020"
branch_labels: str | None = None
depends_on: str | None = None

_BASE_MESSAGE_POLICY = (
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
    "[[:space:]]*[:=]'"
)
_CLOSED_TEMPLATE_POLICY = (
    _BASE_MESSAGE_POLICY
    + " and btrim(replace(message_template, '{{target_display_name}}', '')) <> '' "
    + "and replace(message_template, '{{target_display_name}}', '') !~ '[{}]')"
)
_LEGACY_TEMPLATE_POLICY = _BASE_MESSAGE_POLICY + ")"


def upgrade() -> None:
    op.drop_constraint(
        "ck_douyin_search_exposure_message_safe",
        "douyin_search_exposure_definitions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_douyin_search_exposure_message_safe",
        "douyin_search_exposure_definitions",
        _CLOSED_TEMPLATE_POLICY,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_douyin_search_exposure_message_safe",
        "douyin_search_exposure_definitions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_douyin_search_exposure_message_safe",
        "douyin_search_exposure_definitions",
        _LEGACY_TEMPLATE_POLICY,
    )
