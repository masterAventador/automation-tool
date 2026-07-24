#!/usr/bin/env python3
"""Run explicit clean-install acceptance for a shipped Windows NSIS installer."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Final
from uuid import UUID

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
FRONTEND_ROOT: Final = REPOSITORY_ROOT / "frontend"
PRODUCT_NAME: Final = "自动化运营工具"
APP_IDENTIFIER: Final = "com.aventador.automationtool"
EXECUTOR_RESOURCE: Final = Path("local-executor/package")
EMBEDDED_BROWSER_RESOURCE: Final = Path("embedded-browser")
EMBEDDED_BROWSER_MANIFEST: Final = "distribution-manifest.v1.json"
EMBEDDED_PROFILE_ROOT: Final = Path("embedded-browser-profiles")
EVIDENCE_SCHEMA: Final = "p9-07.windows-clean-install.v1"
CODE_SIGNING_EKU: Final = "1.3.6.1.5.5.7.3.3"
_FILE_ATTRIBUTE_REPARSE_POINT: Final = 0x400
_PYTHON_PROCESS = re.compile(r"^python(?:3(?:\.\d+)?)?\.exe$", re.IGNORECASE)
CHECKPOINTS: Final[tuple[tuple[str, str], ...]] = (
    (
        "startup_diagnostics_ready",
        "确认启动页的 Control Plane、Local Executor、浏览器和数据目录诊断均已就绪",
    ),
    (
        "platform_login_required",
        "用授权抖音测试账号创建 browse 任务，并确认 App 进入等待平台登录",
    ),
    (
        "embedded_browser_open",
        "确认 App 已打开安装包内置 Chromium 的可见运营窗口，且当前仍保持打开",
    ),
    ("platform_scan_detected", "完成一次扫码，并确认 App 检测到平台登录成功"),
    (
        "task_preview_confirmed",
        "确认真实目标预览可见，排除一个目标并提交最终确认",
    ),
    (
        "controlled_task_completed",
        "确认仅执行无写入副作用的受控 browse 任务，并到达终态",
    ),
    (
        "structured_results_visible",
        "确认每个目标均显示结构化结果和可核对证据摘要",
    ),
)
RECOVERY_CHECKPOINTS: Final[tuple[tuple[str, str], ...]] = (
    (
        "platform_session_reused",
        "确认第二次启动复用了刚才的平台登录态，没有再次要求扫码",
    ),
    (
        "task_snapshot_recovered",
        "确认第二次启动恢复了同一任务快照、时间线和目标结果",
    ),
    ("no_duplicate_action", "确认恢复过程没有重新执行已完成或结果不确定的动作"),
    (
        "forced_exit_ready",
        "重新打开内置 Chromium 平台处理窗口并保持打开，准备只强停 App 主进程",
    ),
)
FORCED_RECOVERY_CHECKPOINTS: Final[tuple[tuple[str, str], ...]] = (
    (
        "forced_exit_recovered",
        "确认第三次启动从强停恢复，且启动诊断重新达到就绪",
    ),
    (
        "forced_task_snapshot_recovered",
        "确认强停后仍恢复同一任务快照、时间线和目标结果",
    ),
    ("forced_no_duplicate_action", "确认强停恢复没有重放任何既有外部动作"),
)


@dataclass(frozen=True)
class Arguments:
    evidence: Path
    installer: Path
    interactive_device_acceptance: bool


@dataclass(frozen=True)
class AuthenticodeFacts:
    signer_thumbprint: str


@dataclass(frozen=True)
class RegistryInstallation:
    display_version: str
    install_location: Path
    uninstaller: Path


@dataclass(frozen=True)
class ProcessRecord:
    command_line: str
    executable_path: str
    name: str
    parent_pid: int
    pid: int


@dataclass(frozen=True)
class WindowFacts:
    awareness: int
    height: int
    width: int
    window_dpi: int


@dataclass(frozen=True)
class RuntimeFacts:
    browser: str
    executor_process_count: int
    python_descendant_count: int
    window: WindowFacts


def parse_arguments() -> Arguments:
    parser = argparse.ArgumentParser(
        description="P9-07 explicit Windows clean-install device acceptance"
    )
    parser.add_argument("--interactive-device-acceptance", action="store_true")
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parsed = parser.parse_args()
    return Arguments(
        evidence=parsed.evidence,
        installer=parsed.installer,
        interactive_device_acceptance=parsed.interactive_device_acceptance,
    )


def powershell_executable() -> str:
    executable = shutil.which("powershell.exe") or shutil.which("powershell")
    if executable is None:
        raise RuntimeError("P9-07 Windows PowerShell is unavailable")
    return executable


def run_checked(
    arguments: list[str],
    *,
    capture: bool = False,
    environment: dict[str, str] | None = None,
    timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        check=True,
        cwd=FRONTEND_ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        timeout=timeout,
    )


def run_powershell(script: str, *arguments: str) -> str:
    return run_checked(
        [
            powershell_executable(),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
            *arguments,
        ],
        capture=True,
    ).stdout.strip()


def require_non_elevated_process() -> None:
    elevated = run_powershell(
        "$identity=[Security.Principal.WindowsIdentity]::GetCurrent();"
        "$principal=[Security.Principal.WindowsPrincipal]::new($identity);"
        "$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)"
    )
    if elevated.casefold() != "false":
        raise RuntimeError("P9-07 acceptance must run as a non-elevated user")


def local_app_data() -> Path:
    value = os.environ.get("LOCALAPPDATA")
    if not value:
        raise RuntimeError("P9-07 LocalAppData is unavailable")
    return Path(value).resolve(strict=True)


def roaming_app_data() -> Path:
    value = os.environ.get("APPDATA")
    if not value:
        raise RuntimeError("P9-07 roaming AppData is unavailable")
    return Path(value).resolve(strict=True)


def install_root() -> Path:
    return local_app_data() / PRODUCT_NAME


def private_app_data() -> Path:
    return roaming_app_data() / APP_IDENTIFIER


def normalized_path(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def require_plain_absolute_file(path: Path, suffix: str) -> Path:
    if not path.is_absolute() or path.suffix.lower() != suffix:
        raise RuntimeError("P9-07 input artifact is invalid")
    cursor = path
    while cursor != cursor.parent:
        cursor_metadata = cursor.lstat()
        if stat.S_ISLNK(cursor_metadata.st_mode) or bool(
            getattr(cursor_metadata, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise RuntimeError("P9-07 input artifact is invalid")
        cursor = cursor.parent
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size <= 0
        or stat.S_ISLNK(metadata.st_mode)
        or bool(getattr(metadata, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT)
    ):
        raise RuntimeError("P9-07 input artifact is invalid")
    return path.resolve(strict=True)


def require_evidence_destination(path: Path) -> Path:
    if not path.is_absolute() or path.suffix.lower() != ".json" or path.exists():
        raise RuntimeError("P9-07 evidence destination is invalid")
    raw_parent = path.parent
    parent_metadata = raw_parent.lstat()
    if (
        not raw_parent.is_dir()
        or raw_parent.is_symlink()
        or bool(getattr(parent_metadata, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT)
    ):
        raise RuntimeError("P9-07 evidence destination is invalid")
    parent = raw_parent.resolve(strict=True)
    for forbidden in (install_root(), private_app_data()):
        if parent == forbidden or forbidden in parent.parents:
            raise RuntimeError("P9-07 evidence destination is invalid")
    return parent / path.name


def require_device_boundary(arguments: Arguments) -> tuple[Path, Path]:
    if sys.platform != "win32" or platform.system() != "Windows":
        raise RuntimeError("P9-07 clean-install acceptance requires Windows")
    if platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("P9-07 clean-install acceptance requires Windows x86_64")
    if not arguments.interactive_device_acceptance:
        raise RuntimeError("P9-07 requires --interactive-device-acceptance")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError("P9-07 requires an interactive console")
    require_non_elevated_process()
    if install_root().exists() or private_app_data().exists():
        raise RuntimeError("Refusing to reuse an existing product installation or AppData")
    if windows_registry_installations(machine_wide=False) or windows_registry_installations(
        machine_wide=True
    ):
        raise RuntimeError("Refusing to reuse an existing product registry installation")
    return (
        require_plain_absolute_file(arguments.installer, ".exe"),
        require_evidence_destination(arguments.evidence),
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_authenticode(path: Path, expected_signer: str | None = None) -> AuthenticodeFacts:
    rendered = run_powershell(
        "& { param([string]$LiteralPath) "
        "$signature=Get-AuthenticodeSignature -LiteralPath $LiteralPath;"
        "$chain=[Security.Cryptography.X509Certificates.X509Chain]::new();"
        "$chain.ChainPolicy.RevocationMode='Online';"
        "$chainValid=$false;"
        "if($null -ne $signature.SignerCertificate){"
        "$chainValid=$chain.Build($signature.SignerCertificate)};"
        "$ekus=@();"
        "if($null -ne $signature.SignerCertificate){"
        "$ekus=@($signature.SignerCertificate.EnhancedKeyUsageList | "
        "ForEach-Object {$_.ObjectId.Value})};"
        "[pscustomobject]@{"
        "Status=$signature.Status.ToString();"
        "SignerThumbprint=$signature.SignerCertificate.Thumbprint;"
        "TimeStamperCertificate=($null -ne $signature.TimeStamperCertificate);"
        f"CodeSigning=($ekus -contains '{CODE_SIGNING_EKU}');"
        "ChainValid=$chainValid} | ConvertTo-Json -Compress }",
        os.fspath(path),
    )
    try:
        document = json.loads(rendered)
    except json.JSONDecodeError as error:
        raise RuntimeError("P9-07 Authenticode evidence is invalid") from error
    if not isinstance(document, dict) or set(document) != {
        "ChainValid",
        "CodeSigning",
        "SignerThumbprint",
        "Status",
        "TimeStamperCertificate",
    }:
        raise RuntimeError("P9-07 Authenticode evidence is invalid")
    thumbprint = document.get("SignerThumbprint")
    if (
        document.get("Status") != "Valid"
        or document.get("ChainValid") is not True
        or document.get("CodeSigning") is not True
        or document.get("TimeStamperCertificate") is not True
        or not isinstance(thumbprint, str)
        or re.fullmatch(r"[0-9A-Fa-f]{40,64}", thumbprint) is None
        or (expected_signer is not None and thumbprint.casefold() != expected_signer.casefold())
    ):
        raise RuntimeError("P9-07 requires one valid timestamped Authenticode signer")
    return AuthenticodeFacts(signer_thumbprint=thumbprint.upper())


def windows_registry_installations(*, machine_wide: bool) -> list[RegistryInstallation]:
    winreg: Any = importlib.import_module("winreg")
    hive = winreg.HKEY_LOCAL_MACHINE if machine_wide else winreg.HKEY_CURRENT_USER
    parent_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    records: list[RegistryInstallation] = []
    seen: set[tuple[str, str, str]] = set()
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            parent = winreg.OpenKey(hive, parent_path, 0, winreg.KEY_READ | view)
        except OSError:
            continue
        with parent:
            for index in range(winreg.QueryInfoKey(parent)[0]):
                try:
                    child = winreg.OpenKey(parent, winreg.EnumKey(parent, index))
                    with child:
                        display_name = str(winreg.QueryValueEx(child, "DisplayName")[0])
                        if display_name != PRODUCT_NAME:
                            continue
                        version = str(winreg.QueryValueEx(child, "DisplayVersion")[0])
                        try:
                            location = Path(str(winreg.QueryValueEx(child, "InstallLocation")[0]))
                        except OSError:
                            location = install_root()
                        uninstall = str(winreg.QueryValueEx(child, "UninstallString")[0]).strip()
                        uninstaller = Path(uninstall.strip('"'))
                        identity = (
                            version.casefold(),
                            normalized_path(location),
                            normalized_path(uninstaller),
                        )
                        if identity not in seen:
                            seen.add(identity)
                            records.append(
                                RegistryInstallation(
                                    display_version=version,
                                    install_location=location,
                                    uninstaller=uninstaller,
                                )
                            )
                except OSError:
                    continue
    return records


def wait_for_installation() -> RegistryInstallation:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        current = windows_registry_installations(machine_wide=False)
        if len(current) == 1 and current[0].uninstaller.is_file():
            return current[0]
        time.sleep(0.2)
    raise RuntimeError("P9-07 current-user installation did not complete")


def verify_registry_installation(record: RegistryInstallation) -> None:
    expected_root = install_root()
    if windows_registry_installations(machine_wide=True):
        raise RuntimeError("P9-07 installer wrote HKEY_LOCAL_MACHINE")
    if normalized_path(record.install_location) != normalized_path(
        expected_root
    ) or normalized_path(record.uninstaller) != normalized_path(expected_root / "uninstall.exe"):
        raise RuntimeError("P9-07 InstallLocation or UninstallString is invalid")


def installed_binary(root: Path) -> Path:
    candidates = sorted(
        path
        for path in root.glob("*.exe")
        if path.is_file() and path.name.casefold() != "uninstall.exe"
    )
    if len(candidates) != 1:
        raise RuntimeError("P9-07 installed main executable is not unique")
    return candidates[0]


def embedded_browser_executable(root: Path) -> Path:
    distribution_root = root / EMBEDDED_BROWSER_RESOURCE
    manifest_path = distribution_root / EMBEDDED_BROWSER_MANIFEST
    try:
        distribution_metadata = distribution_root.lstat()
        manifest_metadata = manifest_path.lstat()
    except OSError as error:
        raise RuntimeError("P9-07 embedded Chromium distribution manifest is invalid") from error
    if (
        distribution_root.is_symlink()
        or manifest_path.is_symlink()
        or bool(
            getattr(distribution_metadata, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        )
        or bool(getattr(manifest_metadata, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT)
    ):
        raise RuntimeError("P9-07 embedded Chromium distribution manifest is invalid")
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("P9-07 embedded Chromium distribution manifest is invalid") from error
    if (
        not isinstance(document, dict)
        or document.get("schemaVersion") != 1
        or document.get("policy") != "fail_closed"
        or document.get("target") != "windows-x86_64"
    ):
        raise RuntimeError("P9-07 embedded Chromium distribution manifest is invalid")
    relative_value = document.get("executable")
    if not isinstance(relative_value, str):
        raise RuntimeError("P9-07 embedded Chromium executable is invalid")
    relative = PurePosixPath(relative_value)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or str(relative) != relative_value
        or relative.name.casefold() != "chrome.exe"
    ):
        raise RuntimeError("P9-07 embedded Chromium executable is invalid")
    executable = distribution_root.joinpath(*relative.parts)
    try:
        resolved_root = distribution_root.resolve(strict=True)
        resolved_executable = executable.resolve(strict=True)
    except OSError as error:
        raise RuntimeError("P9-07 embedded Chromium executable is invalid") from error
    if (
        resolved_executable.parent == resolved_root
        or resolved_root not in resolved_executable.parents
        or not resolved_executable.is_file()
        or resolved_executable.is_symlink()
        or bool(
            getattr(resolved_executable.lstat(), "st_file_attributes", 0)
            & _FILE_ATTRIBUTE_REPARSE_POINT
        )
    ):
        raise RuntimeError("P9-07 embedded Chromium executable is invalid")
    return resolved_executable


def audit_release_bundle(root: Path) -> None:
    node = shutil.which("node.exe") or shutil.which("node")
    if node is None:
        raise RuntimeError("P9-07 Node executable is unavailable for the package audit")
    run_checked(
        [
            node,
            "scripts/audit-release-bundle.mjs",
            "--bundle-root",
            os.fspath(root),
            "--executor-package",
            os.fspath(root / EXECUTOR_RESOURCE),
            "--platform",
            "windows",
        ],
        environment=clean_runtime_environment(),
    )


def clean_runtime_environment() -> dict[str, str]:
    allowed = {
        "ALLUSERSPROFILE",
        "APPDATA",
        "COMMONPROGRAMFILES",
        "COMMONPROGRAMFILES(X86)",
        "COMSPEC",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "LOGONSERVER",
        "PATHEXT",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERDOMAIN",
        "USERNAME",
        "USERPROFILE",
        "WINDIR",
    }
    environment = {
        name.upper(): value for name, value in os.environ.items() if name.upper() in allowed
    }
    system_root = environment.get("SYSTEMROOT") or environment.get("WINDIR")
    if not system_root:
        raise RuntimeError("P9-07 Windows system root is unavailable")
    environment["PATH"] = ";".join(
        (
            f"{system_root}\\System32",
            system_root,
            f"{system_root}\\System32\\Wbem",
        )
    )
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        environment.pop(name, None)
    for name in tuple(environment):
        if name.startswith("AUTOMATION_TOOL_"):
            environment.pop(name, None)
    return environment


def process_snapshot() -> list[ProcessRecord]:
    rendered = run_powershell(
        "@(Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,"
        "CommandLine,ExecutablePath) | ConvertTo-Json -Compress"
    )
    try:
        document = json.loads(rendered)
    except json.JSONDecodeError as error:
        raise RuntimeError("P9-07 process snapshot is unavailable") from error
    items = document if isinstance(document, list) else [document]
    records: list[ProcessRecord] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            records.append(
                ProcessRecord(
                    command_line=str(item.get("CommandLine") or ""),
                    executable_path=str(item.get("ExecutablePath") or ""),
                    name=str(item["Name"]),
                    parent_pid=int(item["ParentProcessId"]),
                    pid=int(item["ProcessId"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return records


def descendant_records(root_pid: int, records: list[ProcessRecord]) -> list[ProcessRecord]:
    identities = {root_pid}
    changed = True
    while changed:
        changed = False
        for record in records:
            if record.parent_pid in identities and record.pid not in identities:
                identities.add(record.pid)
                changed = True
    return [record for record in records if record.pid in identities]


def process_still_owned(
    expected: ProcessRecord, current: list[ProcessRecord] | None = None
) -> bool:
    records = process_snapshot() if current is None else current
    return any(
        record.pid == expected.pid
        and record.name.casefold() == expected.name.casefold()
        and normalized_path(Path(record.executable_path))
        == normalized_path(Path(expected.executable_path))
        for record in records
    )


_WINDOW_FACTS_SCRIPT: Final = (
    "& { param([int]$TargetPid) Add-Type -TypeDefinition @'\n"
    "using System; using System.Runtime.InteropServices;\n"
    "public static class P907Native {\n"
    "[StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }\n"
    '[DllImport("shcore.dll")] public static extern int GetProcessDpiAwareness('
    "IntPtr process, out int awareness);\n"
    '[DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr hwnd);\n'
    '[DllImport("user32.dll")] public static extern bool GetWindowRect('
    "IntPtr hwnd, out RECT rect);\n"
    '[DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hwnd); }\n'
    "'@; $process=Get-Process -Id $TargetPid -ErrorAction Stop;"
    "$handle=$process.MainWindowHandle; if($handle -eq [IntPtr]::Zero){exit 2};"
    "if(-not [P907Native]::IsWindowVisible($handle)){exit 3};"
    "$awareness=0; $result=[P907Native]::GetProcessDpiAwareness("
    "$process.Handle,[ref]$awareness); if($result -ne 0){exit 4};"
    "$rect=New-Object P907Native+RECT;"
    "if(-not [P907Native]::GetWindowRect($handle,[ref]$rect)){exit 5};"
    "[pscustomobject]@{Awareness=$awareness;WindowDpi=[P907Native]::GetDpiForWindow($handle);"
    "Width=($rect.R-$rect.L);Height=($rect.B-$rect.T)} | ConvertTo-Json -Compress }"
)


def wait_for_window_facts(pid: int) -> WindowFacts:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            rendered = run_powershell(_WINDOW_FACTS_SCRIPT, str(pid))
            document = json.loads(rendered)
            facts = WindowFacts(
                awareness=int(document["Awareness"]),
                height=int(document["Height"]),
                width=int(document["Width"]),
                window_dpi=int(document["WindowDpi"]),
            )
        except (
            subprocess.CalledProcessError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ):
            time.sleep(0.25)
            continue
        logical_width = facts.width * 96 / facts.window_dpi
        logical_height = facts.height * 96 / facts.window_dpi
        if (
            facts.awareness != 2
            or facts.window_dpi < 120
            or facts.window_dpi > 480
            or logical_width < 960
            or logical_height < 640
        ):
            raise RuntimeError("P9-07 high-DPI window boundary is invalid")
        return facts
    raise RuntimeError("P9-07 visible main window did not become available")


def is_process_in_job(pid: int) -> bool:
    result = run_powershell(
        "& { param([int]$TargetPid) Add-Type -TypeDefinition @'\n"
        "using System; using System.Runtime.InteropServices;\n"
        "public static class P907Job {"
        '[DllImport("kernel32.dll", SetLastError=true)] public static extern bool '
        "IsProcessInJob(IntPtr process, IntPtr job, out bool result); }\n"
        "'@; $process=Get-Process -Id $TargetPid -ErrorAction Stop; $inside=$false;"
        "if(-not [P907Job]::IsProcessInJob($process.Handle,[IntPtr]::Zero,[ref]$inside)){exit 2};"
        "$inside }",
        str(pid),
    )
    return result.casefold() == "true"


def sample_runtime_facts(
    app_pid: int, window: WindowFacts, expected_browser_executable: Path
) -> tuple[RuntimeFacts, list[ProcessRecord]]:
    descendants = descendant_records(app_pid, process_snapshot())
    python_count = sum(bool(_PYTHON_PROCESS.fullmatch(record.name)) for record in descendants)
    executors = [
        record for record in descendants if record.name.casefold() == "automation-tool-executor.exe"
    ]
    browser_records = [
        record for record in descendants if record.name.casefold() in {"chrome.exe", "msedge.exe"}
    ]
    expected_profile_root = private_app_data() / EMBEDDED_PROFILE_ROOT
    if python_count != 0:
        raise RuntimeError("P9-07 App process tree depends on Python")
    if len(executors) != 1 or not is_process_in_job(executors[0].pid):
        raise RuntimeError("P9-07 Local Executor is not owned by its Windows Job Object")
    if not browser_records or any(
        record.name.casefold() != "chrome.exe"
        or normalized_path(Path(record.executable_path))
        != normalized_path(expected_browser_executable)
        or not command_uses_private_profile(
            record.command_line,
            expected_profile_root,
        )
        for record in browser_records
    ):
        raise RuntimeError(
            "P9-07 runtime did not use the packaged Chromium and one "
            "App-owned embedded-browser-profiles root"
        )
    return (
        RuntimeFacts(
            browser="embedded-chromium",
            executor_process_count=len(executors),
            python_descendant_count=python_count,
            window=window,
        ),
        descendants,
    )


def command_uses_private_profile(command_line: str, expected_root: Path) -> bool:
    match = re.search(
        r'(?:^|\s)(?:"--user-data-dir=([^"]+)"|--user-data-dir="([^"]+)"|'
        r"--user-data-dir=([^\s]+))",
        command_line,
        re.IGNORECASE,
    )
    if match is None:
        return False
    raw_path = next((value for value in match.groups() if value is not None), None)
    if raw_path is None:
        return False
    try:
        normalized_root = Path(normalized_path(expected_root))
        normalized_profile = Path(normalized_path(Path(raw_path)))
        relative = normalized_profile.relative_to(normalized_root)
        profile_id = UUID(relative.parts[1])
    except (IndexError, ValueError):
        return False
    return (
        len(relative.parts) == 2
        and relative.parts[0].casefold() == "douyin"
        and profile_id.version == 4
        and str(profile_id) == relative.parts[1].casefold()
    )


def confirm_checkpoint(code: str, instruction: str) -> None:
    print(f"\n[P9-07] {instruction}。")
    if input(f"完成后输入 {code}：").strip() != code:
        raise RuntimeError("P9-07 operator checkpoint was not confirmed")


def launch_app(binary: Path) -> tuple[subprocess.Popen[str], WindowFacts]:
    process = subprocess.Popen(
        [os.fspath(binary)],
        env=clean_runtime_environment(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    try:
        return process, wait_for_window_facts(process.pid)
    except BaseException:
        process.terminate()
        process.wait(timeout=15)
        raise


def wait_owned_processes_gone(records: list[ProcessRecord], timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = process_snapshot()
        if not any(process_still_owned(record, current) for record in records):
            return
        time.sleep(0.2)
    raise RuntimeError("P9-07 managed process tree did not stop")


def path_is_within(path: str, root: Path) -> bool:
    if not path:
        return False
    try:
        return os.path.commonpath(
            (normalized_path(Path(path)), normalized_path(root))
        ) == normalized_path(root)
    except ValueError:
        return False


def managed_processes() -> list[ProcessRecord]:
    app_data_marker = normalized_path(private_app_data()).casefold()
    return [
        record
        for record in process_snapshot()
        if path_is_within(record.executable_path, install_root())
        or app_data_marker in record.command_line.replace('"', "").replace("/", "\\").casefold()
    ]


def require_no_managed_processes() -> None:
    if managed_processes():
        raise RuntimeError("P9-07 installed App left a managed process behind")


def normal_close_checkpoint(
    process: subprocess.Popen[str], descendants: list[ProcessRecord], code: str
) -> None:
    confirm_checkpoint(code, "从 App 菜单正常退出，并等待内置 Chromium 与 Local Executor 一起关闭")
    process.wait(timeout=30)
    if process.returncode != 0:
        raise RuntimeError("P9-07 App did not exit normally")
    wait_owned_processes_gone(descendants)
    require_no_managed_processes()


def force_stop_main_process(
    process: subprocess.Popen[str], descendants: list[ProcessRecord]
) -> None:
    run_powershell(
        "& { param([int]$TargetPid) Stop-Process -Id $TargetPid -Force }",
        str(process.pid),
    )
    process.wait(timeout=15)
    wait_owned_processes_gone(descendants)
    require_no_managed_processes()


def require_private_app_data() -> None:
    path = private_app_data()
    if not path.is_dir() or path.is_symlink():
        raise RuntimeError("P9-07 private AppData is unavailable")
    rendered = run_powershell(
        "& { param([string]$LiteralPath) $acl=Get-Acl -LiteralPath $LiteralPath;"
        "$current=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;"
        "$broad=@('S-1-1-0','S-1-5-11','S-1-5-32-545'); $bad=0;"
        "foreach($rule in $acl.Access){if($rule.AccessControlType -eq 'Allow'){"
        "try{$sid=$rule.IdentityReference.Translate("
        "[Security.Principal.SecurityIdentifier]).Value}catch{continue};"
        "if($broad -contains $sid){$bad++}}};"
        "$owner=$acl.Owner; try{$owner=([Security.Principal.NTAccount]$owner).Translate("
        "[Security.Principal.SecurityIdentifier]).Value}catch{};"
        "[pscustomobject]@{CurrentSid=$current;OwnerSid=$owner;BroadAllowCount=$bad} | "
        "ConvertTo-Json -Compress }",
        os.fspath(path),
    )
    document = json.loads(rendered)
    if (
        not isinstance(document, dict)
        or document.get("OwnerSid") != document.get("CurrentSid")
        or document.get("BroadAllowCount") != 0
    ):
        raise RuntimeError("P9-07 private AppData ACL is over-permissive")


def uninstall_application(record: RegistryInstallation) -> None:
    if not record.uninstaller.is_file():
        raise RuntimeError("P9-07 uninstaller is unavailable")
    run_checked(
        [os.fspath(record.uninstaller), "/S"],
        environment=clean_runtime_environment(),
        timeout=180,
    )
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if (
            not install_root().exists()
            and not windows_registry_installations(machine_wide=False)
            and not windows_registry_installations(machine_wide=True)
        ):
            if not private_app_data().is_dir():
                raise RuntimeError("P9-07 private AppData was removed by uninstall")
            return
        time.sleep(0.2)
    raise RuntimeError("P9-07 uninstaller left application files or registry state")


def move_app_data_to_recycle_bin() -> None:
    path = private_app_data()
    if not path.exists():
        return
    run_powershell(
        "& { param([string]$LiteralPath) Add-Type -AssemblyName Microsoft.VisualBasic;"
        "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory("
        "$LiteralPath,[Microsoft.VisualBasic.FileIO.UIOption]::OnlyErrorDialogs,"
        "[Microsoft.VisualBasic.FileIO.RecycleOption]::SendToRecycleBin) }",
        os.fspath(path),
    )
    if path.exists():
        raise RuntimeError("P9-07 could not move private AppData to the Recycle Bin")


def harden_evidence_acl(path: Path) -> None:
    run_powershell(
        "& { param([string]$LiteralPath) $sid=[Security.Principal.WindowsIdentity]::"
        "GetCurrent().User; $acl=[Security.AccessControl.FileSecurity]::new();"
        "$acl.SetAccessRuleProtection($true,$false);"
        "$rule=[Security.AccessControl.FileSystemAccessRule]::new("
        "$sid,'FullControl','Allow'); $acl.AddAccessRule($rule);"
        "Set-Acl -LiteralPath $LiteralPath -AclObject $acl }",
        os.fspath(path),
    )


def write_evidence(
    destination: Path,
    installer_hash: str,
    signer: AuthenticodeFacts,
    version: str,
    runtime: RuntimeFacts,
) -> None:
    document: dict[str, object] = {
        "schemaVersion": EVIDENCE_SCHEMA,
        "taskId": "P9-07",
        "completedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "platform": {
            "name": "windows",
            "architecture": platform.machine().lower(),
            "osVersion": platform.version(),
            "release": platform.release(),
        },
        "package": {
            "packageSha256": installer_hash,
            "signerThumbprint": signer.signer_thumbprint,
            "version": version,
            "authenticodeValid": True,
            "timestampValid": True,
        },
        "installation": {
            "cleanAppData": True,
            "currentUser": True,
            "launchCount": 3,
            "uninstallPreservedPrivateData": True,
        },
        "runtime": {
            "browser": runtime.browser,
            "executorObserved": runtime.executor_process_count == 1,
            "jobObjectVerified": True,
            "privateBrowserProfile": True,
            "pythonDescendantCount": runtime.python_descendant_count,
            "windowDpi": runtime.window.window_dpi,
            "dpiAwareness": runtime.window.awareness,
        },
        "journey": {
            **{code: True for code, _ in CHECKPOINTS},
            **{code: True for code, _ in RECOVERY_CHECKPOINTS},
            **{code: True for code, _ in FORCED_RECOVERY_CHECKPOINTS},
        },
    }
    payload = (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode=0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
    finally:
        os.close(descriptor)
    harden_evidence_acl(destination)


def stop_owned_runtime(
    process: subprocess.Popen[str] | None, descendants: list[ProcessRecord]
) -> None:
    if process is not None and process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            pass
    owned = {record.pid: record for record in (*descendants, *managed_processes())}
    current = process_snapshot()
    for record in reversed(tuple(owned.values())):
        if not process_still_owned(record, current):
            continue
        try:
            run_powershell(
                "& { param([int]$TargetPid) Stop-Process -Id $TargetPid -Force }",
                str(record.pid),
            )
        except subprocess.CalledProcessError:
            continue


def main() -> int:
    arguments = parse_arguments()
    installer, evidence = require_device_boundary(arguments)
    installer_signer = require_authenticode(installer)
    installer_hash = sha256(installer)
    installation_claimed = False
    record: RegistryInstallation | None = None
    process: subprocess.Popen[str] | None = None
    descendants: list[ProcessRecord] = []
    runtime: RuntimeFacts | None = None

    try:
        installation_claimed = True
        run_checked(
            [os.fspath(installer), "/S"],
            environment=clean_runtime_environment(),
            timeout=180,
        )
        record = wait_for_installation()
        verify_registry_installation(record)
        binary = installed_binary(record.install_location)
        browser_executable = embedded_browser_executable(record.install_location)
        require_authenticode(binary, installer_signer.signer_thumbprint)
        require_authenticode(record.uninstaller, installer_signer.signer_thumbprint)
        audit_release_bundle(record.install_location)

        print(
            "[P9-07] 仅允许自有或明确授权的测试账号；本验收使用 browse，"
            "不得评论、私信、绕过验证码或接管用户默认浏览器 Profile。"
        )
        process, window = launch_app(binary)
        for code, instruction in CHECKPOINTS:
            confirm_checkpoint(code, instruction)
            if code == "embedded_browser_open":
                runtime, descendants = sample_runtime_facts(
                    process.pid,
                    window,
                    browser_executable,
                )
        require_private_app_data()
        normal_close_checkpoint(process, descendants, "first_app_closed")
        process = None
        descendants = []

        process, window = launch_app(binary)
        for code, instruction in RECOVERY_CHECKPOINTS:
            confirm_checkpoint(code, instruction)
        second_runtime, descendants = sample_runtime_facts(
            process.pid,
            window,
            browser_executable,
        )
        if runtime is None or second_runtime.browser != runtime.browser:
            raise RuntimeError("P9-07 embedded browser changed across restart")
        force_stop_main_process(process, descendants)
        process = None
        descendants = []

        process, _ = launch_app(binary)
        for code, instruction in FORCED_RECOVERY_CHECKPOINTS:
            confirm_checkpoint(code, instruction)
        third_descendants = descendant_records(process.pid, process_snapshot())
        normal_close_checkpoint(process, third_descendants, "third_app_closed")
        process = None
        descendants = []

        uninstall_application(record)
        require_private_app_data()
        move_app_data_to_recycle_bin()
    finally:
        stop_owned_runtime(process, descendants)
        cleanup_record = record
        if cleanup_record is None and installation_claimed:
            current_records = windows_registry_installations(machine_wide=False)
            if len(current_records) == 1:
                cleanup_record = current_records[0]
            elif (install_root() / "uninstall.exe").is_file():
                cleanup_record = RegistryInstallation(
                    display_version="unknown",
                    install_location=install_root(),
                    uninstaller=install_root() / "uninstall.exe",
                )
        if cleanup_record is not None and cleanup_record.uninstaller.is_file():
            with suppress(RuntimeError, subprocess.CalledProcessError):
                uninstall_application(cleanup_record)
        if (
            not install_root().exists()
            and not windows_registry_installations(machine_wide=False)
            and not windows_registry_installations(machine_wide=True)
            and private_app_data().exists()
        ):
            move_app_data_to_recycle_bin()

    if (
        runtime is None
        or record is None
        or install_root().exists()
        or private_app_data().exists()
        or windows_registry_installations(machine_wide=False)
        or windows_registry_installations(machine_wide=True)
    ):
        raise RuntimeError("P9-07 final cleanup facts are incomplete")
    write_evidence(
        evidence,
        installer_hash,
        installer_signer,
        record.display_version,
        runtime,
    )
    print(f"[P9-07] Windows clean-install device acceptance passed; evidence: {evidence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
