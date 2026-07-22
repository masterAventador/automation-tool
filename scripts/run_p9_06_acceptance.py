#!/usr/bin/env python3
"""Run explicit clean-install acceptance for a shipped macOS DMG."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
FRONTEND_ROOT: Final = REPOSITORY_ROOT / "frontend"
APP_IDENTIFIER: Final = "com.aventador.automationtool"
INSTALL_NAME: Final = "Automation Tool P9-06 Acceptance.app"
INSTALLED_APP: Final = Path.home() / "Applications" / INSTALL_NAME
APP_DATA: Final = Path.home() / "Library" / "Application Support" / APP_IDENTIFIER
EXISTING_PRODUCT_APPS: Final = (
    Path("/Applications/自动化运营工具.app"),
    Path.home() / "Applications" / "自动化运营工具.app",
)
EXECUTOR_RESOURCE: Final = Path("Contents/Resources/local-executor/package")
EVIDENCE_SCHEMA: Final = "p9-06.macos-clean-install.v1"
SYSTEM_PATH: Final = "/usr/bin:/bin:/usr/sbin:/sbin"
PYTHON_PROCESS = re.compile(
    r"(?:^|/)(?:python|python3(?:\.\d+)?)(?:\s|$)", re.IGNORECASE
)
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
        "external_browser_open",
        "确认 App 已打开可见的外部 Chrome 或 Edge，且当前仍保持打开",
    ),
    (
        "platform_scan_detected",
        "完成一次扫码，并确认 App 检测到平台登录成功",
    ),
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
    (
        "no_duplicate_action",
        "确认恢复过程没有重新执行已经完成或结果不确定的动作",
    ),
)


@dataclass(frozen=True)
class Arguments:
    dmg: Path
    evidence: Path
    interactive_device_acceptance: bool


@dataclass(frozen=True)
class ProcessRecord:
    pid: int
    parent_pid: int
    command: str


@dataclass(frozen=True)
class RuntimeFacts:
    browser: str
    executor_process_count: int
    python_descendant_count: int


@dataclass(frozen=True)
class PackageFacts:
    bundle_identifier: str
    package_sha256: str
    version: str


def parse_arguments() -> Arguments:
    parser = argparse.ArgumentParser(
        description="P9-06 explicit macOS clean-install device acceptance",
    )
    parser.add_argument("--interactive-device-acceptance", action="store_true")
    parser.add_argument("--dmg", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parsed = parser.parse_args()
    return Arguments(
        dmg=parsed.dmg,
        evidence=parsed.evidence,
        interactive_device_acceptance=parsed.interactive_device_acceptance,
    )


def require_plain_absolute_file(path: Path, *, suffix: str) -> Path:
    if not path.is_absolute() or path.suffix.lower() != suffix:
        raise RuntimeError("P9-06 input artifact is invalid")
    cursor = path
    while cursor != cursor.parent:
        metadata = cursor.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise RuntimeError("P9-06 input artifact is invalid")
        cursor = cursor.parent
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
        raise RuntimeError("P9-06 input artifact is invalid")
    return path.resolve(strict=True)


def require_evidence_destination(path: Path) -> Path:
    if not path.is_absolute() or path.suffix.lower() != ".json" or path.exists():
        raise RuntimeError("P9-06 evidence destination is invalid")
    parent = path.parent
    if not parent.is_dir() or parent.is_symlink():
        raise RuntimeError("P9-06 evidence destination is invalid")
    resolved_parent = parent.resolve(strict=True)
    for forbidden in (INSTALLED_APP, APP_DATA):
        if resolved_parent == forbidden or forbidden in resolved_parent.parents:
            raise RuntimeError("P9-06 evidence destination is invalid")
    return resolved_parent / path.name


def require_device_boundary(arguments: Arguments) -> tuple[Path, Path]:
    if sys.platform != "darwin":
        raise RuntimeError("P9-06 clean-install acceptance requires macOS")
    if not arguments.interactive_device_acceptance:
        raise RuntimeError("P9-06 requires --interactive-device-acceptance")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError("P9-06 requires an interactive console")
    if os.geteuid() == 0:
        raise RuntimeError("P9-06 refuses to run as root")
    if INSTALLED_APP.exists() or INSTALLED_APP.is_symlink():
        raise RuntimeError("Refusing to reuse an existing P9-06 installation")
    if any(path.exists() or path.is_symlink() for path in EXISTING_PRODUCT_APPS):
        raise RuntimeError("Refusing to run beside an existing product installation")
    if APP_DATA.exists() or APP_DATA.is_symlink():
        raise RuntimeError("Refusing to reuse existing production AppData")
    return (
        require_plain_absolute_file(arguments.dmg, suffix=".dmg"),
        require_evidence_destination(arguments.evidence),
    )


def run_checked(
    arguments: list[str],
    *,
    capture: bool = False,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        check=True,
        cwd=FRONTEND_ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        timeout=300,
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def one_mounted_app(mount: Path) -> Path:
    candidates = sorted(path for path in mount.glob("*.app") if path.is_dir())
    if len(candidates) != 1 or candidates[0].is_symlink():
        raise RuntimeError("P9-06 DMG must contain exactly one plain App bundle")
    return candidates[0]


def read_bundle_facts(app: Path, package_sha256: str) -> PackageFacts:
    with (app / "Contents/Info.plist").open("rb") as source:
        information = plistlib.load(source)
    bundle_identifier = information.get("CFBundleIdentifier")
    version = information.get("CFBundleShortVersionString")
    if (
        bundle_identifier != APP_IDENTIFIER
        or not isinstance(version, str)
        or not version
    ):
        raise RuntimeError("P9-06 App identity is invalid")
    return PackageFacts(
        bundle_identifier=bundle_identifier,
        package_sha256=package_sha256,
        version=version,
    )


def bundle_binary(app: Path) -> Path:
    with (app / "Contents/Info.plist").open("rb") as source:
        executable = plistlib.load(source).get("CFBundleExecutable")
    if (
        not isinstance(executable, str)
        or not executable
        or Path(executable).name != executable
    ):
        raise RuntimeError("P9-06 App executable identity is invalid")
    binary = app / "Contents/MacOS" / executable
    if binary.is_symlink() or not binary.is_file() or not os.access(binary, os.X_OK):
        raise RuntimeError("P9-06 App executable is unavailable")
    return binary


def verify_release_app(app: Path) -> None:
    run_checked(["codesign", "--verify", "--deep", "--strict", os.fspath(app)])
    signature = run_checked(
        ["codesign", "--display", "--verbose=4", os.fspath(app)],
        capture=True,
    )
    rendered = f"{signature.stdout}\n{signature.stderr}"
    if (
        "Authority=Developer ID Application:" not in rendered
        or "TeamIdentifier=not set" in rendered
        or "Signature=adhoc" in rendered
    ):
        raise RuntimeError("P9-06 requires a Developer ID signed App")
    run_checked(
        ["spctl", "--assess", "--type", "execute", "--verbose=4", os.fspath(app)]
    )
    run_checked(["xcrun", "stapler", "validate", os.fspath(app)])
    executor = app / EXECUTOR_RESOURCE
    run_checked(
        [
            "node",
            "scripts/audit-release-bundle.mjs",
            "--bundle-root",
            os.fspath(app),
            "--executor-package",
            os.fspath(executor),
            "--platform",
            "macos",
        ]
    )


def file_inventory(root: Path) -> dict[str, tuple[int, str]]:
    inventory: dict[str, tuple[int, str]] = {}
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or (
            not stat.S_ISDIR(metadata.st_mode) and not stat.S_ISREG(metadata.st_mode)
        ):
            raise RuntimeError("P9-06 App contains an unsafe filesystem entry")
        if stat.S_ISREG(metadata.st_mode):
            inventory[path.relative_to(root).as_posix()] = (
                metadata.st_size,
                sha256(path),
            )
    return inventory


def install_app(source: Path) -> None:
    applications = INSTALLED_APP.parent
    if applications.exists():
        if applications.is_symlink() or not applications.is_dir():
            raise RuntimeError("P9-06 per-user Applications directory is unsafe")
    else:
        applications.mkdir(mode=0o700)
    run_checked(
        [
            "/usr/bin/ditto",
            "--rsrc",
            "--extattr",
            os.fspath(source),
            os.fspath(INSTALLED_APP),
        ]
    )
    if file_inventory(source) != file_inventory(INSTALLED_APP):
        raise RuntimeError("P9-06 installed App inventory changed")
    verify_release_app(INSTALLED_APP)


def clean_launch_environment() -> dict[str, str]:
    allowed = {
        "HOME",
        "LANG",
        "LC_ALL",
        "LOGNAME",
        "SHELL",
        "TMPDIR",
        "USER",
        "__CF_USER_TEXT_ENCODING",
    }
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    environment["PATH"] = SYSTEM_PATH
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        environment.pop(name, None)
    for name in tuple(environment):
        if name.startswith("AUTOMATION_TOOL_"):
            environment.pop(name, None)
    return environment


def process_snapshot() -> list[ProcessRecord]:
    completed = subprocess.run(
        ["/bin/ps", "-axo", "pid=,ppid=,command="],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=10,
    )
    records: list[ProcessRecord] = []
    for line in completed.stdout.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) != 3:
            continue
        try:
            records.append(
                ProcessRecord(
                    pid=int(fields[0]), parent_pid=int(fields[1]), command=fields[2]
                )
            )
        except ValueError:
            continue
    return records


def descendant_records(
    root_pid: int, records: list[ProcessRecord]
) -> list[ProcessRecord]:
    descendant_ids = {root_pid}
    changed = True
    while changed:
        changed = False
        for record in records:
            if record.parent_pid in descendant_ids and record.pid not in descendant_ids:
                descendant_ids.add(record.pid)
                changed = True
    return [record for record in records if record.pid in descendant_ids]


def wait_for_app_process(binary: Path, timeout: float = 30.0) -> int:
    deadline = time.monotonic() + timeout
    rendered = os.fspath(binary)
    while time.monotonic() < deadline:
        matches = [
            record.pid for record in process_snapshot() if rendered in record.command
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RuntimeError("P9-06 observed more than one installed App process")
        time.sleep(0.25)
    raise RuntimeError("P9-06 installed App did not start")


def wait_for_process_exit(pid: int, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(record.pid != pid for record in process_snapshot()):
            return
        time.sleep(0.25)
    raise RuntimeError("P9-06 App did not exit cleanly")


def sample_runtime_facts(app_pid: int) -> RuntimeFacts:
    descendants = descendant_records(app_pid, process_snapshot())
    python_count = sum(
        bool(PYTHON_PROCESS.search(record.command)) for record in descendants
    )
    executor_count = sum("local-executor" in record.command for record in descendants)
    browsers: set[str] = set()
    expected_profile = f"--user-data-dir={APP_DATA / 'browser-profiles'}"
    for record in descendants:
        if "Google Chrome" in record.command:
            browsers.add("chrome")
        if "Microsoft Edge" in record.command:
            browsers.add("edge")
    if python_count != 0:
        raise RuntimeError("P9-06 App process tree depends on Python")
    if executor_count < 1:
        raise RuntimeError("P9-06 Local Executor process was not observed")
    if len(browsers) != 1:
        raise RuntimeError("P9-06 requires exactly one trusted browser product")
    browser_records = [
        record
        for record in descendants
        if "Google Chrome" in record.command or "Microsoft Edge" in record.command
    ]
    if not any(expected_profile in record.command for record in browser_records):
        raise RuntimeError(
            "P9-06 browser did not use the App-owned browser-profiles root"
        )
    return RuntimeFacts(
        browser=next(iter(browsers)),
        executor_process_count=executor_count,
        python_descendant_count=python_count,
    )


def confirm_checkpoint(code: str, instruction: str) -> None:
    print(f"\n[P9-06] {instruction}。")
    entered = input(f"完成后输入 {code}：").strip()
    if entered != code:
        raise RuntimeError("P9-06 operator checkpoint was not confirmed")


def launch_app() -> tuple[subprocess.Popen[str], int]:
    process = subprocess.Popen(
        ["/usr/bin/open", "-n", "-W", os.fspath(INSTALLED_APP)],
        env=clean_launch_environment(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )
    try:
        return process, wait_for_app_process(bundle_binary(INSTALLED_APP))
    except BaseException:
        process.terminate()
        process.wait(timeout=10)
        raise


def require_process_tree_gone(app_pid: int) -> None:
    wait_for_process_exit(app_pid)
    records = process_snapshot()
    residual = [
        record
        for record in records
        if os.fspath(INSTALLED_APP) in record.command
        or os.fspath(APP_DATA) in record.command
    ]
    if residual:
        raise RuntimeError("P9-06 installed App left a managed process behind")


def normal_close_checkpoint(
    process: subprocess.Popen[str], app_pid: int, code: str
) -> None:
    confirm_checkpoint(
        code, "从 App 菜单正常退出，并等待外部浏览器与 Local Executor 一起关闭"
    )
    require_process_tree_gone(app_pid)
    process.wait(timeout=10)
    if process.returncode != 0:
        raise RuntimeError("P9-06 App launch wrapper failed")


def write_evidence(
    destination: Path,
    package: PackageFacts,
    runtime: RuntimeFacts,
) -> None:
    build = run_checked(["sw_vers", "-buildVersion"], capture=True).stdout.strip()
    document: dict[str, object] = {
        "schemaVersion": EVIDENCE_SCHEMA,
        "taskId": "P9-06",
        "completedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "platform": {
            "name": "macos",
            "architecture": platform.machine().lower(),
            "osVersion": platform.mac_ver()[0],
            "osBuild": build,
        },
        "package": {
            "bundleIdentifier": package.bundle_identifier,
            "version": package.version,
            "packageSha256": package.package_sha256,
            "developerIdVerified": True,
            "gatekeeperAccepted": True,
            "notarizationTicketValidated": True,
        },
        "installation": {
            "cleanAppData": True,
            "perUser": True,
            "launchCount": 2,
        },
        "runtime": {
            "browser": runtime.browser,
            "privateBrowserProfile": True,
            "executorObserved": runtime.executor_process_count > 0,
            "pythonDescendantCount": runtime.python_descendant_count,
        },
        "journey": {
            **{code: True for code, _ in CHECKPOINTS},
            **{code: True for code, _ in RECOVERY_CHECKPOINTS},
        },
    }
    payload = (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    descriptor = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        mode=0o600,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
    finally:
        os.close(descriptor)


def move_to_trash(path: Path) -> None:
    if not path.exists():
        return
    if path.is_symlink():
        raise RuntimeError("P9-06 refuses to clean an unsafe path")
    trash = Path.home() / ".Trash"
    if not trash.exists():
        trash.mkdir(mode=0o700)
    if trash.is_symlink() or not trash.is_dir():
        raise RuntimeError("P9-06 Trash directory is unsafe")
    destination = trash / f"p9-06-{path.name}-{uuid.uuid4().hex}"
    shutil.move(os.fspath(path), os.fspath(destination))


def process_still_owned(expected: ProcessRecord) -> bool:
    return any(
        record.pid == expected.pid and record.command == expected.command
        for record in process_snapshot()
    )


def stop_failed_launch(
    process: subprocess.Popen[str] | None, app_pid: int | None
) -> None:
    owned_processes: list[ProcessRecord] = []
    if app_pid is not None:
        owned_processes = descendant_records(app_pid, process_snapshot())
        try:
            os.kill(app_pid, signal.SIGTERM)
            wait_for_process_exit(app_pid, timeout=15)
        except (OSError, RuntimeError):
            pass
        for record in reversed(owned_processes):
            if not process_still_owned(record):
                continue
            try:
                os.kill(record.pid, signal.SIGTERM)
            except OSError:
                continue
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and any(
            process_still_owned(record) for record in owned_processes
        ):
            time.sleep(0.1)
        for record in reversed(owned_processes):
            if not process_still_owned(record):
                continue
            try:
                os.kill(record.pid, signal.SIGKILL)
            except OSError:
                continue
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def main() -> int:
    arguments = parse_arguments()
    dmg, evidence = require_device_boundary(arguments)
    run_checked(["hdiutil", "verify", os.fspath(dmg)])
    package_hash = sha256(dmg)
    process: subprocess.Popen[str] | None = None
    app_pid: int | None = None
    package: PackageFacts | None = None
    runtime: RuntimeFacts | None = None

    try:
        with tempfile.TemporaryDirectory(
            prefix="automation-tool-p906-mount-", dir="/private/tmp"
        ) as raw:
            mount = Path(raw)
            run_checked(
                [
                    "hdiutil",
                    "attach",
                    "-nobrowse",
                    "-readonly",
                    "-mountpoint",
                    os.fspath(mount),
                    os.fspath(dmg),
                ]
            )
            try:
                source_app = one_mounted_app(mount)
                package = read_bundle_facts(source_app, package_hash)
                verify_release_app(source_app)
                install_app(source_app)
            finally:
                run_checked(["hdiutil", "detach", os.fspath(mount)])

        print(
            "[P9-06] 仅允许自有或明确授权的测试账号；本验收使用 browse，"
            "不得评论、私信、绕过验证码或接管用户默认浏览器 Profile。"
        )
        process, app_pid = launch_app()
        for code, instruction in CHECKPOINTS:
            confirm_checkpoint(code, instruction)
            if code == "external_browser_open":
                runtime = sample_runtime_facts(app_pid)
        if (
            not APP_DATA.is_dir()
            or APP_DATA.is_symlink()
            or stat.S_IMODE(APP_DATA.stat().st_mode) != 0o700
        ):
            raise RuntimeError("P9-06 App did not create private AppData")
        normal_close_checkpoint(process, app_pid, "first_app_closed")
        process = None
        app_pid = None

        process, app_pid = launch_app()
        for code, instruction in RECOVERY_CHECKPOINTS:
            confirm_checkpoint(code, instruction)
        normal_close_checkpoint(process, app_pid, "second_app_closed")
        process = None
        app_pid = None

    finally:
        stop_failed_launch(process, app_pid)
        residual = [
            record
            for record in process_snapshot()
            if os.fspath(INSTALLED_APP) in record.command
            or os.fspath(APP_DATA) in record.command
        ]
        if residual:
            raise RuntimeError("P9-06 could not clean its managed process tree")
        if INSTALLED_APP.exists():
            move_to_trash(INSTALLED_APP)
        if APP_DATA.exists():
            move_to_trash(APP_DATA)
    if package is None or runtime is None:
        raise RuntimeError("P9-06 acceptance facts are incomplete")
    write_evidence(evidence, package, runtime)
    print(f"[P9-06] macOS clean-install device acceptance passed; evidence: {evidence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
