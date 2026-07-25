from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from conftest import assert_private_profile_directory, create_private_profile_directory

from automation_tool.executor.browser_runtime import BrowserLaunchRequest, BrowserRuntime
from automation_tool.executor.ledger import ExecutorLedger
from automation_tool.executor.rpa.douyin.side_effect_recovery import (
    DouyinSideEffectRecovery,
    DouyinSideEffectRecoveryEvidence,
    DouyinSideEffectRecoveryState,
)
from automation_tool.executor.side_effect_ledger import SideEffectState
from automation_tool.protocol import (
    ACTION_AUTHORIZATION_VERSION,
    ActionAuthorizationClaims,
    DouyinSearchExposureAction,
    ProtocolActionId,
    ProtocolExecutionAttemptId,
    ProtocolExecutorId,
    ProtocolInstallationId,
    ProtocolTargetId,
    ProtocolTaskId,
    action_authorization_idempotency_key,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures"
COMMENT_FIXTURE = FIXTURE_ROOT / "douyin_comment_pages" / "comment-action.html"
MESSAGE_FIXTURE = FIXTURE_ROOT / "douyin_direct_message_pages" / "message-action.html"
COMMENT_URL = "https://www.douyin.com/video/7351234567890123456"
MESSAGE_URL = "https://www.douyin.com/user/creator-001"
NOW = datetime(2026, 7, 21, 3, 0, tzinfo=UTC)
INSTALLATION_ID = ProtocolInstallationId("123e4567-e89b-42d3-a456-426614174005")
EXECUTOR_ID = ProtocolExecutorId("123e4567-e89b-42d3-a456-426614174006")
ATTEMPT_ID = ProtocolExecutionAttemptId("123e4567-e89b-42d3-a456-426614174007")
TASK_ID = ProtocolTaskId("123e4567-e89b-42d3-a456-426614174008")
COMMENT_ACTION_ID = ProtocolActionId("823e4567-e89b-42d3-a456-426614174001")
MESSAGE_ACTION_ID = ProtocolActionId("823e4567-e89b-42d3-a456-426614174002")


class Clock:
    def now(self) -> datetime:
        return NOW + timedelta(seconds=4)


def seed_dispatched(
    opened: ExecutorLedger,
    *,
    action_id: ProtocolActionId,
    target_id: ProtocolTargetId,
    action: DouyinSearchExposureAction,
) -> None:
    claims = ActionAuthorizationClaims(
        version=ACTION_AUTHORIZATION_VERSION,
        action_id=action_id,
        target_id=target_id,
        execution_attempt_id=ATTEMPT_ID,
        task_id=TASK_ID,
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        platform="douyin",
        action=action,
        idempotency_key=action_authorization_idempotency_key(action_id),
        authorized_at=NOW - timedelta(seconds=1),
        deadline_at=NOW + timedelta(minutes=4),
    )
    opened.admit_action(
        claims=claims,
        authorization_fingerprint=hashlib.sha256(str(action_id).encode()).digest(),
        admitted_at=NOW,
        minimum_interval_seconds=1,
        task_action_limit=100,
    )
    fingerprint = hashlib.sha256(f"effect:{action_id}".encode()).digest()
    opened.prepare_side_effect(
        action_id=str(action_id),
        effect_fingerprint=fingerprint,
        prepared_at=NOW + timedelta(seconds=1),
    )
    opened.begin_side_effect_dispatch(
        action_id=str(action_id),
        effect_fingerprint=fingerprint,
        dispatched_at=NOW + timedelta(seconds=2),
    )


def test_production_recovery_reads_both_final_facts_without_dispatching(
    tmp_path: Path, staged_embedded_chromium: Path,
) -> None:
    profile = tmp_path / "automation-tool-a7-13-profile"
    create_private_profile_directory(profile)
    opened = ExecutorLedger(
        state_directory=tmp_path / "automation-tool-a7-13-state",
        installation_id=str(INSTALLATION_ID),
        executor_id=str(EXECUTOR_ID),
    )
    seed_dispatched(
        opened,
        action_id=COMMENT_ACTION_ID,
        target_id=ProtocolTargetId("823e4567-e89b-42d3-a456-426614174101"),
        action=DouyinSearchExposureAction.COMMENT,
    )
    seed_dispatched(
        opened,
        action_id=MESSAGE_ACTION_ID,
        target_id=ProtocolTargetId("823e4567-e89b-42d3-a456-426614174102"),
        action=DouyinSearchExposureAction.DIRECT_MESSAGE,
    )
    documents = {
        COMMENT_URL: COMMENT_FIXTURE.read_text(encoding="utf-8"),
        MESSAGE_URL: MESSAGE_FIXTURE.read_text(encoding="utf-8"),
    }
    runtime = BrowserRuntime()

    with runtime.running(
        BrowserLaunchRequest(
            executable_path=staged_embedded_chromium,
            profile_directory=profile,
            headless=True,
        )
    ):
        window = runtime.primary_window()
        page = cast(Any, window.playwright_page)

        def fulfill(route: Any) -> None:
            route.fulfill(
                status=200,
                content_type="text/html",
                body=documents[route.request.url],
            )

        page.route("https://www.douyin.com/**", fulfill)
        page.goto(COMMENT_URL, wait_until="domcontentloaded")
        page.locator('[role="status"]').evaluate("element => { element.hidden = false; }")
        comment = DouyinSideEffectRecovery(
            window=window,
            ledger=opened,
            clock=Clock(),
        ).run(action_id=COMMENT_ACTION_ID)
        assert comment.state is DouyinSideEffectRecoveryState.VERIFIED
        assert comment.evidence is DouyinSideEffectRecoveryEvidence.COMMENT_CONFIRMED
        assert page.evaluate("window.__commentDispatches") == 0
        assert page.locator("textarea").input_value() == ""

        page.goto(MESSAGE_URL, wait_until="domcontentloaded")
        page.locator('[role="status"]').evaluate("element => { element.hidden = false; }")
        message = DouyinSideEffectRecovery(
            window=window,
            ledger=opened,
            clock=Clock(),
        ).run(action_id=MESSAGE_ACTION_ID)
        assert message.state is DouyinSideEffectRecoveryState.VERIFIED
        assert message.evidence is DouyinSideEffectRecoveryEvidence.MESSAGE_CONFIRMED
        assert page.evaluate("window.__conversationEntries") == 0
        assert page.evaluate("window.__messageDispatches") == 0
        assert page.locator("textarea").input_value() == ""

        replay = DouyinSideEffectRecovery(
            window=window,
            ledger=opened,
            clock=Clock(),
        ).run(action_id=MESSAGE_ACTION_ID)
        assert replay.evidence is DouyinSideEffectRecoveryEvidence.ALREADY_VERIFIED
        assert replay.replayed is True
        assert page.evaluate("window.__conversationEntries") == 0
        assert page.evaluate("window.__messageDispatches") == 0

    comment_effect = opened.get_side_effect(str(COMMENT_ACTION_ID))
    message_effect = opened.get_side_effect(str(MESSAGE_ACTION_ID))
    assert comment_effect is not None and comment_effect.state is SideEffectState.VERIFIED
    assert message_effect is not None and message_effect.state is SideEffectState.VERIFIED
    assert not runtime.is_running
    assert_private_profile_directory(profile)
