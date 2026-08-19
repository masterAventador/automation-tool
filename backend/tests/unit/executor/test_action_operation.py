from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from automation_tool.executor.action_gate import ExecutorActionGate, LocalActionHardPolicy
from automation_tool.executor.action_operation import (
    DouyinActionOperationRejected,
    ProductionDouyinActionOperation,
)
from automation_tool.executor.browser_authority import BrowserLaunchAuthority
from automation_tool.executor.browser_runtime import BrowserLaunchRequest, BrowserWindow
from automation_tool.executor.ledger import ExecutorLedger
from automation_tool.executor.rpa.douyin.browse import (
    DouyinBrowseExecutionEvidence,
    DouyinBrowseExecutionObservation,
    DouyinBrowseExecutionState,
)
from automation_tool.executor.rpa.douyin.comment_action import (
    DouyinCommentActionEvidence,
    DouyinCommentActionIntent,
    DouyinCommentActionReceipt,
    DouyinCommentActionState,
)
from automation_tool.executor.rpa.douyin.direct_message_action import (
    DouyinDirectMessageActionEvidence,
    DouyinDirectMessageActionIntent,
    DouyinDirectMessageActionReceipt,
    DouyinDirectMessageActionState,
)
from automation_tool.executor.side_effect_ledger import SideEffectState
from automation_tool.protocol import (
    DouyinSearchExposureAction,
    ProtocolActionId,
    ProtocolExecutionAttemptId,
    ProtocolExecutorId,
    ProtocolInstallationId,
    ProtocolTargetId,
    ProtocolTaskId,
    TaskActionCommandEnvelope,
    action_authorization_idempotency_key,
)

NOW = datetime(2026, 7, 21, 10, 0, tzinfo=UTC)
INSTALLATION_ID = ProtocolInstallationId("123e4567-e89b-42d3-a456-426614174003")
EXECUTOR_ID = ProtocolExecutorId("123e4567-e89b-42d3-a456-426614174004")
TASK_ID = ProtocolTaskId("123e4567-e89b-42d3-a456-426614174005")
ATTEMPT_ID = ProtocolExecutionAttemptId("123e4567-e89b-42d3-a456-426614174006")
ACTION_ID = ProtocolActionId("223e4567-e89b-42d3-a456-426614174001")
TARGET_ID = ProtocolTargetId("223e4567-e89b-42d3-a456-426614174002")


class Clock:
    def now(self) -> datetime:
        return NOW


class FakePage:
    def __init__(self) -> None:
        self.destinations: list[str] = []

    def goto(self, url: str, *, wait_until: str, timeout: float) -> None:
        assert wait_until == "domcontentloaded"
        assert timeout == 30_000
        self.destinations.append(url)


class FakeRuntime:
    def __init__(
        self,
        page: FakePage,
        *,
        fail_start: bool = False,
        fail_close: bool = False,
    ) -> None:
        self.page = page
        self.fail_start = fail_start
        self.fail_close = fail_close
        self.started = False
        self.closed = False

    def start(self, request: BrowserLaunchRequest) -> None:
        assert request.headless is True
        if self.fail_start:
            raise RuntimeError("private runtime failure")
        self.started = True

    def primary_window(self) -> BrowserWindow:
        assert self.started
        return BrowserWindow._for_runtime(object(), cast(Any, self.page))

    def close(self) -> None:
        self.closed = True
        if self.fail_close:
            raise RuntimeError("private close failure")


@dataclass
class FakeBrowse:
    observation: DouyinBrowseExecutionObservation
    calls: int = 0

    def run(self, *, cancellation_requested: Any) -> DouyinBrowseExecutionObservation:
        assert cancellation_requested() is False
        self.calls += 1
        return self.observation


class FakeEntry:
    def __init__(self) -> None:
        self.clicks = 0

    def click(self, *, timeout: float) -> None:
        assert timeout == 15_000
        self.clicks += 1


