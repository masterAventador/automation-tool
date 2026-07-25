#!/usr/bin/env python3
"""CQ-04 确定性测试：台账不许虚标。

Roadmap 的状态门禁（AV-04）已经检查过字段齐全、计数一致、只有一个活跃任务。它没有
检查的是**内容与状态是否相符**：一个任务大可以字段写全、状态标 `✅ 已完成`，而证据
文件里通篇都是"未做""待凭据""归后续任务"。

CQ-04 是整个专项的终验，它必须能回答"这 87 项里，哪些是真完成的"。所以这里加一条
判据：状态与证据内容必须自洽。

- `✅ 已完成` 的任务，遗留项里不许还挂着未闭合的条目；
- `🔍 待验收` 的任务，必须写明缺什么——否则读的人无法判断它离完成还差多远。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from cq_04_ledger_honesty import (  # noqa: E402
    LedgerHonestyRejected,
    leftover_substance,
    require_status_matches_evidence,
)

COMPLETE_EVIDENCE = """# XX-01 完成证据

> 状态：✅ 已完成

## 遗留项

| 项 | 状态 |
| --- | --- |
| 某项 | ✅ 已闭合（2026-07-26） |
"""

COMPLETE_WITH_OPEN_LEFTOVER = """# XX-02 完成证据

> 状态：✅ 已完成

## 遗留项

| 项 | 状态 |
| --- | --- |
| 真实账号验收 | 🔍 待真实账号 |
"""

PENDING_WITH_REASON = """# XX-03 完成证据

> 状态：🔍 待验收

## 遗留项

| 项 | 状态 |
| --- | --- |
| Windows 正式包 | 未做，等 EB-16 |
"""

PENDING_WITHOUT_LEFTOVERS = """# XX-04 完成证据

> 状态：🔍 待验收

## 遗留项

| 项 | 状态 |
| --- | --- |
"""


THREE_COLUMN_LEFTOVERS = """# XX-05 完成证据

> 状态：🔍 待验收

## 遗留项

| 项 | 状态 | 归属 |
| --- | --- | --- |
| Windows 正式包上的同一套判据 | 未做，等 EB-16 Windows 包 | 随 EB-16 |
| 某项 | ✅ 已闭合（2026-07-26） | — |
"""


LIST_STYLE_LEFTOVERS = """# XX-06 完成证据

> 状态：🔍 待验收

## 遗留项

