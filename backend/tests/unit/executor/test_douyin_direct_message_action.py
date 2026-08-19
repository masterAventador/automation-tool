"""抖音私信动作：闸门+台账外层不变，页面层由自愈式技能编排器驱动。

与评论动作同构：admit → prepare（幂等回放）→ 技能路由+回放（外部步前钩子
登记 dispatch）→ 结果证据（「私信发送成功」toast）成立则 verify。技能缺席/
回放失败如实落为「待技能录制/修复」；外部步之后的失败只对账、绝不重发。
权限受限（暂时无法私信/关注后才能私信）在技能流程里表现为锚点不可达——
外部步之前失败，落 SKILL_RECOVERY_PENDING，没有任何东西被发出去。"""

from __future__ import annotations

from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Any, cast

import pytest

from automation_tool.executor.action_authorization import ActionAuthorizationExpectation
from automation_tool.executor.action_gate import (
    ExecutorActionGate,
    LocalActionHardPolicy,
)
from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.ledger import ExecutorLedger
from automation_tool.executor.rpa.douyin.direct_message_action import (
    DouyinDirectMessageActionEvidence,
    DouyinDirectMessageActionExecution,
    DouyinDirectMessageActionIntent,
    DouyinDirectMessageActionReceipt,
    DouyinDirectMessageActionRejected,
    DouyinDirectMessageActionState,
)
from automation_tool.executor.rpa.douyin.skills import DOUYIN_DIRECT_MESSAGE_SKILL_ID
from automation_tool.executor.side_effect_ledger import SideEffectState
from automation_tool.executor.skill_orchestrator import (
    SkillOrchestrator,
    load_seed_registry,
)
from automation_tool.executor.skill_registry import SkillRegistry
from automation_tool.protocol import (
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
)

NOW = datetime(2026, 8, 19, 9, 0, tzinfo=UTC)
INSTALLATION_ID = ProtocolInstallationId("123e4567-e89b-42d3-a456-426614174005")
EXECUTOR_ID = ProtocolExecutorId("123e4567-e89b-42d3-a456-426614174006")
ATTEMPT_ID = ProtocolExecutionAttemptId("123e4567-e89b-42d3-a456-426614174007")
TASK_ID = ProtocolTaskId("123e4567-e89b-42d3-a456-426614174008")

MESSAGE_ENTRY = "私信"
MESSAGE_BOX = "发送私信"
SUCCESS_TOAST = "私信发送成功"


class Clock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class FakeReplayPage:
    """脚本化语义页面：锚点按名字可寻，条件按名字成立。"""

    def __init__(
        self, *, missing: set[str] | None = None, failing: set[str] | None = None
    ) -> None:
        self.missing = missing or set()
        self.failing = failing or set()
        self.side_effects: list[tuple[str, str, str, str | None]] = []
        self.filled: list[str] = []

    def find(
        self,
        role: str,
        name: str,
        *,
        near_text: str | None = None,
        relative_position: str | None = None,
    ) -> object | None:
        return None if name in self.missing else (role, name)

    def holds(self, kind: str, *, role=None, name=None, pattern=None) -> bool:
        return (pattern or name) not in self.failing

    def act(self, kind: str, handle: object, value: str | None) -> None:
        role, name = cast(tuple[str, str], handle)
        self.side_effects.append((kind, role, name, value))
        if kind == "fill" and value is not None:
            self.filled.append(value)

    def current_path(self) -> str:
        return "/user"

    @property
    def send_clicks(self) -> int:
        # 发送按钮与输入框同名，靠 role 区分。
        return sum(
            1
            for kind, role, name, _ in self.side_effects
            if (kind, role, name) == ("click", "button", MESSAGE_BOX)
        )


def authorization(index: int) -> ActionAuthorizationExpectation:
    action_id = ProtocolActionId(f"123e4567-e89b-42d3-a456-4266144{index:05d}")
    return ActionAuthorizationExpectation(
        action_id=action_id,
        target_id=ProtocolTargetId(f"123e4567-e89b-42d3-a456-4266145{index:05d}"),
        execution_attempt_id=ATTEMPT_ID,
        task_id=TASK_ID,
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        platform="douyin",
        action=DouyinSearchExposureAction.DIRECT_MESSAGE,
        idempotency_key=action_authorization_idempotency_key(action_id),
    )


