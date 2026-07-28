"""The counts guard must fail when the ledger's numbers drift."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent / "check_local_editing_roadmap_counts.py"
_LEDGER = (
    Path(__file__).resolve().parents[1] / "docs/local-video-editing-roadmap.md"
)


def _run(ledger_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--ledger", str(ledger_path)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_real_ledger_passes() -> None:
    result = _run(_LEDGER)
    assert result.returncode == 0, result.stdout + result.stderr


def test_wrong_total_fails(tmp_path: Path) -> None:
    broken = tmp_path / "roadmap.md"
    broken.write_text(
        _LEDGER.read_text(encoding="utf-8").replace("任务总数：24", "任务总数：99"),
        encoding="utf-8",
    )
    result = _run(broken)
    assert result.returncode != 0
    assert "任务总数" in result.stdout + result.stderr


def test_section_count_mismatch_fails(tmp_path: Path) -> None:
    broken = tmp_path / "roadmap.md"
    broken.write_text(
        _LEDGER.read_text(encoding="utf-8").replace(
            "### 3.4 本地渲染引擎（6 项）", "### 3.4 本地渲染引擎（9 项）"
        ),
        encoding="utf-8",
    )
    result = _run(broken)
    assert result.returncode != 0
    assert "3.4" in result.stdout + result.stderr


def test_status_sum_mismatch_fails(tmp_path: Path) -> None:
    """声明的四个状态计数加起来必须等于任务总数。"""
    broken = tmp_path / "roadmap.md"
    broken.write_text(
        _LEDGER.read_text(encoding="utf-8").replace(
            "- 🔍 待验收：0", "- 🔍 待验收：5"
        ),
        encoding="utf-8",
    )
    result = _run(broken)
    assert result.returncode != 0
    assert "各状态相加" in result.stdout + result.stderr


def test_two_in_flight_fails(tmp_path: Path) -> None:
    """同一时间最多一个任务处于 RED 或实现中。"""
    text = _LEDGER.read_text(encoding="utf-8")
    text = text.replace(
        "| LE-02 | Material 素材库领域对象 | `Material`（kind/时长/分辨率/内容摘要/"
        "has_audio/响度/镜头边界/AI 描述与标签）、`MaterialId`、校验与去重规则；"
        "用户改过的描述不被 AI 覆盖 | LE-01 | ⬜ 未开始 |",
        "| LE-02 | Material 素材库领域对象 | `Material`（kind/时长/分辨率/内容摘要/"
        "has_audio/响度/镜头边界/AI 描述与标签）、`MaterialId`、校验与去重规则；"
        "用户改过的描述不被 AI 覆盖 | LE-01 | 🚧 实现中 |",
        1,
    )
    text = text.replace(
        "| LE-03 | Timeline 重写 | `TimelineClip` 补 `source_in_ms`/`source_out_ms`/"
        "`gain_db`；`TimelineTrackKind` 拆成 visual/narration/ambient/music/caption；"
        "首期锁死\"取片时长等于占位时长\"（不变速）并有拒绝用例 | LE-02 | ⬜ 未开始 |",
        "| LE-03 | Timeline 重写 | `TimelineClip` 补 `source_in_ms`/`source_out_ms`/"
        "`gain_db`；`TimelineTrackKind` 拆成 visual/narration/ambient/music/caption；"
        "首期锁死\"取片时长等于占位时长\"（不变速）并有拒绝用例 | LE-02 | 🚧 实现中 |",
        1,
    )
    # 同步声明的进度计数，让「相加对不上」与「跨表核对」两类检查都保持通过，
    # 只让「同一时间最多一个任务处于 RED/实现中」这一条单独触发。
    text = text.replace("- 🧪 RED / 🚧 实现中：0", "- 🧪 RED / 🚧 实现中：2")
    text = text.replace("- ⬜ 未开始：23", "- ⬜ 未开始：21")
    broken = tmp_path / "roadmap.md"
    broken.write_text(text, encoding="utf-8")
    result = _run(broken)
    assert result.returncode != 0
    assert "同一时间最多一个任务" in result.stdout + result.stderr


def test_status_cross_check_mismatch_fails(tmp_path: Path) -> None:
    """声明的状态计数即使总和对了，也必须真的匹配表格里每行的当前状态。

    回归用例：这正是复现过的真实事故——VE 线整条 8 行都标 ✅ 已完成，而
    产品路径其实没装配。只检查总和相加对不上远远不够，声明的数字本身可能和
    表格内容完全脱节。
    """
    broken = tmp_path / "roadmap.md"
    text = _LEDGER.read_text(encoding="utf-8")
    text = text.replace("- ✅ 已完成：1", "- ✅ 已完成：24")
    text = text.replace("- ⬜ 未开始：23", "- ⬜ 未开始：0")
    broken.write_text(text, encoding="utf-8")
    result = _run(broken)
    assert result.returncode != 0
    assert "已完成" in result.stdout + result.stderr
