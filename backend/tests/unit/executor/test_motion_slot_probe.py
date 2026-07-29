"""渲染前的同会话溢出对比：原文档与替换后文档各量一遍，自己算差值。

为什么不拿运行期读数比冻结基线（PC-14 设计点 3）：基线是 Node 探针量的，
跨驱动有系统性偏差；同一会话里量两份文档，驱动差异当场抵消。冻结基线里的
宽度与字号退化为回抛给模型的提示。
"""

from __future__ import annotations

import pytest

from automation_tool.executor.motion_authoring.slot_budget import SlotBudget
from automation_tool.executor.motion_authoring.slot_overflow_probe import (
    SLOT_PROBE_JS,
    SlotProbeRejected,
    require_no_new_overflow,
    session_budgets,
)

FROZEN = (
    SlotBudget(
        index=13,
        usable_width_px=366,
        font_size_px=58,
        baseline_overflows_x=False,
        baseline_overflows_y=False,
    ),
    SlotBudget(
        index=15,
        usable_width_px=520,
        font_size_px=34,
        baseline_overflows_x=True,
        baseline_overflows_y=False,
    ),
)


def test_session_budgets_take_the_baseline_from_the_same_session_original() -> None:
    """冻结基线说 13 号不溢出，但本会话的原文档量出来纵向溢出——以后者为准。"""
    effective = session_budgets(FROZEN, {13: (False, True), 15: (False, False)})
    by_index = {budget.index: budget for budget in effective}
    assert by_index[13].baseline_overflows_y is True
    assert by_index[13].baseline_overflows_x is False
    # 冻结基线说 15 号横向溢出，本会话原文档没有——同样以本会话为准。
    assert by_index[15].baseline_overflows_x is False
    # 提示用的宽度与字号保持冻结值。
    assert by_index[13].usable_width_px == 366
    assert by_index[13].font_size_px == 58


def test_session_budgets_refuse_a_slot_the_original_did_not_measure() -> None:
    """量不到的槽不能默认「不溢出」——那是把测量缺失当成测量通过。"""
    with pytest.raises(SlotProbeRejected):
        session_budgets(FROZEN, {13: (False, False)})


def test_require_no_new_overflow_names_every_offending_slot() -> None:
    """回抛给模型要一次给全：两个槽都超，两个都要在错误里带宽度与字号。"""
    effective = session_budgets(
        FROZEN, {13: (False, False), 15: (False, False)}
    )
    with pytest.raises(SlotProbeRejected) as caught:
        require_no_new_overflow(
            effective, {13: (True, False), 15: (False, True)}
        )
    message = str(caught.value)
    assert "13" in message and "366" in message and "58" in message
    assert "15" in message and "520" in message and "34" in message


def test_require_no_new_overflow_accepts_no_worse_than_original() -> None:
    effective = session_budgets(
        FROZEN, {13: (False, True), 15: (True, False)}
    )
    require_no_new_overflow(effective, {13: (False, True), 15: (True, False)})


def test_require_no_new_overflow_refuses_a_slot_the_substituted_run_missed() -> None:
    effective = session_budgets(
        FROZEN, {13: (False, False), 15: (False, False)}
    )
    with pytest.raises(SlotProbeRejected):
        require_no_new_overflow(effective, {13: (False, False)})


def test_the_probe_js_reads_the_marks_the_working_copy_writes() -> None:
    """探针脚本必须按 data-motion-slot 找槽——那是第一步专门打的标记，
    多槽共盒时标记列出全部序号，探针要拆开逐号上报。"""
    assert "data-motion-slot" in SLOT_PROBE_JS
    assert "scrollWidth" in SLOT_PROBE_JS and "scrollHeight" in SLOT_PROBE_JS
