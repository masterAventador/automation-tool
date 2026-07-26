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
from production_assets import snapshot_production_assets
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
_TAURI_UNKNOWN_BUNDLE_MARKER = b"BUNDLE_TYPE_VAR_UNK"
_TAURI_NSIS_BUNDLE_MARKER = b"BUNDLE_TYPE_VAR_NSS"


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
    payload = json.dumps(
        {"script": script, "arguments": list(arguments)},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    bootstrap = (
        "$P904Payload=ConvertFrom-Json -InputObject ([Console]::In.ReadToEnd());"
        "$P904Arguments=@($P904Payload.arguments);"
        "$P904Script=[ScriptBlock]::Create([string]$P904Payload.script);"
        "& $P904Script @P904Arguments"
    )
    completed = subprocess.run(
        [
            powershell_executable(),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            bootstrap,
        ],
        cwd=FRONTEND_ROOT,
        env=installer_environment(),
        check=True,
        text=True,
        input=payload,
        capture_output=True,
        timeout=1800,
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


def expected_nsis_binary_sha256(path: Path) -> str:
    built_binary = path.read_bytes()
    if (
        len(_TAURI_UNKNOWN_BUNDLE_MARKER) != len(_TAURI_NSIS_BUNDLE_MARKER)
        or built_binary.count(_TAURI_UNKNOWN_BUNDLE_MARKER) != 1
        or _TAURI_NSIS_BUNDLE_MARKER in built_binary
    ):
        raise RuntimeError("P9-04 built binary has an invalid Tauri bundle marker")
    bundled_binary = built_binary.replace(
        _TAURI_UNKNOWN_BUNDLE_MARKER,
        _TAURI_NSIS_BUNDLE_MARKER,
        1,
    )
    return hashlib.sha256(bundled_binary).hexdigest()


def package_files(root: Path) -> dict[str, tuple[int, str]]:
    result: dict[str, tuple[int, str]] = {}
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or bool(
            getattr(metadata, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise RuntimeError(
                "P9-04 bundled Executor contains a link or reparse point"
            )
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
    configuration: dict[str, Any] = json.loads(
        CANDIDATE_TAURI_CONFIG.read_text(encoding="utf-8")
    )
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


def write_acceptance_configuration(
    temporary: Path, executor: Path, base: dict[str, Any]
) -> Path:
    executor_resource_source = os.path.relpath(executor, TAURI_ROOT).replace(
        os.sep, "/"
    )
    if os.path.isabs(executor_resource_source) or ":" in executor_resource_source:
        raise RuntimeError(
            "P9-04 Executor resource source must be relative to the Tauri root"
        )
    configuration: dict[str, Any] = json.loads(json.dumps(base))
    configuration.update(
        {
            "productName": PRODUCT_NAME,
            "identifier": APP_IDENTIFIER,
            "mainBinaryName": MAIN_BINARY_NAME,
        }
    )
    configuration["bundle"]["resources"] = {
        f"{executor_resource_source}/": f"{EXECUTOR_RESOURCE.as_posix()}/"
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


def authenticode_facts(path: Path) -> dict[str, Any]:
    output = run_powershell(
        "param([string]$LiteralPath);"
        "$signature=Get-AuthenticodeSignature -LiteralPath $LiteralPath;"
        "[PSCustomObject]@{"
        "status=$signature.Status.ToString();"
        "hasSigner=($null -ne $signature.SignerCertificate);"
        "hasTimestamp=($null -ne $signature.TimeStamperCertificate)"
        "} | ConvertTo-Json -Compress",
        os.fspath(path),
    )
    facts: Any = json.loads(output)
    if (
        not isinstance(facts, dict)
        or set(facts) != {"status", "hasSigner", "hasTimestamp"}
        or not isinstance(facts["status"], str)
        or not isinstance(facts["hasSigner"], bool)
        or not isinstance(facts["hasTimestamp"], bool)
    ):
        raise RuntimeError("P9-04 Authenticode facts are invalid")
    return facts


def require_unsigned(path: Path) -> None:
    facts = authenticode_facts(path)
    if facts["status"] != "NotSigned" or facts["hasSigner"] or facts["hasTimestamp"]:
        raise RuntimeError(
            "P9-04 ordinary candidate signing status is inconsistent "
            f"({json.dumps(facts, sort_keys=True)})"
        )


def require_file_version(path: Path) -> None:
    version = run_powershell(
        "param([string]$LiteralPath);"
        "(Get-Item -LiteralPath $LiteralPath).VersionInfo.ProductVersion",
        os.fspath(path),
    ).split("+")[0]
    if version not in {"0.1.0", "0.1.0.0"}:
        raise RuntimeError("P9-04 Windows binary version is inconsistent")


def normalized_windows_path(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def parse_windows_registry_path(value: Any) -> Path:
    literal = str(value).strip()
    if literal.startswith('"') or literal.endswith('"'):
        if not (literal.startswith('"') and literal.endswith('"')):
            raise RuntimeError("P9-04 Windows registry path has unbalanced quoting")
        literal = literal[1:-1]
    if not literal or '"' in literal:
        raise RuntimeError("P9-04 Windows registry path is invalid")
    path = Path(literal)
    if not path.is_absolute():
        raise RuntimeError("P9-04 Windows registry path is not absolute")
    return path


def install_root(*, product_name: str = PRODUCT_NAME) -> Path:
    """Where a `currentUser` NSIS package for `product_name` installs itself.

    The product name is a parameter so the EB-16 Windows release acceptance can
    reuse this and `windows_registry_installations` for the real product
    instead of keeping a second copy of the same registry walk. The default
    keeps every P9-04 call site unchanged.
    """
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("P9-04 Windows local AppData is unavailable")
    root = Path(local_app_data) / product_name
    if normalized_windows_path(root.parent) != normalized_windows_path(
        Path(local_app_data)
    ):
        raise RuntimeError("P9-04 Windows install root is outside LocalAppData")
    return root


def windows_registry_installations(
    *, machine_wide: bool, product_name: str = PRODUCT_NAME
) -> list[WindowsRegistryInstallation]:
    winreg: Any = importlib.import_module("winreg")

    hive = winreg.HKEY_LOCAL_MACHINE if machine_wide else winreg.HKEY_CURRENT_USER
    parent_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    expected_root = install_root(product_name=product_name)
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
                    child = winreg.OpenKey(
                        parent,
                        winreg.EnumKey(parent, index),
                        0,
                        winreg.KEY_READ | view,
                    )
                    with child:
                        try:
                            display_name = str(
                                winreg.QueryValueEx(child, "DisplayName")[0]
                            )
                        except OSError:
                            continue
                        if display_name != product_name:
                            continue
                        try:
                            display_version = str(
                                winreg.QueryValueEx(child, "DisplayVersion")[0]
                            )
                            uninstaller = parse_windows_registry_path(
                                winreg.QueryValueEx(child, "UninstallString")[0]
                            )
                            location = parse_windows_registry_path(
                                winreg.QueryValueEx(child, "InstallLocation")[0]
                            )
                        except OSError as error:
                            raise RuntimeError(
                                "P9-04 Windows uninstall registry record is incomplete"
                            ) from error
                        if normalized_windows_path(
                            uninstaller
                        ) != normalized_windows_path(expected_uninstaller):
                            raise RuntimeError(
                                "P9-04 Windows uninstall registry path is unexpected"
                            )
                        identity = (
                            display_version.casefold(),
                            normalized_windows_path(location),
                            normalized_windows_path(uninstaller),
                        )
                        if identity not in seen:
                            seen.add(identity)
                            records.append(
                                WindowsRegistryInstallation(
                                    display_version=display_version,
                                    install_location=location,
                                    uninstaller=uninstaller,
                                )
                            )
                except OSError:
                    continue
    return records


def expected_windows_registry_values(*, machine_wide: bool) -> dict[str, Any]:
    winreg: Any = importlib.import_module("winreg")
    hive = winreg.HKEY_LOCAL_MACHINE if machine_wide else winreg.HKEY_CURRENT_USER
    key_path = (
        r"Software\Microsoft\Windows\CurrentVersion\Uninstall" f"\\{PRODUCT_NAME}"
    )
    snapshots: dict[str, Any] = {}
    for view_name, view in (
        ("64", winreg.KEY_WOW64_64KEY),
        ("32", winreg.KEY_WOW64_32KEY),
    ):
        try:
            key = winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ | view)
        except OSError as error:
            snapshots[view_name] = {"missing": getattr(error, "winerror", None)}
            continue
        with key:
            values: dict[str, Any] = {}
            for index in range(winreg.QueryInfoKey(key)[1]):
                name, value, _kind = winreg.EnumValue(key, index)
                values[name] = value
            snapshots[view_name] = values
    return snapshots


def related_installer_processes() -> Any:
    output = run_powershell(
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -like '*P904*' -or "
        "$_.ExecutablePath -like '*P904*' } | "
        "Select-Object ProcessId,Name,ExecutablePath | ConvertTo-Json -Compress"
    )
    return json.loads(output) if output else []


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
        raise RuntimeError(
            "P9-04 Windows registry installation boundary is inconsistent"
        )


def wait_for_installation() -> None:
    deadline = time.monotonic() + 60
    current_user_records: list[WindowsRegistryInstallation] = []
    while time.monotonic() < deadline:
        current_user_records = windows_registry_installations(machine_wide=False)
        if (
            (install_root() / f"{MAIN_BINARY_NAME}.exe").is_file()
            and (install_root() / "uninstall.exe").is_file()
            and current_user_records
        ):
            return
        time.sleep(0.1)
    root = install_root()
    raise RuntimeError(
        "P9-04 Windows candidate installation did not complete "
        f"(root={root.exists()}, "
        f"binary={(root / f'{MAIN_BINARY_NAME}.exe').is_file()}, "
        f"uninstaller={(root / 'uninstall.exe').is_file()}, "
        f"hkcu_records={len(current_user_records)}, "
        "hklm_records="
        f"{len(windows_registry_installations(machine_wide=True))}, "
        "hkcu_expected_key="
        f"{json.dumps(expected_windows_registry_values(machine_wide=False), sort_keys=True)}, "
        "hklm_expected_key="
        f"{json.dumps(expected_windows_registry_values(machine_wide=True), sort_keys=True)}, "
        "related_processes="
        f"{json.dumps(related_installer_processes(), sort_keys=True)})"
    )


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
    built_binary: Path,
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
    expected_binary_sha256 = expected_nsis_binary_sha256(built_binary)
    installed_binary_sha256 = sha256(binary)
    if installed_binary_sha256 != expected_binary_sha256:
        raise RuntimeError(
            "P9-04 installed Windows binary is not the built candidate "
            f"(expected_nsis_sha256={expected_binary_sha256}, "
            f"installed_sha256={installed_binary_sha256})"
        )
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
    local_acceptance_root = REPOSITORY_ROOT / ".local"
    local_acceptance_root.mkdir(exist_ok=True)
    local_root_metadata = local_acceptance_root.lstat()
    if not stat.S_ISDIR(local_root_metadata.st_mode) or bool(
        getattr(local_root_metadata, "st_file_attributes", 0)
        & _FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise RuntimeError("P9-04 local acceptance root is unavailable")
    with tempfile.TemporaryDirectory(
        prefix="automation-tool-p904-acceptance-", dir=local_acceptance_root
    ) as raw:
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

        configuration = write_acceptance_configuration(
            temporary, executor, base_configuration
        )
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
        audited_assets = snapshot_production_assets(temporary / "audited-distribution")
        binary = target / "release" / f"{MAIN_BINARY_NAME}.exe"
        installer = one_file(
            target / "release/bundle/nsis",
            "*-setup.exe",
            "P9-04 NSIS installer was not generated exactly once",
        )
        require_unsigned(binary)
        require_unsigned(installer)
        require_file_version(binary)
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
                os.fspath(audited_assets),
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
                built_binary=binary,
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
