#!/usr/bin/env python3
"""EB-17：正式包在没有系统浏览器的机器上也只认包内 Chromium。

两条互补的判据：

1. **包里不存在系统浏览器的位置**。想回退到系统 Chrome/Edge，实现总得先写下它
   装在哪。所以正式包的二进制与文本资源里不得出现任何系统浏览器安装位置的字面量。
   有字面量不代表一定会用，但没有字面量就不可能去找——这是可回归的下界。
2. **运行前后不多出第二套浏览器**。产品不允许在运行时下载浏览器，所以把已知的
   浏览器缓存与安装位置做成快照，跑完对比。目录"不存在"与"存在但为空"必须可区分，
   否则监控一个打错的路径会永远显示通过。

包内 Chromium 自己的路径里当然带 chrome 字样，所以扫描按**绝对安装位置**匹配，
不按关键词匹配。
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

# 系统浏览器的安装位置。用完整路径而不是关键词：包内 Chromium 的目录名
# （chrome-mac-arm64 / Google Chrome for Testing）本身就含 chrome。
SYSTEM_BROWSER_MARKERS: Final = (
    "/Applications/Google Chrome.app",
    "/Applications/Google Chrome Canary.app",
    "/Applications/Microsoft Edge.app",
    "/Applications/Chromium.app",
    r"C:\Program Files\Google\Chrome",
    r"C:\Program Files (x86)\Google\Chrome",
    r"C:\Program Files (x86)\Microsoft\Edge",
    r"C:\Program Files\Microsoft\Edge",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/opt/google/chrome",
    # 上游默认的浏览器下载缓存：产品运行时不允许出现下载行为，
    # 包里出现这个位置就说明还留着下载路径。
    "ms-playwright",
)

# 只读这些后缀之外的文件也可能藏字符串，所以扫描按字节读全部文件，
# 但跳过体积超过这个上限的（包内 Chromium 的框架二进制有 230MB，
# 它是上游产物，不受本规则约束）。
MAX_SCANNED_FILE_BYTES: Final = 64 * 1024 * 1024

# 上游产物：它们自己引用什么不归本规则管，本规则约束的是产品自己的代码与配置。
# 包内 Chromium 是上游浏览器；Playwright 的 driver 库支持 channel="chrome"，
# 库里当然写着 Chrome 装在哪，删不得——执行器要靠它驱动包内 Chromium。
# 上游库有没有被走到，靠运行期不下载、不启动系统浏览器来证明，不靠字符串扫描。
UPSTREAM_ARTIFACT_PATH_SEGMENTS: Final = (
    ("embedded-browser",),
    ("playwright", "driver"),
)

# 上游 driver 自带的可执行浏览器安装脚本。它们与库代码性质不同：库代码要产品
# 显式去调才有影响，这些是**现成的可执行下载器**，用户机器上任何能跑 shell 的
# 路径都能用它绕开"只用包内 Chromium"。一个都不许进包。
BROWSER_INSTALLER_DIRECTORY: Final = ("playwright", "driver", "package", "bin")
BROWSER_INSTALLER_PREFIXES: Final = ("reinstall_", "install_")


class CleanMachineRejected(RuntimeError):
    """正式包引用了系统浏览器，或运行期多出了第二套浏览器。"""


def _reject(message: str) -> None:
    raise CleanMachineRejected(f"clean machine check rejected: {message}")


def scan_package_for_system_browser_references(application: Path) -> int:
    """扫描正式包，拒绝任何系统浏览器安装位置的字面量。

    返回实际扫描的文件数，供调用方判断扫描是否落空——一个扫了 0 个文件的
    检查会永远"通过"。
    """
    if not application.is_dir():
        _reject(f"package does not exist: {application}")
    markers = {marker: marker.encode("utf-8") for marker in SYSTEM_BROWSER_MARKERS}
    scanned = 0
    for path in sorted(application.rglob("*")):
        if _matches_any_segment(path.parts, UPSTREAM_ARTIFACT_PATH_SEGMENTS):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        if path.stat().st_size > MAX_SCANNED_FILE_BYTES:
            continue
        payload = path.read_bytes()
        scanned += 1
        for marker, raw in markers.items():
            if raw in payload:
                _reject(
                    f"{path.relative_to(application).as_posix()} names a system "
                    f"browser location: {marker}"
                )
    if scanned == 0:
        _reject(f"scanned no file inside {application} — the check proved nothing")
    return scanned


def _matches_any_segment(
    parts: tuple[str, ...], segment_groups: tuple[tuple[str, ...], ...]
) -> bool:
    """路径里是否出现过任一段连续目录名。"""
    for segments in segment_groups:
        span = len(segments)
        if any(parts[index : index + span] == segments for index in range(len(parts))):
            return True
    return False


def require_no_browser_installer_scripts(application: Path) -> None:
    """拒绝包内任何可执行的系统浏览器安装脚本。

    这条与 `scan_package_for_system_browser_references` 互补：那条管产品自己的
    代码里有没有系统浏览器的位置，这条管包里有没有现成的下载器。
    """
    if not application.is_dir():
        _reject(f"package does not exist: {application}")
    found = [
        path.relative_to(application).as_posix()
        for path in sorted(application.rglob("*"))
        if path.is_file()
        and not path.is_symlink()
        and _matches_any_segment(path.parts, (BROWSER_INSTALLER_DIRECTORY,))
        and path.name.startswith(BROWSER_INSTALLER_PREFIXES)
    ]
    if found:
        _reject(f"package ships {len(found)} system browser installer script(s): {found}")


def browser_inventory(roots: list[Path]) -> dict[Path, tuple[str, ...] | None]:
    """快照浏览器缓存/安装位置的直接子项。

    值为 ``None`` 表示该位置不存在，与"存在但为空"区分开：把两者混为空集合，
    会让一个打错的监控路径永远显示通过。
    """
    inventory: dict[Path, tuple[str, ...] | None] = {}
    for root in roots:
        inventory[root] = (
            tuple(sorted(entry.name for entry in root.iterdir()))
            if root.is_dir()
            else None
        )
    return inventory


def require_no_new_browser(
    before: dict[Path, tuple[str, ...] | None],
    after: dict[Path, tuple[str, ...] | None],
) -> None:
    """拒绝运行期新出现的浏览器安装。"""
    if before.keys() != after.keys():
        _reject("browser inventory roots changed between snapshots")
    for root, previous in before.items():
        current = after[root]
        if previous is None and current is None:
            continue
        if previous is None:
            _reject(f"{root} appeared during the run: {current}")
        if current is None:
            _reject(f"{root} disappeared during the run")
        added = sorted(set(current) - set(previous))
        if added:
            _reject(f"{root} gained a browser during the run: {added}")


__all__ = [
    "BROWSER_INSTALLER_DIRECTORY",
    "BROWSER_INSTALLER_PREFIXES",
    "MAX_SCANNED_FILE_BYTES",
    "SYSTEM_BROWSER_MARKERS",
    "UPSTREAM_ARTIFACT_PATH_SEGMENTS",
    "CleanMachineRejected",
    "browser_inventory",
    "require_no_browser_installer_scripts",
    "require_no_new_browser",
    "scan_package_for_system_browser_references",
]
