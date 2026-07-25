#!/usr/bin/env python3
"""EB-16 acceptance: one real macOS first-release package, end to end.

The deterministic package gate runs first, then the real chain: the
digest-locked Chromium archive is staged by the production EB-03 builder,
promoted by the EB-05 manifest builder, mapped into the Tauri bundle next to
the real signed Local Executor, and one production-mode `.app` plus `.dmg` are
built. The audits run against the built artifacts, never against sources:
exactly one complete Chromium for this target, no second browser, no
WebDriver, no hidden test window configuration, real measured sizes, inner
code signatures, DMG round trip, install, launch, quit, process residue and
uninstall residue.

Signing identity: this host has no Apple Developer ID, so the package is
built ad-hoc (`signingIdentity: "-"`). Developer ID signing, notarization and
Gatekeeper acceptance stay external credential gates and are recorded as
outstanding evidence in `docs/development/EB-16.md`.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"

sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))
sys.path.insert(0, str(BACKEND_ROOT / "src"))

from build_embedded_browser_distribution import (  # noqa: E402
    build_distribution_manifest,
)
from build_embedded_chromium_staging import (  # noqa: E402
    build_staging,
    load_staging_contract,
    sha256_file,
)
from check_embedded_browser_package import (  # noqa: E402
    PackageAuditReport,
    audit_embedded_browser_package,
    browser_resource_root,
)
from release_assembly import (  # noqa: E402
    install_and_seal,
    install_video_runtime,
    require_packaged_browser,
    require_packaged_video_runtime,
)
from prepare_video_runtime import prepare as prepare_video_runtime  # noqa: E402
from run_p9_03_acceptance import (  # noqa: E402
    APP_IDENTIFIER,
    BASE_TAURI_CONFIG,
    CANDIDATE_TAURI_CONFIG,
    CARGO_MANIFEST,
    EXECUTOR_RESOURCE,
    PRODUCTION_ASSETS,
    app_binary,
    executor_signing_material,
    one_directory,
    one_file,
    pnpm_executable,
    release_environment,
    run_checked,
    verify_manifest_signature,
)

STAGING_CONTRACT = REPOSITORY_ROOT / "contracts/browser/embedded-chromium-staging.v1.json"
_EB_03_CACHE = ".local/embedded-browser-video-studio/eb-03-cache/chrome-mac-arm64.zip"


def _first_existing(*candidates: Path) -> Path:
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


DEFAULT_ARCHIVES = {
    # The EB-03 archive cache lives in the primary checkout's .local; resolve
    # it both from the primary checkout itself and from a wt/<task> worktree.
    "macos-arm64": _first_existing(
        REPOSITORY_ROOT / _EB_03_CACHE,
        REPOSITORY_ROOT.parent.parent / _EB_03_CACHE,
    ),
    "macos-x86_64": _first_existing(
        REPOSITORY_ROOT / ".local/eb-mac-x64/chrome-mac-x64.zip",
        REPOSITORY_ROOT.parent.parent / ".local/eb-mac-x64/chrome-mac-x64.zip",
    ),
}
DEFAULT_WORK_DIRECTORY = REPOSITORY_ROOT / ".local/eb-16/run"
LAUNCH_ENVIRONMENT_FLAG = "AUTOMATION_TOOL_EB16_LAUNCH_VISIBLE_APP"
LOCAL_CONTROL_PLANE_PORT = 8765
# The desktop startup gate calls both of these through the production Rust
# network bridge before it mounts the workbench, so observing them on a real
# Control Plane proves the packaged App reached its normal startup path.
STARTUP_CHECK_PATHS = ("/api/v1/health", "/api/v1/version")


class AcceptanceFailed(RuntimeError):
    """The first-release package acceptance failed."""


def announce(message: str) -> None:
    print(f"[EB-16] {message}", flush=True)


def require_macos() -> tuple[str, str]:
    if platform.system() != "Darwin":
        raise AcceptanceFailed("EB-16 macOS package acceptance requires macOS")
    machine = platform.machine().lower()
    if machine in {"arm64", "aarch64"}:
        return "macos-arm64", "aarch64"
    if machine in {"x86_64", "amd64"}:
        return "macos-x86_64", "x86_64"
    raise AcceptanceFailed("EB-16 macOS architecture is unsupported")


def run_deterministic_gate() -> None:
    announce("Running the deterministic package gate")
    result = subprocess.run(
        [sys.executable, "scripts/test_embedded_browser_package.py"],
        cwd=REPOSITORY_ROOT,
        check=False,
    )
    if result.returncode != 0:
        raise AcceptanceFailed("deterministic package gate failed")


def stage_browser_distribution(target_id: str, archive: Path, output: Path) -> None:
    announce(f"Staging the digest-locked {target_id} Chromium from {archive.name}")
    if not archive.is_file():
        raise AcceptanceFailed(f"locked archive is not downloaded yet: {archive}")
    contract = load_staging_contract(STAGING_CONTRACT)
    build_staging(
        contract=contract,
        target_id=target_id,
        archive_path=archive,
        archive_sha256=sha256_file(archive),
        output=output,
    )
    build_distribution_manifest(staging=output, target_id=target_id)


def build_executor_candidate(
    output: Path, architecture: str
) -> tuple[Path, str, Any]:
    from automation_tool.executor.macos_candidate import build_macos_executor_candidate
    from automation_tool.executor.package_manifest import (
        write_signed_executor_manifest,
    )

    announce("Building the real signed Local Executor candidate")
    build_macos_executor_candidate(backend_root=BACKEND_ROOT, output_directory=output)
    seed, public_key, private_key = executor_signing_material()
    write_signed_executor_manifest(
        bundle_directory=output,
        executor_version="0.1.0",
        build_id="eb-16-macos-release",
        target_platform="macos",
        target_architecture=architecture,
        signing_private_key=seed,
    )
    return output, public_key, private_key


def write_release_configuration(directory: Path, executor: Path) -> Path:
    """Declare only the resources the Tauri bundler can copy safely.

    The embedded browser is deliberately not declared here: the bundler
    follows symlinks, which drops the Chrome for Testing framework links and
    breaks its upstream signature. The release packager installs that tree
    itself right after the bundle is produced.
    """
    configuration = json.loads(CANDIDATE_TAURI_CONFIG.read_text(encoding="utf-8"))
    configuration["bundle"]["macOS"] = {"signingIdentity": "-"}
    configuration["bundle"]["resources"] = {
        f"{os.fspath(executor)}{os.sep}": f"{EXECUTOR_RESOURCE.as_posix()}/",
    }
    destination = directory / "tauri.eb-16.generated.json"
    destination.write_text(
        json.dumps(configuration, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return destination


def build_release_package(
    *, configuration: Path, environment: dict[str, str], target: Path
) -> Path:
    announce("Building one production-mode .app (no test features)")
    bundle_root = target / "release/bundle"
    if bundle_root.exists():
        shutil.rmtree(bundle_root)
    run_checked(
        [
            pnpm_executable(),
            "exec",
            "tauri",
            "build",
            "--bundles",
            "app",
            "--config",
            os.fspath(configuration),
            "--ci",
        ],
        environment=environment,
    )
    return one_directory(target / "release/bundle/macos", ".app")


def install_runtime_resources_and_sign(
    application: Path, staging: Path, target_id: str, video_runtime: Path
) -> None:
    """Run the shared release assembly step, the same one a release uses.

    The acceptance script must not keep its own copy of this: when it did, the
    verified path and the shipped path were different paths, and the shipped
    one had no browser in it at all.

    Ordering matters twice over. The video runtime is installed *before*
    `install_and_seal`, because that call seals the bundle at the end and a
    signature taken before a resource lands does not cover it. The browser is
    installed last for the same reason it is installed here at all: the macOS
    bundler destroys its symlinked framework, so it cannot be declared under
    `bundle.resources` and has to arrive afterwards.
    """
    announce("Installing the video runtime resources into the built bundle")
    installed = install_video_runtime(
        application=application, staging=video_runtime, platform="macos"
    )
    announce(f"Video runtime installed: {sorted(installed)}")
    announce("Installing the embedded browser, verifying it, then re-sealing")
    install_and_seal(
        application=application,
        staging=staging,
        target_id=target_id,
        platform="macos",
        seal=lambda bundle: run_checked(
            ["codesign", "--force", "--sign", "-", os.fspath(bundle)]
        ),
    )


def create_disk_image(application: Path, output: Path, target_id: str) -> Path:
    # A bundle without a verified browser must not reach a distributable
    # artifact; this is the gate the ordinary candidate build fails.
    require_packaged_browser(
        application=application, target_id=target_id, platform="macos"
    )
    # The same gate for the video runtime. Without it a package ships whose
    # video features fail on the user's machine while every acceptance run
    # stays green, which is exactly what happened.
    require_packaged_video_runtime(application=application, platform="macos")
    announce("Creating the release disk image from the final App bundle")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    run_checked(
        [
            "hdiutil",
            "create",
            "-volname",
            application.stem,
            "-srcfolder",
            os.fspath(application),
            "-fs",
            "HFS+",
            "-format",
            "UDZO",
            "-quiet",
            os.fspath(output),
        ]
    )
    return output


def audit_package_payload(application: Path, target_id: str) -> PackageAuditReport:
    report = audit_embedded_browser_package(
        bundle_root=application, target_id=target_id, platform="macos"
    )
    announce(
        f"Package payload verified: {report.browser_files} browser files "
        f"({report.browser_bytes} bytes) inside {report.package_files} package "
        f"files ({report.package_bytes} bytes)"
    )
    return report


def merge_configuration(base: object, overlay: object) -> object:
    """Merge a Tauri config overlay the same way `tauri build --config` does."""
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = dict(base)
        for key, value in overlay.items():
            merged[key] = merge_configuration(merged.get(key), value)
        return merged
    return overlay


def effective_configuration(overlay: Path, directory: Path) -> Path:
    """Write the configuration the build actually used, for auditing."""
    merged = merge_configuration(
        json.loads(BASE_TAURI_CONFIG.read_text(encoding="utf-8")),
        json.loads(overlay.read_text(encoding="utf-8")),
    )
    destination = directory / "tauri.eb-16.effective.json"
    destination.write_text(
        json.dumps(merged, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return destination


def audit_package_content(
    application: Path,
    executor_package: Path,
    environment: dict[str, str],
    configuration: Path,
) -> None:
    announce("Auditing the built binary, configuration and whole bundle content")
    run_checked(
        [
            "node",
            "scripts/audit-production-package.mjs",
            "--binary",
            os.fspath(app_binary(application)),
            "--cargo-manifest",
            os.fspath(CARGO_MANIFEST),
            "--tauri-config",
            os.fspath(configuration),
            "--dist",
            os.fspath(PRODUCTION_ASSETS),
        ],
        environment=environment,
    )
    run_checked(
        [
            "node",
            "scripts/audit-release-bundle.mjs",
            "--bundle-root",
            os.fspath(application),
            "--executor-package",
            os.fspath(executor_package),
            "--embedded-browser",
            os.fspath(browser_resource_root(application, "macos")),
            "--platform",
            "macos",
        ]
    )


def packaged_distribution(application: Path, target_id: str) -> tuple[Path, dict]:
    """The packaged browser executable and the manifest that declared it.

    One reader for both, so every consumer inherits the target check instead
    of re-deriving the path from an unchecked document.
    """
    root = browser_resource_root(application, "macos")
    document = json.loads(
        (root / "distribution-manifest.v1.json").read_text(encoding="utf-8")
    )
    if document["target"] != target_id:
        raise AcceptanceFailed("packaged distribution manifest target drifted")
    return root / Path(*str(document["executable"]).split("/")), document


def packaged_browser_executable(application: Path, target_id: str) -> Path:
    return packaged_distribution(application, target_id)[0]


def signature_details(path: Path) -> str:
    details = subprocess.run(
        ["codesign", "--display", "--verbose=4", os.fspath(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return f"{details.stdout}\n{details.stderr}"


def verify_embedded_mach_o_signature(path: Path, expected_identifier: str) -> None:
    """Verify one embedded Mach-O signature outside its enclosing bundle.

    `codesign --verify` on a path inside a bundle always re-resolves to the
    bundle, and Chrome for Testing ships without bundle-level code seals
    (`_CodeSignature` is absent upstream), so the packaged code is verified by
    checking the embedded ad-hoc linker signature of the Mach-O itself.
    """
    rendered = signature_details(path)
    if f"Identifier={expected_identifier}" not in rendered:
        raise AcceptanceFailed("packaged browser code identity drifted")
    if "linker-signed" not in rendered or "adhoc" not in rendered:
        raise AcceptanceFailed("packaged browser lost its upstream code signature")
    with tempfile.TemporaryDirectory(
        prefix="automation-tool-eb16-signature-", dir="/private/tmp"
    ) as raw:
        detached = Path(raw) / "packaged-mach-o"
        shutil.copy2(path, detached)
        run_checked(["codesign", "--verify", "--strict", os.fspath(detached)])


def verify_code_signatures(application: Path, target_id: str) -> None:
    announce("Verifying outer and inner code signatures on the built package")
    run_checked(["codesign", "--verify", "--strict", os.fspath(application)])
    rendered = signature_details(application)
    if (
        f"Identifier={APP_IDENTIFIER}" not in rendered
        or "Signature=adhoc" not in rendered
        or "TeamIdentifier=not set" not in rendered
        or "Developer ID" in rendered
        or "Apple Distribution" in rendered
    ):
        raise AcceptanceFailed("EB-16 App signing boundary is inconsistent")
    executable = packaged_browser_executable(application, target_id)
    verify_embedded_mach_o_signature(executable, "Google Chrome for Testing")
    framework = (
        executable.parent.parent
        / "Frameworks/Google Chrome for Testing Framework.framework"
        / "Versions/Current/Google Chrome for Testing Framework"
    )
    verify_embedded_mach_o_signature(framework, "Google Chrome for Testing Framework")
    announce(
        "Outer ad-hoc signature seals the final bundle and the packaged browser "
        "keeps its upstream ad-hoc linker signature"
    )


def install_from_disk_image(
    disk_image: Path, install_root: Path, mount_point: Path
) -> Path:
    announce("Verifying and mounting the built .dmg, then installing the App")
    run_checked(["hdiutil", "verify", os.fspath(disk_image)])
    mount_point.mkdir(parents=True)
    run_checked(
        [
            "hdiutil",
            "attach",
            "-nobrowse",
            "-readonly",
            "-mountpoint",
            os.fspath(mount_point),
            os.fspath(disk_image),
        ]
    )
    try:
        source = one_directory(mount_point, ".app")
        install_root.mkdir(parents=True)
        destination = install_root / source.name
        shutil.copytree(
            source, destination, symlinks=True, copy_function=shutil.copy2
        )
    finally:
        # Never raise from here: if `attach` itself failed, a failing `detach`
        # would replace the real reason for the failure.
        subprocess.run(
            ["hdiutil", "detach", os.fspath(mount_point)],
            check=False,
            capture_output=True,
            text=True,
        )
    return destination


def cargo_executable() -> str:
    executable = shutil.which("cargo")
    if executable is not None:
        return executable
    fallback = Path.home() / ".cargo/bin/cargo"
    if fallback.is_file():
        return os.fspath(fallback)
    raise AcceptanceFailed("cargo is unavailable")


def verify_installed_startup_gate_inputs(
    installed: Path, environment: dict[str, str], target: Path
) -> None:
    """Run the production startup gate logic against the installed package."""
    announce("Checking every startup gate input of the installed package")
    with tempfile.TemporaryDirectory(
        prefix="automation-tool-eb16-startup-", dir="/private/tmp"
    ) as raw:
        probe_environment = {
            **environment,
            "EB16_INSTALLED_RESOURCES": os.fspath(installed / "Contents/Resources"),
            "EB16_APP_DATA": os.fspath(Path(raw) / "app-data"),
            "CARGO_TARGET_DIR": os.fspath(target),
        }
        run_checked(
            [
                cargo_executable(),
                "test",
                "--release",
                "--manifest-path",
                os.fspath(CARGO_MANIFEST),
                "--test",
                "installed_release_startup",
                "--locked",
                "--",
                "--ignored",
                "--nocapture",
            ],
            cwd=REPOSITORY_ROOT,
            environment=probe_environment,
        )


def probe_packaged_browser(application: Path, target_id: str) -> str:
    """Launch the packaged Chromium itself, offline and headless."""
    announce("Launching the packaged Chromium from the installed App (offline)")
    # Reuse the one resolver so this probe also inherits its target check.
    executable, document = packaged_distribution(application, target_id)
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory(
        prefix="automation-tool-eb16-browser-", dir="/private/tmp"
    ) as raw:
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
                page = context.new_page()
                page.set_content("<title>eb-16</title>")
                if page.title() != "eb-16":
                    raise AcceptanceFailed("packaged browser could not render a page")
            finally:
                context.close()
        # This runner owns the browser it started: on every path — success,
        # assertion failure, timeout or cancellation — the whole process tree
        # of this run's profile is terminated before the residue assertion.
        terminate_processes_matching(os.fspath(profile))
        require_no_process_matching(os.fspath(profile))
    if document["runtime"]["chromium"]["browser_version"] != version:
        raise AcceptanceFailed("packaged browser version differs from the manifest")
    announce(f"Packaged Chromium reported version {version} and exited cleanly")
    return version


def processes_matching(marker: str) -> list[tuple[int, str]]:
    listing = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        check=True,
        capture_output=True,
        text=True,
    )
    matches: list[tuple[int, str]] = []
    for line in listing.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_text, _, command = stripped.partition(" ")
        if marker in command and int(pid_text) != os.getpid():
            matches.append((int(pid_text), command))
    return matches


def terminate_processes_matching(marker: str) -> None:
    """Terminate only the processes this run started under `marker`."""
    for attempt_signal in (signal.SIGTERM, signal.SIGKILL):
        matches = processes_matching(marker)
        if not matches:
            return
        for pid, _ in matches:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, attempt_signal)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and processes_matching(marker):
            time.sleep(0.1)


def require_no_process_matching(marker: str) -> None:
    residue = processes_matching(marker)
    if residue:
        raise AcceptanceFailed(f"processes still reference {marker}: {residue[:3]}")


def snapshot_paths(root: Path) -> set[Path]:
    if not root.exists():
        return set()
    return {root, *root.rglob("*")}


def user_app_data_fingerprint(root: Path) -> dict[str, object]:
    """Identity of the real user's App data, to prove the run never wrote it."""
    if not root.exists():
        return {"exists": False, "inode": None, "entries": 0}
    return {
        "exists": True,
        "inode": root.stat().st_ino,
        "entries": len(snapshot_paths(root)),
    }