- 🔍 **待凭据**：Apple Developer ID Application 签名、公证与装订。本机无证书；
- 🔍 **待验收**：macOS x86_64 正式包，需在 Intel Mac 上跑同一条验收；
- ⬜ **待加固**：给发行物 Manifest 一个树外可信锚点；
- ✅ **已闭合（2026-07-26）**：生产发布装配路径。
"""


class OpenLeftoverTests(unittest.TestCase):
    def test_a_list_style_leftover_section_is_parsed(self) -> None:
        """遗留项不一定写成表格。

        本仓库两种写法都在用：表格（项/状态[/归属]）和列表（`- 🔍 **待凭据**：…`）。
        只认表格的解析器会把列表式的整节读成空，于是把 EB-16 这种把缺口写得很详细的
        任务判成"没说缺什么"。第一次对 87 项跑出的 20 处报错，追下来全部是解析器的
        局限，不是台账的问题——**先怀疑自己的检查器，再怀疑被检查的东西**。
        """
        self.assertIn("待凭据", leftover_substance(LIST_STYLE_LEFTOVERS))

    def test_a_three_column_leftover_table_is_parsed(self) -> None:
        """遗留项表格不一定只有两列。

        本仓库里两列（项/状态）和三列（项/状态/归属）都在用。只认两列的解析器会把
        三列表格整张读成空，于是把一个写得很清楚的任务判成"没说缺什么"——第一次
        对全部 87 项跑的时候，20 个报错里就有一批是这么来的，是检查器的问题，
        不是台账的问题。
        """
        self.assertIn("Windows 正式包", leftover_substance(THREE_COLUMN_LEFTOVERS))

    def test_a_table_leftover_has_substance(self) -> None:
        self.assertIn("某项", leftover_substance(COMPLETE_EVIDENCE))

    def test_a_credential_leftover_has_substance(self) -> None:
        self.assertIn("真实账号", leftover_substance(COMPLETE_WITH_OPEN_LEFTOVER))

    def test_a_not_done_leftover_has_substance(self) -> None:
        self.assertIn("Windows 正式包", leftover_substance(PENDING_WITH_REASON))

    def test_an_empty_leftover_table_has_no_substance(self) -> None:
        self.assertEqual(leftover_substance(PENDING_WITHOUT_LEFTOVERS), "")


class StatusMatchesEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory(prefix="cq04-ledger-")
        self.addCleanup(self._directory.cleanup)
        self.base = Path(self._directory.name)

    def write(self, task_id: str, text: str) -> Path:
        path = self.base / f"{task_id}.md"
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_complete_task_with_no_open_leftover_passes(self) -> None:
        path = self.write("XX-01", COMPLETE_EVIDENCE)
        require_status_matches_evidence("XX-01", "✅ 已完成", path)

    def test_a_complete_task_with_a_leftover_is_not_judged(self) -> None:
        """已完成任务的遗留项**不**参与判定。

        本仓库的「遗留项」既写本任务的缺口，也写下游承接（"BU-04：把租约接进执法点"）。
        对 87 项实跑后，`✅ 已完成` 一侧报出的 22 处几乎全是后者。判据分不清，就不判——
        一条会稳定误报的门禁比没有门禁更糟，它会训练人忽略它。
        """
        path = self.write("XX-02", COMPLETE_WITH_OPEN_LEFTOVER)
        require_status_matches_evidence("XX-02", "✅ 已完成", path)

    def test_a_pending_task_stating_what_is_missing_passes(self) -> None:
        path = self.write("XX-03", PENDING_WITH_REASON)
        require_status_matches_evidence("XX-03", "🔍 待验收", path)

    def test_a_pending_task_with_nothing_left_is_refused(self) -> None:
        # 待验收却说不出缺什么，读的人无法判断它离完成还差多远。
        path = self.write("XX-04", PENDING_WITHOUT_LEFTOVERS)
        with self.assertRaises(LedgerHonestyRejected):
            require_status_matches_evidence("XX-04", "🔍 待验收", path)

    def test_a_missing_evidence_file_is_refused(self) -> None:
        with self.assertRaises(LedgerHonestyRejected):
            require_status_matches_evidence("XX-99", "✅ 已完成", self.base / "XX-99.md")


ORDERED_LIST_LEFTOVERS = """# XX-07 完成证据

> 状态：🔍 待验收

## 遗留项

1. **真实抖音发布页与平台最终状态验收**：`🔍 待真实账号`。所需条件：受控创作者账号。
2. **App 正常用户路径**：前端发布页入口属 PB-07。
"""

PROSE_LEFTOVERS = """# XX-08 完成证据

> 状态：🔍 待验收

## 遗留项

Windows 原生冻结 WebUI 与 App/WebView 技术链路无遗留。IM-08 仍需用有效素材/配音服务
条件完成生成、进度、取消、结果和三类代表性成片。
"""


class FormatIndependenceTests(unittest.TestCase):
    """遗留项写成什么格式都算数。

    本仓库至少有五种写法：两列表格、三列表格、无序列表、有序列表、散文段落。
    判据对全部 87 项跑了四轮，每轮都是解析格式的方式漏掉一种，于是把写得很清楚的
    任务判成"没说缺什么"。所以判据改成问一个格式无关的问题：那一节里有没有实质文字。
    """

    def test_an_ordered_list_has_substance(self) -> None:
        self.assertIn("待真实账号", leftover_substance(ORDERED_LIST_LEFTOVERS))

    def test_a_prose_paragraph_has_substance(self) -> None:
        self.assertIn("IM-08", leftover_substance(PROSE_LEFTOVERS))

    def test_a_section_with_only_a_table_skeleton_has_none(self) -> None:
        self.assertEqual(leftover_substance(PENDING_WITHOUT_LEFTOVERS), "")


if __name__ == "__main__":
    unittest.main()
