"""抖音业务侧的技能装载：仓库里签好名的种子必须能过验证并被路由到。

这组测试直接读 contracts/browser-use 下提交的锚与种子记录——种子 JSON 被
手改、锚被换、skillId 常量与文档漂移，任何一样都会在这里红。"""

from __future__ import annotations

from automation_tool.executor.rpa.douyin.skills import (
    DOUYIN_COMMENT_MESSAGE_PARAMETER,
    DOUYIN_COMMENT_SKILL_ID,
    DOUYIN_COMMENT_SUCCESS_NAME,
    DOUYIN_COMMENT_SUCCESS_ROLE,
    default_orchestrator,
)
from automation_tool.executor.skill_orchestrator import load_seed_registry


class TestCommittedSeeds:
    def test_the_comment_seed_verifies_and_matches_its_constant(self) -> None:
        registry = load_seed_registry()

        record = registry.at(DOUYIN_COMMENT_SKILL_ID, 1)
        skill = record.skill

        assert skill.platform == "douyin"
        assert skill.domain == "www.douyin.com"
        assert skill.external_step_count == 1
        assert skill.risk_level == "high"
        # 评论正文只能以运行时参数进入回放，绝不进入存储的技能文档。
        assert any(
            step.action.kind == "fill"
            and step.action.parameter == DOUYIN_COMMENT_MESSAGE_PARAMETER
            for step in skill.steps
        )
        # 外部步的结果由可见证据证明（评论不产生导航），且对账用的常量
        # 必须与种子文档的 successEvidence 完全一致——两处漂移会让对账
        # 验证一个技能从不承诺的事实。
        assert any(
            evidence.kind == "element_visible"
            and evidence.role == DOUYIN_COMMENT_SUCCESS_ROLE
            and evidence.name == DOUYIN_COMMENT_SUCCESS_NAME
            for evidence in skill.success_evidence
        )

    def test_the_default_orchestrator_is_shared_so_stats_accumulate(self) -> None:
        assert default_orchestrator() is default_orchestrator()