class FakeProfilePage:
    def __init__(self, entry: object) -> None:
        self.entry = entry

    def first_video_entry(self) -> object:
        return self.entry


class FakeComment:
    def __init__(self) -> None:
        self.intents: list[DouyinCommentActionIntent] = []

    def run(self, *, intent: DouyinCommentActionIntent) -> DouyinCommentActionReceipt:
        self.intents.append(intent)
        return DouyinCommentActionReceipt(
            action_id=ACTION_ID,
            target_id=TARGET_ID,
            state=DouyinCommentActionState.VERIFIED,
            evidence=DouyinCommentActionEvidence.COMMENT_CONFIRMED,
            side_effect_state=SideEffectState.VERIFIED,
            side_effect_revision=3,
            replayed=False,
        )


class FakeDirectMessage:
    def __init__(self) -> None:
        self.intents: list[DouyinDirectMessageActionIntent] = []

    def run(
        self,
        *,
        intent: DouyinDirectMessageActionIntent,
    ) -> DouyinDirectMessageActionReceipt:
        self.intents.append(intent)
        return DouyinDirectMessageActionReceipt(
            action_id=ACTION_ID,
            target_id=TARGET_ID,
            state=DouyinDirectMessageActionState.VERIFIED,
            evidence=DouyinDirectMessageActionEvidence.MESSAGE_CONFIRMED,
            side_effect_state=SideEffectState.VERIFIED,
            side_effect_revision=3,
            replayed=False,
        )


