#!/usr/bin/env python3
"""Run H8-22 against real ad-hoc macOS App and updater packages."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import plistlib
import shutil
import signal
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
TAURI_CONFIG = TAURI_ROOT / "tauri.update-macos-package-e2e.conf.json"
ACCEPTANCE_ASSETS = FRONTEND_ROOT / "dist-h822-mac"
APP_IDENTIFIER = "com.aventador.automationtool.h822macacceptance"
APP_NAME = "Automation Tool H822 Mac Acceptance.app"
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


def require_macos() -> None:
    if sys.platform != "darwin":
        raise RuntimeError("H8-22 ad-hoc package acceptance requires macOS")
    target, arch = current_update_platform()
    if target != "darwin" or arch not in {"aarch64", "x86_64"}:
        raise RuntimeError("H8-22 macOS package target is unsupported")


def app_data_directory() -> Path:
    path = Path.home() / "Library" / "Application Support" / APP_IDENTIFIER
    if path.name != APP_IDENTIFIER:
        raise RuntimeError("H8-22 AppData boundary is invalid")
    return path


def require_configuration() -> dict[str, Any]:
    configuration = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    windows = configuration.get("app", {}).get("windows", [])
    bundle = configuration.get("bundle", {})
    macos = bundle.get("macOS", {})
    if (
        configuration.get("identifier") != APP_IDENTIFIER
        or configuration.get("productName") != APP_NAME.removesuffix(".app")
        or len(windows) != 1
        or windows[0].get("visible") is not False
        or bundle.get("createUpdaterArtifacts") is not True
        or macos.get("signingIdentity") != "-"
    ):
        raise RuntimeError("H8-22 macOS package configuration is not isolated ad-hoc")
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
    public_candidates = [
        Path(f"{private_key}.pub"),
        private_key.with_suffix(".pub"),
    ]
    public_key = next((path for path in public_candidates if path.is_file()), None)
    if public_key is None or not private_key.is_file():
        raise RuntimeError("H8-22 ephemeral updater key generation failed")
    if private_key.stat().st_mode & 0o077:
        private_key.chmod(0o600)
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
    for directory in (
        TAURI_ROOT / "target" / "debug" / "bundle" / "macos",
        TAURI_ROOT / "target" / "debug" / "bundle" / "dmg",
    ):
        if not directory.is_dir():
            continue
        for path in directory.glob("Automation Tool H822 Mac Acceptance*"):
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)


def write_version_configuration(
    directory: Path, base: dict[str, Any], version: str
) -> Path:
    configuration = json.loads(json.dumps(base))
    configuration["version"] = version
    path = directory / f"tauri-h822-{version}.json"
    path.write_text(
        json.dumps(configuration, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return path


def find_built_app() -> Path:
    expected = TAURI_ROOT / "target" / "debug" / "bundle" / "macos" / APP_NAME
    if not expected.is_dir():
        raise RuntimeError("H8-22 Tauri App bundle was not generated")
    return expected


def find_updater_archive() -> tuple[Path, Path]:
    directory = TAURI_ROOT / "target" / "debug" / "bundle" / "macos"
    archive = directory / f"{APP_NAME}.tar.gz"
    signature = Path(f"{archive}.sig")
    if not archive.is_file() or not signature.is_file():
        raise RuntimeError("H8-22 updater archive or signature was not generated")
    return archive, signature


def find_built_dmg() -> Path:
    directory = TAURI_ROOT / "target" / "debug" / "bundle" / "dmg"
    candidates = sorted(directory.glob("Automation Tool H822 Mac Acceptance*.dmg"))
    if len(candidates) != 1:
        raise RuntimeError("H8-22 initial DMG was not generated exactly once")
    return candidates[0]


def bundle_binary(app: Path) -> Path:
    directory = app / "Contents" / "MacOS"
    candidates = [path for path in directory.iterdir() if path.is_file()]
    if len(candidates) != 1:
        raise RuntimeError("H8-22 App bundle does not have one main binary")
    return candidates[0]


def bundle_version(app: Path) -> str:
    with (app / "Contents" / "Info.plist").open("rb") as handle:
        document = plistlib.load(handle)
    version = document.get("CFBundleShortVersionString")
    if not isinstance(version, str):
        raise RuntimeError("H8-22 CFBundleShortVersionString is unavailable")
    return version


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wait_for_binary_hash(binary: Path, expected: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            if file_sha256(binary) == expected:
                return
        except FileNotFoundError:
            pass
        time.sleep(0.1)
    raise RuntimeError("H8-22 official updater did not replace the installed App")


def verify_ad_hoc_bundle(app: Path, expected_version: str) -> None:
    if bundle_version(app) != expected_version:
        raise RuntimeError("H8-22 installed App version is not the expected version")
    run_checked(["codesign", "--verify", "--deep", "--strict", str(app)])
    details = run_checked(
        ["codesign", "--display", "--verbose=4", str(app)], capture=True
    )
    signing_output = f"{details.stdout}\n{details.stderr}"
    if (
        "Signature=adhoc" not in signing_output
        or "TeamIdentifier=not set" not in signing_output
    ):
        raise RuntimeError("H8-22 package is not isolated ad-hoc code")
    if "Developer ID" in signing_output or "Apple Distribution" in signing_output:
        raise RuntimeError("H8-22 acceptance unexpectedly used a release identity")


def build_version(
    temporary: Path,
    base_configuration: dict[str, Any],
    environment: dict[str, str],
    version: str,
    include_dmg: bool,
) -> tuple[Path, UpdateArtifact, Path | None]:
    clean_bundle_outputs()
    configuration = write_version_configuration(temporary, base_configuration, version)
    bundles = ["app", "dmg"] if include_dmg else ["app"]
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
            *bundles,
            "--config",
            str(configuration),
            "--ci",
        ],
        environment=environment,
    )
    built_app = find_built_app()
    verify_ad_hoc_bundle(built_app, version)
    preserved_app = temporary / "built-apps" / version / APP_NAME
    preserved_app.parent.mkdir(parents=True)
    shutil.copytree(built_app, preserved_app, symlinks=True, copy_function=shutil.copy2)
    archive, signature = find_updater_archive()
    preserved_archive = temporary / "updates" / f"{version}.app.tar.gz"
    preserved_archive.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(archive, preserved_archive)
    artifact = UpdateArtifact(
        version=version,
        payload=preserved_archive.read_bytes(),
        signature=signature.read_text(encoding="utf-8").strip(),
    )
    preserved_dmg: Path | None = None
    if include_dmg:
        dmg = find_built_dmg()
        preserved_dmg = temporary / "installers" / dmg.name
        preserved_dmg.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dmg, preserved_dmg)
        run_checked(["hdiutil", "verify", str(preserved_dmg)])
    return preserved_app, artifact, preserved_dmg


def signed_invalid_artifact(temporary: Path, private_key: Path) -> UpdateArtifact:
    invalid = temporary / "updates" / "0.4.0-invalid.app.tar.gz"
    invalid.write_bytes(b"not-a-valid-gzip-or-macos-app-archive")
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
        raise RuntimeError("H8-22 invalid package signature was not generated")
    return UpdateArtifact(
        version="0.4.0",
        payload=invalid.read_bytes(),
        signature=signature.read_text(encoding="utf-8").strip(),
    )


def copy_apps_from_dmg(
    dmg: Path, install_roots: list[Path], mount_point: Path
) -> list[Path]:
    mount_point.mkdir(parents=True)
    run_checked(
        [
            "hdiutil",
            "attach",
            "-nobrowse",
            "-readonly",
            "-mountpoint",
            str(mount_point),
            str(dmg),
        ]
    )
    installed: list[Path] = []
    try:
        source = mount_point / APP_NAME
        if not source.is_dir():
            raise RuntimeError("H8-22 DMG does not contain the expected App")
        for root in install_roots:
            root.mkdir(parents=True)
            destination = root / APP_NAME
            shutil.copytree(
                source, destination, symlinks=True, copy_function=shutil.copy2
            )
            verify_ad_hoc_bundle(destination, "0.1.0")
            installed.append(destination)
    finally:
        run_checked(["hdiutil", "detach", str(mount_point)])
    return installed


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
                    "url": f"https://127.0.0.1:{port}/h822-macos-artifact/{version}",
                    "signature": artifact.signature,
                    "sha256": artifact.sha256,
                    "sizeBytes": len(artifact.payload),
                    "notes": "H8-22 isolated ad-hoc macOS package acceptance",
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
                raise RuntimeError("H8-22 feed scenario is invalid")
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
        "/h822-macos-artifact/{version}",
        artifact,
        methods=["GET"],
        include_in_schema=False,
    )
    return app


def matching_app_pids(binary: Path) -> list[int]:
    result = run_checked(["ps", "-axo", "pid=,command="], capture=True)
    expected = str(binary)
    matches: list[int] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_text, _, command = stripped.partition(" ")
        command = command.lstrip()
        if command == expected or command.startswith(f"{expected} "):
            matches.append(int(pid_text))
    return matches


def terminate_installed_app(binary: Path) -> None:
    for pid in matching_app_pids(binary):
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not matching_app_pids(binary):
            return
        time.sleep(0.05)
    for pid in matching_app_pids(binary):
        os.kill(pid, signal.SIGKILL)
    if matching_app_pids(binary):
        raise RuntimeError("H8-22 packaged App process did not stop")


def run_packaged_app(
    base_environment: dict[str, str],
    app: Path,
    scenario: str,
    webdriver_port: int,
    expected_binary_sha256: str | None = None,
) -> None:
    binary = bundle_binary(app)
    require_port_closed(webdriver_port)
    environment = {
        **base_environment,
        "H822_MAC_APP_BINARY": str(binary),
        "H822_MAC_SCENARIO": scenario,
    }
    if expected_binary_sha256 is not None:
        environment["H822_MAC_EXPECTED_BINARY_SHA256"] = expected_binary_sha256
    try:
        result = subprocess.run(
            [
                pnpm_executable(),
                "exec",
                "wdio",
                "run",
                "wdio.update-macos-package.conf.ts",
            ],
            cwd=FRONTEND_ROOT,
            env=environment,
            check=False,
        )
    finally:
        terminate_installed_app(binary)
    if result.returncode != 0:
        if scenario != "optional-install" or expected_binary_sha256 is None:
            result.check_returncode()
        wait_for_binary_hash(binary, expected_binary_sha256)
    require_port_closed(webdriver_port)


def wait_for_installed_version(app: Path, version: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            if bundle_version(app) == version:
                return
        except (FileNotFoundError, plistlib.InvalidFileException):
            pass
        time.sleep(0.1)
    raise RuntimeError(f"H8-22 packaged App did not reach version {version}")


def launch_for_forced_reopen(
    environment: dict[str, str], app: Path, expected_version: str
) -> None:
    binary = bundle_binary(app)
    process = subprocess.Popen(
        [str(binary)],
        cwd=FRONTEND_ROOT,
        env={**environment, "H822_MAC_SCENARIO": "forced-reopen"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_installed_version(app, expected_version)
    finally:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
        terminate_installed_app(binary)


def reset_app_data(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def run() -> None:
    require_macos()
    configuration = require_configuration()
    private_app_data = app_data_directory()
    reset_app_data(private_app_data)
    if ACCEPTANCE_ASSETS.exists():
        shutil.rmtree(ACCEPTANCE_ASSETS)
    update_port = unused_loopback_port()
    webdriver_port = unused_loopback_port()
    if update_port == webdriver_port:
        raise RuntimeError("H8-22 requires distinct update and WebDriver ports")
    server: uvicorn.Server | None = None
    server_thread: threading.Thread | None = None
    installed_apps: list[Path] = []
    with tempfile.TemporaryDirectory(
        prefix="automation-tool-h822-macos-package-", dir="/private/tmp"
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
            _, _, dmg = build_version(
                temporary, configuration, environment, "0.1.0", include_dmg=True
            )
            app_02, artifact_02, _ = build_version(
                temporary, configuration, environment, "0.2.0", include_dmg=False
            )
            app_03, artifact_03, _ = build_version(
                temporary, configuration, environment, "0.3.0", include_dmg=False
            )
            if dmg is None:
                raise RuntimeError("H8-22 initial DMG is unavailable")
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
            install_roots = [
                temporary / "installed" / "optional",
                temporary / "installed" / "forced",
                temporary / "installed" / "failure",
            ]
            installed_apps = copy_apps_from_dmg(
                dmg, install_roots, temporary / "mounted-dmg"
            )
            optional_app, forced_app, failure_app = installed_apps
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
                name="automation-tool-h822-macos-package",
                daemon=True,
            )
            server_thread.start()
            wait_for_port(update_port)

            run_packaged_app(
                runtime_environment,
                optional_app,
                "optional-decisions",
                webdriver_port,
            )
            verify_ad_hoc_bundle(optional_app, "0.1.0")
            expected_03_hash = file_sha256(bundle_binary(app_03))
            run_packaged_app(
                runtime_environment,
                optional_app,
                "optional-install",
                webdriver_port,
                expected_binary_sha256=expected_03_hash,
            )
            wait_for_installed_version(optional_app, "0.3.0")
            verify_ad_hoc_bundle(optional_app, "0.3.0")
            run_packaged_app(
                runtime_environment,
                optional_app,
                "verify-installed",
                webdriver_port,
            )

            reset_app_data(private_app_data)
            mode.update({"scenario": "forced", "feed_count": 0})
            run_packaged_app(
                runtime_environment, forced_app, "forced-first", webdriver_port
            )
            verify_ad_hoc_bundle(forced_app, "0.1.0")
            launch_for_forced_reopen(runtime_environment, forced_app, "0.2.0")
            verify_ad_hoc_bundle(forced_app, "0.2.0")
            if file_sha256(bundle_binary(forced_app)) != file_sha256(
                bundle_binary(app_02)
            ):
                raise RuntimeError(
                    "H8-22 forced update did not install the expected App"
                )
            run_packaged_app(
                runtime_environment,
                forced_app,
                "verify-installed",
                webdriver_port,
            )

            reset_app_data(private_app_data)
            mode.update({"scenario": "failure", "feed_count": 0})
            old_failure_hash = file_sha256(bundle_binary(failure_app))
            run_packaged_app(
                runtime_environment,
                failure_app,
                "installer-failure",
                webdriver_port,
            )
            verify_ad_hoc_bundle(failure_app, "0.1.0")
            if file_sha256(bundle_binary(failure_app)) != old_failure_hash:
                raise RuntimeError("H8-22 failed installer modified the old App")

            if artifact_ledger != ["0.2.0", "0.3.0", "0.2.0", "0.4.0"]:
                raise RuntimeError("H8-22 package downloads were not exact and bounded")
            optional_versions = [
                entry["version"]
                for entry in feed_ledger
                if entry["scenario"] == "optional"
            ]
            if (
                optional_versions[:3] != ["0.2.0"] * 3
                or "0.3.0" not in optional_versions
            ):
                raise RuntimeError("H8-22 optional feed decisions were not preserved")
        finally:
            if server is not None:
                server.should_exit = True
            if server_thread is not None:
                server_thread.join(timeout=10)
                if server_thread.is_alive():
                    raise RuntimeError("H8-22 update server did not stop")
            for app in installed_apps:
                if app.exists():
                    terminate_installed_app(bundle_binary(app))
            reset_app_data(private_app_data)
            if ACCEPTANCE_ASSETS.exists():
                shutil.rmtree(ACCEPTANCE_ASSETS)
            clean_bundle_outputs()
            require_port_closed(update_port)
            require_port_closed(webdriver_port)
    print(
        "H8-22 ad-hoc macOS DMG, optional, forced, replacement, and failure recovery acceptance passed"
    )


if __name__ == "__main__":
    run()
