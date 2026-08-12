"""Closed Executor wire contract for one server-authorized Douyin action."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from automation_tool.protocol import (
    ACTION_MESSAGE_TEMPLATE_VERSION,
    DOUYIN_ACTION_COMMAND_VERSION,
    DouyinActionCommandPayload,
    DouyinSearchExposureAction,
    TaskActionCommandEnvelope,
    parse_executor_message,
)

NOW = datetime(2026, 7, 21, 6, 0, tzinfo=UTC)


def payload(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "action_version": DOUYIN_ACTION_COMMAND_VERSION,
        "action_id": "123e4567-e89b-42d3-a456-426614174001",
        "target_id": "223e4567-e89b-42d3-a456-426614174001",
        "action": DouyinSearchExposureAction.COMMENT.value,
        "platform_target_id": "douyin-user-1",
        "display_name": "目标一",
        "public_handle": "target-one",
        "source": "general_search_author",
        "page_revision": 1,
        "message_template_version": ACTION_MESSAGE_TEMPLATE_VERSION,
        "message_template": "你好 {{target_display_name}}",
    }
    values.update(overrides)
    return values


def envelope_payload(**payload_overrides: object) -> dict[str, object]:
    return {
        "protocol_version": "1.0",
        "message_id": "323e4567-e89b-42d3-a456-426614174001",
        "message_type": "action.execute",
        "sent_at": NOW.isoformat(),
        "deadline_at": (NOW + timedelta(minutes=1)).isoformat(),
        "installation_id": "423e4567-e89b-42d3-a456-426614174001",
        "executor_id": "523e4567-e89b-42d3-a456-426614174001",
        "correlation_id": "623e4567-e89b-42d3-a456-426614174001",
        "idempotency_key": "action:123e4567-e89b-42d3-a456-426614174001",
        "sequence": 2,
        "payload": payload(**payload_overrides),
        "task_id": "723e4567-e89b-42d3-a456-426614174001",
        "execution_attempt_id": "823e4567-e89b-42d3-a456-426614174001",
    }


def test_action_command_is_exact_typed_and_parses_through_the_formal_entry() -> None:
    parsed = parse_executor_message(
        json.dumps(envelope_payload(), ensure_ascii=False, separators=(",", ":"))
    )

    assert isinstance(parsed, TaskActionCommandEnvelope)
    assert isinstance(parsed.payload, DouyinActionCommandPayload)
    assert parsed.payload.action is DouyinSearchExposureAction.COMMENT
    assert parsed.payload.message_template == "你好 {{target_display_name}}"
    assert "token" not in parsed.payload.model_dump(mode="json")


@pytest.mark.parametrize(
    "overrides",
    [
        {"action_version": "latest"},
        {"action": "browse"},
        {"message_template_version": None},
        {"message_template": None},
        {"action": "direct_message", "message_template": "{{unknown}}"},
        {"action": "browse", "message_template": None, "message_template_version": None},
    ],
)
def test_action_command_rejects_noncanonical_or_incoherent_payloads(
    overrides: dict[str, object],
) -> None:
    if overrides == {
        "action": "browse",
        "message_template": None,
        "message_template_version": None,
    }:
        assert DouyinActionCommandPayload.model_validate(payload(**overrides)).action is (
            DouyinSearchExposureAction.BROWSE
        )
        return

    with pytest.raises(ValidationError):
        DouyinActionCommandPayload.model_validate(payload(**overrides))


def test_action_command_rejects_unknown_fields_and_cross_scope_idempotency() -> None:
    unknown = payload()
    unknown["private_path"] = "/private/data"
    with pytest.raises(ValidationError):
        DouyinActionCommandPayload.model_validate(unknown)

    crossed = envelope_payload()
    crossed["idempotency_key"] = "action:923e4567-e89b-42d3-a456-426614174001"
    with pytest.raises(ValidationError):
        TaskActionCommandEnvelope.model_validate(crossed)
