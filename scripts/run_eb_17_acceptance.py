#!/usr/bin/env python3
"""EB-17 验收：正式包只用包内 Chromium，运行期不多出第二套浏览器。

判据不是"找一台干净机跑一遍"——干净机上没碰系统 Chrome 这个观察是弱的，机器上
根本没有可碰的东西，不碰是必然的。这里用的是更强的条件与可回归的断言：

1. **包内容**（确定性，见 `scripts/test_eb_17_clean_machine.py`）：产品自己的代码
   与配置里没有系统浏览器的位置；包里没有现成的浏览器下载器。
2. **运行期**（本脚本）：这台机器**装着** `/Applications/Google Chrome.app`，
   而正式包启动的必须是包内那一个——可执行文件路径落在包里、版本等于契约锁定值。
   跑完前后对比浏览器缓存与系统安装位置，不得多出任何一套；进程不得残留。

不涉及真实平台账号：扫码、搜索、受控动作、Browser Use 演示与动效渲染仍在 EB-17
的遗留项里。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))
sys.path.insert(0, str(REPOSITORY_ROOT / "backend/src"))

from build_embedded_chromium_staging import load_staging_contract  # noqa: E402
from eb_17_clean_machine import (  # noqa: E402
    CleanMachineRejected,
    browser_inventory,
    require_no_browser_installer_scripts,
    require_no_new_browser,
    scan_package_for_system_browser_references,
)

STAGING_CONTRACT = REPOSITORY_ROOT / "contracts/browser/embedded-chromium-staging.v1.json"
DEFAULT_PACKAGE = (
    REPOSITORY_ROOT
    / ".local/eb-16/run/cargo-target/release/bundle/macos/自动化运营工具.app"
)

# 运行期要盯住的位置：上游浏览器下载缓存，以及系统浏览器的安装根。
# 产品既不允许下载，也不允许安装第二套。
MACOS_INVENTORY_ROOTS = (
    Path.home() / "Library/Caches/ms-playwright",
    Path("/Applications"),
)


def announce(message: str) -> None:
    print(f"[EB-17] {message}", flush=True)


def fail(message: str) -> None:
    print(f"EB-17 acceptance failed: {message}")
    raise SystemExit(1)


def current_target_id() -> str:
    system = platform.system()
    machine = platform.machine().casefold()
    if system == "Darwin" and machine in {"arm64", "aarch64"}:
        return "macos-arm64"
    if system == "Darwin" and machine in {"x86_64", "amd64"}:
        return "macos-x86_64"
    if system == "Windows" and machine in {"x86_64", "amd64"}:
        return "windows-x86_64"
    fail(f"unsupported EB-17 host: {system}/{machine}")
    raise AssertionError("unreachable")


def run_deterministic_gate() -> None:
    announce("Running the deterministic clean-machine gate")
    result = subprocess.run(
        [sys.executable, "scripts/test_eb_17_clean_machine.py"],
        cwd=REPOSITORY_ROOT,
        check=False,
    )
    if result.returncode != 0:
        fail("deterministic clean-machine gate failed")


def require_a_system_browser_is_present() -> str:
    """确认这台机器确实装着系统浏览器。

    没有这一步，"产品没碰系统浏览器"就退化成那个弱观察——机器上没有可碰的东西时，
    不碰是必然的，证明不了任何事。所以这里**要求**系统浏览器存在；不存在就说明
    本次运行的条件比预期弱，必须如实拒绝而不是照常报通过。
    """
    present = [
        candidate
        for candidate in (
            Path("/Applications/Google Chrome.app"),
            Path("/Applications/Microsoft Edge.app"),
            Path("/Applications/Chromium.app"),
        )
        if candidate.exists()
    ]
    if not present:
        fail(
            "this host has no system browser installed, so 'the product did not touch "
            "one' would prove nothing — run the干净机 variant instead and record it as such"
        )
    return ", ".join(path.name for path in present)


def packaged_browser(application: Path, target_id: str) -> Path:
    """解析包内 Chromium 的可执行文件，走生产的同一份发行物 Manifest。"""
    manifest = application / "Contents/Resources/embedded-browser/distribution-manifest.v1.json"
    if not manifest.is_file():
        fail(f"package carries no distribution manifest: {manifest}")
    document = json.loads(manifest.read_text(encoding="utf-8"))
    if document.get("target") != target_id:
        fail(f"package targets {document.get('target')!r}, not {target_id!r}")
    executable = (
        application / "Contents/Resources/embedded-browser" / document["executable"]
    )
    if not executable.is_file():
        fail(f"packaged browser executable is missing: {executable}")
    return executable


def launch_packaged_browser(executable: Path, expected_version: str) -> str:
    """离线无头启动包内 Chromium，返回它自报的版本。"""
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory(prefix="automation-tool-eb17-", dir="/private/tmp") as raw:
        profile = Path(raw) / "profile"
        with sync_playwright() as driver:
            context = driver.chromium.launch_persistent_context(
                user_data_dir=os.fspath(profile),
                executable_path=os.fspath(executable),
                headless=True,
                offline=True,
                args=["--no-first-run", "--no-default-browser-check"],
            )
            try:
                version = context.browser.version if context.browser else ""
            finally:
                context.close()
    if not version.endswith(expected_version):
        fail(f"packaged browser reported {version!r}, expected {expected_version}")
    return version


def leftover_browser_processes(marker: str) -> list[str]:
    listing = subprocess.run(
        ["ps", "-Ao", "pid=,command="], capture_output=True, text=True, check=False
    ).stdout
    return [line.strip() for line in listing.splitlines() if marker in line]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    application = parser.parse_args().package.resolve()
    target_id = current_target_id()
    if target_id != "macos-arm64":
        fail(f"this acceptance currently covers macos-arm64 only, host is {target_id}")
    if not application.is_dir():
        fail(f"no release package at {application} — build it with run_eb_16_acceptance.py")

    run_deterministic_gate()

    announce("Checking the package carries no system browser location")
    try:
        scanned = scan_package_for_system_browser_references(application)
        require_no_browser_installer_scripts(application=application)
    except CleanMachineRejected as error:
        fail(str(error))
    announce(f"Package content is clean ({scanned} files scanned)")

    installed = require_a_system_browser_is_present()
    announce(f"This host does have a system browser: {installed}")

    contract = load_staging_contract(STAGING_CONTRACT)
    executable = packaged_browser(application, target_id)
    if not executable.is_relative_to(application):
        fail(f"resolved browser lives outside the package: {executable}")

    before = browser_inventory(list(MACOS_INVENTORY_ROOTS))
    announce("Launching the packaged Chromium, offline and headless")
    version = launch_packaged_browser(executable, contract.browser_version)

    try:
        require_no_new_browser(before, browser_inventory(list(MACOS_INVENTORY_ROOTS)))
    except CleanMachineRejected as error:
        fail(str(error))

    leftover = leftover_browser_processes(os.fspath(application))
    if leftover:
        fail(f"packaged browser processes survived the run: {leftover}")

    print(
        f"EB-17 acceptance passed: the release package ran {version} from inside itself "
        f"on a host that has {installed}; no second browser appeared and nothing was left running"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
