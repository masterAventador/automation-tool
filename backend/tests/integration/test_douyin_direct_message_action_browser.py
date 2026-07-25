from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from conftest import assert_private_profile_directory, create_private_profile_directory
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from automation_tool.executor.action_authorization import (
    ActionAuthorizationExpectation,
    Ed25519ActionAuthorizationVerifier,
)
from automation_tool.executor.action_gate import ExecutorActionGate, LocalActionHardPolicy
from automation_tool.executor.browser_runtime import BrowserLaunchRequest, BrowserRuntime
from automation_tool.executor.ledger import ExecutorLedger
from automation_tool.executor.rpa.douyin.direct_message_action import (
    DouyinDirectMessageActionEvidence,
    DouyinDirectMessageActionExecution,
    DouyinDirectMessageActionIntent,
    DouyinDirectMessageActionState,
)
from automation_tool.executor.side_effect_ledger import SideEffectState
from automation_tool.protocol import (
    ACTION_AUTHORIZATION_VERSION,
    ActionAuthorizationClaims,
    ActionMessageTemplate,
    DouyinCandidateSummary,
    DouyinSearchExposureAction,
    ProtocolActionId,
    ProtocolExecutionAttemptId,
    ProtocolExecutorId,
    ProtocolInstallationId,
    ProtocolTargetId,
    ProtocolTaskId,
    action_authorization_idempotency_key,
    action_authorization_signing_input,
    encode_action_authorization_token,
)

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "douyin_direct_message_pages"
    / "message-action.html"
)
ACTION_URL = "https://www.douyin.com/user/creator-001"
NOW = datetime(2026, 7, 21, 1, 0, tzinfo=UTC)
PRIVATE_KEY = bytes(range(32))
INSTALLATION_ID = ProtocolInstallationId("123e4567-e89b-42d3-a456-426614174005")
EXECUTOR_ID = ProtocolExecutorId("123e4567-e89b-42d3-a456-426614174006")
ATTEMPT_ID = ProtocolExecutionAttemptId("123e4567-e89b-42d3-a456-426614174007")
TASK_ID = ProtocolTaskId("123e4567-e89b-42d3-a456-426614174008")
ACTION_ID = ProtocolActionId("623e4567-e89b-42d3-a456-426614174001")
TARGET_ID = ProtocolTargetId("623e4567-e89b-42d3-a456-426614174002")


class Clock:
    def now(self) -> datetime:
        return NOW


def authorization() -> tuple[str, ActionAuthorizationExpectation]:
    claims = ActionAuthorizationClaims(
        version=ACTION_AUTHORIZATION_VERSION,
        action_id=ACTION_ID,
        target_id=TARGET_ID,
        execution_attempt_id=ATTEMPT_ID,
        task_id=TASK_ID,
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        platform="douyin",
        action=DouyinSearchExposureAction.DIRECT_MESSAGE,
        idempotency_key=action_authorization_idempotency_key(ACTION_ID),
        authorized_at=NOW - timedelta(seconds=1),
        deadline_at=NOW + timedelta(minutes=4),
    )
    token = encode_action_authorization_token(
        claims,
        Ed25519PrivateKey.from_private_bytes(PRIVATE_KEY).sign(
            action_authorization_signing_input(claims)
        ),
    )
    return token, ActionAuthorizationExpectation(
        action_id=claims.action_id,
        target_id=claims.target_id,
        execution_attempt_id=claims.execution_attempt_id,
        task_id=claims.task_id,
        installation_id=claims.installation_id,
        executor_id=claims.executor_id,
        platform=claims.platform,
        action=claims.action,
        idempotency_key=claims.idempotency_key,
    )


def test_production_direct_message_action_enters_and_dispatches_once_headlessly(
    tmp_path: Path, staged_embedded_chromium: Path,
) -> None:
    profile = tmp_path / "automation-tool-a7-12-profile"
    create_private_profile_directory(profile)
    clock = Clock()
    ledger = ExecutorLedger(
        state_directory=tmp_path / "automation-tool-a7-12-state",
        installation_id=str(INSTALLATION_ID),
        executor_id=str(EXECUTOR_ID),
    )
    gate = ExecutorActionGate(
        ledger=ledger,
        verifier=Ed25519ActionAuthorizationVerifier(
            public_key=Ed25519PrivateKey.from_private_bytes(PRIVATE_KEY)
            .public_key()
            .public_bytes_raw(),
            clock=clock,
        ),
        policy=LocalActionHardPolicy(minimum_interval=timedelta(seconds=1), task_action_limit=100),
        clock=clock,
    )
    token, expected = authorization()
    intent = DouyinDirectMessageActionIntent(
        authorization=expected,
        message_template=ActionMessageTemplate(
            source="您好 {{target_display_name}}, 这是隔离验收私信"
        ),
        target_summary=DouyinCandidateSummary(display_name="测试目标", public_handle=None),
    )
    document = FIXTURE.read_text(encoding="utf-8")
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
        page.route(
            "https://www.douyin.com/user/**",
            lambda route: route.fulfill(status=200, content_type="text/html", body=document),
        )
        page.goto(ACTION_URL, wait_until="domcontentloaded")

        first = DouyinDirectMessageActionExecution(
            window=window,
            action_gate=gate,
            ledger=ledger,
            clock=clock,
        ).run(token=token, intent=intent)
        assert first.state is DouyinDirectMessageActionState.VERIFIED
        assert first.evidence is DouyinDirectMessageActionEvidence.MESSAGE_CONFIRMED
        assert page.evaluate("window.__conversationEntries") == 1
        assert page.evaluate("window.__messageDispatches") == 1
        assert page.locator("textarea").input_value() == ("您好 测试目标, 这是隔离验收私信")

        replay = DouyinDirectMessageActionExecution(
            window=window,
            action_gate=gate,
            ledger=ledger,
            clock=clock,
        ).run(token=token, intent=intent)
        assert replay.evidence is DouyinDirectMessageActionEvidence.REPLAY_VERIFIED
        assert replay.replayed is True
        assert page.evaluate("window.__conversationEntries") == 1
        assert page.evaluate("window.__messageDispatches") == 1

    persisted = ledger.get_side_effect(str(ACTION_ID))
    assert persisted is not None and persisted.state is SideEffectState.VERIFIED
    assert not runtime.is_running
    assert_private_profile_directory(profile)
