#!/usr/bin/env python3
"""Build and audit one disposable production-mode Windows Tauri candidate."""

from __future__ import annotations

import base64
import hashlib
import importlib
import json
import os
import platform
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from automation_tool.executor.package_manifest import write_signed_executor_manifest
from automation_tool.executor.windows_candidate import (
    audit_windows_executor_candidate,
    build_windows_executor_candidate,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
FRONTEND_ROOT = REPOSITORY_ROOT / "frontend"
TAURI_ROOT = FRONTEND_ROOT / "src-tauri"
BASE_TAURI_CONFIG = TAURI_ROOT / "tauri.conf.json"
CANDIDATE_TAURI_CONFIG = TAURI_ROOT / "tauri.windows-candidate.conf.json"
CARGO_MANIFEST = TAURI_ROOT / "Cargo.toml"
PRODUCTION_ASSETS = FRONTEND_ROOT / "dist"
EXECUTOR_RESOURCE = Path("local-executor/package")
PRODUCT_NAME = "Automation Tool P904 Windows Acceptance"
APP_IDENTIFIER = "com.aventador.automationtool.p904windowsacceptance"
MAIN_BINARY_NAME = "automation-tool-p904-windows-acceptance"
UPDATE_ENDPOINT = (
    "https://updates.candidate.invalid/desktop-updates/v1/stable/"
    "{{target}}/{{arch}}/{{current_version}}"
)
UPDATE_PUBLIC_KEY = base64.b64encode(
    b"untrusted comment: minisign public key E7620F1842B4E81F\n"
    b"RWQf6LRCGA9i53mlYecO4IzT51TGPpvWucNSCh1CBM0QTaLn73Y7GFO3"
).decode()
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


@dataclass(frozen=True, slots=True)
class WindowsRegistryInstallation:
    display_version: str
    install_location: Path
    uninstaller: Path


def require_windows() -> str:
    if sys.platform != "win32" or platform.system() != "Windows":
        raise RuntimeError("P9-04 acceptance requires Windows")
    if platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("P9-04 acceptance requires Windows x86_64")
    return "x86_64"


def pnpm_executable() -> str:
    executable = shutil.which("pnpm.cmd") or shutil.which("pnpm")
    if executable is None:
        raise RuntimeError("P9-04 pnpm executable is unavailable")
    return executable


def powershell_executable() -> str:
    executable = shutil.which("powershell.exe") or shutil.which("powershell")
    if executable is None:
        raise RuntimeError("P9-04 Windows PowerShell is unavailable")
    return executable


def run_checked(
    arguments: list[str],
    *,
    cwd: Path = FRONTEND_ROOT,
    environment: dict[str, str] | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=cwd,
        env=environment,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        timeout=1800,
    )


def run_powershell(script: str, *arguments: str) -> str:
    completed = run_checked(
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
    )
    return completed.stdout.strip()


def require_non_elevated_process() -> None:
    elevated = run_powershell(
        "$identity=[Security.Principal.WindowsIdentity]::GetCurrent();"
        "$principal=[Security.Principal.WindowsPrincipal]::new($identity);"
        "$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)"
    )
    if elevated.casefold() != "false":
        raise RuntimeError("P9-04 acceptance must run as a non-elevated user")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_files(root: Path) -> dict[str, tuple[int, str]]:
    result: dict[str, tuple[int, str]] = {}
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or bool(
            getattr(metadata, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise RuntimeError("P9-04 bundled Executor contains a link or reparse point")
        if not stat.S_ISDIR(metadata.st_mode) and not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError("P9-04 bundled Executor contains a special file")
        if stat.S_ISREG(metadata.st_mode):
            result[path.relative_to(root).as_posix()] = (metadata.st_size, sha256(path))
    return result


def executor_signing_material() -> tuple[bytes, str, Ed25519PrivateKey]:
    seed = secrets.token_bytes(32)
    private_key = Ed25519PrivateKey.from_private_bytes(seed)
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    encoded = base64.urlsafe_b64encode(public_bytes).rstrip(b"=").decode()
    return seed, encoded, private_key


def verify_manifest_signature(package: Path, private_key: Ed25519PrivateKey) -> None:
    manifest = (package / "executor-manifest.v1.json").read_bytes()
    envelope = (package / "executor-manifest.v1.sig").read_text(encoding="ascii")
    prefix, separator, encoded = envelope.strip().partition(".")
    if prefix != "atems1" or separator != ".":
        raise RuntimeError("P9-04 Executor signature envelope is invalid")
    signature = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    private_key.public_key().verify(signature, manifest)


def audit_release_bundle(bundle: Path, package: Path) -> None:
    run_checked(
        [
            "node",
            "scripts/audit-release-bundle.mjs",
            "--bundle-root",
            os.fspath(bundle),
            "--executor-package",
            os.fspath(package),
            "--platform",
            "windows",
        ],
        environment=installer_environment(),
    )


def require_candidate_configuration() -> dict[str, Any]:
    configuration: dict[str, Any] = json.loads(CANDIDATE_TAURI_CONFIG.read_text(encoding="utf-8"))
    bundle = configuration.get("bundle", {})
    windows = bundle.get("windows", {})
    nsis = windows.get("nsis", {})
    if (
        set(configuration) != {"$schema", "bundle"}
        or set(bundle) != {"active", "targets", "windows"}
        or set(windows) != {"nsis"}
        or set(nsis) != {"installMode"}
        or bundle.get("active") is not True
        or bundle.get("targets") != ["nsis"]
        or nsis.get("installMode") != "currentUser"
        or windows.get("certificateThumbprint") is not None
        or windows.get("signCommand") is not None
    ):
        raise RuntimeError("P9-04 Windows candidate configuration is not minimal")
    return configuration


def write_acceptance_configuration(temporary: Path, executor: Path, base: dict[str, Any]) -> Path:
    configuration: dict[str, Any] = json.loads(json.dumps(base))
    configuration.update(
        {
            "productName": PRODUCT_NAME,
            "identifier": APP_IDENTIFIER,
            "mainBinaryName": MAIN_BINARY_NAME,
        }
    )
    configuration["bundle"]["resources"] = {
        f"{os.fspath(executor)}{os.sep}": f"{EXECUTOR_RESOURCE.as_posix()}/"
    }
    destination = temporary / "tauri.p9-04.generated.json"
    destination.write_text(
        json.dumps(configuration, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return destination


def release_environment(target: Path, executor_public_key: str) -> dict[str, str]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("AUTOMATION_TOOL_")
        and not name.startswith("APPLE_")
        and not name.startswith("TAURI_SIGNING_")
        and name != "CARGO_TARGET_DIR"
    }
    environment.update(
        {
            "AUTOMATION_TOOL_EXECUTOR_VERIFYING_KEY": executor_public_key,
            "AUTOMATION_TOOL_ACTION_AUTHORIZATION_PUBLIC_KEY": executor_public_key,
            "AUTOMATION_TOOL_LOCAL_ACTION_MINIMUM_INTERVAL_SECONDS": "60",
            "AUTOMATION_TOOL_LOCAL_ACTION_TASK_LIMIT": "1",
            "AUTOMATION_TOOL_UPDATE_ENDPOINT": UPDATE_ENDPOINT,
            "AUTOMATION_TOOL_UPDATE_PUBLIC_KEY": UPDATE_PUBLIC_KEY,
            "CARGO_TARGET_DIR": os.fspath(target),
            "CI": "true",
        }
    )
    return environment


def installer_environment() -> dict[str, str]:
    return {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("AUTOMATION_TOOL_")
        and not name.startswith("APPLE_")
        and not name.startswith("TAURI_SIGNING_")
    }


def authenticode_status(path: Path) -> str:
    return run_powershell(
        "& { param([string]$LiteralPath) "
        "(Get-AuthenticodeSignature -LiteralPath $LiteralPath).Status.ToString() }",
        os.fspath(path),
    )


def require_unsigned(path: Path) -> None:
    if authenticode_status(path) != "NotSigned":
        raise RuntimeError("P9-04 ordinary candidate signing status is inconsistent")


def require_file_version(path: Path) -> None:
    version = run_powershell(
        "& { param([string]$LiteralPath) "
        "(Get-Item -LiteralPath $LiteralPath).VersionInfo.ProductVersion }",
        os.fspath(path),
    ).split("+")[0]
    if version not in {"0.1.0", "0.1.0.0"}:
        raise RuntimeError("P9-04 Windows binary version is inconsistent")


def normalized_windows_path(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def install_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("P9-04 Windows local AppData is unavailable")
    root = Path(local_app_data) / PRODUCT_NAME
    if normalized_windows_path(root.parent) != normalized_windows_path(Path(local_app_data)):
        raise RuntimeError("P9-04 Windows install root is outside LocalAppData")
    return root


def windows_registry_installations(*, machine_wide: bool) -> list[WindowsRegistryInstallation]:
    winreg: Any = importlib.import_module("winreg")

    hive = winreg.HKEY_LOCAL_MACHINE if machine_wide else winreg.HKEY_CURRENT_USER
    parent_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    expected_root = install_root()
    expected_uninstaller = expected_root / "uninstall.exe"
    records: list[WindowsRegistryInstallation] = []
    seen: set[tuple[str, str, str]] = set()
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            parent = winreg.OpenKey(
                hive,
                parent_path,
                0,
                winreg.KEY_READ | view,
            )
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
                        display_version = str(winreg.QueryValueEx(child, "DisplayVersion")[0])
                        uninstall_string = str(
                            winreg.QueryValueEx(child, "UninstallString")[0]
                        ).strip()
                        if uninstall_string.casefold() not in {
                            os.fspath(expected_uninstaller).casefold(),
                            f'"{expected_uninstaller}"'.casefold(),
                        }:
                            raise RuntimeError(
                                "P9-04 Windows uninstall registry path is unexpected"
                            )
                        try:
                            location = Path(str(winreg.QueryValueEx(child, "InstallLocation")[0]))
                        except OSError:
                            location = expected_root
                        identity = (
                            display_version.casefold(),
                            normalized_windows_path(location),
                            normalized_windows_path(expected_uninstaller),
                        )
                        if identity not in seen:
                            seen.add(identity)
                            records.append(
                                WindowsRegistryInstallation(
                                    display_version=display_version,
                                    install_location=location,
                                    uninstaller=expected_uninstaller,
                                )
                            )
                except RuntimeError:
                    raise
                except OSError:
                    continue
    return records


def one_file(parent: Path, pattern: str, failure: str) -> Path:
    candidates = sorted(path for path in parent.glob(pattern) if path.is_file())
    if len(candidates) != 1:
        raise RuntimeError(failure)
    return candidates[0]


def verify_registry_installation() -> None:
    records = windows_registry_installations(machine_wide=False)
    if len(records) != 1:
        raise RuntimeError("P9-04 Windows HKCU installation record is not unique")
    if windows_registry_installations(machine_wide=True):
        raise RuntimeError("P9-04 Windows candidate wrote an HKLM installation record")
    record = records[0]
    version = record.display_version.split("+")[0]
    if version not in {"0.1.0", "0.1.0.0"}:
        raise RuntimeError("P9-04 Windows registry version is inconsistent")
    if normalized_windows_path(record.install_location) != normalized_windows_path(
        install_root()
    ) or normalized_windows_path(record.uninstaller) != normalized_windows_path(
        install_root() / "uninstall.exe"
    ):
        raise RuntimeError("P9-04 Windows registry installation boundary is inconsistent")


def wait_for_installation() -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if (
            (install_root() / f"{MAIN_BINARY_NAME}.exe").is_file()
            and (install_root() / "uninstall.exe").is_file()
            and windows_registry_installations(machine_wide=False)
        ):
            return
        time.sleep(0.1)
    raise RuntimeError("P9-04 Windows candidate installation did not complete")


def uninstall_owned_application() -> None:
    root = install_root()
    uninstaller = root / "uninstall.exe"
    if uninstaller.is_file():
        run_checked(
            [os.fspath(uninstaller), "/S"],
            environment=installer_environment(),
        )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if (
            not root.exists()
            and not windows_registry_installations(machine_wide=False)
            and not windows_registry_installations(machine_wide=True)
        ):
            return
        time.sleep(0.1)
    raise RuntimeError("P9-04 Windows candidate uninstaller left owned state")


def verify_installed_candidate(
    *,
    architecture: str,
    expected_binary_sha256: str,
    expected_inventory: dict[str, tuple[int, str]],
    private_key: Ed25519PrivateKey,
    temporary: Path,
) -> tuple[int, int]:
    root = install_root()
    binary = root / f"{MAIN_BINARY_NAME}.exe"
    uninstaller = root / "uninstall.exe"
    package = root / EXECUTOR_RESOURCE
    require_unsigned(binary)
    require_unsigned(uninstaller)
    require_file_version(binary)
    if sha256(binary) != expected_binary_sha256:
        raise RuntimeError("P9-04 installed Windows binary is not the built candidate")
    verify_registry_installation()
    audit = audit_windows_executor_candidate(
        bundle_directory=package,
        expected_architecture=architecture,
        forbidden_development_roots=(REPOSITORY_ROOT, temporary),
    )
    verify_manifest_signature(package, private_key)
    if package_files(package) != expected_inventory:
        raise RuntimeError("P9-04 installed Executor inventory is inconsistent")
    audit_release_bundle(root, package)
    return audit.file_count, audit.package_size


def main() -> int:
    architecture = require_windows()
    require_non_elevated_process()
    base_configuration = require_candidate_configuration()
    if (
        install_root().exists()
        or windows_registry_installations(machine_wide=False)
        or windows_registry_installations(machine_wide=True)
    ):
        raise RuntimeError("P9-04 isolated acceptance installation is already occupied")

    installation_claimed = False
    with tempfile.TemporaryDirectory(prefix="automation-tool-p904-acceptance-") as raw:
        temporary = Path(raw).resolve(strict=True)
        executor = temporary / "executor" / "automation-tool-executor"
        print("[P9-04] Building the isolated P9-02 Executor candidate")
        build_windows_executor_candidate(
            backend_root=BACKEND_ROOT,
            output_directory=executor,
        )
        seed, public_key, private_key = executor_signing_material()
        write_signed_executor_manifest(
            bundle_directory=executor,
            executor_version="0.1.0",
            build_id="p9-04-windows-candidate",
            target_platform="windows",
            target_architecture=architecture,
            signing_private_key=seed,
        )
        executor_audit = audit_windows_executor_candidate(
            bundle_directory=executor,
            expected_architecture=architecture,
            forbidden_development_roots=(REPOSITORY_ROOT, temporary),
        )
        verify_manifest_signature(executor, private_key)
        expected_inventory = package_files(executor)

        configuration = write_acceptance_configuration(temporary, executor, base_configuration)
        target = temporary / "tauri-target"
        environment = release_environment(target, public_key)
        print("[P9-04] Building one production-mode ordinary NSIS candidate")
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
        binary = target / "release" / f"{MAIN_BINARY_NAME}.exe"
        installer = one_file(
            target / "release/bundle/nsis",
            "*-setup.exe",
            "P9-04 NSIS installer was not generated exactly once",
        )
        require_unsigned(binary)
        require_unsigned(installer)
        require_file_version(binary)
        expected_binary_sha256 = sha256(binary)
        run_checked(
            [
                "node",
                "scripts/audit-production-package.mjs",
                "--binary",
                os.fspath(binary),
                "--cargo-manifest",
                os.fspath(CARGO_MANIFEST),
                "--tauri-config",
                os.fspath(BASE_TAURI_CONFIG),
                "--dist",
                os.fspath(PRODUCTION_ASSETS),
            ],
            environment=environment,
        )

        try:
            installation_claimed = True
            print("[P9-04] Installing and auditing the isolated current-user candidate")
            run_checked(
                [os.fspath(installer), "/S"],
                environment=installer_environment(),
            )
            wait_for_installation()
            installed_files, installed_bytes = verify_installed_candidate(
                architecture=architecture,
                expected_binary_sha256=expected_binary_sha256,
                expected_inventory=expected_inventory,
                private_key=private_key,
                temporary=temporary,
            )
        finally:
            if installation_claimed:
                uninstall_owned_application()

    print(
        "[P9-04] Windows Tauri candidate passed production, ordinary signing, "
        "current-user install/uninstall, registry, resource and Manifest audits: "
        f"{installed_files} Executor files, {installed_bytes} bytes; "
        f"raw signed payload was {executor_audit.package_size} bytes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
