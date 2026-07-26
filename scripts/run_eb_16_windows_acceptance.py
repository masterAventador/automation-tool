#!/usr/bin/env python3
"""EB-16 acceptance on Windows: one real x86_64 first-release NSIS package.

The macOS half of EB-16 lives in `scripts/run_eb_16_acceptance.py`. This is the
Windows half of the same task, and it deliberately reuses the same pieces: the
EB-03 staging builder, the EB-05 manifest builder, the shared release assembly
step in `scripts/release_assembly.py`, and the EB-16 package audit. Only the
container differs — a `currentUser` NSIS installer instead of an `.app` plus
`.dmg`.

Where the browser is installed differs too, and for a reason. On macOS the
Tauri bundler cannot be trusted with the browser tree: it follows symlinks
while copying resources, which breaks the Chrome for Testing framework, so the
assembler installs the tree into the finished `.app` afterwards. The Windows
target has no symlinks at all (EB-03 contract), and an NSIS installer is a
sealed executable that cannot be opened up after the fact — the payload has to
exist before the bundler runs. So here the assembler runs *first*, on the
payload tree that the bundler will then copy, and `require_packaged_browser`
guards the `tauri build` call the same way it guards `hdiutil create` on
macOS: no distributable artifact is produced from an unverified browser.

Signing: this host has no Authenticode certificate, so the produced package is
unsigned. That is recorded as a measured fact (`Get-AuthenticodeSignature`
reports `NotSigned`), never asserted away.
"""

from __future__ import annotations

import argparse
import json
import os
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

from build_embedded_browser_distribution import (  # noqa: E402
    build_distribution_manifest,
)
from build_embedded_chromium_staging import (  # noqa: E402
    build_staging,
    load_staging_contract,
    sha256_file,
)
from check_embedded_browser_package import (  # noqa: E402
    BROWSER_RESOURCE_NAME,
    PackageAuditReport,
    audit_embedded_browser_package,
    browser_resource_root,
)
from eb_17_clean_machine import (  # noqa: E402
    require_no_browser_installer_scripts,
    scan_package_for_system_browser_references,
)
from embedded_browser_archives import (  # noqa: E402
    WINDOWS_X86_64_ARCHIVE,
    archive_path,
)
from prepare_video_runtime import prepare as prepare_video_runtime  # noqa: E402
from production_assets import (  # noqa: E402
    AUDITED_DISTRIBUTION_NAME,
    require_frozen_distribution,
    snapshot_production_assets,
)
from release_assembly import (  # noqa: E402
    VIDEO_RUNTIME_RESOURCES,
    install_and_seal,
    install_video_runtime,
    require_packaged_browser,
    require_packaged_video_runtime,
)
from run_p9_04_acceptance import (  # noqa: E402
    authenticode_facts,
    executor_signing_material,
    install_root,
    one_file,
    package_files,
    release_environment,
    installer_environment,
    require_windows,
    run_checked,
    run_powershell,
    verify_manifest_signature,
    windows_registry_installations,
)

BASE_TAURI_CONFIG = TAURI_ROOT / "tauri.conf.json"
CANDIDATE_TAURI_CONFIG = TAURI_ROOT / "tauri.windows-candidate.conf.json"
CARGO_MANIFEST = TAURI_ROOT / "Cargo.toml"
STAGING_CONTRACT = REPOSITORY_ROOT / "contracts/browser/embedded-chromium-staging.v1.json"
EXECUTOR_RESOURCE = Path("local-executor/package")
TARGET_ID = "windows-x86_64"
DEFAULT_WORK_DIRECTORY = REPOSITORY_ROOT / ".local/eb-16-windows/run"


class AcceptanceFailed(RuntimeError):
    """The Windows first-release package acceptance failed."""


def announce(message: str) -> None:
    print(f"[EB-16-WIN] {message}", flush=True)


def product_name() -> str:
    name = json.loads(BASE_TAURI_CONFIG.read_text(encoding="utf-8")).get("productName")
    if not isinstance(name, str) or not name:
        raise AcceptanceFailed("the base Tauri configuration has no product name")
    return name


def stage_browser_distribution(archive: Path, output: Path) -> None:
    announce(f"Staging the digest-locked {TARGET_ID} Chromium from {archive.name}")
    if not archive.is_file():
        raise AcceptanceFailed(f"locked archive is not downloaded yet: {archive}")
    contract = load_staging_contract(STAGING_CONTRACT)
    build_staging(
        contract=contract,
        target_id=TARGET_ID,
        archive_path=archive,
        archive_sha256=sha256_file(archive),
        output=output,
    )
    build_distribution_manifest(staging=output, target_id=TARGET_ID)


