#!/usr/bin/env python3
"""Build and audit one isolated material-video Worker candidate."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / "vendor/moneyprinterturbo"
WORKER = ROOT / "workers/material_montage"
CONTRACT_PATH = ROOT / "contracts/quality/material-video-worker-package.v1.json"
ENTRYPOINT = "automation-tool-material-video-worker"


class MaterialVideoWorkerPackageError(RuntimeError):
    """The isolated Worker candidate does not satisfy its release contract."""


@dataclass(frozen=True)
class MaterialVideoWorkerAudit:
    file_count: int
    package_bytes: int
    startup_seconds: float
    python_version: str
    dependency_count: int


def reject(message: str) -> NoReturn:
    raise MaterialVideoWorkerPackageError(f"智能素材成片本机服务包被拒绝：{message}")


def load_contract() -> dict[str, object]:
    try:
        value = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        reject(f"无法读取打包契约：{error}")
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        reject("打包契约版本无效")
    return value


def run(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str] | None = None,
    timeout: int = 900,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if result.returncode != 0:
        reject(result.stderr.strip() or result.stdout.strip() or "构建命令失败")
    return result


def environment_python(runtime: Path) -> Path:
    return runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def probe_environment() -> dict[str, str]:
    environment = {"PATH": os.defpath, "LANG": "C.UTF-8", "NO_PROXY": "*"}
    for name in ("SystemRoot", "WINDIR", "TEMP", "TMP"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def build_candidate(output: Path) -> MaterialVideoWorkerAudit:
    contract = load_contract()
    output = output.resolve(strict=False)
    if output.exists():
        reject("输出目录已存在，拒绝覆盖")
    if not output.parent.is_dir() or output == ROOT or ROOT in output.parents:
        reject("输出目录必须位于仓库之外")
    upstream_before = run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=UPSTREAM
    ).stdout
    with tempfile.TemporaryDirectory(prefix="material-video-worker-build-") as directory:
        temporary = Path(directory)
        runtime = temporary / "runtime"
        environment = dict(os.environ)
        environment["UV_PROJECT_ENVIRONMENT"] = str(runtime)
        run(
            [
                "uv",
                "sync",
                "--project",
                str(UPSTREAM),
                "--locked",
                "--no-dev",
                "--no-install-project",
            ],
            cwd=ROOT,
            environment=environment,
        )
        python = environment_python(runtime)
        expected_python = contract.get("python")
        if not isinstance(expected_python, dict):
            reject("Python 契约缺失")
        actual_python = run(
            [str(python), "-c", "import platform; print(platform.python_version())"],
            cwd=ROOT,
        ).stdout.strip()
        if actual_python != expected_python.get("version"):
            reject("Python 版本未精确锁定")

        license_inventory = temporary / "dependency-licenses.json"
        run(
            [
                str(python),
                str(WORKER / "dependency_audit.py"),
                "--output",
                str(license_inventory),
            ],
            cwd=ROOT,
        )
        inventory = json.loads(license_inventory.read_text(encoding="utf-8"))
        dependencies = contract.get("dependencies")
        if not isinstance(dependencies, dict):
            reject("依赖契约缺失")
        if inventory.get("distributionCount") != dependencies.get(
            "expectedInstalledDistributionCount"
        ):
            reject("锁定环境的依赖数量漂移")
        installed = {
            str(item["name"]).lower(): str(item["version"])
            for item in inventory.get("distributions", [])
            if isinstance(item, dict) and "name" in item and "version" in item
        }
        required = dependencies.get("required")
        if not isinstance(required, dict) or any(
            installed.get(str(name).lower()) != version for name, version in required.items()
        ):
            reject("关键视频、配音或字幕依赖漂移")

        build = contract.get("build")
        if not isinstance(build, dict) or build.get("tool") != "PyInstaller":
            reject("构建工具契约缺失")
        run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(python),
                f"pyinstaller=={build.get('version')}",
            ],
            cwd=ROOT,
        )
        dist = temporary / "dist"
        run(
            [
                str(python),
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--clean",
                "--distpath",
                str(dist),
                "--workpath",
                str(temporary / "work"),
                str(WORKER / "material-video-worker.spec"),
            ],
            cwd=ROOT,
            timeout=1800,
        )
        candidate = dist / ENTRYPOINT
        license_directory = candidate / "_internal/licenses"
        license_directory.mkdir(parents=True, exist_ok=False)
        shutil.copy2(
            license_inventory,
            license_directory / "material-video-worker-dependencies.json",
        )
        audit = audit_candidate(candidate, contract)
        shutil.move(str(candidate), str(output))

    upstream_after = run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=UPSTREAM
    ).stdout
    if upstream_after != upstream_before:
        reject("打包过程写入了上游源码目录")
    return audit


def audit_candidate(
    candidate: Path, contract: dict[str, object] | None = None
) -> MaterialVideoWorkerAudit:
    contract = load_contract() if contract is None else contract
    build = contract.get("build")
    dependencies = contract.get("dependencies")
    probe_contract = contract.get("probe")
    if not all(isinstance(value, dict) for value in (build, dependencies, probe_contract)):
        reject("候选审计契约缺失")
    assert isinstance(build, dict)
    assert isinstance(dependencies, dict)
    assert isinstance(probe_contract, dict)
    if not candidate.is_dir() or candidate.is_symlink():
        reject("候选目录无效")
    files = 0
    package_bytes = 0
    root = candidate.resolve(strict=True)
    for path in candidate.rglob("*"):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            target = path.resolve(strict=True)
            if root not in target.parents:
                reject("候选包含越界链接")
            continue
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            reject("候选包含特殊文件")
        files += 1
        package_bytes += metadata.st_size
    if files > build.get("maximumFiles", 0) or package_bytes > build.get("maximumBytes", 0):
        reject("候选文件数或包体超过上限")
    executable = candidate / (f"{ENTRYPOINT}.exe" if os.name == "nt" else ENTRYPOINT)
    if not executable.is_file() or not os.access(executable, os.X_OK):
        reject("候选缺少独立可执行入口")
    if any(path.name.startswith("automation-tool-executor") for path in candidate.rglob("*")):
        reject("候选错误混入 RPA Executor")
    inventory_path = candidate / "_internal/licenses/material-video-worker-dependencies.json"
    if not inventory_path.is_file():
        reject("候选缺少依赖许可证清单")
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    dependency_count = inventory.get("distributionCount")
    if dependency_count != dependencies.get("expectedInstalledDistributionCount"):
        reject("候选许可证清单数量漂移")

    started = time.perf_counter()
    probe = subprocess.run(
        [str(executable), "--probe"],
        cwd=candidate,
        env=probe_environment(),
        capture_output=True,
        text=True,
        check=False,
        timeout=probe_contract.get(
            "maximumProbeStartupSeconds", build["maximumProbeStartupSeconds"]
        ),
    )
    startup_seconds = time.perf_counter() - started
    if probe.returncode != 0:
        startup_modules = ["upstream-app"] + [
            name
            for name, module in {
                "moviepy": "moviepy",
                "streamlit": "streamlit",
                "edge-tts": "edge_tts",
                "fastapi": "fastapi",
                "uvicorn": "uvicorn",
                "pydub": "pydub",
            }.items()
            if module
        ]
        unavailable: list[str] = []
        for name in startup_modules:
            try:
                dependency_probe = subprocess.run(
                    [str(executable), "--probe-dependency", name],
                    cwd=candidate,
                    env=probe_environment(),
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
            except subprocess.TimeoutExpired:
                unavailable.append(name)
            else:
                if dependency_probe.returncode != 0:
                    try:
                        detail = json.loads(dependency_probe.stdout)
                    except json.JSONDecodeError:
                        unavailable.append(name)
                    else:
                        missing = detail.get("missingModule")
                        unavailable.append(f"{name}({missing})" if missing else name)
        reject(
            "候选启动依赖不可用"
            + (f"：{','.join(unavailable)}" if unavailable else "：组合初始化失败")
        )
    try:
        payload = json.loads(probe.stdout)
    except json.JSONDecodeError:
        reject("候选启动探针没有返回结构化结果")
    expected_required = dependencies.get("required")
    if (
        payload.get("protocolVersion") != probe_contract.get("protocolVersion")
        or payload.get("status") != probe_contract.get("status")
        or payload.get("python") != contract["python"]["version"]
        or payload.get("dependencies") != expected_required
        or payload.get("capabilities") != probe_contract.get("requiredCapabilities")
    ):
        reject("候选启动能力或依赖版本与契约不一致")
    if startup_seconds > build["maximumProbeStartupSeconds"]:
        reject("候选冷启动超过上限")
    return MaterialVideoWorkerAudit(
        file_count=files,
        package_bytes=package_bytes,
        startup_seconds=startup_seconds,
        python_version=payload["python"],
        dependency_count=dependency_count,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    audit = build_candidate(parse_args().output)
    print(
        "智能素材成片本机服务候选已通过："
        f"{audit.file_count} files, {audit.package_bytes} bytes, "
        f"startup {audit.startup_seconds:.3f}s, Python {audit.python_version}, "
        f"{audit.dependency_count} dependencies"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