def compose_command(project_name: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        project_name,
        "--file",
        os.fspath(REPOSITORY_ROOT / "compose.yaml"),
    ]


def isolated_database_environment(database_port: int) -> tuple[dict[str, str], str]:
    from run_h8_20_acceptance import unused_loopback_port

    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AUTOMATION_TOOL_")
    }
    password = secrets.token_hex(24)
    database_url = (
        "postgresql+asyncpg://automation_tool_eb16:"
        f"{password}@127.0.0.1:{database_port}/automation_tool_eb16"
    )
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_eb16_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_eb16_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(unused_loopback_port()),
            "AUTOMATION_TOOL_TEST_DB_USER": "automation_tool_eb16",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": password,
            "AUTOMATION_TOOL_TEST_DB_NAME": "automation_tool_eb16",
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": database_url,
        }
    )
    return environment, database_url


def start_local_control_plane(
    request_ledger: list[str], environment: dict[str, str]
) -> tuple[Any, threading.Thread]:
    import uvicorn
    from automation_tool.control_plane.bootstrap.app import create_app
    from fastapi import Request
    from fastapi.responses import Response

    previous = {key: os.environ.get(key) for key in environment}
    os.environ.update(environment)
    try:
        app = create_app()
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    @app.middleware("http")
    async def record(request: Request, call_next: Any) -> Response:
        request_ledger.append(request.url.path)
        return await call_next(request)

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=LOCAL_CONTROL_PLANE_PORT,
            access_log=False,
            log_level="critical",
        )
    )
    thread = threading.Thread(
        target=server.run, name="automation-tool-eb16-control-plane", daemon=True
    )
    thread.start()
    return server, thread