def build_executor_candidate(output: Path, architecture: str) -> tuple[str, Any]:
    from automation_tool.executor.package_manifest import (
        write_signed_executor_manifest,
    )
    from automation_tool.executor.windows_candidate import (
        build_windows_executor_candidate,
    )

    announce("Building the real signed Local Executor candidate")
    build_windows_executor_candidate(
        backend_root=BACKEND_ROOT, output_directory=output
    )
    seed, public_key, private_key = executor_signing_material()
    write_signed_executor_manifest(
        bundle_directory=output,
        executor_version="0.1.0",
        build_id="eb-16-windows-release",
        target_platform="windows",
        target_architecture=architecture,
        signing_private_key=seed,
    )
    return public_key, private_key


def seal_windows_payload(payload: Path) -> None:
    """The Windows analogue of the macOS re-seal, which is a no-op by design.

    On macOS the bundle is signed before the browser is installed, so the
    signature has to be retaken afterwards or it does not cover the browser.
    On Windows nothing has been signed at this point: Authenticode is applied
    by the bundler to the installer and the main binary, both of which are
    produced *after* this payload. The ordering property the macOS re-seal
    buys is therefore already structural here, so there is nothing to redo —
    but the absence of a signing identity must not be silently assumed, so it
    is asserted against the configuration instead.
    """
    windows = json.loads(CANDIDATE_TAURI_CONFIG.read_text(encoding="utf-8"))["bundle"][
        "windows"
    ]
    if (
        windows.get("certificateThumbprint") is not None
        or windows.get("signCommand") is not None
    ):
        raise AcceptanceFailed(
            "the Windows configuration declares a signing identity, so the payload "
            "must be sealed here rather than assumed unsigned"
        )
    announce(f"Payload assembled at {payload} (no Authenticode identity on this host)")


def relative_to_tauri_root(path: Path) -> str:
    """Tauri resolves resource sources against its own root, not the drive."""
    relative = os.path.relpath(path, TAURI_ROOT).replace(os.sep, "/")
    if os.path.isabs(relative) or ":" in relative:
        raise AcceptanceFailed("resource source must be relative to the Tauri root")
    return relative