def command(
    action: DouyinSearchExposureAction,
) -> TaskActionCommandEnvelope:
    template = (
        None if action is DouyinSearchExposureAction.BROWSE else "您好 {{target_display_name}}"
    )
    return TaskActionCommandEnvelope.model_validate_json(
        json.dumps(
            {
                "protocol_version": "1.0",
                "message_id": "323e4567-e89b-42d3-a456-426614174001",
                "message_type": "action.execute",
                "sent_at": NOW.isoformat(),
                "deadline_at": (NOW + timedelta(minutes=4)).isoformat(),
                "installation_id": str(INSTALLATION_ID),
                "executor_id": str(EXECUTOR_ID),
                "correlation_id": "323e4567-e89b-42d3-a456-426614174002",
                "idempotency_key": f"action:{ACTION_ID}",
                "sequence": 2,
                "payload": {
                    "action_version": "douyin.action-command.v1",
                    "action_id": str(ACTION_ID),
                    "target_id": str(TARGET_ID),
                    "action": action.value,
                    "platform_target_id": "creator-001",
                    "display_name": "目标一",
                    "public_handle": "target-one",
                    "source": "general_search_author",
                    "page_revision": 1,
                    "message_template_version": (
                        None if template is None else "action-message-template.v1"
                    ),
                    "message_template": template,
                },
                "task_id": str(TASK_ID),
                "execution_attempt_id": str(ATTEMPT_ID),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def operation(
    tmp_path: Path,
    *,
    browse: FakeBrowse,
    runtime: FakeRuntime,
    profile_entry: object | None = None,
    comment: FakeComment | None = None,
    direct_message: FakeDirectMessage | None = None,
) -> tuple[ProductionDouyinActionOperation, ExecutorActionGate, ExecutorLedger]:
    tmp_path.mkdir(mode=0o700)
    executable = tmp_path / "trusted-browser"
    executable.write_bytes(b"browser")
    executable.chmod(0o700)
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    authority = BrowserLaunchAuthority()
    authority.authorize(
        BrowserLaunchRequest(
            executable_path=executable,
            profile_directory=profile,
            headless=True,
        )
    )
    ledger = ExecutorLedger(
        state_directory=tmp_path / "state",
        installation_id=str(INSTALLATION_ID),
        executor_id=str(EXECUTOR_ID),
    )
    clock = Clock()
    gate = ExecutorActionGate(
        ledger=ledger,
        policy=LocalActionHardPolicy(
            minimum_interval=timedelta(seconds=1),
            task_action_limit=100,
        ),
        clock=clock,
    )
    return (
        ProductionDouyinActionOperation(
            ledger=ledger,
            action_gate=gate,
            browser_authority=authority,
            clock=clock,
            runtime_factory=lambda: runtime,
            browse_factory=lambda _window, _candidate: browse,
            comment_factory=lambda _window, _gate, _ledger, _clock: cast(Any, comment),
            direct_message_factory=lambda _window, _gate, _ledger, _clock: cast(
                Any, direct_message
            ),
            profile_page_factory=lambda _window: FakeProfilePage(profile_entry),
        ),
        gate,
        ledger,
    )


def completed_browse() -> FakeBrowse:
    return FakeBrowse(
        DouyinBrowseExecutionObservation(
            state=DouyinBrowseExecutionState.COMPLETED,
            evidence=DouyinBrowseExecutionEvidence.PROFILE_VISIBLE,
        )
    )


def test_production_operation_routes_browse_comment_and_direct_message(tmp_path: Path) -> None:
    browse_runtime = FakeRuntime(FakePage(), fail_close=True)
    browse = completed_browse()
    browse_operation, _gate, _ledger = operation(
        tmp_path / "browse",
        browse=browse,
        runtime=browse_runtime,
    )
    assert browse_operation.run(command(DouyinSearchExposureAction.BROWSE)).evidence.value == (
        "profile_visible"
    )
    assert browse.calls == 1 and browse_runtime.closed is True

    entry = FakeEntry()
    comment_execution = FakeComment()
    comment_operation, _gate, _ledger = operation(
        tmp_path / "comment",
        browse=completed_browse(),
        runtime=FakeRuntime(FakePage()),
        profile_entry=entry,
        comment=comment_execution,
    )
    comment_fact = comment_operation.run(command(DouyinSearchExposureAction.COMMENT))
    assert (comment_fact.message_type, comment_fact.evidence.value) == (
        "step.completed",
        "comment_confirmed",
    )
    assert entry.clicks == 1 and len(comment_execution.intents) == 1

    direct_page = FakePage()
    direct_execution = FakeDirectMessage()
    direct_operation, _gate, _ledger = operation(
        tmp_path / "direct",
        browse=completed_browse(),
        runtime=FakeRuntime(direct_page),
        direct_message=direct_execution,
    )
    direct_fact = direct_operation.run(command(DouyinSearchExposureAction.DIRECT_MESSAGE))
    assert (direct_fact.message_type, direct_fact.evidence.value) == (
        "step.completed",
        "message_confirmed",
    )
    assert direct_page.destinations == ["https://www.douyin.com/user/creator-001"]
    assert len(direct_execution.intents) == 1


def test_operation_maps_gate_browser_and_profile_failures_without_side_effects(
    tmp_path: Path,
) -> None:
    limited_operation, gate, _ledger = operation(
        tmp_path / "limited",
        browse=completed_browse(),
        runtime=FakeRuntime(FakePage()),
    )
    gate.engage_emergency_stop()
    assert limited_operation.run(command(DouyinSearchExposureAction.BROWSE)).evidence.value == (
        "local_safety_limit"
    )

    failed_runtime = FakeRuntime(FakePage(), fail_start=True)
    unavailable_operation, _gate, _ledger = operation(
        tmp_path / "runtime",
        browse=completed_browse(),
        runtime=failed_runtime,
    )
    assert unavailable_operation.run(command(DouyinSearchExposureAction.BROWSE)).evidence.value == (
        "page_unavailable"
    )
    assert failed_runtime.closed is True

    pending_browse = FakeBrowse(
        DouyinBrowseExecutionObservation(
            state=DouyinBrowseExecutionState.UNKNOWN,
            evidence=DouyinBrowseExecutionEvidence.PAGE_VERSION_UNKNOWN,
        )
    )
    comment_operation, _gate, _ledger = operation(
        tmp_path / "pending-skill",
        browse=pending_browse,
        runtime=FakeRuntime(FakePage()),
        profile_entry=FakeEntry(),
        comment=FakeComment(),
    )
    # 浏览技能待录制/修复 → 页面无法安全识别，评论动作如实失败。
    assert comment_operation.run(command(DouyinSearchExposureAction.COMMENT)).evidence.value == (
        "page_version_unknown"
    )

    missing_entry_operation, _gate, _ledger = operation(
        tmp_path / "missing-entry",
        browse=completed_browse(),
        runtime=FakeRuntime(FakePage()),
        profile_entry=object(),
        comment=FakeComment(),
    )
    assert (
        missing_entry_operation.run(command(DouyinSearchExposureAction.COMMENT)).evidence.value
        == "page_unavailable"
    )


def test_operation_constructor_and_runtime_input_are_closed(tmp_path: Path) -> None:
    valid, _gate, _ledger = operation(
        tmp_path / "valid",
        browse=completed_browse(),
        runtime=FakeRuntime(FakePage()),
    )
    assert repr(valid) == "ProductionDouyinActionOperation(<redacted>)"
    with pytest.raises(DouyinActionOperationRejected):
        valid.run(cast(TaskActionCommandEnvelope, object()))

    ledger = ExecutorLedger(
        state_directory=tmp_path / "constructor-state",
        installation_id=str(INSTALLATION_ID),
        executor_id=str(EXECUTOR_ID),
    )
    with pytest.raises(DouyinActionOperationRejected):
        ProductionDouyinActionOperation(
            ledger=ledger,
            action_gate=cast(ExecutorActionGate, object()),
            browser_authority=BrowserLaunchAuthority(),
            clock=Clock(),
        )


def test_terminal_side_effect_replay_returns_without_starting_a_browser(tmp_path: Path) -> None:
    runtime = FakeRuntime(FakePage())
    active, _gate, ledger = operation(
        tmp_path / "terminal-replay",
        browse=completed_browse(),
        runtime=runtime,
        profile_entry=FakeEntry(),
        comment=FakeComment(),
    )
    source = command(DouyinSearchExposureAction.COMMENT)
    assert active.run(source).message_type == "step.completed"
    effect_fingerprint = b"\x11" * 32
    prepared_at = NOW + timedelta(seconds=1)
    ledger.prepare_side_effect(
        action_id=str(ACTION_ID),
        effect_fingerprint=effect_fingerprint,
        prepared_at=prepared_at,
    )
    ledger.begin_side_effect_dispatch(
        action_id=str(ACTION_ID),
        effect_fingerprint=effect_fingerprint,
        dispatched_at=prepared_at,
    )
    ledger.verify_side_effect(
        action_id=str(ACTION_ID),
        effect_fingerprint=effect_fingerprint,
        verification_fingerprint=b"\x22" * 32,
        verified_at=prepared_at,
    )
    runtime.started = False
    runtime.fail_start = True

    replay = active.run(source)

    assert (replay.message_type, replay.evidence.value) == (
        "step.completed",
        "comment_confirmed",
    )
    assert runtime.started is False


def test_side_effect_replay_lookup_failure_stops_before_browser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = FakeRuntime(FakePage())
    active, _gate, ledger = operation(
        tmp_path / "replay-ledger-failure",
        browse=completed_browse(),
        runtime=runtime,
        profile_entry=FakeEntry(),
        comment=FakeComment(),
    )
    monkeypatch.setattr(
        ledger,
        "get_side_effect",
        lambda _action_id: (_ for _ in ()).throw(RuntimeError("private ledger failure")),
    )

    result = active.run(command(DouyinSearchExposureAction.COMMENT))

    assert (result.message_type, result.evidence.value) == (
        "step.failed",
        "executor_reported_failure",
    )
    assert runtime.started is False
