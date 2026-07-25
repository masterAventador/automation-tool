#!/usr/bin/env python3
"""EB-17 确定性测试：正式包不认识系统浏览器。

EB-17 要证明的是「未安装 Chrome/Edge 的全新环境也能跑」。但「干净机上没碰
系统 Chrome」这个观察本身是弱的——机器上根本没有可碰的东西，不碰是必然的，
证明不了产品的行为。

真正有力的两个方向：

1. **有系统浏览器却不碰它**。这台开发机装着 `/Applications/Google Chrome.app`，
   如果产品在这种条件下仍然只用包内 Chromium，那就同时排除了「发现」和「回退」。
   这比干净机是更强的条件。
2. **产品里根本不存在系统浏览器的位置**。EB-10 删掉了生产的浏览器发现链路，
   这里把它变成可回归的断言：正式包的二进制与资源里不得出现任何系统浏览器安装
   位置的字面量。有字面量不等于会用，但没有字面量就不可能去找。

这一层是确定性的（不启动 App、不联网）。真实包的启动、断网与残留检查在
`scripts/run_eb_17_acceptance.py`。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from eb_17_clean_machine import (  # noqa: E402
    SYSTEM_BROWSER_MARKERS,
    CleanMachineRejected,
    browser_inventory,
    require_no_browser_installer_scripts,
    require_no_new_browser,
    scan_package_for_system_browser_references,
)


class SystemBrowserReferenceTests(unittest.TestCase):
    """产品里不该存在系统浏览器的位置。"""

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory(prefix="eb17-test-")
        self.addCleanup(self._directory.cleanup)
        self.bundle = Path(self._directory.name) / "样例.app"
        binary = self.bundle / "Contents/MacOS/样例"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"\x00\x01production binary\x00")
        (self.bundle / "Contents/Info.plist").write_bytes(b"<plist/>")

    def test_a_clean_bundle_passes(self) -> None:
        scan_package_for_system_browser_references(self.bundle)

    def test_a_bundle_naming_a_system_browser_is_refused(self) -> None:
        # 回退到系统浏览器的实现总得先写下它的位置。
        binary = self.bundle / "Contents/MacOS/样例"
        binary.write_bytes(b"\x00fallback=/Applications/Google Chrome.app\x00")
        with self.assertRaises(CleanMachineRejected):
            scan_package_for_system_browser_references(self.bundle)

    def test_every_declared_marker_is_actually_detectable(self) -> None:
        # 词表里写了却扫不到的标记等于没写；每一条都要能真的把包判红。
        for marker in SYSTEM_BROWSER_MARKERS:
            binary = self.bundle / "Contents/MacOS/样例"
            binary.write_bytes(marker.encode("utf-8"))
            with self.assertRaises(CleanMachineRejected, msg=marker):
                scan_package_for_system_browser_references(self.bundle)

    def test_the_bundles_own_chromium_is_not_mistaken_for_a_system_one(self) -> None:
        # 包内 Chromium 的路径里当然有 chrome 字样，不能因此判红。
        browser = self.bundle / "Contents/Resources/embedded-browser/chrome-mac-arm64"
        browser.mkdir(parents=True)
        (browser / "Google Chrome for Testing").write_bytes(b"embedded browser binary")
        scan_package_for_system_browser_references(self.bundle)

    def test_the_upstream_driver_library_is_not_the_products_own_code(self) -> None:
        """Playwright 的 driver 库里硬编码系统浏览器位置是它的固有事实。

        它支持 `channel="chrome"`，库里当然写着 Chrome 装在哪。删不得——执行器要靠
        这个 driver 驱动包内 Chromium。产品的约束是"产品自己不去发现、选择、回退"，
        不是"依赖库里不许出现这些字符串"。所以本扫描只管产品自己的代码与配置；
        上游库有没有被走到，靠运行期不下载、不启动系统浏览器来证明。
        """
        library = (
            self.bundle
            / "Contents/Resources/local-executor/package/_internal"
            / "playwright/driver/package/lib"
        )
        library.mkdir(parents=True)
        (library / "coreBundle.js").write_bytes(
            b'const chrome = "/Applications/Google Chrome.app";'
        )
        scan_package_for_system_browser_references(self.bundle)


class BrowserInstallerScriptTests(unittest.TestCase):
    """可执行的浏览器安装脚本一个都不许进包。

    上游 driver 自带 17 个联网下载并安装系统浏览器的脚本。它们与库代码不同：
    库代码要产品显式去调才有影响，而这些是**现成的可执行下载器**——用户机器上
    任何能跑 shell 的路径都能用它绕开"只用包内 Chromium"。
    """

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory(prefix="eb17-installer-")
        self.addCleanup(self._directory.cleanup)
        self.bundle = Path(self._directory.name) / "样例.app"
        self.driver_bin = (
            self.bundle
            / "Contents/Resources/local-executor/package/_internal"
            / "playwright/driver/package/bin"
        )
        self.driver_bin.mkdir(parents=True)

    def test_a_package_without_installer_scripts_passes(self) -> None:
        (self.driver_bin / "README.md").write_bytes(b"driver notes")
        require_no_browser_installer_scripts(self.bundle)

    def test_a_chrome_installer_script_is_refused(self) -> None:
        (self.driver_bin / "reinstall_chrome_stable_mac.sh").write_bytes(b"#!/bin/sh\n")
        with self.assertRaises(CleanMachineRejected):
            require_no_browser_installer_scripts(self.bundle)

    def test_an_edge_installer_script_is_refused(self) -> None:
        (self.driver_bin / "reinstall_msedge_beta_win.ps1").write_bytes(b"# ps1")
        with self.assertRaises(CleanMachineRejected):
            require_no_browser_installer_scripts(self.bundle)

    def test_the_media_pack_and_wsl_installers_are_refused(self) -> None:
        for name in ("install_media_pack.ps1", "install_webkit_wsl.ps1"):
            script = self.driver_bin / name
            script.write_bytes(b"# installer")
            with self.assertRaises(CleanMachineRejected, msg=name):
                require_no_browser_installer_scripts(self.bundle)
            script.unlink()


class BrowserInventoryTests(unittest.TestCase):
    """运行前后对比：不得多出第二套浏览器。"""

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory(prefix="eb17-inventory-")
        self.addCleanup(self._directory.cleanup)
        self.base = Path(self._directory.name)
        self.cache = self.base / "ms-playwright"
        self.cache.mkdir()
        (self.cache / "chromium-1228").mkdir()

    def test_an_unchanged_inventory_passes(self) -> None:
        before = browser_inventory([self.cache])
        require_no_new_browser(before, browser_inventory([self.cache]))

    def test_a_newly_downloaded_browser_is_caught(self) -> None:
        before = browser_inventory([self.cache])
        (self.cache / "chromium-1301").mkdir()
        with self.assertRaises(CleanMachineRejected):
            require_no_new_browser(before, browser_inventory([self.cache]))

    def test_a_missing_inventory_root_is_not_silently_empty(self) -> None:
        # 目录不存在与目录为空必须可区分：否则监控一个打错的路径会永远"通过"。
        missing = self.base / "从未存在"
        inventory = browser_inventory([missing])
        self.assertEqual(inventory[missing], None)
        self.assertEqual(browser_inventory([self.cache])[self.cache], ("chromium-1228",))


if __name__ == "__main__":
    unittest.main()
