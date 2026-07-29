"""The desktop E2E suite must not hand-roll its own idea of the navigation.

Why this gate exists
--------------------
The AI-first redesign (`c4d0d14`) replaced the single `RPA 运营工作台` heading
with a per-section one and moved the video studio behind a segmented control.
The Playwright suite came through it green, because `frontend/e2e/navigation.ts`
had already collected the route into one module and that commit updated it.

The WebdriverIO suite did not. 46 of its 48 spec files had written the route out
by hand — `//li[contains(@class,'ant-menu-item')…工作台]` followed by
`h2=RPA 运营工作台` — so the redesign broke all of them at once, and nobody saw
it, because no gate runs that suite in bulk (each spec is driven by its own
`run_*_acceptance.py`).

Two shapes of damage, and the second is the one worth a gate:

* 44 assertions on the vanished heading, which fail loudly the next time anyone
  runs them;
* 5 **negative** assertions — `assert.doesNotMatch(bodyText, /RPA 运营工作台/)`,
  the check that a locked-out or signed-out App must not show the workbench.
  That string appears nowhere in the product any more, so those now pass over an
  App that *is* showing the workbench. A gate that cannot fail is worse than a
  missing one: it reports safety it is not providing.

So both suites now read the same module, and this gate keeps the two modules
naming the same destinations. The Playwright suite runs, so a renamed sidebar
entry turns it red; this gate then forces the desktop module to follow rather
than rot for another redesign.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP_SUITE = ROOT / "frontend/e2e-tauri"
DESKTOP_NAVIGATION = DESKTOP_SUITE / "navigation.ts"
PLAYWRIGHT_NAVIGATION = ROOT / "frontend/e2e/navigation.ts"

# The heading the redesign removed. Kept here as the one place that still spells
# it, so a spec reintroducing it is refused rather than silently always-green.
RETIRED_WORKBENCH_HEADING = "RPA 运营工作台"

_SIDEBAR_CALL = re.compile(r"openSidebarDestination\(page,\s*\"([^\"]+)\"\)")
_DESKTOP_SECTION = re.compile(r"openWorkbenchSection\(\"([^\"]+)\"\)")
_SEGMENT_PLAYWRIGHT = re.compile(r"hasText:\s*\"([^\"]+)\"")
# 段名可能直接传给 `openSegment`，也可能经由 `openStudioPanel` 这样的包装转发。
# 抓两种，否则收敛一次实现就会让门禁误报「桌面模块少了这个段」——判据没变，
# 变的只是它去哪里找那个字面量。
_SEGMENT_DESKTOP = re.compile(r"open(?:Segment|StudioPanel)\(\"([^\"]+)\"\)")
# 侧边栏点击的每一种写法，不只是旧 XPath 那一种。第一版只认 `ant-menu-item`，
# 于是 11 处 `browser.$("li=平台状态")` 从门禁底下原样穿过去了——而门禁**报告**
# 这条规则成立。一道报告了它并不提供的保障的闸，正是这次事故本身的形状。
_SIDEBAR_SELECTOR = re.compile(r"ant-menu-item|\$\(\s*[\"'`]li=|role='menuitem'|role=\\\"menuitem")


def _specs() -> list[Path]:
    return sorted(DESKTOP_SUITE.glob("*.spec.ts"))


class DesktopNavigationTests(unittest.TestCase):
    def test_the_desktop_suite_has_one_navigation_module(self) -> None:
        self.assertTrue(
            DESKTOP_NAVIGATION.is_file(),
            "frontend/e2e-tauri/navigation.ts must exist: 48 copies of the route "
            "is how one redesign broke 46 files at once",
        )

    def test_no_spec_spells_the_retired_workbench_heading(self) -> None:
        offenders = [
            spec.name
            for spec in _specs()
            if RETIRED_WORKBENCH_HEADING in spec.read_text(encoding="utf-8")
        ]
        self.assertEqual(
            offenders,
            [],
            f"{RETIRED_WORKBENCH_HEADING!r} no longer exists in the product. A "
            "positive assertion on it always fails; a negative one always passes, "
            "which is a login gate that reports safety it is not providing. Use "
            "navigation.ts's WORKBENCH_MARKERS instead: "
            f"{offenders}",
        )

    def test_no_spec_hand_rolls_a_sidebar_click(self) -> None:
        offenders = [
            spec.name
            for spec in _specs()
            if _SIDEBAR_SELECTOR.search(spec.read_text(encoding="utf-8"))
        ]
        self.assertEqual(
            offenders,
            [],
            "the sidebar route belongs to navigation.ts alone; a spec that spells "
            f"its own menu XPath is the next redesign's 46 files: {offenders}",
        )

    def test_both_suites_name_the_same_destinations(self) -> None:
        """The two modules must agree, because only one of them is exercised.

        The Playwright suite runs in bulk and its module is therefore correct by
        construction. The desktop one is exercised by whichever acceptance
        entrypoints happen to run, which is not all of them — so it is held to
        the one that is.
        """
        playwright = PLAYWRIGHT_NAVIGATION.read_text(encoding="utf-8")
        desktop = DESKTOP_NAVIGATION.read_text(encoding="utf-8")

        expected = set(_SIDEBAR_CALL.findall(playwright))
        actual = set(_DESKTOP_SECTION.findall(desktop))
        self.assertTrue(expected, "the Playwright module must name its destinations")
        self.assertEqual(
            expected - actual,
            set(),
            "the desktop module is missing sidebar destinations the Playwright "
            f"suite proves exist: {sorted(expected - actual)}",
        )

        expected_segments = set(_SEGMENT_PLAYWRIGHT.findall(playwright))
        actual_segments = set(_SEGMENT_DESKTOP.findall(desktop))
        self.assertEqual(
            expected_segments - actual_segments,
            set(),
            "the desktop module is missing segments the Playwright suite uses: "
            f"{sorted(expected_segments - actual_segments)}",
        )


class SpecDestinationTests(unittest.TestCase):
    """spec 传的目的地名字必须是产品里真有的那批。

    第 4 条只比对两个 navigation 模块，看不到 spec 传了什么。于是
    `plain-language-comprehension.spec.ts` 把五个**已被删除**的侧边栏名字原样
    传给了新 helper——选择器换了、目的地一个没改，五次点击全部找不到元素，
    而门禁全绿。这一条把判据挪到实参上。
    """

    def test_every_destination_a_spec_asks_for_exists(self) -> None:
        shell = (ROOT / "frontend/src/app/WorkbenchShell.tsx").read_text(encoding="utf-8")
        declared = set(re.findall(r'label: "([^"]+)"', shell))
        self.assertTrue(declared, "the shell must declare its sidebar labels")
        asked: dict[str, list[str]] = {}
        for spec in [*_specs(), DESKTOP_NAVIGATION]:
            for name in _DESKTOP_SECTION.findall(spec.read_text(encoding="utf-8")):
                asked.setdefault(name, []).append(spec.name)
        unknown = {name: files for name, files in asked.items() if name not in declared}
        self.assertEqual(
            unknown,
            {},
            "these destinations do not exist in the shell's own navigation, so the "
            f"click cannot land: {unknown}",
        )


def main() -> int:
    result = unittest.main(module=__name__, exit=False, verbosity=0).result
    checks = result.testsRun
    if not result.wasSuccessful():
        return 1
    print(f"desktop e2e navigation: {checks} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
