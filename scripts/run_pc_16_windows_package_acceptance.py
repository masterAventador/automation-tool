#!/usr/bin/env python3
"""PC-16 Windows package acceptance through one installed NSIS App.

This is the Windows counterpart of ``run_pc_16_macos_package_acceptance.py``.
It deliberately builds an acceptance-only App shell (hidden window + WebDriver
mount), but every packaged runtime byte follows the production Windows release
path: digest-locked Chromium, signed Local Executor, three video resources and
the frozen motion catalog are assembled before the NSIS bundler runs.

The runner then installs the package under an isolated current-user product
identity, verifies all 134 catalog documents and every manifest digest from the
installed copy, checks the packaged Chinese font, runs the existing T36
one-sentence user journey against the installed binary, and uninstalls it.

This file can be prepared and statically checked on macOS. A successful result
is only possible on native Windows x86_64 and is the evidence PC-13/PC-16 still
require before either task may move from ``待验收`` to ``已完成``.

Usage:
    uv run --project backend --locked python scripts/run_pc_16_windows_package_acceptance.py
    uv run --project backend --locked python scripts/run_pc_16_windows_package_acceptance.py --secret PATH
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
FRONTEND_ROOT = REPOSITORY_ROOT / "frontend"
TAURI_ROOT = FRONTEND_ROOT / "src-tauri"

sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))
sys.path.insert(0, str(BACKEND_ROOT / "src"))

from build_motion_catalog_release import (  # noqa: E402
    aggregate_digest,
    stage_for_release as stage_motion_catalog,
)
from desktop_e2e_prerequisites import startup_gate_environment  # noqa: E402
from embedded_browser_archives import (  # noqa: E402
    WINDOWS_X86_64_ARCHIVE,
    archive_path,
)
from prepare_video_runtime import prepare as prepare_video_runtime  # noqa: E402
from release_assembly import (  # noqa: E402
    ReleaseAssemblyRejected,
    install_and_seal,
    install_motion_catalog,
    install_video_runtime,
    require_packaged_browser,
    require_packaged_motion_catalog,
    require_packaged_video_runtime,
)
from release_configuration import (  # noqa: E402
    merge_configuration,
    write_windows_release_configuration,
)
from run_e4_14_acceptance import require_port_available, start_control_plane  # noqa: E402
from run_eb_16_windows_acceptance import (  # noqa: E402
    build_executor_candidate,
    pnpm_executable,
    require_no_process_matching,
    seal_windows_payload,
    stage_browser_distribution,
    terminate_processes_matching,
)
from run_p9_04_acceptance import (  # noqa: E402
    install_root,
    installer_environment,
    one_file,
    package_files,
    release_environment,
    require_non_elevated_process,
    require_windows,
    run_checked,
    windows_registry_installations,
)
from run_t36_acceptance import (  # noqa: E402
    _answers_the_authoring_protocol,
    inspect_film,
    read_model_key,
)
from run_i2_13_acceptance import compose_command  # noqa: E402
from run_vf_06_acceptance import (  # noqa: E402
    require_port_closed,
    unused_loopback_port,
)

APP_IDENTIFIER = "com.aventador.automationtool.pc16windowspackage"
PRODUCT_NAME = "Automation Tool PC16 Windows Package Acceptance"
MAIN_BINARY_NAME = "automation-tool-pc16-windows-package"
ACCEPTANCE_CONFIG = TAURI_ROOT / "tauri.pc16-windows-package-e2e.conf.json"
BASE_CONFIG = TAURI_ROOT / "tauri.conf.json"
WDIO_CONFIG = "wdio.pc16-windows-package.conf.ts"
SPEC = "./e2e-tauri/motion-one-sentence.spec.ts"
TARGET_ID = "windows-x86_64"
DEFAULT_SECRET = REPOSITORY_ROOT / ".local/secrets/bailian-model.json"
DEFAULT_WORK_DIRECTORY = REPOSITORY_ROOT / ".local/pc16-windows-package-acceptance"
EVIDENCE = (
    REPOSITORY_ROOT
    / ".local/embedded-browser-video-studio/pc-16-windows-evidence"
)
TYPOGRAPHY_CONTRACT = (
    REPOSITORY_ROOT / "contracts/video/motion-part-typography.v1.json"
)
RELEASE_LOCK = REPOSITORY_ROOT / "contracts/video/motion-catalog-release.v1.json"
PROJECT_STEM = "automation-tool-pc16-windows"


class AcceptanceFailed(RuntimeError):
    """The PC-16 Windows installed-package journey is unavailable."""


def announce(message: str) -> None:
    print(f"[PC-16-WIN] {message}", flush=True)


def private_app_data() -> Path:
    roaming = os.environ.get("APPDATA")
    if not roaming:
        raise AcceptanceFailed("Windows roaming AppData is unavailable")
    return Path(roaming) / APP_IDENTIFIER


def installed_root() -> Path:
    return install_root(product_name=PRODUCT_NAME)


def installed_binary(root: Path) -> Path:
    binary = root / f"{MAIN_BINARY_NAME}.exe"
    if not binary.is_file():
        raise AcceptanceFailed(f"installed App binary is missing at {binary}")
    return binary


def isolated_ports() -> tuple[int, int, int]:
    ports: list[int] = []
    while len(ports) < 3:
        candidate = unused_loopback_port()
        if candidate not in ports:
            require_port_available(candidate)
            ports.append(candidate)
    return ports[0], ports[1], ports[2]


def isolated_environment(
    *,
    control_plane_port: int,
    database_port: int,
    webdriver_port: int,
    cargo_target: Path,
    executor_public_key: str,
) -> dict[str, str]:
    environment = release_environment(cargo_target, executor_public_key)
    database_password = secrets.token_hex(24)
    database_name = "automation_tool_pc16_windows"
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_pc16_windows_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_pc16_windows_dev",
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
            "TAURI_WEBDRIVER_PORT": str(webdriver_port),
        }
    )
    return startup_gate_environment(
        environment, control_plane_port=control_plane_port
    )


def write_build_configuration(
    *,
    directory: Path,
    executor: Path,
    payload: Path,
) -> Path:
    resource_configuration = write_windows_release_configuration(
        directory=directory,
        executor=executor,
        payload=payload,
        name="tauri.pc16-windows-resources.json",
    )
    merged: object = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
    for overlay in (ACCEPTANCE_CONFIG, resource_configuration):
        merged = merge_configuration(
            merged,
            json.loads(overlay.read_text(encoding="utf-8")),
        )
    if not isinstance(merged, dict):
        raise AcceptanceFailed("PC-16 Windows Tauri configuration is invalid")
    destination = directory / "tauri.pc16-windows-effective.json"
    destination.write_text(
        json.dumps(merged, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return destination


def build_installer(
    *,
    configuration: Path,
    environment: dict[str, str],
    cargo_target: Path,
) -> tuple[Path, Path]:
    announce("Building the isolated debug NSIS package with production resources")
    bundle_root = cargo_target / "debug/bundle"
    if bundle_root.exists():
        shutil.rmtree(bundle_root)
    run_checked(
        [
            pnpm_executable(),
            "exec",
            "tauri",
            "build",
            "--debug",
            "--features",
            "control-plane-e2e",
            "--bundles",
            "nsis",
            "--config",
            os.fspath(configuration),
            "--ci",
        ],
        environment=environment,
    )
    binary = cargo_target / "debug" / f"{MAIN_BINARY_NAME}.exe"
    if not binary.is_file():
        raise AcceptanceFailed("PC-16 Windows built App binary is missing")
    installer = one_file(
        cargo_target / "debug/bundle/nsis",
        "*-setup.exe",
        "PC-16 Windows NSIS installer was not generated exactly once",
    )
    return binary, installer


def install_package(installer: Path, root: Path) -> None:
    if root.exists() or windows_registry_installations(
        machine_wide=False, product_name=PRODUCT_NAME
    ):
        raise AcceptanceFailed(
            f"an earlier PC-16 Windows installation still occupies {root}"
        )
    announce(f"Installing the current-user NSIS package into {root}")
    run_checked([os.fspath(installer), "/S"], environment=installer_environment())
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        records = windows_registry_installations(
            machine_wide=False, product_name=PRODUCT_NAME
        )
        if (
            (root / f"{MAIN_BINARY_NAME}.exe").is_file()
            and (root / "uninstall.exe").is_file()
            and len(records) == 1
        ):
            if windows_registry_installations(
                machine_wide=True, product_name=PRODUCT_NAME
            ):
                raise AcceptanceFailed(
                    "PC-16 Windows acceptance package wrote an HKLM installation"
                )
            return
        time.sleep(0.2)
    raise AcceptanceFailed("PC-16 Windows NSIS installation did not converge")


def _manifest_records(catalog: Path) -> list[dict[str, Any]]:
    try:
        document = json.loads(
            (catalog / "manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AcceptanceFailed(
            "installed motion catalog manifest is unreadable"
        ) from error
    records = document.get("files")
    if not isinstance(records, list) or not records:
        raise AcceptanceFailed("installed motion catalog manifest has no files")
    return records


def audit_installed_motion_catalog(root: Path) -> dict[str, object]:
    installed = require_packaged_motion_catalog(
        application=root, platform="windows"
    )
    catalog = installed["motion-catalog"]
    records = _manifest_records(catalog)
    declared: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise AcceptanceFailed("motion catalog manifest record is invalid")
        relative = record.get("path")
        if (
            not isinstance(relative, str)
            or not relative
            or relative.startswith("/")
            or "\\" in relative
            or any(part in {"", ".", ".."} for part in relative.split("/"))
            or relative in declared
        ):
            raise AcceptanceFailed("motion catalog manifest path is invalid")
        declared[relative] = record

    inventory = package_files(catalog)
    actual_paths = set(inventory) - {"manifest.json"}
    if actual_paths != set(declared):
        raise AcceptanceFailed(
            "installed motion catalog files differ from its manifest"
        )
    for relative, record in declared.items():
        size, digest = inventory[relative]
        if size != record.get("bytes") or digest != record.get("sha256"):
            raise AcceptanceFailed(
                f"installed motion catalog digest drifted: {relative}"
            )

    lock = json.loads(RELEASE_LOCK.read_text(encoding="utf-8"))
    generated = lock.get("generated", {})
    if (
        len(records) != generated.get("fileCount")
        or aggregate_digest(records) != generated.get("aggregateSha256")
    ):
        raise AcceptanceFailed(
            "installed motion catalog aggregate differs from the release lock"
        )

    documents = sorted(
        relative
        for relative in declared
        if relative.startswith("items/") and relative.endswith(".html")
    )
    if len(documents) != 134:
        raise AcceptanceFailed(
            f"installed motion catalog has {len(documents)} part documents, not 134"
        )

    typography = json.loads(TYPOGRAPHY_CONTRACT.read_text(encoding="utf-8"))
    font_relative = typography.get("cjk", {}).get("artifactPath")
    if not isinstance(font_relative, str) or font_relative not in inventory:
        raise AcceptanceFailed("installed Windows package carries no locked CJK font")
    font_bytes, font_digest = inventory[font_relative]
    if font_bytes <= 0:
        raise AcceptanceFailed("installed Windows CJK font is empty")

    facts: dict[str, object] = {
        "files": len(records),
        "aggregateSha256": aggregate_digest(records),
        "partDocuments": len(documents),
        "cjkFont": font_relative,
        "cjkFontBytes": font_bytes,
        "cjkFontSha256": font_digest,
    }
    announce(
        "Installed catalog audit: "
        f"{facts['partDocuments']} parts, {facts['files']} files, "
        f"CJK font {facts['cjkFontBytes']} bytes"
    )
    return facts


def verify_catalog_failure_matrix(catalog: Path) -> dict[str, str]:
    outcomes: dict[str, str] = {}

    def expect_release_gate_rejection(root: Path, name: str) -> None:
        try:
            require_packaged_motion_catalog(
                application=root, platform="windows"
            )
        except ReleaseAssemblyRejected:
            outcomes[name] = "rejected"
            return
        raise AcceptanceFailed(f"catalog tamper was accepted: {name}")

    with tempfile.TemporaryDirectory(
        prefix="automation-tool-pc16-windows-tamper-"
    ) as temporary:
        workspace = Path(temporary)

        expect_release_gate_rejection(workspace / "missing-tree", "missing-tree")

        missing_manifest = workspace / "missing-manifest"
        shutil.copytree(catalog, missing_manifest / "motion-catalog")
        (missing_manifest / "motion-catalog/manifest.json").unlink()
        expect_release_gate_rejection(missing_manifest, "missing-manifest")

        missing_sentinel = workspace / "missing-sentinel"
        shutil.copytree(catalog, missing_sentinel / "motion-catalog")
        (
            missing_sentinel
            / "motion-catalog/items/lt-bold-block/lt-bold-block.html"
        ).unlink()
        expect_release_gate_rejection(missing_sentinel, "missing-sentinel")

        intact = workspace / "intact"
        shutil.copytree(catalog, intact / "motion-catalog")
        require_packaged_motion_catalog(application=intact, platform="windows")
        outcomes["intact"] = "accepted"

    announce(f"Catalog failure matrix: {json.dumps(outcomes, sort_keys=True)}")
    return outcomes


def run_desktop_acceptance(
    *,
    binary: Path,
    api_key: str,
    evidence_video: Path,
    environment: dict[str, str],
) -> None:
    webdriver_port = int(environment["TAURI_WEBDRIVER_PORT"])
    prepared = dict(environment)
    prepared.update(
        {
            "PC16_WINDOWS_APP_BINARY": os.fspath(binary),
            "AUTOMATION_TOOL_T36_MODEL_KEY": api_key,
            "AUTOMATION_TOOL_T36_EVIDENCE_VIDEO": os.fspath(evidence_video),
        }
    )
    require_port_closed(webdriver_port)
    run_checked(
        [
            pnpm_executable(),
            "exec",
            "wdio",
            "run",
            WDIO_CONFIG,
            "--spec",
            SPEC,
        ],
        environment=prepared,
    )
    require_port_closed(webdriver_port)


def uninstall_and_check(root: Path) -> None:
    announce("Uninstalling the isolated NSIS package")
    terminate_processes_matching(os.fspath(root))
    uninstaller = root / "uninstall.exe"
    if uninstaller.is_file():
        run_checked([os.fspath(uninstaller), "/S"], environment=installer_environment())
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        if (
            not root.exists()
            and not windows_registry_installations(
                machine_wide=False, product_name=PRODUCT_NAME
            )
            and not windows_registry_installations(
                machine_wide=True, product_name=PRODUCT_NAME
            )
        ):
            require_no_process_matching(os.fspath(root))
            return
        time.sleep(0.2)
    raise AcceptanceFailed("PC-16 Windows uninstaller left owned state")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secret", type=Path, default=DEFAULT_SECRET)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIRECTORY)
    return parser.parse_args()


def main() -> int:
    architecture = require_windows()
    require_non_elevated_process()
    if architecture != "x86_64":
        raise AcceptanceFailed("PC-16 Windows package acceptance requires x86_64")
    arguments = parse_arguments()
    api_key = read_model_key(arguments.secret.resolve())
    archive = arguments.archive or archive_path(
        REPOSITORY_ROOT, WINDOWS_X86_64_ARCHIVE
    )
    work_directory: Path = arguments.work_dir
    build_directory = work_directory / "build"
    cargo_target = work_directory / "cargo-target"
    root = installed_root()
    app_data = private_app_data()

    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True)
    evidence_video = EVIDENCE / "pc16-windows-package-one-sentence.mp4"
    if app_data.exists():
        shutil.rmtree(app_data)
    if build_directory.exists():
        shutil.rmtree(build_directory)
    build_directory.mkdir(parents=True)

    control_plane_port, database_port, webdriver_port = isolated_ports()
    project_name = f"{PROJECT_STEM}-{os.getpid()}"
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None
    installed = False
    run_failed = True

    staging = build_directory / "browser-staging"
    stage_browser_distribution(archive, staging)
    executor = build_directory / "executor/automation-tool-executor"
    executor_public_key, _executor_private_key = build_executor_candidate(
        executor, architecture
    )
    executor_entrypoint = executor / "automation-tool-executor.exe"
    if not _answers_the_authoring_protocol(executor_entrypoint):
        raise AcceptanceFailed(
            "the packaged Windows Executor cannot answer --author-motion"
        )
    payload = build_directory / "payload"
    video_runtime = prepare_video_runtime(platform="windows")
    catalog_staging = stage_motion_catalog(
        staging=build_directory / "catalog-staging"
    ).parent
    announce("Assembling all production runtime trees before NSIS")
    install_video_runtime(
        application=payload, staging=video_runtime, platform="windows"
    )
    install_motion_catalog(
        application=payload, staging=catalog_staging, platform="windows"
    )
    install_and_seal(
        application=payload,
        staging=staging,
        target_id=TARGET_ID,
        platform="windows",
        seal=seal_windows_payload,
    )
    require_packaged_browser(
        application=payload, target_id=TARGET_ID, platform="windows"
    )
    require_packaged_video_runtime(application=payload, platform="windows")
    require_packaged_motion_catalog(application=payload, platform="windows")

    configuration = write_build_configuration(
        directory=build_directory,
        executor=executor,
        payload=payload,
    )
    environment = isolated_environment(
        control_plane_port=control_plane_port,
        database_port=database_port,
        webdriver_port=webdriver_port,
        cargo_target=cargo_target,
        executor_public_key=executor_public_key,
    )
    _built_binary, installer = build_installer(
        configuration=configuration,
        environment=environment,
        cargo_target=cargo_target,
    )
    audit: dict[str, object] | None = None
    matrix: dict[str, str] | None = None
    try:
        install_package(installer, root)
        installed = True
        binary = installed_binary(root)
        require_packaged_browser(
            application=root, target_id=TARGET_ID, platform="windows"
        )
        require_packaged_video_runtime(application=root, platform="windows")
        audit = audit_installed_motion_catalog(root)
        matrix = verify_catalog_failure_matrix(root / "motion-catalog")

        require_port_available(database_port)
        announce(f"Starting isolated PostgreSQL as {project_name}")
        subprocess.run(
            [*compose, "up", "--detach", "--wait", "postgres-test"],
            check=True,
            cwd=REPOSITORY_ROOT,
            env=environment,
        )
        announce("Applying the production Alembic migration chain")
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            cwd=BACKEND_ROOT,
            env=environment,
        )
        announce(f"Starting Control Plane on port {control_plane_port}")
        server = start_control_plane(
            port=control_plane_port, environment=environment
        )
        run_desktop_acceptance(
            binary=binary,
            api_key=api_key,
            evidence_video=evidence_video,
            environment=environment,
        )
        inspect_film(evidence_video)
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
        owns_installation = (
            installed
            or root.exists()
            or bool(
                windows_registry_installations(
                    machine_wide=False, product_name=PRODUCT_NAME
                )
            )
        )
        if owns_installation:
            uninstall_and_check(root)
        if app_data.exists():
            shutil.rmtree(app_data)
        require_port_closed(control_plane_port)
        require_port_closed(database_port)
        require_port_closed(webdriver_port)

    report = EVIDENCE / "pc16-windows-package-acceptance.json"
    report.write_text(
        json.dumps(
            {
                "target": TARGET_ID,
                "architecture": architecture,
                "catalog": audit,
                "failureMatrix": matrix,
                "film": os.fspath(evidence_video),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    announce(f"Acceptance passed; evidence at {report}")
    if run_failed:
        raise AssertionError("unreachable")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AcceptanceFailed, ReleaseAssemblyRejected) as error:
        print(f"PC-16 Windows package acceptance failed: {error}")
        raise SystemExit(1) from error