def registered_gui_application(installed: Path) -> str:
    """Return the Launch Services record of the installed App, or an empty text."""
    found = subprocess.run(
        ["lsappinfo", "find", f"bundleid={APP_IDENTIFIER}"],
        check=False,
        capture_output=True,
        text=True,
    )
    reference = found.stdout.strip()
    if not reference:
        return ""
    details = subprocess.run(
        ["lsappinfo", "info", reference],
        check=False,
        capture_output=True,
        text=True,
    )
    rendered = details.stdout
    if os.fspath(installed) not in rendered:
        raise AcceptanceFailed(
            "a different App instance is registered with Launch Services"
        )
    return rendered


def launch_installed_application(
    installed: Path, request_ledger: list[str], home: Path
) -> dict[str, object]:
    announce("Launching the installed release App from its normal entry point")
    announce("A real product window will appear on screen until the App is quit")
    binary = app_binary(installed)
    if registered_gui_application(installed):
        raise AcceptanceFailed("an App instance is already running")
    # The App is given its own HOME, so `app_data_dir` resolves inside this
    # run's temporary directory: the launch is a genuine first install and the
    # real user's App data is never read, moved or written.
    (home / "Library/Application Support").mkdir(parents=True)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AUTOMATION_TOOL_") and not key.startswith("TAURI_")
    }
    environment["HOME"] = os.fspath(home)
    process = subprocess.Popen(
        [os.fspath(binary)],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 120
        startup_seen = False
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AcceptanceFailed("installed App exited before opening")
            if all(path in request_ledger for path in STARTUP_CHECK_PATHS):
                startup_seen = True
                break
            time.sleep(0.2)
        if not startup_seen:
            raise AcceptanceFailed(
                "installed App never ran its startup check against the Control Plane; "
                f"observed {sorted(set(request_ledger))[:8]}"
            )
        announce(
            "Startup path reached: the packaged App requested "
            f"{sorted({path for path in request_ledger if path.startswith('/api/')})}"
        )
        registration = registered_gui_application(installed)
        if not registration:
            raise AcceptanceFailed(
                "the installed App did not register as a GUI application"
            )
        announce("Launch Services registered the installed App as a GUI application")
        announce("Quitting the App")
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=60)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)
    require_no_process_matching(os.fspath(installed))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and registered_gui_application(installed):
        time.sleep(0.2)
    if registered_gui_application(installed):
        raise AcceptanceFailed("the App is still registered as running after quitting")
    return {
        "requests": sorted(set(request_ledger)),
        "exit_code": process.returncode,
        "launch_services_record": [
            line.strip()
            for line in registration.splitlines()
            if "bundle path" in line or "pid =" in line
        ],
    }


