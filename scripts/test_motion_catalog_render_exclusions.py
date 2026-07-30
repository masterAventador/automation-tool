#!/usr/bin/env python3
"""独立渲染豁免清单的卫生门禁。

BM-16 的逐项 sweep 允许一张带原因的豁免清单（19 个目录项渲染不出来的根因
全部在内容/上游侧，PC-21 §18.6 逐类定性）。豁免机制的通病是腐烂成万能
通行证——往里加个名字就能绕过（§9.1 的教训）。这里守三条：

1. 清单里的每一项必须真实存在于目录清单——死条目会让清单越攒越松；
2. 原因类别必须来自已定性的封闭集合——不许发明新类别悄悄塞项进来；
3. 每一项必须写非空理由——「先豁免着」不算理由。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUSIONS = ROOT / "contracts/quality/motion-catalog-standalone-render-exclusions.v1.json"
CATALOG = ROOT / "contracts/quality/motion-catalog.v1.json"

# PC-21 §18.6 定性的类别；新增类别必须先补定性证据再扩这个集合。
KNOWN_CLASSES = frozenset(
    {
        "template-host-instantiated",
        "replacement-stub-api",
        "static-by-design-overlay",
        "absolute-path-reference",
        "experimental-api-dependency",
        "frozen-render-pipeline",
    }
)


class RenderExclusionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.exclusions = json.loads(EXCLUSIONS.read_text(encoding="utf-8"))
        self.catalog_names = {
            entry["name"]
            for entry in json.loads(CATALOG.read_text(encoding="utf-8"))["items"]
        }

    def test_every_exclusion_names_a_real_catalog_item(self) -> None:
        dead = sorted(set(self.exclusions["items"]) - self.catalog_names)
        self.assertEqual(dead, [])

    def test_every_exclusion_carries_a_known_class_and_a_reason(self) -> None:
        for name, entry in self.exclusions["items"].items():
            self.assertIn(entry["class"], KNOWN_CLASSES, name)
            self.assertTrue(entry["reason"].strip(), name)

    def test_the_exclusion_count_matches_the_declared_total(self) -> None:
        self.assertEqual(len(self.exclusions["items"]), self.exclusions["total"])

    def test_resolved_runtime_fetch_items_cannot_return_to_the_exclusion_list(self) -> None:
        runtime_fetch = sorted(
            name
            for name, entry in self.exclusions["items"].items()
            if entry["class"] == "runtime-fetch-blocked"
        )
        self.assertEqual(runtime_fetch, [])


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=1).result
    if not result.wasSuccessful():
        sys.exit(1)
    print(f"motion catalog render exclusions: {result.testsRun} checks passed")
