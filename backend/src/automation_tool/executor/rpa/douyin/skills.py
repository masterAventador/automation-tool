"""抖音业务流程对接自愈式自动化的装载点。

种子技能由 ``scripts/sign_seed_skills.py`` 在开发期签名后提交进
``contracts/browser-use``；这里只做验证与装载。业务动作层通过
``default_orchestrator()`` 拿到全进程共享的编排器——共享才能让路由统计
（成功优先、连败让位）跨动作累积。

skillId 常量与提交的种子文档一一对应，测试锁死两者不漂移。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Final

from automation_tool.executor.skill_orchestrator import (
    SkillOrchestrator,
    load_seed_registry,
)

# scripts/sign_seed_skills.py 由指纹确定性推导出的 id：同一份步骤永远同一个 id。
DOUYIN_COMMENT_SKILL_ID: Final = "89628f94-6c21-4585-8739-75a4b0ca923d"
DOUYIN_COMMENT_MESSAGE_PARAMETER: Final = "comment_message"
# 结果证据（种子技能 successEvidence 的同一事实）：副作用对账复用它判断
# 成功 toast。测试锁定这些常量与提交的种子文档一致。
DOUYIN_COMMENT_SUCCESS_ROLE: Final = "status"
DOUYIN_COMMENT_SUCCESS_NAME: Final = "评论成功"

DOUYIN_DIRECT_MESSAGE_SKILL_ID: Final = "761d13fa-26c4-4413-a0a0-6f55dbf92a0d"
DOUYIN_DIRECT_MESSAGE_PARAMETER: Final = "direct_message"
DOUYIN_DIRECT_MESSAGE_SUCCESS_ROLE: Final = "status"
DOUYIN_DIRECT_MESSAGE_SUCCESS_NAME: Final = "私信发送成功"

DOUYIN_SEARCH_SKILL_ID: Final = "3d3b3c4a-a0c6-4912-838c-0b3d6e5e5fd1"
DOUYIN_SEARCH_KEYWORD_PARAMETER: Final = "search_keyword"

DOUYIN_BROWSE_PROFILE_SKILL_ID: Final = "b4359ce7-fd15-4e56-8a36-4462de3ecb71"


@lru_cache(maxsize=1)
def default_orchestrator() -> SkillOrchestrator:
    """验证并装载提交的种子技能，进程内共享一份（含路由统计）。

    种子缺失或验证失败会在这里抛异常——宁可动作失败得响亮，也不静默
    退回任何写死脚本。"""
    return SkillOrchestrator(load_seed_registry())


__all__ = [
    "DOUYIN_BROWSE_PROFILE_SKILL_ID",
    "DOUYIN_COMMENT_MESSAGE_PARAMETER",
    "DOUYIN_COMMENT_SKILL_ID",
    "DOUYIN_COMMENT_SUCCESS_NAME",
    "DOUYIN_COMMENT_SUCCESS_ROLE",
    "DOUYIN_DIRECT_MESSAGE_PARAMETER",
    "DOUYIN_DIRECT_MESSAGE_SKILL_ID",
    "DOUYIN_DIRECT_MESSAGE_SUCCESS_NAME",
    "DOUYIN_DIRECT_MESSAGE_SUCCESS_ROLE",
    "DOUYIN_SEARCH_KEYWORD_PARAMETER",
    "DOUYIN_SEARCH_SKILL_ID",
    "default_orchestrator",
]