def run_launch_phase(installed: Path, work_directory: Path) -> dict[str, object]:
    from acceptance_postgres import managed_test_postgres
    from run_h8_20_acceptance import require_port_closed, unused_loopback_port, wait_for_port

    require_port_closed(LOCAL_CONTROL_PLANE_PORT)
    database_port = unused_loopback_port()
    project_name = f"automation-tool-eb16-{os.getpid()}"
    environment, _ = isolated_database_environment(database_port)
    real_app_data = Path.home() / "Library/Application Support" / APP_IDENTIFIER
    before_real = user_app_data_fingerprint(real_app_data)
    request_ledger: list[str] = []
    server: Any = None
    thread: threading.Thread | None = None
    announce(f"Starting isolated PostgreSQL as {project_name}")
    with tempfile.TemporaryDirectory(
        prefix="automation-tool-eb16-home-", dir="/private/tmp"
    ) as raw_home:
        home = Path(raw_home)
        with managed_test_postgres(
            compose=compose_command(project_name),
            database_port=database_port,
            environment=environment,
            repository_root=REPOSITORY_ROOT,
        ):
            try:
                announce("Applying the production migration chain")
                subprocess.run(
                    [sys.executable, "-m", "alembic", "upgrade", "head"],
                    check=True,
                    cwd=BACKEND_ROOT,
                    env=environment,
                )
                announce(
                    "Starting the local Control Plane on "
                    f"127.0.0.1:{LOCAL_CONTROL_PLANE_PORT}"
                )
                server, thread = start_local_control_plane(request_ledger, environment)
                wait_for_port(LOCAL_CONTROL_PLANE_PORT)
                result = launch_installed_application(installed, request_ledger, home)
            finally:
                if server is not None:
                    server.should_exit = True
                if thread is not None:
                    thread.join(timeout=20)
                    if thread.is_alive():
                        raise AcceptanceFailed(
                            "EB-16 Control Plane thread did not stop"
                        )
        require_port_closed(LOCAL_CONTROL_PLANE_PORT)
        isolated_app_data = home / "Library/Application Support" / APP_IDENTIFIER
        created = sorted(
            os.fspath(path.relative_to(isolated_app_data.parent))
            for path in snapshot_paths(isolated_app_data)
        )
        if not created:
            raise AcceptanceFailed(
                "the App did not create its data inside the isolated HOME; "
                "the run may have touched the real user directory"
            )
        result["first_install_app_data_created"] = created
        result["isolated_home_used"] = True
    after_real = user_app_data_fingerprint(real_app_data)
    if after_real != before_real:
        raise AcceptanceFailed("the acceptance modified the real user App data")
    result["user_app_data_untouched"] = before_real
    (work_directory / "launch-evidence.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def uninstall_and_check_residue(installed: Path, install_root: Path) -> None:
    announce("Uninstalling the App and checking for residue")
    shutil.rmtree(installed)
    remaining = sorted(path.name for path in install_root.iterdir())
    if remaining:
        raise AcceptanceFailed(f"uninstall left residue in the install root: {remaining}")
    require_no_process_matching(os.fspath(install_root))


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIRECTORY)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-launch", action="store_true")
    parser.add_argument("--keep", action="store_true")
    return parser.parse_args()


