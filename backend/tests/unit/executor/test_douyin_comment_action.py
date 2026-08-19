"""抖音评论动作：闸门+台账外层不变，页面层由自愈式技能编排器驱动。

流程：admit → 台账 prepare（幂等回放）→ 技能路由+回放（外部步前钩子登记
dispatch）→ 结果证据成立则 verify。技能缺席/回放失败不再有任何写死选择器
兜底——如实落为「待技能录制/修复」，外部步之后的失败只做对账。

这组测试用仓库里真实签名的种子技能 + 真实编排器 + 脚本化语义页面，
台账与闸门都是真实实现（tmp 目录 SQLite）。"""

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
from automation_tool.executor.rpa.douyin.comment_action import (
    DouyinCommentActionEvidence,
    DouyinCommentActionExecution,
    DouyinCommentActionIntent,
    DouyinCommentActionReceipt,
    DouyinCommentActionRejected,
    DouyinCommentActionState,
)
from automation_tool.executor.rpa.douyin.skills import DOUYIN_COMMENT_SKILL_ID
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

NOW = datetime(2026, 8, 19, 8, 0, tzinfo=UTC)
INSTALLATION_ID = ProtocolInstallationId("123e4567-e89b-42d3-a456-426614174005")
EXECUTOR_ID = ProtocolExecutorId("123e4567-e89b-42d3-a456-426614174006")
ATTEMPT_ID = ProtocolExecutionAttemptId("123e4567-e89b-42d3-a456-426614174007")
TASK_ID = ProtocolTaskId("123e4567-e89b-42d3-a456-426614174008")

COMMENT_INPUT = "留下你的精彩评论"
COMMENT_SUBMIT = "发表评论"
SUCCESS_TOAST = "评论成功"


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
        self.side_effects: list[tuple[str, str, str | None]] = []
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
        _role, name = cast(tuple[str, str], handle)
        self.side_effects.append((kind, name, value))
        if kind == "fill" and value is not None:
            self.filled.append(value)

    def current_path(self) -> str:
        return "/video"

    @property
    def submit_clicks(self) -> int:
        return sum(
            1 for kind, name, _ in self.side_effects if (kind, name) == ("click", COMMENT_SUBMIT)
        )


def resource_id(index: int) -> str:
    return f"123e4567-e89b-42d3-a456-4266141{74100 + index:05d}"[:36]


def authorization(index: int) -> ActionAuthorizationExpectation:
    action_id = ProtocolActionId(f"123e4567-e89b-42d3-a456-4266142{index:05d}")
    return ActionAuthorizationExpectation(
        action_id=action_id,
        target_id=ProtocolTargetId(f"123e4567-e89b-42d3-a456-4266143{index:05d}"),
        execution_attempt_id=ATTEMPT_ID,
        task_id=TASK_ID,
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        platform="douyin",
        action=DouyinSearchExposureAction.COMMENT,
        idempotency_key=action_authorization_idempotency_key(action_id),
    )


def dependencies(
    state_directory: Path,
    *,
    clock: Clock | None = None,
    minimum_interval: timedelta = timedelta(seconds=1),
    task_action_limit: int = 100,
) -> tuple[ExecutorActionGate, ExecutorLedger, Clock]:
    clock = Clock() if clock is None else clock
    ledger = ExecutorLedger(
        state_directory=state_directory,
        installation_id=str(INSTALLATION_ID),
        executor_id=str(EXECUTOR_ID),
    )
    gate = ExecutorActionGate(
        ledger=ledger,
        policy=LocalActionHardPolicy(
            minimum_interval=minimum_interval, task_action_limit=task_action_limit
        ),
        clock=clock,
    )
    return gate, ledger, clock


def intent(
    expected: ActionAuthorizationExpectation,
    source: str = "固定评论内容",
) -> DouyinCommentActionIntent:
    return DouyinCommentActionIntent(
        authorization=expected,
        message_template=ActionMessageTemplate(source=source),
        target_summary=DouyinCandidateSummary(display_name="目标账号", public_handle=None),
    )


