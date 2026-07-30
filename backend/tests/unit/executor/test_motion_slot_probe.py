"""渲染前的同会话溢出对比：原文档与替换后文档各量一遍，比的是超出量之差。

为什么不拿运行期读数比冻结基线（PC-14 设计点 3）：基线是 Node 探针量的，
跨驱动有系统性偏差；同一会话里量两份文档，驱动差异当场抵消。冻结基线里的
宽度与字号退化为回抛给模型的提示。

为什么比「超出量之差」而不是各算布尔再比（2026-07-30 两轮实测改写）：

1. 中文行框比拉丁行框高约 11-12% 字号——lt-clean-bar 的 body 槽按拉丁行框定高
   32px，「环比上升」四个字量出 35px。布尔判定下任意中文、不管多短都算新溢出，
   5/37 个零件对产品主语言直接不可用，修复轮向模型要求一件改短救不了的事；
2. 基线自己就常带几像素超出（lt-clean-bar 槽 14 的拉丁原文案量出 59/55）。
   容差若按绝对值套在替换文档上，基线的超出会吃掉容差——中文行框差一叠加就
   误判。所以容差加在增量上：替换后超出 > 基线超出 + 容差 才算更糟。

为什么读数带整篇文档的尺寸（同日第三轮实测）：大多数零件的容器跟着内容长开，
盒级 scroll/client 永远相等，量不出「文字把整个零件推出舞台」——lt-clean-bar
的 55 字标题把文档从 1920px 撑到 3019px，盒级读数毫无动静。所以每份文档同时
上报 documentElement 的横竖尺寸，同一把相对尺子判「是否比原文更出舞台」。

容差：X 加 1px（与冻结预算探针同一把尺子，纯舍入）；Y 加 max(1, round(15% ×
字号))——盖住中西文行框差，而多折一行（≥120% 字号）远超容差必被抓住。
"""

from __future__ import annotations

import pytest