def main() -> int:
    target_id, architecture = require_macos()
    arguments = parse_arguments()
    archive = arguments.archive or DEFAULT_ARCHIVES[target_id]
    work_directory: Path = arguments.work_dir
    build_directory = work_directory / "build"
    cargo_target = work_directory / "cargo-target"
    install_root = work_directory / "installed"
    work_directory.mkdir(parents=True, exist_ok=True)

    run_deterministic_gate()

    if not arguments.skip_build:
        if build_directory.exists():
            shutil.rmtree(build_directory)
        build_directory.mkdir(parents=True)
        browser = build_directory / "embedded-browser"
        stage_browser_distribution(target_id, archive, browser)
        executor = build_directory / "executor" / "automation-tool-executor"
        _, public_key, private_key = build_executor_candidate(executor, architecture)
        (build_directory / "executor-verifying-key").write_text(
            public_key, encoding="utf-8"
        )
        configuration = write_release_configuration(build_directory, executor)
        effective = effective_configuration(configuration, build_directory)
        environment = release_environment(cargo_target, public_key)
        application = build_release_package(
            configuration=configuration, environment=environment, target=cargo_target
        )
        announce("Preparing the pinned video runtime resources (cached per machine)")
        video_runtime = prepare_video_runtime(platform="macos")
        install_runtime_resources_and_sign(
            application, browser, target_id, video_runtime
        )
        disk_image = create_disk_image(
            application,
            cargo_target / "release/bundle/dmg" / f"{application.stem}_0.1.0.dmg",
            target_id,
        )
    else:
        private_key = None
        public_key = (build_directory / "executor-verifying-key").read_text(
            encoding="utf-8"
        )
        environment = release_environment(cargo_target, public_key)
        effective = effective_configuration(
            build_directory / "tauri.eb-16.generated.json", build_directory
        )
        bundle_root = cargo_target / "release/bundle"
        application = one_directory(bundle_root / "macos", ".app")
        disk_image = one_file(bundle_root / "dmg", ".dmg")

    announce(f"Built application: {application}")
    announce(f"Built disk image: {disk_image} ({disk_image.stat().st_size} bytes)")

    report = audit_package_payload(application, target_id)
    audit_package_content(
        application,
        application / "Contents/Resources" / EXECUTOR_RESOURCE,
        environment,
        effective,
    )
    verify_code_signatures(application, target_id)
    if private_key is not None:
        verify_manifest_signature(
            application / "Contents/Resources" / EXECUTOR_RESOURCE, private_key
        )

    if install_root.exists():
        shutil.rmtree(install_root)
    mount_point = work_directory / "mounted-dmg"
    if mount_point.exists():
        shutil.rmtree(mount_point)
    try:
        installed = install_from_disk_image(disk_image, install_root, mount_point)
        installed_report = audit_package_payload(installed, target_id)
        if (
            installed_report.browser_files != report.browser_files
            or installed_report.browser_bytes != report.browser_bytes
            or installed_report.package_files != report.package_files
            or installed_report.package_bytes != report.package_bytes
        ):
            raise AcceptanceFailed("installed App payload differs from the built App")
        verify_code_signatures(installed, target_id)
        verify_installed_startup_gate_inputs(installed, environment, cargo_target)
        browser_version = probe_packaged_browser(installed, target_id)

        launch_result: dict[str, object] | None = None
        if arguments.skip_launch or os.environ.get(LAUNCH_ENVIRONMENT_FLAG) != "1":
            announce(
                "Skipping the visible App launch phase "
                f"(set {LAUNCH_ENVIRONMENT_FLAG}=1 to run it)"
            )
        else:
            launch_result = run_launch_phase(installed, work_directory)

        uninstall_and_check_residue(installed, install_root)
    finally:
        # The installed copy and the mount point are this run's resources on
        # every path, including a failure part way through the audits.
        subprocess.run(
            ["hdiutil", "detach", os.fspath(mount_point)],
            check=False,
            capture_output=True,
            text=True,
        )
        if not arguments.keep:
            shutil.rmtree(install_root, ignore_errors=True)
            shutil.rmtree(mount_point, ignore_errors=True)

    evidence = {
        "target": target_id,
        "architecture": architecture,
        "application": os.fspath(application),
        "disk_image_bytes": disk_image.stat().st_size,
        "browser_files": report.browser_files,
        "browser_bytes": report.browser_bytes,
        "package_files": report.package_files,
        "package_bytes": report.package_bytes,
        "browser_version": browser_version,
        "launch": launch_result,
    }
    (work_directory / "eb-16-acceptance.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    announce(
        "EB-16 acceptance passed: one ad-hoc signed "
        f"{target_id} package with {report.browser_files} browser files "
        f"({report.browser_bytes} bytes), package {report.package_bytes} bytes, "
        f"disk image {disk_image.stat().st_size} bytes"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AcceptanceFailed as error:
        print(f"EB-16 acceptance failed: {error}")
        raise SystemExit(1) from error
