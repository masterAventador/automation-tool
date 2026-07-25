#!/usr/bin/env python3
"""CQ-03 确定性测试：并发时的隔离判据。

三条业务线会同时用到浏览器——RPA 的运营 Profile、Browser Use 的独立临时会话、
动效渲染的独立进程。它们共用同一个包内 Chromium 二进制，但**必须互不相交**：
Browser Use 与渲染进程不得接触运营 Profile（CLAUDE.md 第 5、6 节），否则一次渲染
崩溃就能带走用户的平台登录态。

这里是那几条判据本身。真实的并发运行在 `scripts/run_cq_03_acceptance.py`。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from cq_03_concurrent_isolation import (  # noqa: E402
    ConcurrentIsolationRejected,
    directory_fingerprint,
    require_disjoint_profiles,
    require_untouched,
)


class DisjointProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory(prefix="cq03-profiles-")
        self.addCleanup(self._directory.cleanup)
        self.base = Path(self._directory.name)

    def test_three_separate_profiles_pass(self) -> None:
        require_disjoint_profiles(
            {
                "operations": self.base / "operations",
                "browser_use": self.base / "browser-use",
                "render": self.base / "render",
            }
        )

    def test_two_lines_sharing_one_profile_are_refused(self) -> None:
        shared = self.base / "shared"
        with self.assertRaises(ConcurrentIsolationRejected):
            require_disjoint_profiles(
                {"operations": shared, "browser_use": shared, "render": self.base / "r"}
            )

    def test_a_profile_nested_inside_another_is_refused(self) -> None:
        # 嵌套比同名更隐蔽：路径字符串不同，写入却会落进对方的树里。
        operations = self.base / "operations"
        with self.assertRaises(ConcurrentIsolationRejected):
            require_disjoint_profiles(
                {
                    "operations": operations,
                    "browser_use": operations / "nested",
                    "render": self.base / "render",
                }
            )

    def test_fewer_than_two_lines_is_refused(self) -> None:
        # 一条线的"隔离"是空话；判据必须拒绝这种无意义的调用，
        # 否则编排里少启一条线也会静默通过。
        with self.assertRaises(ConcurrentIsolationRejected):
            require_disjoint_profiles({"operations": self.base / "operations"})


class UntouchedProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory(prefix="cq03-untouched-")
        self.addCleanup(self._directory.cleanup)
        self.profile = Path(self._directory.name) / "operations"
        (self.profile / "Default").mkdir(parents=True)
        (self.profile / "Default/Cookies").write_bytes(b"platform session")

    def test_an_untouched_profile_passes(self) -> None:
        before = directory_fingerprint(self.profile)
        require_untouched(self.profile, before, directory_fingerprint(self.profile))

    def test_a_new_file_in_the_profile_is_caught(self) -> None:
        before = directory_fingerprint(self.profile)
        (self.profile / "Default/Preferences").write_bytes(b"written by another line")
        with self.assertRaises(ConcurrentIsolationRejected):
            require_untouched(self.profile, before, directory_fingerprint(self.profile))

    def test_a_changed_file_in_the_profile_is_caught(self) -> None:
        before = directory_fingerprint(self.profile)
        (self.profile / "Default/Cookies").write_bytes(b"overwritten")
        with self.assertRaises(ConcurrentIsolationRejected):
            require_untouched(self.profile, before, directory_fingerprint(self.profile))

    def test_a_deleted_file_in_the_profile_is_caught(self) -> None:
        before = directory_fingerprint(self.profile)
        (self.profile / "Default/Cookies").unlink()
        with self.assertRaises(ConcurrentIsolationRejected):
            require_untouched(self.profile, before, directory_fingerprint(self.profile))

    def test_an_empty_fingerprint_is_not_silently_equal(self) -> None:
        # 指纹取自不存在的目录时必须可区分，否则监控一个打错的路径会永远"通过"。
        missing = self.profile.parent / "从未存在"
        self.assertIsNone(directory_fingerprint(missing))
        with self.assertRaises(ConcurrentIsolationRejected):
            require_untouched(missing, None, directory_fingerprint(self.profile))


if __name__ == "__main__":
    unittest.main()