def dependencies(
    state_directory: Path,
    *,
    minimum_interval: timedelta = timedelta(seconds=1),
) -> tuple[ExecutorActionGate, ExecutorLedger, Clock]:
    clock = Clock()
    ledger = ExecutorLedger(
        state_directory=state_directory,
        installation_id=str(INSTALLATION_ID),
        executor_id=str(EXECUTOR_ID),
    )
    gate = ExecutorActionGate(
        ledger=ledger,
        policy=LocalActionHardPolicy(
            minimum_interval=minimum_interval, task_action_limit=100
        ),
        clock=clock,
    )
    return gate, ledger, clock


def intent(
    expected: ActionAuthorizationExpectation,
    source: str = "固定私信内容",
) -> DouyinDirectMessageActionIntent:
    return DouyinDirectMessageActionIntent(
        authorization=expected,
        message_template=ActionMessageTemplate(source=source),
        target_summary=DouyinCandidateSummary(display_name="目标账号", public_handle=None),
    )


def seeded_orchestrator() -> SkillOrchestrator:
    return SkillOrchestrator(load_seed_registry())


def execute(
    page: FakeReplayPage,
    gate: ExecutorActionGate,
    ledger: ExecutorLedger,
    clock: Clock,
    action_intent: DouyinDirectMessageActionIntent,
    *,
    orchestrator: SkillOrchestrator | None = None,
) -> DouyinDirectMessageActionReceipt:
    return DouyinDirectMessageActionExecution(
        window=BrowserWindow._for_runtime(object(), cast(Any, object())),
        action_gate=gate,
        ledger=ledger,
        clock=clock,
        orchestrator=orchestrator if orchestrator is not None else seeded_orchestrator(),
        replay_page_factory=lambda _window: page,
    ).run(intent=action_intent)


class TestSkillDrivenHappyPath:
    def test_replay_dispatches_once_verifies_and_keeps_the_message_out_of_the_ledger(
        self, tmp_path: Path
    ) -> None:
        expected = authorization(1)
        gate, ledger, clock = dependencies(tmp_path / "state")
        page = FakeReplayPage()
        action_intent = DouyinDirectMessageActionIntent(
            authorization=expected,
            message_template=ActionMessageTemplate(
                source="您好 {{target_display_name}} 想和您聊聊"
            ),
            target_summary=DouyinCandidateSummary(display_name="目标账号", public_handle=None),
        )

        receipt = execute(page, gate, ledger, clock, action_intent)

        assert receipt.state is DouyinDirectMessageActionState.VERIFIED
        assert receipt.evidence is DouyinDirectMessageActionEvidence.MESSAGE_CONFIRMED
        assert receipt.side_effect_state is SideEffectState.VERIFIED
        assert receipt.side_effect_revision == 3
        assert receipt.replayed is False
        assert page.filled == ["您好 目标账号 想和您聊聊"]
        assert page.send_clicks == 1
        persisted = ledger.get_side_effect(str(expected.action_id))
        assert persisted is not None and persisted.state is SideEffectState.VERIFIED
        assert "您好" not in ledger.database_path.read_bytes().decode(
            "utf-8", errors="ignore"
        )
        assert str(expected.action_id) not in repr(receipt)


class TestHonestSkillStates:
    def test_no_published_skill_lands_awaiting_recording(self, tmp_path: Path) -> None:
        expected = authorization(2)
        gate, ledger, clock = dependencies(tmp_path / "state")
        page = FakeReplayPage()

        receipt = execute(
            page,
            gate,
            ledger,
            clock,
            intent(expected),
            orchestrator=SkillOrchestrator(SkillRegistry(trusted_public_key=b"\x01" * 32)),
        )

        assert receipt.evidence is (
            DouyinDirectMessageActionEvidence.SKILL_AWAITING_RECORDING
        )
        assert receipt.side_effect_state is SideEffectState.PREPARED
        assert page.side_effects == []

    def test_a_blocked_entry_lands_recovery_pending_with_nothing_sent(
        self, tmp_path: Path
    ) -> None:
        """权限受限（暂时无法私信/关注后才能私信）的页面没有私信入口——
        锚点不可达，外部步之前失败，如实报待修复且什么都没发出去。"""
        expected = authorization(3)
        gate, ledger, clock = dependencies(tmp_path / "state")
        page = FakeReplayPage(missing={MESSAGE_ENTRY})

        receipt = execute(page, gate, ledger, clock, intent(expected))

        assert receipt.evidence is DouyinDirectMessageActionEvidence.SKILL_RECOVERY_PENDING
        assert receipt.side_effect_state is SideEffectState.PREPARED
        persisted = ledger.get_side_effect(str(expected.action_id))
        assert persisted is not None and persisted.state is SideEffectState.PREPARED
        assert page.send_clicks == 0

    def test_a_post_dispatch_failure_reconciles_and_never_resends(
        self, tmp_path: Path
    ) -> None:
        expected = authorization(4)
        gate, ledger, clock = dependencies(tmp_path / "state")
        page = FakeReplayPage(failing={SUCCESS_TOAST})

        receipt = execute(page, gate, ledger, clock, intent(expected))

        assert receipt.state is DouyinDirectMessageActionState.OUTCOME_UNCERTAIN
        assert receipt.evidence is (
            DouyinDirectMessageActionEvidence.SKILL_RECONCILE_REQUIRED
        )
        assert receipt.side_effect_state is SideEffectState.UNCERTAIN
        assert page.send_clicks == 1