def seeded_orchestrator() -> SkillOrchestrator:
    # 每个用例新建（统计独立），但装载的是仓库里真实提交的种子。
    return SkillOrchestrator(load_seed_registry())


def empty_orchestrator() -> SkillOrchestrator:
    return SkillOrchestrator(SkillRegistry(trusted_public_key=b"\x01" * 32))


def execute(
    page: FakeReplayPage,
    gate: ExecutorActionGate,
    ledger: ExecutorLedger,
    clock: Clock,
    action_intent: DouyinCommentActionIntent,
    *,
    orchestrator: SkillOrchestrator | None = None,
) -> DouyinCommentActionReceipt:
    return DouyinCommentActionExecution(
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
        action_intent = DouyinCommentActionIntent(
            authorization=expected,
            message_template=ActionMessageTemplate(
                source="您好 {{target_display_name}} 内容很有启发"
            ),
            target_summary=DouyinCandidateSummary(display_name="目标账号", public_handle=None),
        )

        receipt = execute(page, gate, ledger, clock, action_intent)

        assert receipt.state is DouyinCommentActionState.VERIFIED
        assert receipt.evidence is DouyinCommentActionEvidence.COMMENT_CONFIRMED
        assert receipt.side_effect_state is SideEffectState.VERIFIED
        assert receipt.side_effect_revision == 3
        assert receipt.replayed is False
        # 模板已渲染、以运行时参数进入回放；发表恰好点了一次。
        assert page.filled == ["您好 目标账号 内容很有启发"]
        assert page.submit_clicks == 1
        persisted = ledger.get_side_effect(str(expected.action_id))
        assert persisted is not None and persisted.state is SideEffectState.VERIFIED
        # 评论正文不落台账。
        assert "您好" not in ledger.database_path.read_bytes().decode(
            "utf-8", errors="ignore"
        )
        assert str(expected.action_id) not in repr(receipt)

    def test_the_dispatch_is_registered_before_the_external_click(
        self, tmp_path: Path
    ) -> None:
        expected = authorization(2)
        gate, ledger, clock = dependencies(tmp_path / "state")

        observed: list[SideEffectState] = []

        class ObservingPage(FakeReplayPage):
            def act(self, kind: str, handle: object, value: str | None) -> None:
                _role, name = cast(tuple[str, str], handle)
                if (kind, name) == ("click", COMMENT_SUBMIT):
                    effect = ledger.get_side_effect(str(expected.action_id))
                    assert effect is not None
                    observed.append(effect.state)
                super().act(kind, handle, value)

        receipt = execute(ObservingPage(), gate, ledger, clock, intent(expected))

        assert receipt.completed is True
        # 点击发生时台账已是 DISPATCHED——不是点完才补记。
        assert observed == [SideEffectState.DISPATCHED]


class TestHonestSkillStates:
    def test_no_published_skill_lands_awaiting_recording(self, tmp_path: Path) -> None:
        expected = authorization(3)
        gate, ledger, clock = dependencies(tmp_path / "state")
        page = FakeReplayPage()

        receipt = execute(
            page, gate, ledger, clock, intent(expected), orchestrator=empty_orchestrator()
        )

        assert receipt.state is DouyinCommentActionState.NOT_DISPATCHED
        assert receipt.evidence is DouyinCommentActionEvidence.SKILL_AWAITING_RECORDING
        assert receipt.side_effect_state is SideEffectState.PREPARED
        assert receipt.side_effect_revision == 1
        assert page.side_effects == []

    def test_a_pre_dispatch_replay_failure_lands_recovery_pending(
        self, tmp_path: Path
    ) -> None:
        expected = authorization(4)
        gate, ledger, clock = dependencies(tmp_path / "state")
        page = FakeReplayPage(missing={COMMENT_INPUT})

        receipt = execute(page, gate, ledger, clock, intent(expected))

        assert receipt.state is DouyinCommentActionState.NOT_DISPATCHED
        assert receipt.evidence is DouyinCommentActionEvidence.SKILL_RECOVERY_PENDING
        assert receipt.side_effect_state is SideEffectState.PREPARED
        # 外部步从未尝试：台账停在 PREPARED，页面上没有发表点击。
        persisted = ledger.get_side_effect(str(expected.action_id))
        assert persisted is not None and persisted.state is SideEffectState.PREPARED
        assert page.submit_clicks == 0

    def test_a_post_dispatch_failure_reconciles_and_never_resends(
        self, tmp_path: Path
    ) -> None:
        expected = authorization(5)
        gate, ledger, clock = dependencies(tmp_path / "state")
        # 发表点击成功，但「评论成功」toast 一直没出现——结果不确定。
        page = FakeReplayPage(failing={SUCCESS_TOAST})

        receipt = execute(page, gate, ledger, clock, intent(expected))

        assert receipt.state is DouyinCommentActionState.OUTCOME_UNCERTAIN
        assert receipt.evidence is DouyinCommentActionEvidence.SKILL_RECONCILE_REQUIRED
        assert receipt.side_effect_state is SideEffectState.UNCERTAIN
        assert receipt.side_effect_revision == 3
        assert page.submit_clicks == 1


class TestLedgerBracket:
    def test_a_refused_dispatch_stops_before_any_external_click(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        expected = authorization(6)
        gate, ledger, clock = dependencies(tmp_path / "state")
        page = FakeReplayPage()

        def reject_dispatch(*args: object, **kwargs: object) -> object:
            raise RuntimeError("private ledger failure")

        monkeypatch.setattr(ExecutorLedger, "begin_side_effect_dispatch", reject_dispatch)
        receipt = execute(page, gate, ledger, clock, intent(expected))

        assert receipt.evidence is DouyinCommentActionEvidence.DISPATCH_PERMISSION_REJECTED
        assert receipt.side_effect_state is SideEffectState.PREPARED
        # 填充已发生（内部步），但外部点击没有。
        assert page.filled == ["固定评论内容"]
        assert page.submit_clicks == 0

    def test_a_failed_verification_settles_uncertain(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        expected = authorization(7)
        gate, ledger, clock = dependencies(tmp_path / "state")
        page = FakeReplayPage()

        def reject_verify(*args: object, **kwargs: object) -> object:
            raise RuntimeError("private verification failure")

        monkeypatch.setattr(ExecutorLedger, "verify_side_effect", reject_verify)
        receipt = execute(page, gate, ledger, clock, intent(expected))

        assert receipt.state is DouyinCommentActionState.OUTCOME_UNCERTAIN
        assert receipt.evidence is DouyinCommentActionEvidence.VERIFICATION_UNAVAILABLE
        assert receipt.side_effect_state is SideEffectState.UNCERTAIN

    def test_an_unavailable_ledger_yields_no_effect(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        expected = authorization(8)
        gate, ledger, clock = dependencies(tmp_path / "state")
        page = FakeReplayPage()

        def reject_prepare(*args: object, **kwargs: object) -> object:
            raise RuntimeError("private ledger failure")

        monkeypatch.setattr(ExecutorLedger, "prepare_side_effect", reject_prepare)
        receipt = execute(page, gate, ledger, clock, intent(expected))

        assert receipt.evidence is DouyinCommentActionEvidence.LEDGER_UNAVAILABLE
        assert receipt.side_effect_state is None
        assert page.side_effects == []


class TestIdempotency:
    def test_a_verified_effect_replays_without_touching_the_page(
        self, tmp_path: Path
    ) -> None:
        expected = authorization(9)
        gate, ledger, clock = dependencies(tmp_path / "state")
        first = execute(FakeReplayPage(), gate, ledger, clock, intent(expected))
        assert first.completed is True

        untouched = FakeReplayPage()
        second = execute(untouched, gate, ledger, clock, intent(expected))

        assert second.state is DouyinCommentActionState.VERIFIED
        assert second.evidence is DouyinCommentActionEvidence.REPLAY_VERIFIED
        assert second.replayed is True
        assert untouched.side_effects == []

    def test_an_uncertain_effect_replays_without_touching_the_page(
        self, tmp_path: Path
    ) -> None:
        expected = authorization(10)
        gate, ledger, clock = dependencies(tmp_path / "state")
        first = execute(
            FakeReplayPage(failing={SUCCESS_TOAST}), gate, ledger, clock, intent(expected)
        )
        assert first.state is DouyinCommentActionState.OUTCOME_UNCERTAIN

        untouched = FakeReplayPage()
        second = execute(untouched, gate, ledger, clock, intent(expected))

        assert second.evidence is DouyinCommentActionEvidence.REPLAY_UNCERTAIN
        assert second.replayed is True
        assert untouched.side_effects == []

    def test_a_changed_copy_for_the_same_action_is_refused(self, tmp_path: Path) -> None:
        expected = authorization(11)
        gate, ledger, clock = dependencies(tmp_path / "state")
        first = execute(
            FakeReplayPage(missing={COMMENT_INPUT}), gate, ledger, clock,
            intent(expected, "第一版评论"),
        )
        assert first.side_effect_state is SideEffectState.PREPARED

        untouched = FakeReplayPage()
        changed = execute(untouched, gate, ledger, clock, intent(expected, "第二版评论"))

        assert changed.evidence is DouyinCommentActionEvidence.LEDGER_UNAVAILABLE
        assert untouched.side_effects == []


class TestLocalLimits:
    def test_the_minimum_interval_blocks_a_second_action_without_effect(
        self, tmp_path: Path
    ) -> None:
        gate, ledger, clock = dependencies(
            tmp_path / "state", minimum_interval=timedelta(minutes=5)
        )
        first = execute(FakeReplayPage(), gate, ledger, clock, intent(authorization(12)))
        assert first.completed is True

        untouched = FakeReplayPage()
        limited = execute(untouched, gate, ledger, clock, intent(authorization(13)))

        assert limited.state is DouyinCommentActionState.NOT_DISPATCHED
        assert limited.evidence is DouyinCommentActionEvidence.LOCAL_MINIMUM_INTERVAL
        assert limited.side_effect_state is None
        assert untouched.side_effects == []


class TestGuards:
    def test_the_execution_runs_exactly_once(self, tmp_path: Path) -> None:
        expected = authorization(14)
        gate, ledger, clock = dependencies(tmp_path / "state")
        execution = DouyinCommentActionExecution(
            window=BrowserWindow._for_runtime(object(), cast(Any, object())),
            action_gate=gate,
            ledger=ledger,
            clock=clock,
            orchestrator=seeded_orchestrator(),
            replay_page_factory=lambda _window: FakeReplayPage(),
        )
        assert execution.run(intent=intent(expected)).completed is True
        with pytest.raises(DouyinCommentActionRejected):
            execution.run(intent=intent(expected))

    def test_receipt_invariants_refuse_impossible_combinations(self) -> None:
        with pytest.raises(DouyinCommentActionRejected):
            DouyinCommentActionReceipt(
                action_id=ProtocolActionId("123e4567-e89b-42d3-a456-426614200001"),
                target_id=ProtocolTargetId("123e4567-e89b-42d3-a456-426614200002"),
                state=DouyinCommentActionState.VERIFIED,
                evidence=DouyinCommentActionEvidence.SKILL_AWAITING_RECORDING,
                side_effect_state=SideEffectState.VERIFIED,
                side_effect_revision=3,
                replayed=False,
            )

    def test_the_wired_skill_is_the_committed_comment_seed(self) -> None:
        # 动作层路由的就是仓库里那份种子——常量漂移在这里红。
        registry = load_seed_registry()
        assert registry.at(DOUYIN_COMMENT_SKILL_ID, 1).skill.platform == "douyin"
