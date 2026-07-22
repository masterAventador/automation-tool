#!/usr/bin/env python3
"""Run H8-22 against real unsigned Windows NSIS and updater packages."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Request
from fastapi.responses import Response

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
FRONTEND_ROOT = REPOSITORY_ROOT / "frontend"
TAURI_ROOT = FRONTEND_ROOT / "src-tauri"
TAURI_CONFIG = TAURI_ROOT / "tauri.update-windows-package-e2e.conf.json"
ACCEPTANCE_ASSETS = FRONTEND_ROOT / "dist-h822-windows"
APP_IDENTIFIER = "com.aventador.automationtool.h822windowsacceptance"
PRODUCT_NAME = "Automation Tool H822 Windows Acceptance"
MAIN_BINARY_NAME = "automation-tool-h822-windows-acceptance"
KEY_PASSWORD = "h822-ephemeral-package-acceptance"

sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))
from run_h8_20_acceptance import (  # noqa: E402
    current_update_platform,
    pnpm_executable,
    require_port_closed,
    unused_loopback_port,
    wait_for_port,
    write_tls_identity,
)


@dataclass(frozen=True)
class UpdateArtifact:
    version: str
    payload: bytes
    signature: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()


@dataclass(frozen=True)
class WindowsRegistryInstallation:
    display_version: str
    install_location: Path
    uninstaller: Path


def require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("H8-22 unsigned Windows package acceptance requires Windows")
    target, arch = current_update_platform()
    if target != "windows" or arch != "x86_64":
        raise RuntimeError("H8-22 Windows package acceptance requires Windows x86_64")


def app_data_directory() -> Path:
    roaming = os.environ.get("APPDATA")
    if not roaming:
        raise RuntimeError("H8-22 Windows roaming AppData is unavailable")
    return Path(roaming) / APP_IDENTIFIER


def install_root() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise RuntimeError("H8-22 Windows local AppData is unavailable")
    root = Path(local) / PRODUCT_NAME
    if root.name != PRODUCT_NAME:
        raise RuntimeError("H8-22 Windows install boundary is invalid")
    return root


def installed_binary() -> Path:
    return install_root() / f"{MAIN_BINARY_NAME}.exe"


def normalized_windows_path(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def windows_registry_installation() -> WindowsRegistryInstallation | None:
    import winreg

    parent_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    expected_root = install_root()
    expected_uninstaller = expected_root / "uninstall.exe"
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            parent = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
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
                        display_version = str(
                            winreg.QueryValueEx(child, "DisplayVersion")[0]
                        )
                        uninstall_string = str(
                            winreg.QueryValueEx(child, "UninstallString")[0]
                        ).strip()
                        quoted_uninstaller = f'"{expected_uninstaller}"'
                        if uninstall_string.casefold() not in {
                            str(expected_uninstaller).casefold(),
                            quoted_uninstaller.casefold(),
                        }:
                            raise RuntimeError(
                                "H8-22 Windows uninstall registry path is unexpected"
                            )
                        try:
                            install_location = Path(
                                str(winreg.QueryValueEx(child, "InstallLocation")[0])
                            )
                        except OSError:
                            install_location = expected_uninstaller.parent
                        if normalized_windows_path(
                            install_location
                        ) != normalized_windows_path(expected_root):
                            raise RuntimeError(
                                "H8-22 Windows install registry path is unexpected"
                            )
                        return WindowsRegistryInstallation(
                            display_version=display_version,
                            install_location=install_location,
                            uninstaller=expected_uninstaller,
                        )
                except RuntimeError:
                    raise
                except OSError:
                    continue
    return None


def require_configuration() -> dict[str, Any]:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    bundle = configuration.get("bundle", {})
    windows_bundle = bundle.get("windows", {})
    nsis = windows_bundle.get("nsis", {})
    updater_windows = (
        configuration.get("plugins", {}).get("updater", {}).get("windows", {})
    )
    if (
        configuration.get("identifier") != APP_IDENTIFIER
        or configuration.get("productName") != PRODUCT_NAME
        or configuration.get("mainBinaryName") != MAIN_BINARY_NAME
        or len(windows) != 1
        or windows[0].get("visible") is not False
        or bundle.get("targets") != ["nsis"]
        or bundle.get("createUpdaterArtifacts") is not True
        or nsis.get("installMode") != "currentUser"
        or updater_windows.get("installMode") != "passive"
        or windows_bundle.get("certificateThumbprint") is not None
        or windows_bundle.get("signCommand") is not None
    ):
        raise RuntimeError(
            "H8-22 Windows package configuration is not isolated unsigned NSIS"
        )
    return configuration


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
    )


def powershell_executable() -> str:
    executable = shutil.which("powershell.exe") or shutil.which("powershell")
    if executable is None:
        raise RuntimeError("H8-22 cannot find Windows PowerShell")
    return executable


def run_powershell(script: str, *arguments: str) -> str:
    result = run_checked(
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
    return result.stdout.strip()


def authenticode_status(path: Path) -> str:
    return run_powershell(
        "& { param([string]$LiteralPath) "
        "(Get-AuthenticodeSignature -LiteralPath $LiteralPath).Status.ToString() }",
        str(path),
    )


def verify_unsigned_installer(installer: Path) -> None:
    if authenticode_status(installer) != "NotSigned":
        raise RuntimeError(
            "H8-22 Windows installer is not the expected unsigned package"
        )


def verify_unsigned_binary(binary: Path) -> None:
    if authenticode_status(binary) != "NotSigned":
        raise RuntimeError("H8-22 installed Windows binary is not unsigned")


def file_version(binary: Path) -> str:
    return run_powershell(
        "& { param([string]$LiteralPath) "
        "(Get-Item -LiteralPath $LiteralPath).VersionInfo.ProductVersion }",
        str(binary),
    )


def require_file_version(binary: Path, expected: str) -> None:
    actual = file_version(binary).split("+")[0]
    if actual not in {expected, f"{expected}.0"}:
        raise RuntimeError(
            f"H8-22 installed Windows version is {actual!r}, expected {expected!r}"
        )


def generate_ephemeral_update_key(directory: Path) -> tuple[Path, str]:
    private_key = directory / "h822-update.key"
    run_checked(
        [
            pnpm_executable(),
            "exec",
            "tauri",
            "signer",
            "generate",
            "--ci",
            "--password",
            KEY_PASSWORD,
            "--write-keys",
            str(private_key),
        ]
    )
    public_candidates = [Path(f"{private_key}.pub"), private_key.with_suffix(".pub")]
    public_key = next((path for path in public_candidates if path.is_file()), None)
    if public_key is None or not private_key.is_file():
        raise RuntimeError("H8-22 ephemeral updater key generation failed")
    public_text = public_key.read_text(encoding="utf-8").strip()
    if "minisign public key" not in public_text:
        try:
            public_text = base64.b64decode(public_text, validate=True).decode()
        except (binascii.Error, UnicodeDecodeError) as error:
            raise RuntimeError(
                "H8-22 ephemeral updater public key is invalid"
            ) from error
    if "minisign public key" not in public_text:
        raise RuntimeError("H8-22 ephemeral updater public key is invalid")
    return private_key, public_text


def build_environment(
    update_port: int,
    webdriver_port: int,
    private_key: Path,
    public_key: str,
) -> dict[str, str]:
    environment = dict(os.environ)
    for name in (
        "AUTOMATION_TOOL_UPDATE_ENDPOINT",
        "AUTOMATION_TOOL_UPDATE_PUBLIC_KEY",
        "AUTOMATION_TOOL_UPDATE_ACCEPT_INVALID_TLS",
        "AUTOMATION_TOOL_UPDATE_INSTALL_PROBE",
        "TAURI_SIGNING_PRIVATE_KEY",
        "TAURI_SIGNING_PRIVATE_KEY_PATH",
        "TAURI_SIGNING_PRIVATE_KEY_PASSWORD",
        "TAURI_WEBDRIVER_PORT",
        "H821_SCENARIO",
        "H822_MAC_APP_BINARY",
        "H822_MAC_EXPECTED_BINARY_SHA256",
        "H822_MAC_SCENARIO",
        "H822_WINDOWS_APP_BINARY",
        "H822_WINDOWS_EXPECTED_BINARY_SHA256",
        "H822_WINDOWS_SCENARIO",
    ):
        environment.pop(name, None)
    environment["AUTOMATION_TOOL_UPDATE_ENDPOINT"] = (
        f"https://127.0.0.1:{update_port}/desktop-updates/v1/stable/"
        "{{target}}/{{arch}}/{{current_version}}"
    )
    environment["AUTOMATION_TOOL_UPDATE_PUBLIC_KEY"] = base64.b64encode(
        public_key.encode()
    ).decode()
    environment["AUTOMATION_TOOL_UPDATE_ACCEPT_INVALID_TLS"] = "1"
    environment["TAURI_SIGNING_PRIVATE_KEY"] = private_key.read_text(encoding="utf-8")
    environment["TAURI_SIGNING_PRIVATE_KEY_PASSWORD"] = KEY_PASSWORD
    environment["TAURI_WEBDRIVER_PORT"] = str(webdriver_port)
    return environment


def clean_bundle_outputs() -> None:
    nsis_directory = TAURI_ROOT / "target" / "debug" / "bundle" / "nsis"
    if nsis_directory.is_dir():
        for path in nsis_directory.glob(f"{PRODUCT_NAME}*"):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
    for suffix in (".exe", ".pdb"):
        (TAURI_ROOT / "target" / "debug" / f"{MAIN_BINARY_NAME}{suffix}").unlink(
            missing_ok=True
        )


def write_version_configuration(
    directory: Path, base: dict[str, Any], version: str
) -> Path:
    configuration = json.loads(json.dumps(base))
    configuration["version"] = version
    path = directory / f"tauri-h822-windows-{version}.json"
    path.write_text(
        json.dumps(configuration, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return path


def find_built_binary() -> Path:
    binary = TAURI_ROOT / "target" / "debug" / f"{MAIN_BINARY_NAME}.exe"
    if not binary.is_file():
        raise RuntimeError("H8-22 Windows binary was not generated")
    return binary


def find_built_nsis() -> tuple[Path, Path]:
    directory = TAURI_ROOT / "target" / "debug" / "bundle" / "nsis"
    candidates = sorted(directory.glob(f"{PRODUCT_NAME}*-setup.exe"))
    if len(candidates) != 1:
        raise RuntimeError(
            "H8-22 Windows NSIS installer was not generated exactly once"
        )
    installer = candidates[0]
    signature = Path(f"{installer}.sig")
    if not signature.is_file():
        raise RuntimeError("H8-22 Windows updater signature was not generated")
    return installer, signature


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wait_for_binary_hash(binary: Path, expected: str) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if file_sha256(binary) == expected:
                return
        except (FileNotFoundError, PermissionError):
            pass
        time.sleep(0.1)
    raise RuntimeError(
        "H8-22 official updater did not replace the installed Windows App"
    )


def matching_update_installer_pids() -> list[int]:
    output = run_powershell(
        "& { param([string]$ProductName) "
        "Get-CimInstance Win32_Process | Where-Object { "
        "$_.ExecutablePath -and $_.CommandLine -and "
        "$_.ExecutablePath.IndexOf($ProductName, "
        "[StringComparison]::OrdinalIgnoreCase) -ge 0 -and "
        "$_.CommandLine.IndexOf('/UPDATE', "
        "[StringComparison]::OrdinalIgnoreCase) -ge 0 } | "
        "ForEach-Object { $_.ProcessId } }",
        PRODUCT_NAME,
    )
    return [int(line) for line in output.splitlines() if line.strip()]


def wait_for_update_installer_exit() -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if not matching_update_installer_pids():
            return
        time.sleep(0.1)
    raise RuntimeError("H8-22 Windows updater installer did not exit")


def build_version(
    temporary: Path,
    base_configuration: dict[str, Any],
    environment: dict[str, str],
    version: str,
) -> tuple[Path, UpdateArtifact, Path]:
    clean_bundle_outputs()
    configuration = write_version_configuration(temporary, base_configuration, version)
    run_checked(
        [
            pnpm_executable(),
            "exec",
            "tauri",
            "build",
            "--debug",
            "--features",
            "desktop-e2e",
            "--bundles",
            "nsis",
            "--config",
            str(configuration),
            "--ci",
        ],
        environment=environment,
    )
    built_binary = find_built_binary()
    require_file_version(built_binary, version)
    verify_unsigned_binary(built_binary)
    preserved_binary = temporary / "built-binaries" / version / built_binary.name
    preserved_binary.parent.mkdir(parents=True)
    shutil.copy2(built_binary, preserved_binary)
    installer, signature = find_built_nsis()
    verify_unsigned_installer(installer)
    preserved_installer = temporary / "installers" / version / installer.name
    preserved_installer.parent.mkdir(parents=True)
    shutil.copy2(installer, preserved_installer)
    artifact = UpdateArtifact(
        version=version,
        payload=preserved_installer.read_bytes(),
        signature=signature.read_text(encoding="utf-8").strip(),
    )
    return preserved_binary, artifact, preserved_installer


def signed_invalid_artifact(temporary: Path, private_key: Path) -> UpdateArtifact:
    invalid = temporary / "updates" / "0.4.0-invalid.exe"
    invalid.parent.mkdir(parents=True)
    invalid.write_bytes(b"not-a-valid-windows-installer")
    run_checked(
        [
            pnpm_executable(),
            "exec",
            "tauri",
            "signer",
            "sign",
            "--private-key-path",
            str(private_key),
            "--password",
            KEY_PASSWORD,
            str(invalid),
        ]
    )
    signature = Path(f"{invalid}.sig")
    if not signature.is_file():
        raise RuntimeError("H8-22 invalid Windows package signature was not generated")
    return UpdateArtifact(
        version="0.4.0",
        payload=invalid.read_bytes(),
        signature=signature.read_text(encoding="utf-8").strip(),
    )


def build_update_app(
    port: int,
    artifacts: dict[str, UpdateArtifact],
    mode: dict[str, object],
    feed_ledger: list[dict[str, str]],
    artifact_ledger: list[str],
) -> Any:
    sys.path.insert(0, str(BACKEND_ROOT / "src"))
    from automation_tool.control_plane.application.desktop_updates import (
        DesktopUpdateCatalog,
    )
    from automation_tool.control_plane.bootstrap.app import create_app

    target, arch = current_update_platform()

    def catalog(version: str, policy: str) -> DesktopUpdateCatalog:
        artifact = artifacts[version]
        return DesktopUpdateCatalog.from_documents(
            [
                {
                    "version": version,
                    "channel": "stable",
                    "policy": policy,
                    "target": target,
                    "arch": arch,
                    "url": f"https://127.0.0.1:{port}/h822-windows-artifact/{version}",
                    "signature": artifact.signature,
                    "sha256": artifact.sha256,
                    "sizeBytes": len(artifact.payload),
                    "notes": "H8-22 isolated unsigned Windows package acceptance",
                    "publishedAt": "2026-07-22T00:00:00Z",
                }
            ]
        )

    catalogs = {
        ("0.2.0", "optional"): catalog("0.2.0", "optional"),
        ("0.3.0", "optional"): catalog("0.3.0", "optional"),
        ("0.2.0", "forced"): catalog("0.2.0", "forced"),
        ("0.4.0", "optional"): catalog("0.4.0", "optional"),
    }
    app = create_app(
        database=None, desktop_update_catalog=catalogs[("0.2.0", "optional")]
    )

    @app.middleware("http")
    async def select_catalog(request: Request, call_next: Any) -> Response:
        if request.url.path.startswith("/desktop-updates/v1/"):
            scenario = str(mode["scenario"])
            count = int(mode["feed_count"]) + 1
            mode["feed_count"] = count
            if scenario == "optional":
                version = "0.3.0" if count >= 4 else "0.2.0"
                policy = "optional"
            elif scenario == "forced":
                version = "0.2.0"
                policy = "forced"
            elif scenario == "failure":
                version = "0.4.0"
                policy = "optional"
            else:
                raise RuntimeError("H8-22 Windows feed scenario is invalid")
            request.app.state.desktop_update_catalog = catalogs[(version, policy)]
            feed_ledger.append(
                {"scenario": scenario, "version": version, "policy": policy}
            )
        return await call_next(request)

    async def artifact(version: str) -> Response:
        selected = artifacts.get(version)
        if selected is None:
            return Response(status_code=404)
        artifact_ledger.append(version)
        return Response(
            selected.payload,
            headers={
                "cache-control": "no-store",
                "content-length": str(len(selected.payload)),
            },
            media_type="application/octet-stream",
        )

    app.add_api_route(
        "/h822-windows-artifact/{version}",
        artifact,
        methods=["GET"],
        include_in_schema=False,
    )
    return app


def matching_app_pids(binary: Path) -> list[int]:
    output = run_powershell(
        "& { param([string]$TargetPath) "
        "$target = [IO.Path]::GetFullPath($TargetPath); "
        "Get-CimInstance Win32_Process | Where-Object { "
        "$_.ExecutablePath -and "
        "[IO.Path]::GetFullPath($_.ExecutablePath) -eq $target } | "
        "ForEach-Object { $_.ProcessId } }",
        str(binary),
    )
    return [int(line) for line in output.splitlines() if line.strip()]


def terminate_installed_app(binary: Path) -> None:
    for pid in matching_app_pids(binary):
        subprocess.run(
            ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
            cwd=FRONTEND_ROOT,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not matching_app_pids(binary):
            return
        time.sleep(0.1)
    raise RuntimeError("H8-22 packaged Windows App process did not stop")


def verify_installed_app(expected_version: str, expected_hash: str) -> None:
    binary = installed_binary()
    if not binary.is_file():
        raise RuntimeError("H8-22 installed Windows App is missing")
    require_file_version(binary, expected_version)
    if file_sha256(binary) != expected_hash:
        raise RuntimeError("H8-22 installed Windows binary hash is unexpected")
    verify_unsigned_binary(binary)
    if not (install_root() / "uninstall.exe").is_file():
        raise RuntimeError("H8-22 installed Windows uninstaller is missing")
    registry = windows_registry_installation()
    if registry is None:
        raise RuntimeError("H8-22 installed Windows registry record is missing")
    actual_registry_version = registry.display_version.split("+")[0]
    if actual_registry_version not in {expected_version, f"{expected_version}.0"}:
        raise RuntimeError("H8-22 installed Windows registry version is unexpected")
    if normalized_windows_path(registry.uninstaller) != normalized_windows_path(
        install_root() / "uninstall.exe"
    ):
        raise RuntimeError("H8-22 installed Windows registry owner is unexpected")


def install_initial_package(
    installer: Path, expected_version: str, expected_hash: str
) -> None:
    if install_root().exists():
        raise RuntimeError("H8-22 isolated Windows install root is already occupied")
    run_checked([str(installer), "/S"])
    wait_for_binary_hash(installed_binary(), expected_hash)
    verify_installed_app(expected_version, expected_hash)


def uninstall_owned_application() -> None:
    root = install_root()
    binary = installed_binary()
    if binary.exists():
        terminate_installed_app(binary)
    uninstaller = root / "uninstall.exe"
    if uninstaller.is_file():
        run_checked([str(uninstaller), "/S"])
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if not root.exists() and windows_registry_installation() is None:
            return
        time.sleep(0.1)
    if root.exists() or windows_registry_installation() is not None:
        raise RuntimeError(
            "H8-22 Windows NSIS uninstaller did not remove its owned installation"
        )


def run_packaged_app(
    base_environment: dict[str, str],
    scenario: str,
    webdriver_port: int,
    expected_binary_sha256: str | None = None,
) -> None:
    binary = installed_binary()
    require_port_closed(webdriver_port)
    environment = {
        **base_environment,
        "H822_WINDOWS_APP_BINARY": str(binary),
        "H822_WINDOWS_SCENARIO": scenario,
    }
    if expected_binary_sha256 is not None:
        environment["H822_WINDOWS_EXPECTED_BINARY_SHA256"] = expected_binary_sha256
    try:
        result = subprocess.run(
            [
                pnpm_executable(),
                "exec",
                "wdio",
                "run",
                "wdio.update-windows-package.conf.ts",
            ],
            cwd=FRONTEND_ROOT,
            env=environment,
            check=False,
        )
        if result.returncode != 0 and (
            scenario != "optional-install" or expected_binary_sha256 is None
        ):
            result.check_returncode()
        if expected_binary_sha256 is not None:
            wait_for_binary_hash(binary, expected_binary_sha256)
            wait_for_update_installer_exit()
    finally:
        if binary.exists():
            terminate_installed_app(binary)
    require_port_closed(webdriver_port)


def launch_for_forced_reopen(
    environment: dict[str, str], webdriver_port: int, expected_hash: str
) -> None:
    binary = installed_binary()
    require_port_closed(webdriver_port)
    process = subprocess.Popen(
        [str(binary)],
        cwd=FRONTEND_ROOT,
        env={**environment, "H822_WINDOWS_SCENARIO": "forced-reopen"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_binary_hash(binary, expected_hash)
        wait_for_update_installer_exit()
    finally:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
        if binary.exists():
            terminate_installed_app(binary)
    require_port_closed(webdriver_port)


def reset_app_data(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def run() -> None:
    require_windows()
    configuration = require_configuration()
    private_app_data = app_data_directory()
    uninstall_owned_application()
    reset_app_data(private_app_data)
    if ACCEPTANCE_ASSETS.exists():
        shutil.rmtree(ACCEPTANCE_ASSETS)
    update_port = unused_loopback_port()
    webdriver_port = unused_loopback_port()
    if update_port == webdriver_port:
        raise RuntimeError("H8-22 requires distinct update and WebDriver ports")
    server: uvicorn.Server | None = None
    server_thread: threading.Thread | None = None
    with tempfile.TemporaryDirectory(
        prefix="automation-tool-h822-windows-package-"
    ) as raw:
        temporary = Path(raw)
        certificate_path, certificate_key_path = write_tls_identity(temporary)
        private_key, public_key = generate_ephemeral_update_key(temporary)
        updater_configuration = configuration.setdefault("plugins", {}).setdefault(
            "updater", {}
        )
        updater_configuration["pubkey"] = base64.b64encode(public_key.encode()).decode()
        environment = build_environment(
            update_port, webdriver_port, private_key, public_key
        )
        try:
            binary_01, _, installer_01 = build_version(
                temporary, configuration, environment, "0.1.0"
            )
            binary_02, artifact_02, _ = build_version(
                temporary, configuration, environment, "0.2.0"
            )
            binary_03, artifact_03, _ = build_version(
                temporary, configuration, environment, "0.3.0"
            )
            invalid = signed_invalid_artifact(temporary, private_key)
            runtime_environment = dict(environment)
            for name in (
                "TAURI_SIGNING_PRIVATE_KEY",
                "TAURI_SIGNING_PRIVATE_KEY_PATH",
                "TAURI_SIGNING_PRIVATE_KEY_PASSWORD",
            ):
                runtime_environment.pop(name, None)
            artifacts = {
                "0.2.0": artifact_02,
                "0.3.0": artifact_03,
                "0.4.0": invalid,
            }
            feed_ledger: list[dict[str, str]] = []
            artifact_ledger: list[str] = []
            mode: dict[str, object] = {"scenario": "optional", "feed_count": 0}
            update_app = build_update_app(
                update_port, artifacts, mode, feed_ledger, artifact_ledger
            )
            server = uvicorn.Server(
                uvicorn.Config(
                    update_app,
                    host="127.0.0.1",
                    port=update_port,
                    ssl_certfile=str(certificate_path),
                    ssl_keyfile=str(certificate_key_path),
                    access_log=False,
                    log_level="critical",
                )
            )
            server_thread = threading.Thread(
                target=server.run,
                name="automation-tool-h822-windows-package",
                daemon=True,
            )
            server_thread.start()
            wait_for_port(update_port)

            hash_01 = file_sha256(binary_01)
            hash_02 = file_sha256(binary_02)
            hash_03 = file_sha256(binary_03)
            install_initial_package(installer_01, "0.1.0", hash_01)
            run_packaged_app(runtime_environment, "optional-decisions", webdriver_port)
            verify_installed_app("0.1.0", hash_01)
            run_packaged_app(
                runtime_environment,
                "optional-install",
                webdriver_port,
                expected_binary_sha256=hash_03,
            )
            verify_installed_app("0.3.0", hash_03)
            run_packaged_app(runtime_environment, "verify-installed", webdriver_port)

            uninstall_owned_application()
            reset_app_data(private_app_data)
            mode.update({"scenario": "forced", "feed_count": 0})
            install_initial_package(installer_01, "0.1.0", hash_01)
            run_packaged_app(runtime_environment, "forced-first", webdriver_port)
            verify_installed_app("0.1.0", hash_01)
            launch_for_forced_reopen(runtime_environment, webdriver_port, hash_02)
            verify_installed_app("0.2.0", hash_02)
            run_packaged_app(runtime_environment, "verify-installed", webdriver_port)

            uninstall_owned_application()
            reset_app_data(private_app_data)
            mode.update({"scenario": "failure", "feed_count": 0})
            install_initial_package(installer_01, "0.1.0", hash_01)
            run_packaged_app(runtime_environment, "installer-failure", webdriver_port)
            verify_installed_app("0.1.0", hash_01)

            if artifact_ledger != ["0.2.0", "0.3.0", "0.2.0", "0.4.0"]:
                raise RuntimeError("H8-22 Windows package downloads were not exact")
            optional_versions = [
                entry["version"]
                for entry in feed_ledger
                if entry["scenario"] == "optional"
            ]
            if (
                optional_versions[:3] != ["0.2.0"] * 3
                or "0.3.0" not in optional_versions
            ):
                raise RuntimeError(
                    "H8-22 Windows optional feed decisions were not preserved"
                )
        finally:
            if server is not None:
                server.should_exit = True
            if server_thread is not None:
                server_thread.join(timeout=10)
                if server_thread.is_alive():
                    raise RuntimeError("H8-22 Windows update server did not stop")
            uninstall_owned_application()
            reset_app_data(private_app_data)
            if ACCEPTANCE_ASSETS.exists():
                shutil.rmtree(ACCEPTANCE_ASSETS)
            clean_bundle_outputs()
            require_port_closed(update_port)
            require_port_closed(webdriver_port)
    print(
        "H8-22 unsigned Windows NSIS, optional, forced, replacement, and failure recovery acceptance passed"
    )


if __name__ == "__main__":
    run()