from automation_tool.executor.motion_authoring.slot_budget import SlotBudget
from automation_tool.executor.motion_authoring.slot_overflow_probe import (
    SLOT_PROBE_JS,
    ProbeReading,
    SlotProbeRejected,
    SlotProbeUnmeasured,
    require_no_new_overflow,
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

# 槽读数形状：(scrollWidth, clientWidth, scrollHeight, clientHeight)。
FITS = (300, 366, 60, 60)
STAGE = (1920, 1080)


def _reading(
    slots: dict[int, tuple[int, int, int, int]],
    stage: tuple[int, int] = STAGE,
) -> ProbeReading:
    return ProbeReading(slots=slots, stage=stage)


BOTH_FIT = _reading({13: FITS, 15: FITS})


def test_cjk_line_box_excess_within_tolerance_is_not_overflow() -> None:
    """实测（lt-clean-bar 槽 16，27px 字）：中文行框比拉丁高 3px，属于文字系统
    差异不是文案长度问题，改短救不了——不许拿去烧修复轮。34px 字容差 5px。"""
    require_no_new_overflow(
        FROZEN,
        BOTH_FIT,
        # 15 号：超出 4px，基线超出 0，容差 round(34*0.15)=5 → 4 ≤ 0+5，放行。
        _reading({13: FITS, 15: (200, 300, 64, 60)}),
    )


def test_the_tolerance_rides_on_top_of_the_baselines_own_excess() -> None:
    """实测（lt-clean-bar 槽 14，52px 字）：拉丁原文案自己就超 4px，中文行框差
    再叠 6px 到 10px。容差按绝对值套会误判；按增量套（10 ≤ 4+8）放行。"""
    require_no_new_overflow(
        (FROZEN[0],),
        _reading({13: (300, 366, 64, 60)}),
        _reading({13: (200, 366, 70, 60)}),
    )


def test_an_extra_wrapped_line_is_overflow_and_names_the_budget() -> None:
    """多折一行（+34px ≈ 一倍字号）远超容差，必须点名槽号、宽度与字号。"""
    with pytest.raises(SlotProbeRejected) as caught:
        require_no_new_overflow(
            FROZEN, BOTH_FIT, _reading({13: FITS, 15: (200, 300, 94, 60)})
        )
    message = str(caught.value)
    assert "15" in message and "520" in message and "34" in message
    assert "vertically" in message


def test_one_pixel_of_width_is_rounding_but_two_is_overflow() -> None:
    """冻结预算的探针就是 +1 判的（measure-motion-part-slots.mjs），运行期换一把
    更严的尺子会把同一个槽判出不同结果。"""
    require_no_new_overflow(
        FROZEN, BOTH_FIT, _reading({13: (367, 366, 60, 60), 15: FITS})
    )
    with pytest.raises(SlotProbeRejected) as caught:
        require_no_new_overflow(
            FROZEN, BOTH_FIT, _reading({13: (368, 366, 60, 60), 15: FITS})
        )
    assert "horizontally" in str(caught.value)


def test_copy_that_pushes_the_part_beyond_its_stage_is_overflow() -> None:
    """实测（lt-clean-bar，55 字标题）：盒子自动长开、盒级读数毫无动静，
    文档却从 1920px 撑到 3019px——文字冲出舞台，帧外的部分没人看得见。"""
    with pytest.raises(SlotProbeRejected) as caught:
        require_no_new_overflow(
            FROZEN,
            BOTH_FIT,
            _reading({13: FITS, 15: FITS}, stage=(3019, 1080)),
        )
    message = str(caught.value)
    assert "stage" in message
    assert "1920" in message and "3019" in message


def test_stage_growth_within_the_line_box_grace_is_not_escape() -> None:
    """中文行框差同样会把文档撑高几像素；容差随本篇最大字号走（58px → 9px）。"""
    require_no_new_overflow(
        FROZEN, BOTH_FIT, _reading({13: FITS, 15: FITS}, stage=(1921, 1088))
    )


def test_a_stage_already_escaped_by_the_original_is_tolerated() -> None:
    """基线自己就出舞台的零件维持现状即放行——判据永远是「比原文更糟吗」。"""
    require_no_new_overflow(
        FROZEN,
        _reading({13: FITS, 15: FITS}, stage=(2400, 1080)),
        _reading({13: FITS, 15: FITS}, stage=(2401, 1080)),
    )


def test_every_offending_slot_is_named_at_once() -> None:
    """回抛给模型要一次给全：两个槽都超，两个都要在错误里带宽度与字号。"""
    with pytest.raises(SlotProbeRejected) as caught:
        require_no_new_overflow(
            FROZEN,
            BOTH_FIT,
            _reading({13: (400, 366, 60, 60), 15: (200, 300, 120, 60)}),
        )
    message = str(caught.value)
    assert "13" in message and "366" in message and "58" in message
    assert "15" in message and "520" in message and "34" in message


def test_overflow_no_worse_than_the_original_is_tolerated() -> None:
    """基线本就大幅溢出的方向维持现状即放行——14/48 槽的遮罩式揭示是设计。"""
    require_no_new_overflow(
        FROZEN,
        _reading({13: (300, 366, 120, 60), 15: (400, 300, 60, 60)}),
        _reading({13: (300, 366, 122, 60), 15: (400, 300, 60, 60)}),
    )


def test_a_slot_the_original_run_missed_is_refused_not_passed() -> None:
    """量不到的槽不能默认「不溢出」——那是把测量缺失当成测量通过。"""
    with pytest.raises(SlotProbeUnmeasured):
        require_no_new_overflow(FROZEN, _reading({13: FITS}), BOTH_FIT)


def test_a_slot_the_substituted_run_missed_is_refused_not_passed() -> None:
    with pytest.raises(SlotProbeUnmeasured):
        require_no_new_overflow(FROZEN, BOTH_FIT, _reading({13: FITS}))


def test_the_probe_js_reads_the_marks_the_working_copy_writes() -> None:
    """探针脚本必须按 data-motion-slot 找槽——那是第一步专门打的标记，
    多槽共盒时标记列出全部序号，探针要拆开逐号上报；上报像素而不是布尔，
    容差判定在拿得到字号的这一侧做；同时上报整篇文档的尺寸供舞台判定。"""
    assert "data-motion-slot" in SLOT_PROBE_JS
    assert "scrollWidth" in SLOT_PROBE_JS and "scrollHeight" in SLOT_PROBE_JS
    assert "clientWidth" in SLOT_PROBE_JS and "clientHeight" in SLOT_PROBE_JS
    assert "documentElement" in SLOT_PROBE_JS
