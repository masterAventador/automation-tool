#!/usr/bin/env python3
"""VF-06 deterministic acceptance entrypoint."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from desktop_e2e_prerequisites import video_studio_startup_harness
from release_assembly import VIDEO_RUNTIME_RESOURCES

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
TAURI_CONFIG = FRONTEND / "src-tauri" / "tauri.video-studio-e2e.conf.json"
APP_IDENTIFIER = "com.aventador.automationtool.vf06acceptance"
# `tauri build --debug --no-bundle` 产出裸可执行文件, Tauri 把它所在目录当作资源目录。
DEBUG_APP_RESOURCE_ROOT = FRONTEND / "src-tauri" / "target" / "debug"
EMBEDDED_BROWSER_MANIFEST = Path("embedded-browser") / "distribution-manifest.v1.json"

# material-video-webui.spec.ts 属于 IM-05 验收范围: 它要求真实冻结 Worker 已经装进
# App 资源目录, 由 scripts/run_im_05_acceptance.py 构建并装配后单独覆盖;
# VF-06 全量验收只运行不依赖冻结 Worker 的视频工作台 spec.
#
# plain-language-comprehension.spec.ts 属于本范围, 尽管 CQ-01 另有自己的入口。
# 它与 material-video-webui.spec.ts 的区别是决定性的: IM-05 那条没有冻结 Worker
# 就必然失败, 而这条对 CQ-01 的 `CQ01_PAGE_TEXT_FILE` 只做可选处理
# (`if (CAPTURE_FILE !== undefined)`), 缺了它照样跑完全部断言, 只是不落盘那份
# 文本快照。所以这里没有「跑不了」的理由, 把它排除掉只会让通俗语言这条用户可见
# 要求在 VF-06 全量里没人验。
SPECS = (
    "./e2e-tauri/video-studio.spec.ts",
    "./e2e-tauri/video-creation-methods.spec.ts",
    "./e2e-tauri/motion-style-catalog.spec.ts",
    "./e2e-tauri/plain-language-comprehension.spec.ts",
)


def desktop_wdio_arguments() -> list[str]:
    arguments = ["exec", "wdio", "run", "wdio.video-studio.conf.ts"]
    for spec in SPECS:
        arguments.extend(["--spec", spec])
    return arguments


def pnpm_executable() -> str:
    name = "pnpm.cmd" if sys.platform == "win32" else "pnpm"
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f"VF-06 cannot find {name} on PATH")
    return executable


def app_data_directory() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_IDENTIFIER
    if sys.platform == "win32":
        roaming = os.environ.get("APPDATA")
        if roaming is None:
            raise RuntimeError("Windows roaming AppData is unavailable")
        return Path(roaming) / APP_IDENTIFIER
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
    return base / APP_IDENTIFIER


def unused_loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    require_port_closed(port)
    return port


def require_port_closed(port: int) -> None:
    with socket.socket() as probe:
        probe.settimeout(0.2)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(f"VF-06 refuses to reuse occupied loopback port {port}")


class VideoRuntimeStagingRejected(RuntimeError):
    """An acceptance App would start without the runtime it needs."""


def _remove_resource_tree(path: Path) -> None:
    """Remove one worktree resource without following a link into another tree."""
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    else:
        shutil.rmtree(path, ignore_errors=True)


def stage_video_runtime(*, staging: Path, resource_root: Path) -> dict[str, Path]:
    """Install the prepared video runtime where every build reads it.

    The acceptance build resolves ffmpeg and both Workers from the packaged
    resource directory, exactly as the release does. It used to accept the paths
    from environment variables the acceptance scripts set, so no video
    acceptance could notice that the shipped package carried none of them.

    A debug target directory is reused across runs, so an existing tree is
    replaced rather than refused; on any rejection every tree this call wrote is
    removed, so a partial staging cannot be mistaken for a finished one.
    """
    written: list[Path] = []
    try:
        for resource in VIDEO_RUNTIME_RESOURCES:
            source = staging / resource.staging_name
            if not source.is_dir():
                raise VideoRuntimeStagingRejected(
                    f"the staging tree carries no {resource.staging_name} at {source}"
                )
            destination = resource_root.joinpath(*resource.installed_parts)
            top = resource_root / resource.installed_parts[0]
            _remove_resource_tree(top)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, destination, symlinks=True)
            written.append(top)
        return require_staged_video_runtime(resource_root=resource_root)
    except BaseException:
        for path in written:
            _remove_resource_tree(path)
        raise


def require_staged_video_runtime(*, resource_root: Path) -> dict[str, Path]:
    """Fail closed unless every video runtime resource is present and non-empty."""
    platform = "windows" if sys.platform == "win32" else "macos"
    installed: dict[str, Path] = {}
    for resource in VIDEO_RUNTIME_RESOURCES:
        location = resource_root.joinpath(*resource.installed_parts)
        if not location.is_dir():
            raise VideoRuntimeStagingRejected(
                f"the acceptance App carries no {resource.staging_name} at {location}; "
                "run scripts/prepare_video_runtime.py and stage it before building"
            )
        for name in resource.required_for(platform):
            payload = location / name
            if not payload.is_file() or payload.stat().st_size == 0:
                raise VideoRuntimeStagingRejected(
                    f"{resource.staging_name} is incomplete: {name} is missing or "
                    "empty, so the resolver would find the directory and still fail"
                )
        installed[resource.staging_name] = location
    return installed


def require_staged_embedded_browser(*, resource_root: Path) -> Path:
    """Fail closed unless the verified embedded browser is staged.

    The startup gate resolves it through the same authority the release uses, so
    an acceptance App without it never mounts the workbench and every spec fails
    without saying why.
    """
    manifest = resource_root / EMBEDDED_BROWSER_MANIFEST
    if not manifest.is_file():
        raise VideoRuntimeStagingRejected(
            f"the acceptance App carries no embedded-browser distribution at "
            f"{manifest.parent}; build one with "
            "scripts/build_embedded_browser_distribution.py and stage it there"
        )
    return manifest.parent


def run_desktop_acceptance() -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if (
        configuration.get("identifier") != APP_IDENTIFIER
        or len(windows) != 1
        or windows[0].get("visible") is not False
    ):
        raise RuntimeError("VF-06 acceptance must use its hidden isolated App")
    private_app_data = app_data_directory()
    if private_app_data.exists():
        shutil.rmtree(private_app_data)
    port = unused_loopback_port()
    environment = {key: value for key, value in os.environ.items() if key != "TAURI_WEBDRIVER_PORT"}
    environment["TAURI_WEBDRIVER_PORT"] = str(port)
    try:
        with video_studio_startup_harness(
            private_app_data,
            environment=environment,
        ) as environment:
            subprocess.run(
                [pnpm_executable(), "build:tauri:video-studio-test"],
                cwd=FRONTEND,
                env=environment,
                check=True,
            )
            require_port_closed(port)
            subprocess.run(
                [pnpm_executable(), *desktop_wdio_arguments()],
                cwd=FRONTEND,
                env=environment,
                check=True,
            )
            require_port_closed(port)
    finally:
        restore = subprocess.run(
            [pnpm_executable(), "build"],
            cwd=FRONTEND,
            env=environment,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if private_app_data.exists():
            shutil.rmtree(private_app_data)
        require_port_closed(port)
        if restore.returncode != 0:
            raise RuntimeError("VF-06 failed to restore production Vite assets")


def main() -> int:
    required = (
        ROOT / "frontend/src/features/video-studio/VideoStudio.tsx",
        ROOT / "frontend/src/features/video-studio/VideoStudio.test.tsx",
        ROOT / "frontend/e2e-tauri/video-studio.spec.ts",
        TAURI_CONFIG,
        ROOT / "frontend/src/test-production-main.ts",
        ROOT / "docs/development/VF-06.md",
    )
    missing = [path.relative_to(ROOT).as_posix() for path in required if not path.is_file()]
    if missing:
        raise SystemExit(f"VF-06 missing deliverables: {', '.join(missing)}")
    roadmap = (ROOT / "docs/embedded-browser-video-studio-roadmap.md").read_text(encoding="utf-8")
    vf06_rows = [line for line in roadmap.splitlines() if line.startswith("| VF-06 |")]
    if len(vf06_rows) != 1 or not vf06_rows[0].endswith("| ✅ 已完成 |"):
        raise SystemExit("VF-06 roadmap row is missing, duplicated or incomplete")
    subprocess.run(
        [
            "pnpm",
            "--dir",
            "frontend",
            "exec",
            "vitest",
            "run",
            "src/features/video-studio/VideoStudio.test.tsx",
            "src/app/WorkbenchShell.test.tsx",
        ],
        cwd=ROOT,
        check=True,
    )
    run_desktop_acceptance()
    print("VF-06 video studio shell acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
