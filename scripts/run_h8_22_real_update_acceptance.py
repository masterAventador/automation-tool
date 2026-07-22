#!/usr/bin/env python3
"""Install real signed updater packages through isolated hidden App launches."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import plistlib
import shutil
import signal
import stat
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
BASE_CONFIG = TAURI_ROOT / "tauri.real-update-e2e.conf.json"
TARGET_DIRECTORY = TAURI_ROOT / "target-h822-real"
ACCEPTANCE_ASSETS = FRONTEND_ROOT / "dist-h822-real"
APP_IDENTIFIER = "com.aventador.automationtool.h822realacceptance"
PRODUCT_NAME = "AutomationToolH822Acceptance"
OLD_VERSION = "0.1.0"
OPTIONAL_VERSION = "0.2.0"
NEW_VERSION = "0.3.0"
SIGNING_PASSWORD = "h822-isolated-acceptance"

sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))
from run_h8_20_acceptance import (  # noqa: E402
    current_update_platform,
    pnpm_executable,
    require_port_closed,
    unused_loopback_port,
    wait_for_port,
    write_tls_identity,
)


@dataclass(frozen=True, slots=True)
class SignedPackage:
    version: str
    path: Path
    signature: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class WindowsInstallation:
    executable: Path
    version: str
    uninstaller: Path


def remove_generated_path(path: Path) -> None:
    if not path.exists():
        return
    allowed = {TARGET_DIRECTORY.resolve(), ACCEPTANCE_ASSETS.resolve()}
    resolved = path.resolve()
    if resolved not in allowed:
        raise RuntimeError("H8-22 refused to remove an unscoped generated path")
    shutil.rmtree(resolved)


def app_data_directory() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_IDENTIFIER
    if sys.platform == "win32":
        roaming = os.environ.get("APPDATA")
        if not roaming:
            raise RuntimeError("Windows roaming AppData is unavailable")
        return Path(roaming) / APP_IDENTIFIER
    raise RuntimeError("H8-22 real updater acceptance supports only macOS and Windows")


def canonical_temporary_directory() -> Path:
    directory = Path(tempfile.gettempdir()).resolve()
    if not directory.is_dir():
        raise RuntimeError("H8-22 canonical temporary directory is unavailable")
    return directory


def require_hidden_configuration() -> dict[str, Any]:
    configuration = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    if (
        configuration.get("identifier") != APP_IDENTIFIER
        or configuration.get("productName") != PRODUCT_NAME
        or configuration.get("build", {}).get("frontendDist") != "../dist-h822-real"
        or configuration.get("bundle", {}).get("createUpdaterArtifacts") is not True
        or len(windows) != 1
        or windows[0].get("visible") is not False
    ):
        raise RuntimeError("H8-22 real updater must use its hidden isolated bundle")
    return configuration


def isolated_environment(target_directory: Path) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AUTOMATION_TOOL_UPDATE_")
        and not key.startswith("TAURI_SIGNING_")
        and not key.startswith("H822_REAL_")
        and key != "CARGO_TARGET_DIR"
    }
    environment["CARGO_TARGET_DIR"] = os.fspath(target_directory)
    return environment


def generate_signing_key(directory: Path, environment: dict[str, str]) -> str:
    private_key = directory / "updater.key"
    subprocess.run(
        [
            pnpm_executable(),
            "tauri",
            "signer",
            "generate",
            "--ci",
            "--password",
            SIGNING_PASSWORD,
            "--write-keys",
            os.fspath(private_key),
        ],
        cwd=FRONTEND_ROOT,
        env=environment,
        timeout=60,
        check=True,
    )
    public_key = private_key.with_suffix(".key.pub")
    if not private_key.is_file() or not public_key.is_file():
        raise RuntimeError("H8-22 signer did not create the expected key pair")
    if os.name == "posix":
        private_key.chmod(0o600)
        public_key.chmod(0o600)
    encoded_public_key = public_key.read_text(encoding="utf-8").strip()
    decoded = base64.b64decode(encoded_public_key, validate=True)
    if b"minisign public key" not in decoded or len(encoded_public_key) > 8192:
        raise RuntimeError("H8-22 signer returned an invalid public key")
    environment["TAURI_SIGNING_PRIVATE_KEY"] = private_key.read_text(encoding="utf-8")
    environment["TAURI_SIGNING_PRIVATE_KEY_PASSWORD"] = SIGNING_PASSWORD
    return encoded_public_key


def version_config(
    base: dict[str, Any], version: str, public_key: str, directory: Path
) -> Path:
    configuration = json.loads(json.dumps(base))
    configuration["version"] = version
    configuration.setdefault("plugins", {}).setdefault("updater", {})["pubkey"] = (
        public_key
    )
    path = directory / f"tauri-{version}.json"
    path.write_text(json.dumps(configuration, separators=(",", ":")), encoding="utf-8")
    if os.name == "posix":
        path.chmod(0o600)
    return path


def bundle_directory() -> Path:
    platform_directory = "macos" if sys.platform == "darwin" else "nsis"
    return TARGET_DIRECTORY / "debug" / "bundle" / platform_directory


def clear_bundle_output() -> None:
    directory = TARGET_DIRECTORY / "debug" / "bundle"
    if directory.exists():
        shutil.rmtree(directory)


def build_version(config: Path, environment: dict[str, str]) -> None:
    clear_bundle_output()
    bundle = "app" if sys.platform == "darwin" else "nsis"
    subprocess.run(
        [
            pnpm_executable(),
            "tauri",
            "build",
            "--debug",
            "--features",
            "desktop-e2e",
            "--bundles",
            bundle,
            "--config",
            os.fspath(config),
            "--ci",
        ],
        cwd=FRONTEND_ROOT,
        env=environment,
        timeout=3600,
        check=True,
    )


def locate_signed_updater() -> tuple[Path, Path]:
    directory = bundle_directory()
    if not directory.is_dir():
        raise RuntimeError("H8-22 bundle output is missing")
    signatures = sorted(directory.glob("*.sig"))
    candidates = [
        signature.with_suffix("")
        for signature in signatures
        if signature.with_suffix("").is_file()
    ]
    if len(candidates) != 1:
        raise RuntimeError("H8-22 did not produce exactly one signed updater package")
    return candidates[0], signatures[0]


def copy_signed_package(version: str, directory: Path) -> SignedPackage:
    package, signature_path = locate_signed_updater()
    destination = directory / f"{version}.package"
    shutil.copy2(package, destination)
    signature = signature_path.read_text(encoding="utf-8").strip()
    base64.b64decode(signature, validate=True)
    payload = destination.read_bytes()
    return SignedPackage(
        version=version,
        path=destination,
        signature=signature,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )


def locate_old_bundle(directory: Path) -> Path:
    if sys.platform == "darwin":
        bundles = sorted(bundle_directory().glob("*.app"))
        if len(bundles) != 1:
            raise RuntimeError("H8-22 did not create exactly one old macOS App")
        destination = directory / "old.app"
        shutil.copytree(bundles[0], destination, symlinks=True)
        return destination
    installers = [
        path
        for path in bundle_directory().glob("*.exe")
        if not path.name.lower().startswith("uninstall")
    ]
    if len(installers) != 1:
        raise RuntimeError("H8-22 did not create exactly one old Windows installer")
    destination = directory / "old-installer.exe"
    shutil.copy2(installers[0], destination)
    return destination


def build_all_packages(
    temporary: Path,
) -> tuple[dict[str, SignedPackage], Path, str, dict[str, str]]:
    base = require_hidden_configuration()
    environment = isolated_environment(TARGET_DIRECTORY)
    public_key = generate_signing_key(temporary, environment)
    packages: dict[str, SignedPackage] = {}
    for version in (OPTIONAL_VERSION, NEW_VERSION):
        config = version_config(base, version, public_key, temporary)
        build_version(config, environment)
        packages[version] = copy_signed_package(version, temporary)
    old_config = version_config(base, OLD_VERSION, public_key, temporary)
    build_version(old_config, environment)
    old_bundle = locate_old_bundle(temporary)
    return packages, old_bundle, public_key, environment


def build_update_app(
    port: int,
    packages: dict[str, SignedPackage],
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
        package = packages[version]
        return DesktopUpdateCatalog.from_documents(
            [
                {
                    "version": version,
                    "channel": "stable",
                    "policy": policy,
                    "target": target,
                    "arch": arch,
                    "url": f"https://127.0.0.1:{port}/h822-real-artifact/{version}",
                    "signature": package.signature,
                    "sha256": package.sha256,
                    "sizeBytes": package.size_bytes,
                    "notes": "H8-22 real signed isolated updater package",
                    "publishedAt": "2026-07-22T00:00:00Z",
                }
            ]
        )

    optional_02 = catalog(OPTIONAL_VERSION, "optional")
    optional_03 = catalog(NEW_VERSION, "optional")
    forced_02 = catalog(OPTIONAL_VERSION, "forced")
    app = create_app(database=None, desktop_update_catalog=optional_02)

    @app.middleware("http")
    async def select_catalog(request: Request, call_next: Any) -> Response:
        if request.url.path.startswith("/desktop-updates/v1/"):
            scenario = str(mode["scenario"])
            count = int(mode["feed_count"]) + 1
            mode["feed_count"] = count
            if scenario == "optional":
                version = NEW_VERSION if count >= 3 else OPTIONAL_VERSION
                selected = optional_03 if count >= 3 else optional_02
                policy = "optional"
            else:
                version = OPTIONAL_VERSION
                selected = forced_02
                policy = "forced"
                if scenario == "forced-reopen":
                    await asyncio.sleep(2)
            request.app.state.desktop_update_catalog = selected
            feed_ledger.append(
                {"scenario": scenario, "version": version, "policy": policy}
            )
        return await call_next(request)

    async def artifact(version: str) -> Response:
        package = packages.get(version)
        if package is None:
            return Response(status_code=404)
        artifact_ledger.append(version)
        return Response(
            package.path.read_bytes(),
            headers={
                "cache-control": "no-store",
                "content-length": str(package.size_bytes),
            },
            media_type="application/octet-stream",
        )

    app.add_api_route(
        "/h822-real-artifact/{version}",
        artifact,
        methods=["GET"],
        include_in_schema=False,
    )
    return app


def launch_environment(
    build_environment: dict[str, str],
    update_port: int,
    webdriver_port: int,
    public_key: str,
    binary: Path,
    scenario: str,
) -> dict[str, str]:
    environment = dict(build_environment)
    environment.pop("TAURI_SIGNING_PRIVATE_KEY", None)
    environment.pop("TAURI_SIGNING_PRIVATE_KEY_PASSWORD", None)
    environment.pop("AUTOMATION_TOOL_UPDATE_INSTALL_PROBE", None)
    environment["AUTOMATION_TOOL_UPDATE_ENDPOINT"] = (
        f"https://127.0.0.1:{update_port}/desktop-updates/v1/stable/"
        "{{target}}/{{arch}}/{{current_version}}"
    )
    environment["AUTOMATION_TOOL_UPDATE_PUBLIC_KEY"] = public_key
    environment["AUTOMATION_TOOL_UPDATE_ACCEPT_INVALID_TLS"] = "1"
    environment["TAURI_WEBDRIVER_PORT"] = str(webdriver_port)
    environment["H822_REAL_APP_BINARY"] = os.fspath(binary)
    environment["H822_REAL_SCENARIO"] = scenario
    return environment


def run_hidden_app(
    environment: dict[str, str], webdriver_port: int
) -> subprocess.CompletedProcess[str]:
    require_port_closed(webdriver_port)
    completed = subprocess.run(
        [pnpm_executable(), "exec", "wdio", "run", "wdio.real-update.conf.ts"],
        cwd=FRONTEND_ROOT,
        env=environment,
        text=True,
        timeout=300,
        check=False,
    )
    return completed


def wait_for_port_closed(port: int) -> None:
    deadline = time.monotonic() + 30
    while True:
        try:
            require_port_closed(port)
            break
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


def mac_app_binary(bundle: Path) -> Path:
    binary = bundle / "Contents" / "MacOS" / "automation-tool-desktop"
    if not binary.is_file():
        raise RuntimeError("H8-22 macOS App binary is missing")
    return binary


def mac_app_version(bundle: Path) -> str:
    with (bundle / "Contents" / "Info.plist").open("rb") as source:
        value = plistlib.load(source).get("CFBundleShortVersionString")
    if not isinstance(value, str):
        raise RuntimeError("H8-22 macOS App version is missing")
    return value


def windows_installation() -> WindowsInstallation | None:
    if sys.platform != "win32":
        return None
    import winreg

    root = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    for access in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            parent = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, root, 0, winreg.KEY_READ | access
            )
        except OSError:
            continue
        with parent:
            for index in range(winreg.QueryInfoKey(parent)[0]):
                name = winreg.EnumKey(parent, index)
                try:
                    child = winreg.OpenKey(parent, name)
                    with child:
                        display_name = str(winreg.QueryValueEx(child, "DisplayName")[0])
                        if display_name != PRODUCT_NAME:
                            continue
                        version = str(winreg.QueryValueEx(child, "DisplayVersion")[0])
                        uninstall_value = str(
                            winreg.QueryValueEx(child, "UninstallString")[0]
                        )
                        try:
                            install_location = Path(
                                str(winreg.QueryValueEx(child, "InstallLocation")[0])
                            )
                        except OSError:
                            install_location = Path(uninstall_value.strip('"')).parent
                        executable = install_location / "automation-tool-desktop.exe"
                        uninstaller = Path(uninstall_value.strip().strip('"'))
                        return WindowsInstallation(executable, version, uninstaller)
                except OSError:
                    continue
    return None


def uninstall_windows() -> None:
    installation = windows_installation()
    if installation is None:
        return
    terminate_acceptance_processes(installation.executable)
    if installation.uninstaller.is_file():
        subprocess.run(
            [os.fspath(installation.uninstaller), "/S"],
            timeout=180,
            check=True,
        )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and windows_installation() is not None:
        time.sleep(0.2)
    if windows_installation() is not None:
        raise RuntimeError("H8-22 Windows acceptance uninstall did not finish")


def install_old_windows(installer: Path) -> Path:
    uninstall_windows()
    subprocess.run([os.fspath(installer), "/S"], timeout=300, check=True)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        installation = windows_installation()
        if installation is not None and installation.executable.is_file():
            if installation.version != OLD_VERSION:
                raise RuntimeError("H8-22 installed the wrong old Windows version")
            return installation.executable
        time.sleep(0.2)
    raise RuntimeError("H8-22 old Windows App was not installed")


def installed_version(location: Path) -> str:
    if sys.platform == "darwin":
        return mac_app_version(location)
    installation = windows_installation()
    if installation is None or installation.executable != location:
        raise RuntimeError("H8-22 Windows installation record is unavailable")
    return installation.version


def wait_for_version(location: Path, expected: str) -> None:
    deadline = time.monotonic() + 120
    latest = "missing"
    while time.monotonic() < deadline:
        try:
            latest = installed_version(location)
        except (OSError, RuntimeError):
            latest = "transitioning"
        if latest == expected:
            return
        time.sleep(0.25)
    raise RuntimeError(f"H8-22 expected installed version {expected}, found {latest}")


def terminate_acceptance_processes(binary: Path) -> None:
    if sys.platform == "win32":
        escaped = os.fspath(binary).replace("'", "''")
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                (
                    "Get-CimInstance Win32_Process | "
                    f"Where-Object {{ $_.ExecutablePath -eq '{escaped}' }} | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
                ),
            ],
            check=False,
            timeout=30,
        )
        return
    completed = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    prefix = f"{binary}"
    pids: list[int] = []
    for line in completed.stdout.splitlines():
        stripped = line.strip()
        pid_text, separator, command = stripped.partition(" ")
        if separator and (command == prefix or command.startswith(f"{prefix} ")):
            pids.append(int(pid_text))
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    if pids:
        time.sleep(0.5)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def prepare_old_app(old_bundle: Path, destination: Path) -> tuple[Path, Path]:
    if sys.platform == "darwin":
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(old_bundle, destination, symlinks=True)
        if mac_app_version(destination) != OLD_VERSION:
            raise RuntimeError("H8-22 copied the wrong old macOS version")
        return destination, mac_app_binary(destination)
    executable = install_old_windows(old_bundle)
    return executable, executable


def verify_cache(
    private_app_data: Path, package: SignedPackage, expected_version: str
) -> None:
    cache = private_app_data / "app-updates" / "cache-v1"
    candidate = cache / "candidate.package"
    manifest_path = cache / "cache-manifest-v1"
    if not candidate.is_file() or not manifest_path.is_file():
        raise RuntimeError("H8-22 verified update cache is incomplete")
    if candidate.read_bytes() != package.path.read_bytes():
        raise RuntimeError("H8-22 cache does not contain the exact signed package")
    manifest = json.loads(manifest_path.read_bytes())
    if manifest.get("version") != expected_version:
        raise RuntimeError("H8-22 cache version does not match the signed package")
    if os.name == "posix":
        if stat.S_IMODE(cache.stat().st_mode) != 0o700:
            raise RuntimeError("H8-22 update cache directory is not private")
        for path in (candidate, manifest_path):
            if stat.S_IMODE(path.stat().st_mode) != 0o600:
                raise RuntimeError("H8-22 update cache file is not private")


def run() -> None:
    remove_generated_path(TARGET_DIRECTORY)
    remove_generated_path(ACCEPTANCE_ASSETS)
    private_app_data = app_data_directory()
    if private_app_data.exists():
        shutil.rmtree(private_app_data)
    if sys.platform == "win32":
        uninstall_windows()
    update_port = unused_loopback_port()
    webdriver_port = unused_loopback_port()
    if update_port == webdriver_port:
        raise RuntimeError("H8-22 requires isolated update and WebDriver ports")
    feed_ledger: list[dict[str, str]] = []
    artifact_ledger: list[str] = []
    server: uvicorn.Server | None = None
    server_thread: threading.Thread | None = None
    optional_binary: Path | None = None
    forced_binary: Path | None = None
    with tempfile.TemporaryDirectory(
        prefix="automation-tool-h822-real-", dir=canonical_temporary_directory()
    ) as temporary:
        temporary_root = Path(temporary)
        try:
            packages, old_bundle, public_key, build_environment = build_all_packages(
                temporary_root
            )
        except Exception:
            if private_app_data.exists():
                shutil.rmtree(private_app_data)
            if sys.platform == "win32":
                uninstall_windows()
            remove_generated_path(ACCEPTANCE_ASSETS)
            remove_generated_path(TARGET_DIRECTORY)
            raise
        certificate, key = write_tls_identity(temporary_root)
        mode: dict[str, object] = {"scenario": "optional", "feed_count": 0}
        app = build_update_app(
            update_port, packages, mode, feed_ledger, artifact_ledger
        )
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=update_port,
                ssl_certfile=os.fspath(certificate),
                ssl_keyfile=os.fspath(key),
                access_log=False,
                log_level="critical",
            )
        )
        server_thread = threading.Thread(
            target=server.run, name="automation-tool-h822-real", daemon=True
        )
        server_thread.start()
        wait_for_port(update_port)
        try:
            optional_location, optional_binary = prepare_old_app(
                old_bundle, temporary_root / "optional.app"
            )
            optional_environment = launch_environment(
                build_environment,
                update_port,
                webdriver_port,
                public_key,
                optional_binary,
                "optional",
            )
            optional_result = run_hidden_app(optional_environment, webdriver_port)
            if (
                optional_result.returncode != 0
                and installed_version(optional_location) == OLD_VERSION
            ):
                raise RuntimeError(
                    "H8-22 optional App failed before installation; "
                    f"feeds={feed_ledger!r}, artifacts={artifact_ledger!r}"
                )
            wait_for_version(optional_location, NEW_VERSION)
            if optional_result.returncode != 0:
                print(
                    "H8-22 optional WDIO connection ended during the verified App replacement",
                    file=sys.stderr,
                )
            verify_cache(private_app_data, packages[NEW_VERSION], NEW_VERSION)
            if artifact_ledger != [OPTIONAL_VERSION, NEW_VERSION]:
                raise RuntimeError("H8-22 optional package overwrite ledger is invalid")
            terminate_acceptance_processes(optional_binary)
            wait_for_port_closed(webdriver_port)

            if private_app_data.exists():
                shutil.rmtree(private_app_data)
            artifact_ledger.clear()
            mode.update({"scenario": "forced-first", "feed_count": 0})
            forced_location, forced_binary = prepare_old_app(
                old_bundle, temporary_root / "forced.app"
            )
            forced_first_environment = launch_environment(
                build_environment,
                update_port,
                webdriver_port,
                public_key,
                forced_binary,
                "forced-first",
            )
            forced_first = run_hidden_app(forced_first_environment, webdriver_port)
            if forced_first.returncode != 0:
                raise RuntimeError("H8-22 forced first launch UI did not pass")
            verify_cache(private_app_data, packages[OPTIONAL_VERSION], OPTIONAL_VERSION)
            terminate_acceptance_processes(forced_binary)
            wait_for_port_closed(webdriver_port)

            mode.update({"scenario": "forced-reopen", "feed_count": 0})
            forced_reopen_environment = launch_environment(
                build_environment,
                update_port,
                webdriver_port,
                public_key,
                forced_binary,
                "forced-reopen",
            )
            forced_reopen = run_hidden_app(forced_reopen_environment, webdriver_port)
            wait_for_version(forced_location, OPTIONAL_VERSION)
            if forced_reopen.returncode != 0:
                print(
                    "H8-22 forced WDIO connection ended during the verified App replacement",
                    file=sys.stderr,
                )
            if artifact_ledger != [OPTIONAL_VERSION]:
                raise RuntimeError("H8-22 forced package was downloaded more than once")
            terminate_acceptance_processes(forced_binary)
            wait_for_port_closed(webdriver_port)
        finally:
            if optional_binary is not None:
                terminate_acceptance_processes(optional_binary)
            if forced_binary is not None:
                terminate_acceptance_processes(forced_binary)
            wait_for_port_closed(webdriver_port)
            if server is not None:
                server.should_exit = True
            if server_thread is not None:
                server_thread.join(timeout=10)
                if server_thread.is_alive():
                    raise RuntimeError("H8-22 real update server did not stop")
            if private_app_data.exists():
                shutil.rmtree(private_app_data)
            if sys.platform == "win32":
                uninstall_windows()
            remove_generated_path(ACCEPTANCE_ASSETS)
            remove_generated_path(TARGET_DIRECTORY)
            require_port_closed(update_port)
            require_port_closed(webdriver_port)
    optional_versions = [
        entry["version"] for entry in feed_ledger if entry["scenario"] == "optional"
    ]
    if optional_versions[:3] != [OPTIONAL_VERSION, OPTIONAL_VERSION, NEW_VERSION]:
        raise RuntimeError("H8-22 optional feed sequence is invalid")
    if not any(entry["scenario"] == "forced-reopen" for entry in feed_ledger):
        raise RuntimeError("H8-22 forced reopen did not call the production feed")
    print(
        "Real signed optional skip/overwrite/install and forced next-start update passed"
    )


if __name__ == "__main__":
    run()
