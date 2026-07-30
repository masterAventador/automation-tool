#!/usr/bin/env python3
"""PC-22 deterministic contract for the in-App material-video studio.

The real acceptance still has to start the frozen Worker and inspect the child
WebView.  This fast gate protects the product shape around that expensive run:
the old second-window branch must stay deleted, React must never receive the
private loopback endpoint, and the native bridge must own mount, bounds updates,
and cleanup.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUST = ROOT / "frontend/src-tauri/src/material_video_studio.rs"
COMMANDS = ROOT / "frontend/src-tauri/src/lib.rs"
STUDIO = ROOT / "frontend/src/features/video-studio/VideoStudio.tsx"
GATEWAY = ROOT / "frontend/src/platform/tauri/material-video-studio-gateway.ts"
SPEC = ROOT / "frontend/e2e-tauri/material-video-webui.spec.ts"


def require(text: str, marker: str, source: Path) -> None:
    if marker not in text:
        raise AssertionError(f"{source.relative_to(ROOT)} is missing {marker!r}")


def forbid(text: str, marker: str, source: Path) -> None:
    if marker in text:
        raise AssertionError(f"{source.relative_to(ROOT)} still contains {marker!r}")


def main() -> int:
    executed_checks = 0
    rust = RUST.read_text(encoding="utf-8")
    commands = COMMANDS.read_text(encoding="utf-8")
    studio = STUDIO.read_text(encoding="utf-8")
    gateway = GATEWAY.read_text(encoding="utf-8")
    spec = SPEC.read_text(encoding="utf-8")

    for marker in (
        "WebviewBuilder::new",
        ".add_child(",
        "start_service(",
        "mount_embedded_view(",
        "update_embedded_view(",
        "close_embedded_view(",
        "data-automation-tool-studio-state",
        "automation-tool-im05-probe-ready",
    ):
        require(rust, marker, RUST)
        executed_checks += 1
    for marker in ("WebviewWindowBuilder", "WindowEvent", "WINDOW_THEME", ".set_focus()"):
        forbid(rust, marker, RUST)
        executed_checks += 1

    for command in (
        "open_material_video_studio",
        "update_material_video_studio_view",
        "close_material_video_studio",
    ):
        require(commands, command, COMMANDS)
        require(gateway, command, GATEWAY)
        executed_checks += 2
    for acceptance_command in (
        "exercise_material_video_studio_for_acceptance",
        "inspect_material_video_studio_exercise_for_acceptance",
        "inspect_material_video_studio_cleanup_for_acceptance",
    ):
        require(commands, acceptance_command, COMMANDS)
        executed_checks += 1

    for dead_branch in (
        "OPEN_ERRORS",
        "OPEN_STUDIO_HINT_ID",
        "openMessage",
        "setOpenMessage",
        "const [opening",
        "setOpening",
        "打开完整制作界面",
        "当前真实内嵌服务尚未接入",
        "独立完整界面",
    ):
        forbid(studio, dead_branch, STUDIO)
        executed_checks += 1

    require(studio, 'aria-label="智能素材成片完整制作界面"', STUDIO)
    require(spec, "openMaterialVideoStudio", SPEC)
    require(spec, "browser.tauri.listWindows()", SPEC)
    executed_checks += 3

    print("PC-22 embedded material studio contract passed")
    print(f"executed checks: {executed_checks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
