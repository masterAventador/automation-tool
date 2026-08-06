#!/usr/bin/env python3
"""Build and audit one disposable macOS Tauri candidate with its Local Executor."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import plistlib
import secrets
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from automation_tool.executor.macos_candidate import (
    audit_macos_executor_candidate,
    build_macos_executor_candidate,
)
from automation_tool.executor.package_manifest import write_signed_executor_manifest
from production_assets import snapshot_production_assets

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
FRONTEND_ROOT = REPOSITORY_ROOT / "frontend"
TAURI_ROOT = FRONTEND_ROOT / "src-tauri"
BASE_TAURI_CONFIG = TAURI_ROOT / "tauri.conf.json"
CANDIDATE_TAURI_CONFIG = TAURI_ROOT / "tauri.macos-candidate.conf.json"
CARGO_MANIFEST = TAURI_ROOT / "Cargo.toml"
APP_IDENTIFIER = "com.aventador.automationtool"
EXECUTOR_RESOURCE = Path("local-executor/package")


def require_macos() -> str:
    if platform.system() != "Darwin":
        raise RuntimeError("P9-03 acceptance requires macOS")
    machine = platform.machine().lower()
    if machine in {"arm64", "aarch64"}:
        return "aarch64"
    if machine in {"x86_64", "amd64"}:
        return "x86_64"
    raise RuntimeError("P9-03 macOS architecture is unsupported")


def pnpm_executable() -> str:
    executable = shutil.which("pnpm")
    if executable is None:
        raise RuntimeError("P9-03 pnpm executable is unavailable")
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
        if stat.S_ISLNK(metadata.st_mode) or (
            not stat.S_ISDIR(metadata.st_mode) and not stat.S_ISREG(metadata.st_mode)
        ):
            raise RuntimeError("P9-03 bundled Executor contains an unsafe file")
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


def write_candidate_configuration(temporary: Path, executor: Path) -> Path:
    configuration = json.loads(CANDIDATE_TAURI_CONFIG.read_text(encoding="utf-8"))
    configuration["bundle"]["macOS"] = {"signingIdentity": "-"}
    configuration["bundle"]["resources"] = {
        f"{os.fspath(executor)}{os.sep}": f"{EXECUTOR_RESOURCE.as_posix()}/"
    }
    destination = temporary / "tauri.p9-03.generated.json"
    destination.write_text(
        json.dumps(configuration, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return destination


def release_environment(
    target: Path,
    executor_public_key: str,
    *,
    deployment_profile: dict[str, str] | None = None,
    action_authorization_public_key: str | None = None,
    update_endpoint: str | None = None,
    update_public_key: str | None = None,
    pexels_api_key: str | None = None,
) -> dict[str, str]:
    """The environment one release build compiles under.

    Everything the ambient shell offers under `AUTOMATION_TOOL_*` is stripped
    first, so a release is never influenced by a variable left over from an
    acceptance run; what the build compiles in is decided here and nowhere
    else. A customer Demo adds two things to that decision: the signed
    deployment profile (`scripts/customer_demo_release.py`), and an action
    authorization public key whose private half belongs to the deployment
    rather than to this build.
    """
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
            # Without a deployment the two keys coincide, as they always have:
            # a candidate build authorises nothing it did not also sign, and no
            # Control Plane holds the private half either way.
            "AUTOMATION_TOOL_ACTION_AUTHORIZATION_PUBLIC_KEY": (
                action_authorization_public_key or executor_public_key
            ),
            "AUTOMATION_TOOL_LOCAL_ACTION_MINIMUM_INTERVAL_SECONDS": "60",
            "AUTOMATION_TOOL_LOCAL_ACTION_TASK_LIMIT": "1",
            "CARGO_TARGET_DIR": os.fspath(target),
            "CI": "true",
        }
    )
    if pexels_api_key is not None:
        # Compile-time like the deployment profile: the key ships inside the
        # binary. This mapping is the cargo build's whole environment, so the
        # value never reaches a log line or a child process of the built App.
        environment["AUTOMATION_TOOL_PEXELS_API_KEY"] = pexels_api_key
    if update_endpoint is None and update_public_key is None:
        environment["AUTOMATION_TOOL_UPDATE_DISABLED"] = "1"
    elif update_endpoint is not None and update_public_key is not None:
        environment["AUTOMATION_TOOL_UPDATE_ENDPOINT"] = update_endpoint
        environment["AUTOMATION_TOOL_UPDATE_PUBLIC_KEY"] = update_public_key
    else:
        raise ValueError("release update configuration must be supplied as a pair")
    # `build.rs` demands the three profile variables all present or all absent;
    # anything else is a `panic!`. Passing the mapping the material produced
    # keeps that an all-or-nothing decision made in one place.
    environment.update(deployment_profile or {})
    return environment


def one_directory(parent: Path, suffix: str) -> Path:
    candidates = sorted(path for path in parent.glob(f"*{suffix}") if path.is_dir())
    if len(candidates) != 1:
        raise RuntimeError("P9-03 Tauri candidate directory is unavailable")
    return candidates[0]


def one_file(parent: Path, suffix: str) -> Path:
    candidates = sorted(path for path in parent.glob(f"*{suffix}") if path.is_file())
    if len(candidates) != 1:
        raise RuntimeError("P9-03 Tauri candidate file is unavailable")
    return candidates[0]


def app_binary(app: Path) -> Path:
    candidates = sorted(path for path in (app / "Contents/MacOS").iterdir() if path.is_file())
    if len(candidates) != 1:
        raise RuntimeError("P9-03 App does not contain one main binary")
    return candidates[0]


def verify_bundle_identity(app: Path) -> None:
    with (app / "Contents/Info.plist").open("rb") as source:
        information = plistlib.load(source)
    if (
        information.get("CFBundleIdentifier") != APP_IDENTIFIER
        or information.get("CFBundleShortVersionString") != "0.1.0"
    ):
        raise RuntimeError("P9-03 App identity is inconsistent")
    run_checked(["codesign", "--verify", "--deep", "--strict", os.fspath(app)])
    details = run_checked(
        ["codesign", "--display", "--verbose=4", os.fspath(app)],
        capture=True,
    )
    rendered = f"{details.stdout}\n{details.stderr}"
    if (
        "Signature=adhoc" not in rendered
        or "TeamIdentifier=not set" not in rendered
        or "Developer ID" in rendered
        or "Apple Distribution" in rendered
    ):
        raise RuntimeError("P9-03 App signing boundary is inconsistent")


def verify_manifest_signature(package: Path, private_key: Ed25519PrivateKey) -> None:
    manifest = (package / "executor-manifest.v1.json").read_bytes()
    envelope = (package / "executor-manifest.v1.sig").read_text(encoding="ascii")
    prefix, separator, encoded = envelope.strip().partition(".")
    if prefix != "atems1" or separator != ".":
        raise RuntimeError("P9-03 Executor signature envelope is invalid")
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
            "macos",
        ]
    )


def verify_packaged_executor(
    *,
    source: Path,
    packaged: Path,
    architecture: str,
    private_key: Ed25519PrivateKey,
    temporary: Path,
) -> tuple[int, int]:
    if package_files(source) != package_files(packaged):
        raise RuntimeError("P9-03 bundled Executor inventory changed during packaging")
    verify_manifest_signature(packaged, private_key)
    audit = audit_macos_executor_candidate(
        bundle_directory=packaged,
        expected_architecture=architecture,
        forbidden_development_roots=(REPOSITORY_ROOT, temporary),
    )
    if not os.access(packaged / "automation-tool-executor", os.X_OK):
        raise RuntimeError("P9-03 bundled Executor entrypoint is not executable")
    return audit.file_count, audit.package_size


def verify_dmg(dmg: Path, temporary: Path, expected_inventory: dict[str, tuple[int, str]]) -> None:
    run_checked(["hdiutil", "verify", os.fspath(dmg)])
    mount = temporary / "mounted-dmg"
    mount.mkdir()
    run_checked(
        [
            "hdiutil",
            "attach",
            "-nobrowse",
            "-readonly",
            "-mountpoint",
            os.fspath(mount),
            os.fspath(dmg),
        ]
    )
    try:
        app = one_directory(mount, ".app")
        verify_bundle_identity(app)
        packaged = app / "Contents/Resources" / EXECUTOR_RESOURCE
        if package_files(packaged) != expected_inventory:
            raise RuntimeError("P9-03 DMG Executor inventory is inconsistent")
        audit_release_bundle(app, packaged)
    finally:
        run_checked(["hdiutil", "detach", os.fspath(mount)])


def main() -> int:
    architecture = require_macos()
    with tempfile.TemporaryDirectory(
        prefix="automation-tool-p903-acceptance-", dir="/private/tmp"
    ) as raw:
        temporary = Path(raw)
        executor = temporary / "executor" / "automation-tool-executor"
        print("[P9-03] Building the isolated P9-01 Executor candidate")
        executor_audit = build_macos_executor_candidate(
            backend_root=BACKEND_ROOT,
            output_directory=executor,
        )
        seed, public_key, private_key = executor_signing_material()
        write_signed_executor_manifest(
            bundle_directory=executor,
            executor_version="0.1.0",
            build_id="p9-03-macos-candidate",
            target_platform="macos",
            target_architecture=architecture,
            signing_private_key=seed,
        )
        configuration = write_candidate_configuration(temporary, executor)
        target = temporary / "tauri-target"
        environment = release_environment(target, public_key)

        print("[P9-03] Building one production-mode ad-hoc App and DMG")
        run_checked(
            [
                pnpm_executable(),
                "exec",
                "tauri",
                "build",
                "--bundles",
                "app",
                "dmg",
                "--config",
                os.fspath(configuration),
                "--ci",
            ],
            environment=environment,
        )
        audited_assets = snapshot_production_assets(temporary / "audited-distribution")

        bundle_root = target / "release/bundle"
        app = one_directory(bundle_root / "macos", ".app")
        dmg = one_file(bundle_root / "dmg", ".dmg")
        verify_bundle_identity(app)
        packaged_executor = app / "Contents/Resources" / EXECUTOR_RESOURCE
        file_count, package_size = verify_packaged_executor(
            source=executor,
            packaged=packaged_executor,
            architecture=architecture,
            private_key=private_key,
            temporary=temporary,
        )

        print("[P9-03] Auditing the production binary and least-privilege config")
        run_checked(
            [
                "node",
                "scripts/audit-production-package.mjs",
                "--binary",
                os.fspath(app_binary(app)),
                "--cargo-manifest",
                os.fspath(CARGO_MANIFEST),
                "--tauri-config",
                os.fspath(BASE_TAURI_CONFIG),
                "--dist",
                os.fspath(audited_assets),
            ],
            environment=environment,
        )
        audit_release_bundle(app, packaged_executor)
        inventory = package_files(packaged_executor)
        verify_dmg(dmg, temporary, inventory)

    print(
        "[P9-03] macOS Tauri candidate passed resource, Manifest, CSP, Capability, "
        f"ad-hoc App and DMG audits: {file_count} Executor files, {package_size} bytes; "
        f"P9-01 raw payload was {executor_audit.package_size} bytes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