class TestLedgerBracket:
    def test_a_refused_dispatch_stops_before_any_external_click(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        expected = authorization(5)
        gate, ledger, clock = dependencies(tmp_path / "state")
        page = FakeReplayPage()

        def reject_dispatch(*args: object, **kwargs: object) -> object:
            raise RuntimeError("private ledger failure")

        monkeypatch.setattr(ExecutorLedger, "begin_side_effect_dispatch", reject_dispatch)
        receipt = execute(page, gate, ledger, clock, intent(expected))

        assert receipt.evidence is (
            DouyinDirectMessageActionEvidence.DISPATCH_PERMISSION_REJECTED
        )
        assert page.filled == ["固定私信内容"]
        assert page.send_clicks == 0

    def test_a_failed_verification_settles_uncertain(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        expected = authorization(6)
        gate, ledger, clock = dependencies(tmp_path / "state")

        def reject_verify(*args: object, **kwargs: object) -> object:
            raise RuntimeError("private verification failure")

        monkeypatch.setattr(ExecutorLedger, "verify_side_effect", reject_verify)
        receipt = execute(FakeReplayPage(), gate, ledger, clock, intent(expected))

        assert receipt.evidence is DouyinDirectMessageActionEvidence.VERIFICATION_UNAVAILABLE
        assert receipt.side_effect_state is SideEffectState.UNCERTAIN


class TestIdempotency:
    def test_a_verified_effect_replays_without_touching_the_page(
        self, tmp_path: Path
    ) -> None:
        expected = authorization(7)
        gate, ledger, clock = dependencies(tmp_path / "state")
        first = execute(FakeReplayPage(), gate, ledger, clock, intent(expected))
        assert first.completed is True

        untouched = FakeReplayPage()
        second = execute(untouched, gate, ledger, clock, intent(expected))

        assert second.evidence is DouyinDirectMessageActionEvidence.REPLAY_VERIFIED
        assert second.replayed is True
        assert untouched.side_effects == []

    def test_an_uncertain_effect_replays_without_touching_the_page(
        self, tmp_path: Path
    ) -> None:
        expected = authorization(8)
        gate, ledger, clock = dependencies(tmp_path / "state")
        first = execute(
            FakeReplayPage(failing={SUCCESS_TOAST}), gate, ledger, clock, intent(expected)
        )
        assert first.state is DouyinDirectMessageActionState.OUTCOME_UNCERTAIN

        untouched = FakeReplayPage()
        second = execute(untouched, gate, ledger, clock, intent(expected))

        assert second.evidence is DouyinDirectMessageActionEvidence.REPLAY_UNCERTAIN
        assert second.replayed is True
        assert untouched.side_effects == []


class TestGuards:
    def test_the_execution_runs_exactly_once(self, tmp_path: Path) -> None:
        expected = authorization(9)
        gate, ledger, clock = dependencies(tmp_path / "state")
        execution = DouyinDirectMessageActionExecution(
            window=BrowserWindow._for_runtime(object(), cast(Any, object())),
            action_gate=gate,
            ledger=ledger,
            clock=clock,
            orchestrator=seeded_orchestrator(),
            replay_page_factory=lambda _window: FakeReplayPage(),
        )
        assert execution.run(intent=intent(expected)).completed is True
        with pytest.raises(DouyinDirectMessageActionRejected):
            execution.run(intent=intent(expected))

    def test_the_wired_skill_is_the_committed_direct_message_seed(self) -> None:
        registry = load_seed_registry()
        assert (
            registry.at(DOUYIN_DIRECT_MESSAGE_SKILL_ID, 1).skill.platform == "douyin"
        )
