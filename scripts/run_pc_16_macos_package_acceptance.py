#!/usr/bin/env python3
"""PC-16 macOS package acceptance: the one-sentence film out of a real bundle.

What this proves that T36 cannot: T36 drives the *debug layout* App
(`target/debug` + `_up_` resources), while the customer's package resolves its
resources through `resource_dir()` into `Contents/Resources` — a different
path with a canonicalize step only a bundle exercises. T108's accident lived
exactly in that gap: every acceptance green, the delivered package broken.

So this runner builds a bundled `.app`, installs the six production resources
into its `Contents/Resources` **through the same release-assembly functions a
customer build uses** (`stage_browser_distribution`,
`install_runtime_resources_and_sign` — no acceptance-only assembly path), and
then drives the same one-sentence spec T36 runs, against the bundle binary.

The only deltas from the customer package are the ones the single-build-path
rule explicitly allows: the WebDriver mount (class 1) and the hidden test
window (class 2). Resource resolution, assembly and every business path are
the production ones.

Usage:
    backend/.venv/bin/python scripts/run_pc_16_macos_package_acceptance.py
    backend/.venv/bin/python scripts/run_pc_16_macos_package_acceptance.py --secret <path>
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_embedded_chromium_staging import DEFAULT_ARCHIVES  # noqa: E402
from build_motion_catalog_release import (  # noqa: E402
    stage_for_release as stage_motion_catalog,
)
from build_release_package import (  # noqa: E402
    install_runtime_resources_and_sign,
    stage_browser_distribution,
)
from desktop_e2e_prerequisites import (  # noqa: E402
    install_signed_executor_package,
    prepare_startup_gate,
    startup_gate_environment,
)
from prepare_video_runtime import prepare as prepare_video_runtime  # noqa: E402
from release_assembly import load_signing_identity  # noqa: E402
from run_bm_04_acceptance import current_target_id  # noqa: E402
from run_e4_14_acceptance import require_port_available, start_control_plane  # noqa: E402
from run_i2_13_acceptance import BACKEND_ROOT, REPOSITORY_ROOT, compose_command  # noqa: E402
from run_t36_acceptance import (  # noqa: E402
    _answers_the_authoring_protocol,
    inspect_film,
    read_model_key,
)
from run_vf_06_acceptance import (  # noqa: E402
    FRONTEND,
    pnpm_executable,
    require_port_closed,
    unused_loopback_port,
)

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "Automation Tool PC16 Mac Package Acceptance.app"
APP_IDENTIFIER = "com.aventador.automationtool.pc16macpackage"
TAURI_ROOT = FRONTEND / "src-tauri"
TAURI_CONFIG = TAURI_ROOT / "tauri.pc16-macos-package-e2e.conf.json"
WDIO_CONFIG = "wdio.pc16-macos-package.conf.ts"
SPEC = "./e2e-tauri/motion-one-sentence.spec.ts"
EVIDENCE = ROOT / ".local/embedded-browser-video-studio/pc-16-evidence"
DEFAULT_SECRET = ROOT / ".local/secrets/bailian-model.json"
PROJECT_STEM = "automation-tool-pc16"
BUILD_STAGING = ROOT / ".local/pc16-package-acceptance"


def app_data_directory() -> Path:
    if sys.platform != "darwin":
        raise RuntimeError("this acceptance is the macOS half of PC-16")
    return Path.home() / "Library" / "Application Support" / APP_IDENTIFIER


def isolated_ports() -> tuple[int, int]:
    control_plane_port = unused_loopback_port()
    database_port = unused_loopback_port()
    while database_port == control_plane_port:
        database_port = unused_loopback_port()
    require_port_available(control_plane_port)
    require_port_available(database_port)
    return control_plane_port, database_port


def isolated_environment(*, control_plane_port: int, database_port: int) -> dict[str, str]:
    """The environment the App is *built* with and the Control Plane runs with.

    Same rules as T36: the startup gate reads several of these at compile time,
    so they must exist before `tauri build`, and every isolated resource name
    carries this entrypoint's own stem so cleanup can never touch anything else.
    """
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AUTOMATION_TOOL_")
    }
    database_password = secrets.token_hex(24)
    database_name = "automation_tool_pc16"
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_pc16_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_pc16_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": database_name,
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": database_password,
            "AUTOMATION_TOOL_TEST_DB_NAME": database_name,
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": (
                f"postgresql+asyncpg://{database_name}:{database_password}"
                f"@127.0.0.1:{database_port}/{database_name}"
            ),
            "AUTOMATION_TOOL_CONTROL_PLANE_E2E_ORIGIN": (
                f"http://127.0.0.1:{control_plane_port}"
            ),
        }
    )
    return startup_gate_environment(environment, control_plane_port=control_plane_port)


def build_bundle(environment: dict[str, str]) -> Path:
    """One bundled test App, resources still empty — the assembly comes next."""
    for stale in (
        TAURI_ROOT / "target" / "debug" / "bundle" / "macos",
        FRONTEND / "dist-pc16-mac",
    ):
        shutil.rmtree(stale, ignore_errors=True)
    subprocess.run(
        [
            pnpm_executable(),
            "exec",
            "tauri",
            "build",
            "--debug",
            "--bundles",
            "app",
            "--features",
            "control-plane-e2e",
            "--config",
            os.fspath(TAURI_CONFIG),
            "--ci",
        ],
        cwd=FRONTEND,
        env=environment,
        check=True,
    )
    application = TAURI_ROOT / "target" / "debug" / "bundle" / "macos" / APP_NAME
    if not application.is_dir():
        raise RuntimeError(f"PC-16 bundle was not generated at {application}")
    return application


def bundle_binary(application: Path) -> Path:
    binaries = sorted((application / "Contents" / "MacOS").iterdir())
    if len(binaries) != 1:
        raise RuntimeError("PC-16 App bundle does not have one main binary")
    return binaries[0]


def install_bundle_resources(application: Path) -> None:
    """The same assembly a customer package gets, into this bundle.

    `install_runtime_resources_and_sign` is the release's own step — video
    runtime, frozen catalog, embedded browser, digest audits and the outer
    seal, in the order the release does them. The Local Executor lands first
    because the seal at the end must cover it.
    """
    identity = load_signing_identity()
    resources = application / "Contents" / "Resources"

    print("[PC-16] Installing the signed Local Executor into Contents/Resources")
    install_signed_executor_package(resource_root=resources)
    entrypoint = resources / "local-executor/package/automation-tool-executor"
    if not _answers_the_authoring_protocol(entrypoint):
        raise RuntimeError(
            f"{entrypoint} does not answer the one-shot authoring protocol; "
            "this package cannot author"
        )

    target_id = current_target_id()
    browser_staging = BUILD_STAGING / "browser"
    shutil.rmtree(browser_staging, ignore_errors=True)
    stage_browser_distribution(
        target_id,
        DEFAULT_ARCHIVES[target_id].resolve(strict=True),
        browser_staging,
        identity,
    )
    print("[PC-16] Preparing the pinned video runtime (cached per machine)")
    video_runtime = prepare_video_runtime(platform="macos")
    catalog = stage_motion_catalog(staging=BUILD_STAGING / "catalog").parent
    install_runtime_resources_and_sign(
        application,
        staging=browser_staging,
        target_id=target_id,
        video_runtime=video_runtime,
        motion_catalog=catalog,
        identity=identity,
    )


def audit_bundle(application: Path) -> dict[str, object]:
    """Read the assembled bundle back: the six resource trees, parts counted."""
    resources = application / "Contents" / "Resources"
    trees = sorted(
        entry.name
        for entry in resources.iterdir()
        if entry.is_dir() and not entry.name.endswith(".lproj")
    )
    parts = len(list((resources / "motion-catalog" / "items").glob("*/*.html")))
    report = {"resourceTrees": trees, "partDocuments": parts}
    print(f"[PC-16] bundle audit: {json.dumps(report)}")
    expected = {
        "embedded-browser",
        "local-executor",
        "media-toolchain",
        "motion-catalog",
        "motion-video-worker",
    }
    if not expected <= set(trees) or parts != 134:
        raise RuntimeError(f"PC-16 bundle is not production-shaped: {report}")
    return report


def run_desktop_acceptance(
    application: Path,
    api_key: str,
    evidence_video: Path,
    evidence_shots: Path,
    base_environment: dict[str, str],
) -> None:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if (
        configuration.get("identifier") != APP_IDENTIFIER
        or len(windows) != 1
        or windows[0].get("visible") is not False
    ):
        raise RuntimeError("PC-16 acceptance must use its hidden isolated App")
    port = int(base_environment["TAURI_WEBDRIVER_PORT"])
    environment = dict(base_environment)
    environment.update(
        {
            "PC16_MAC_APP_BINARY": os.fspath(bundle_binary(application)),
            "AUTOMATION_TOOL_T36_MODEL_KEY": api_key,
            "AUTOMATION_TOOL_T36_EVIDENCE_VIDEO": str(evidence_video),
            "AUTOMATION_TOOL_T36_EVIDENCE_SHOTS": str(evidence_shots),
        }
    )
    require_port_closed(port)
    subprocess.run(
        [
            pnpm_executable(),
            "exec",
            "wdio",
            "run",
            WDIO_CONFIG,
            "--spec",
            SPEC,
        ],
        cwd=FRONTEND,
        env=environment,
        check=True,
    )
    require_port_closed(port)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--secret", type=Path, default=DEFAULT_SECRET)
    parser.add_argument(
        "--reuse-bundle",
        action="store_true",
        help="跳过构建与装配，直接驱动上一次装配好的 bundle（排查用）",
    )
    arguments = parser.parse_args()
    api_key = read_model_key(arguments.secret.resolve())

    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True)
    evidence_video = EVIDENCE / "pc16-package-one-sentence.mp4"
    evidence_shots = EVIDENCE / "pc16-package-shot-structure.json"

    private_app_data = app_data_directory()
    if private_app_data.exists():
        shutil.rmtree(private_app_data)
    prepare_startup_gate(private_app_data)

    control_plane_port, database_port = isolated_ports()
    # The WebDriver port is compiled into the test build, so it is chosen here,
    # before the bundle exists.
    webdriver_port = unused_loopback_port()
    environment = isolated_environment(
        control_plane_port=control_plane_port, database_port=database_port
    )
    environment["TAURI_WEBDRIVER_PORT"] = str(webdriver_port)

    application = TAURI_ROOT / "target" / "debug" / "bundle" / "macos" / APP_NAME
    if arguments.reuse_bundle:
        if not application.is_dir():
            raise RuntimeError("--reuse-bundle but no assembled bundle exists")
        print(f"[PC-16] Reusing the assembled bundle at {application}")
    else:
        print("[PC-16] Building the bundled test App")
        application = build_bundle(environment)
        install_bundle_resources(application)
    audit = audit_bundle(application)

    project_name = f"{PROJECT_STEM}-{os.getpid()}"
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None
    run_failed = True
    try:
        require_port_available(database_port)
        print(f"[PC-16] Starting isolated PostgreSQL as {project_name}")
        subprocess.run(
            [*compose, "up", "--detach", "--wait", "postgres-test"],
            check=True,
            cwd=REPOSITORY_ROOT,
            env=environment,
        )
        print("[PC-16] Applying the production Alembic migration chain")
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            cwd=BACKEND_ROOT,
            env=environment,
        )
        print(f"[PC-16] Starting Control Plane on isolated port {control_plane_port}")
        server = start_control_plane(port=control_plane_port, environment=environment)
        run_desktop_acceptance(
            application,
            api_key,
            evidence_video,
            evidence_shots,
            environment,
        )
        inspect_film(evidence_video, evidence_shots)
        run_failed = False
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        subprocess.run(
            [*compose, "down", "--volumes", "--remove-orphans"],
            check=False,
            cwd=REPOSITORY_ROOT,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if run_failed:
            print(f"[PC-16] kept the App data directory for diagnosis: {private_app_data}")
        else:
            shutil.rmtree(private_app_data, ignore_errors=True)

    report = EVIDENCE / "pc16-package-acceptance.json"
    report.write_text(
        json.dumps(
            {"bundle": audit, "film": str(evidence_video)},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"[PC-16] acceptance passed; evidence at {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