def write_release_configuration(directory: Path, executor: Path, payload: Path) -> Path:
    """Declare every resource tree the NSIS bundler ships.

    Unlike macOS, the embedded browser *is* declared here: the Windows target
    has no symlinks, so the bundler's copy is faithful, and an NSIS installer
    cannot be opened after it is built. The trees named here have already been
    verified file-by-file by the release assembler.

    The video runtime resources are declared from the same payload for the
    same reason. Leaving them out is precisely the defect this wiring exists
    to prevent: the production video code resolves them from the installed
    resource directory, and a package that omits them fails on the user's
    machine while every acceptance run stays green.
    """
    configuration = json.loads(CANDIDATE_TAURI_CONFIG.read_text(encoding="utf-8"))
    resources = {
        f"{relative_to_tauri_root(executor)}/": f"{EXECUTOR_RESOURCE.as_posix()}/",
        f"{relative_to_tauri_root(browser_resource_root(payload, 'windows'))}/": (
            f"{BROWSER_RESOURCE_NAME}/"
        ),
    }
    for resource in VIDEO_RUNTIME_RESOURCES:
        installed = payload.joinpath(*resource.installed_parts)
        destination = "/".join(resource.installed_parts)
        resources[f"{relative_to_tauri_root(installed)}/"] = f"{destination}/"
    configuration["bundle"]["resources"] = resources
    destination = directory / "tauri.eb-16-windows.generated.json"
    destination.write_text(
        json.dumps(configuration, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return destination


def effective_configuration(overlay: Path, directory: Path) -> Path:
    from run_eb_16_acceptance import merge_configuration

    merged = merge_configuration(
        json.loads(BASE_TAURI_CONFIG.read_text(encoding="utf-8")),
        json.loads(overlay.read_text(encoding="utf-8")),
    )
    destination = directory / "tauri.eb-16-windows.effective.json"
    destination.write_text(
        json.dumps(merged, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return destination


def pnpm_executable() -> str:
    executable = shutil.which("pnpm.cmd") or shutil.which("pnpm")
    if executable is None:
        raise AcceptanceFailed("pnpm is unavailable")
    return executable


def main_binary(directory: Path) -> Path:
    """The one product executable Tauri renamed after the product name.

    Tauri derives the binary name from `mainBinaryName`, falling back to the
    product name; the production configuration sets neither explicitly, so the
    name is resolved from the artifact rather than assumed.
    """
    named = directory / f"{product_name()}.exe"
    if named.is_file():
        return named
    # The install root also holds the uninstaller NSIS generates for itself.
    # It is not a product binary and is excluded by exact name only, so a
    # genuine second executable still makes this ambiguous and fails.
    candidates = sorted(
        path
        for path in directory.glob("*.exe")
        if path.is_file() and path.name.lower() != "uninstall.exe"
    )
    if len(candidates) != 1:
        raise AcceptanceFailed(
            f"no single product executable exists in {directory}: "
            f"{[path.name for path in candidates]}"
        )
    return candidates[0]


def build_release_package(
    *, configuration: Path, environment: dict[str, str], target: Path
) -> tuple[Path, Path]:
    announce("Building one production-mode NSIS installer (no test features)")
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
            "nsis",
            "--config",
            os.fspath(configuration),
            "--ci",
        ],
        environment=environment,
    )
    binary = main_binary(target / "release")
    installer = one_file(
        target / "release/bundle/nsis",
        "*-setup.exe",
        "the NSIS installer was not generated exactly once",
    )
    return binary, installer


def signing_report(binary: Path, installer: Path) -> dict[str, Any]:
    """Measure, do not assume, the Authenticode state of both artifacts."""
    report = {
        "main_binary": authenticode_facts(binary),
        "installer": authenticode_facts(installer),
    }
    announce(f"Authenticode: {json.dumps(report, sort_keys=True)}")
    return report


def install_package(installer: Path, root: Path) -> None:
    announce(f"Installing the built NSIS package into {root}")
    run_checked([os.fspath(installer), "/S"], environment=installer_environment())
    deadline = time.monotonic() + 300
    browser = browser_resource_root(root, "windows")

    def product_executable_present() -> bool:
        # Tauri names the installed binary after `mainBinaryName`, and the
        # production configuration sets neither that nor a matching product
        # name, so the binary is `automation-tool-desktop.exe`. Probe for any
        # non-uninstaller executable instead of assuming a name.
        return any(
            path.is_file() and path.name.lower() != "uninstall.exe"
            for path in root.glob("*.exe")
        )

    while time.monotonic() < deadline:
        if (
            product_executable_present()
            and (root / "uninstall.exe").is_file()
            and browser.is_dir()
            and windows_registry_installations(
                machine_wide=False, product_name=product_name()
            )
        ):
            return
        time.sleep(0.2)
    raise AcceptanceFailed(
        "the NSIS installation did not complete "
        f"(root={root.exists()}, binary={product_executable_present()}, "
        f"uninstaller={(root / 'uninstall.exe').is_file()}, browser={browser.is_dir()}, "
        f"entries={sorted(path.name for path in root.iterdir())[:8] if root.is_dir() else []})"
    )


def audit_package_payload(root: Path) -> PackageAuditReport:
    report = audit_embedded_browser_package(
        bundle_root=root, target_id=TARGET_ID, platform="windows"
    )
    announce(
        f"Package payload verified: {report.browser_files} browser files "
        f"({report.browser_bytes} bytes) inside {report.package_files} package "
        f"files ({report.package_bytes} bytes)"
    )
    return report


def audit_package_content(
    root: Path,
    binary: Path,
    environment: dict[str, str],
    configuration: Path,
    audited_assets: Path,
) -> None:
    announce("Auditing the built binary, configuration and whole installed tree")
    run_checked(
        [
            "node",
            "scripts/audit-production-package.mjs",
            "--binary",
            os.fspath(binary),
            "--cargo-manifest",
            os.fspath(CARGO_MANIFEST),
            "--tauri-config",
            os.fspath(configuration),
            "--dist",
            os.fspath(audited_assets),
        ],
        environment=environment,
    )
    run_checked(
        [
            "node",
            "scripts/audit-release-bundle.mjs",
            "--bundle-root",
            os.fspath(root),
            "--executor-package",
            os.fspath(root / EXECUTOR_RESOURCE),
            "--embedded-browser",
            os.fspath(browser_resource_root(root, "windows")),
            "--platform",
            "windows",
        ],
        environment=installer_environment(),
    )


def audit_clean_machine(root: Path) -> dict[str, Any]:
    """EB-17's two complementary criteria, run over the real installed tree."""
    announce("Checking the installed package against the EB-17 clean-machine rules")
    require_no_browser_installer_scripts(root)
    scanned = scan_package_for_system_browser_references(root)
    announce(
        f"No browser installer script in the package; {scanned} files carry no "
        "system browser location"
    )
    return {"installer_scripts": 0, "scanned_files": scanned}


def packaged_distribution(root: Path) -> tuple[Path, dict[str, Any]]:
    browser = browser_resource_root(root, "windows")
    document = json.loads(
        (browser / "distribution-manifest.v1.json").read_text(encoding="utf-8")
    )
    if document["target"] != TARGET_ID:
        raise AcceptanceFailed("packaged distribution manifest target drifted")
    return browser / Path(*str(document["executable"]).split("/")), document


def processes_matching(marker: str) -> list[dict[str, Any]]:
    output = run_powershell(
        "param([string]$Marker);"
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -like \"*$Marker*\" } | "
        "Select-Object ProcessId,Name | ConvertTo-Json -Compress",
        marker,
    )
    if not output:
        return []
    parsed = json.loads(output)
    return parsed if isinstance(parsed, list) else [parsed]


def terminate_processes_matching(marker: str) -> None:
    for _ in range(2):
        matches = processes_matching(marker)
        if not matches:
            return
        for match in matches:
            subprocess.run(
                ["taskkill", "/PID", str(match["ProcessId"]), "/T", "/F"],
                check=False,
                capture_output=True,
            )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and processes_matching(marker):
            time.sleep(0.2)


def require_no_process_matching(marker: str) -> None:
    residue = processes_matching(marker)
    if residue:
        raise AcceptanceFailed(f"processes still reference {marker}: {residue[:3]}")


def probe_packaged_browser(root: Path) -> str:
    """Launch the packaged Chromium itself, offline and headless."""
    announce("Launching the packaged Chromium from the installed package (offline)")
    executable, document = packaged_distribution(root)
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory(
        prefix="automation-tool-eb16win-browser-",
        dir=REPOSITORY_ROOT / ".local",
    ) as raw:
        profile = Path(raw) / "profile"
        try:
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
                    page.set_content("<title>eb-16-windows</title>")
                    if page.title() != "eb-16-windows":
                        raise AcceptanceFailed(
                            "the packaged browser could not render a page"
                        )
                finally:
                    context.close()
        finally:
            # This runner owns the browser it started, on every path.
            terminate_processes_matching(os.fspath(profile))
        require_no_process_matching(os.fspath(profile))
    if document["runtime"]["chromium"]["browser_version"] != version:
        raise AcceptanceFailed("packaged browser version differs from the manifest")
    announce(f"Packaged Chromium reported version {version} and exited cleanly")
    return version


def uninstall_and_check_residue(root: Path) -> None:
    announce("Uninstalling the package and checking for residue")
    uninstaller = root / "uninstall.exe"
    if uninstaller.is_file():
        run_checked([os.fspath(uninstaller), "/S"], environment=installer_environment())
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        if not root.exists() and not windows_registry_installations(
            machine_wide=False, product_name=product_name()
        ):
            require_no_process_matching(os.fspath(root))
            return
        time.sleep(0.2)
    remaining = sorted(path.name for path in root.iterdir()) if root.exists() else []
    raise AcceptanceFailed(
        f"the uninstaller left residue (root={root.exists()}, entries={remaining[:8]}, "
        f"registry={len(windows_registry_installations(machine_wide=False, product_name=product_name()))})"
    )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIRECTORY)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-uninstall", action="store_true")
    return parser.parse_args()


def main() -> int:
    architecture = require_windows()
    arguments = parse_arguments()
    archive = arguments.archive or archive_path(
        REPOSITORY_ROOT, WINDOWS_X86_64_ARCHIVE
    )
    work_directory: Path = arguments.work_dir
    build_directory = work_directory / "build"
    cargo_target = work_directory / "cargo-target"
    work_directory.mkdir(parents=True, exist_ok=True)

    root = install_root(product_name=product_name())
    if root.exists() or windows_registry_installations(
        machine_wide=False, product_name=product_name()
    ):
        raise AcceptanceFailed(
            f"a previous installation still occupies {root} — uninstall it first"
        )

    if not arguments.skip_build:
        if build_directory.exists():
            shutil.rmtree(build_directory)
        build_directory.mkdir(parents=True)
        staging = build_directory / "browser-staging"
        stage_browser_distribution(archive, staging)
        executor = build_directory / "executor" / "automation-tool-executor"
        public_key, private_key = build_executor_candidate(executor, architecture)
        (build_directory / "executor-verifying-key").write_text(
            public_key, encoding="utf-8"
        )

        # The release assembly step, shared with macOS: install the staged
        # browser into the payload, re-verify it file-by-file against the
        # EB-05 manifest, then seal. Only then may a bundler touch it.
        payload = build_directory / "payload"
        announce("Preparing the pinned video runtime resources (cached per machine)")
        video_runtime = prepare_video_runtime(platform="windows")
        announce("Assembling the release payload, verifying it, then sealing")
        installed_video = install_video_runtime(
            application=payload, staging=video_runtime, platform="windows"
        )
        announce(f"Video runtime staged into the payload: {sorted(installed_video)}")
        install_and_seal(
            application=payload,
            staging=staging,
            target_id=TARGET_ID,
            platform="windows",
            seal=seal_windows_payload,
        )
        # The release gate, in the same position `create_disk_image` holds on
        # macOS: nothing distributable is produced from an unverified browser.
        require_packaged_browser(
            application=payload, target_id=TARGET_ID, platform="windows"
        )
        require_packaged_video_runtime(application=payload, platform="windows")

        configuration = write_release_configuration(
            build_directory, executor, payload
        )
        effective = effective_configuration(configuration, build_directory)
        environment = release_environment(cargo_target, public_key)
        binary, installer = build_release_package(
            configuration=configuration, environment=environment, target=cargo_target
        )
        # Frozen here, next to the artifact it belongs to, so that a later
        # `--skip-build` re-audit of this same package reuses this exact copy.
        audited_assets = snapshot_production_assets(
            build_directory / AUDITED_DISTRIBUTION_NAME
        )
    else:
        private_key = None
        public_key = (build_directory / "executor-verifying-key").read_text(
            encoding="utf-8"
        )
        environment = release_environment(cargo_target, public_key)
        effective = effective_configuration(
            build_directory / "tauri.eb-16-windows.generated.json", build_directory
        )
        binary = main_binary(cargo_target / "release")
        installer = one_file(
            cargo_target / "release/bundle/nsis",
            "*-setup.exe",
            "the NSIS installer was not generated exactly once",
        )
        audited_assets = require_frozen_distribution(
            build_directory / AUDITED_DISTRIBUTION_NAME
        )

    announce(f"Built main binary: {binary} ({binary.stat().st_size} bytes)")
    announce(f"Built installer:   {installer} ({installer.stat().st_size} bytes)")
    signing = signing_report(binary, installer)

    installed_report: PackageAuditReport | None = None
    evidence: dict[str, Any] = {
        "target": TARGET_ID,
        "architecture": architecture,
        "product_name": product_name(),
        "main_binary": os.fspath(binary),
        "main_binary_bytes": binary.stat().st_size,
        "installer": os.fspath(installer),
        "installer_bytes": installer.stat().st_size,
        "signing": signing,
    }
    installed = False
    try:
        install_package(installer, root)
        installed = True
        installed_binary = main_binary(root)
        signing["installed_binary"] = authenticode_facts(installed_binary)
        signing["uninstaller"] = authenticode_facts(root / "uninstall.exe")
        installed_report = audit_package_payload(root)
        audit_package_content(
            root, installed_binary, environment, effective, audited_assets
        )
        evidence["clean_machine"] = audit_clean_machine(root)
        if private_key is not None:
            verify_manifest_signature(root / EXECUTOR_RESOURCE, private_key)
        evidence["executor_files"] = len(package_files(root / EXECUTOR_RESOURCE))
        evidence["browser_version"] = probe_packaged_browser(root)
        evidence["install_root"] = os.fspath(root)
        evidence["browser_files"] = installed_report.browser_files
        evidence["browser_bytes"] = installed_report.browser_bytes
        evidence["package_files"] = installed_report.package_files
        evidence["package_bytes"] = installed_report.package_bytes
    finally:
        # This run owns the installation on every path. When an audit already
        # failed, a failing cleanup must not replace the real reason for it.
        if installed and not arguments.skip_uninstall:
            unwinding = sys.exc_info()[0] is not None
            try:
                uninstall_and_check_residue(root)
                evidence["uninstalled"] = True
            except Exception as cleanup_failure:
                if not unwinding:
                    raise
                announce(f"cleanup after failure did not complete: {cleanup_failure}")

    (work_directory / "eb-16-windows-acceptance.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    announce(
        "EB-16 Windows acceptance passed: one unsigned "
        f"{TARGET_ID} NSIS package ({installer.stat().st_size} bytes) with "
        f"{installed_report.browser_files} browser files "
        f"({installed_report.browser_bytes} bytes), installed tree "
        f"{installed_report.package_bytes} bytes"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AcceptanceFailed as error:
        print(f"EB-16 Windows acceptance failed: {error}")
        raise SystemExit(1) from error
