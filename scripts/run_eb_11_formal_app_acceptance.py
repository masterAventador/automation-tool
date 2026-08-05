#!/usr/bin/env python3
# ruff: noqa: UP017
"""Accept EB-11 only through the notarized macOS App's normal account page.

The accessibility tree is the sole source of product state.  The runner never
reads an operations Profile, cookies, tokens, or browser storage directly.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import importlib
import json
import os
import platform
import plistlib
import re
import secrets
import signal
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

SCRIPT_ROOT = Path(__file__).resolve().parent
if os.fspath(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(SCRIPT_ROOT))

from release_identity import (  # noqa: E402
    SourceFacts,
    repository_source_facts,
    source_commit_is_ancestor,
)

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
APP_IDENTIFIER: Final = "com.aventador.automationtool"
APP_DATA: Final = Path.home() / "Library" / "Application Support" / APP_IDENTIFIER
DEMO_PROFILE_VERSION: Final = "customer-demo-profile.v1"
DEMO_PROFILE_KIND: Final = "demo"
LAUNCH_NONCE_ENVIRONMENT: Final = "AUTOMATION_TOOL_EB11_ACCEPTANCE_NONCE"
EXECUTOR_MANIFEST: Final = Path(
    "Contents/Resources/local-executor/package/executor-manifest.v1.json"
)
BROWSER_MANIFEST: Final = Path("Contents/Resources/embedded-browser/distribution-manifest.v1.json")
ACCOUNT_PAGE_LABEL: Final = "账号与平台"
RECHECK_LABEL: Final = "我已处理，重新检查"
OPEN_LOGIN_LABEL: Final = "打开登录处理"
LOGOUT_LABEL: Final = "安全注销"
CONFIRM_LOGOUT_LABEL: Final = "确认注销"
LOGIN_REQUIRED_LABEL: Final = "需要登录"
HEALTHY_LABEL: Final = "登录正常"
SCAN_CHECKPOINT: Final = "douyin_scan_confirmed"
LOGIN_PROGRESS_MARKERS: Final = (
    "请在打开的运营浏览器中扫码登录。",
    "扫码成功，请在手机抖音中确认登录。",
    "二维码已过期，请重新打开登录处理。",
    "页面需要人工处理，请在运营浏览器中完成后重新检查。",
    "抖音仍未登录，请在运营浏览器中继续处理。",
)
UNAVAILABLE_CODE: Final = "process_unavailable"
SIGNING_CONTRACT: Final = (
    REPOSITORY_ROOT / "contracts" / "quality" / "macos-release-signing.v1.json"
)
RELEASE_IDENTITY_KEY: Final = "AutomationToolReleaseIdentity"
RELEASE_IDENTITY_SCHEMA: Final = "automation-tool.release-identity.v1"
# The Windows carrier for the same seven fields. An NSIS package has no
# `Info.plist`; `build_release_package.py` writes this into the payload.
RELEASE_IDENTITY_NAME: Final = "release-identity.v1.json"
OBSERVED_AT_PATTERN: Final = re.compile(
    r"最近检查[：:]\s*("
    r"\d{4}(?:[/-]\d{1,2}[/-]\d{1,2}|年\d{1,2}月\d{1,2}日)"
    r"[\s,，]*(?:上午|下午)?[\s,，]*\d{1,2}:\d{2}(?::\d{2})?"
    r")"
)
OBSERVED_REVISION_PATTERN: Final = re.compile(
    r"^(?P<year>\d{4})(?:"
    r"[/-](?P<month>\d{1,2})[/-](?P<day>\d{1,2})|"
    r"年(?P<month_cn>\d{1,2})月(?P<day_cn>\d{1,2})日"
    r")[\s,，]*(?P<period>上午|下午)?[\s,，]*"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?$"
)
POLL_SECONDS: Final = 0.5
# The runtime watcher samples far faster than the UI poll: it is racing a
# short-lived Chromium, not waiting for a human-visible state change.
RUNTIME_SAMPLE_SECONDS: Final = 0.05
RUNTIME_WATCHER_JOIN_SECONDS: Final = 5.0
WINDOW_TIMEOUT_SECONDS: Final = 45.0
ACTION_TIMEOUT_SECONDS: Final = 150.0
QUIT_TIMEOUT_SECONDS: Final = 30.0
LAUNCH_CLEANUP_DISCOVERY_SECONDS: Final = WINDOW_TIMEOUT_SECONDS
LAUNCH_CLEANUP_FINAL_QUIET_POLLS: Final = 3
CANONICAL_PROFILE_ID_PATTERN: Final = (
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
USER_DATA_DIRECTORY_PATTERN: Final = re.compile(
    r"(?:^| )--user-data-dir=(?P<value>.*?)(?= --|$)"
)
CURRENT_DOUYIN_PROFILE_FILE: Final = "current-douyin-profile-v1"
PROFILE_LEASE_FILE_PREFIX: Final = ".automation-tool-profile-lease-v1-"
PRIVATE_DIRECTORY_MODE: Final = 0o700
DEFAULT_BROWSER_PROFILE_ROOTS: Final = (
    Path.home() / "Library/Application Support/Google/Chrome",
    Path.home() / "Library/Application Support/Microsoft Edge",
)


class AcceptanceFailed(RuntimeError):
    """The formal App did not satisfy the EB-11 acceptance contract."""


@dataclass(frozen=True)
class Arguments:
    app: Path
    deployment_profile: Path
    evidence: Path
    interactive_device_acceptance: bool


@dataclass(frozen=True)
class AppIdentity:
    bundle_identifier: str
    version: str
    executable_path: Path


@dataclass(frozen=True)
class CodeIdentity:
    identifier: str
    authority: str
    team_id: str
    cdhash: str


@dataclass(frozen=True)
class ArtifactFacts:
    authority: str
    team_id: str
    bundle_cdhash: str
    bundle_tree_sha256: str
    bundle_bytes: int
    executor_build_id: str


@dataclass(frozen=True)
class SignedReleaseIdentity:
    source_git_commit: str
    source_tree_sha256: str
    executor_build_id: str
    target: str
    architecture: str
    deployment_profile_id: str


@dataclass(frozen=True)
class RuntimeContract:
    app_path: Path
    executor_path: Path
    browser_path: Path
    profile_root: Path
    executor_identity: CodeIdentity | None = None
    browser_identity: CodeIdentity | None = None


@dataclass
class ProfileDirectoryBinding:
    path: Path
    parent_fd: int
    directory_fd: int
    parent_identity: tuple[int, int]
    identity: tuple[int, int]

    def close(self) -> None:
        for name in ("directory_fd", "parent_fd"):
            descriptor = getattr(self, name)
            if descriptor >= 0:
                os.close(descriptor)
                setattr(self, name, -1)


@dataclass(frozen=True)
class VerifiedRelease:
    app_identity: AppIdentity
    runtime_contract: RuntimeContract
    artifact: ArtifactFacts
    release_identity: SignedReleaseIdentity
    profile_root: Path


@dataclass
class RuntimeObservation:
    executor_observed: bool = False
    embedded_browser_observed: bool = False
    app_owned_profile_observed: bool = False
    profile_directories: tuple[Path, ...] = ()
    profile_bindings: tuple[ProfileDirectoryBinding, ...] = ()

    def merge(self, other: RuntimeObservation) -> RuntimeObservation:
        profile_directories = tuple(
            sorted(
                {*self.profile_directories, *other.profile_directories},
                key=os.fspath,
            )
        )
        bindings = list(self.profile_bindings)
        binding_keys = {
            (binding.path, binding.parent_identity, binding.identity) for binding in bindings
        }
        for binding in other.profile_bindings:
            key = (binding.path, binding.parent_identity, binding.identity)
            if key in binding_keys:
                binding.close()
                continue
            bindings.append(binding)
            binding_keys.add(key)
        return RuntimeObservation(
            executor_observed=self.executor_observed or other.executor_observed,
            embedded_browser_observed=(
                self.embedded_browser_observed or other.embedded_browser_observed
            ),
            app_owned_profile_observed=(
                self.app_owned_profile_observed or other.app_owned_profile_observed
            ),
            profile_directories=profile_directories,
            profile_bindings=tuple(bindings),
        )

    def close(self) -> None:
        for binding in self.profile_bindings:
            binding.close()


@dataclass(frozen=True)
class ProcessRecord:
    pid: int
    ppid: int
    command: str
    started_at: str = ""
    launch_nonce: str = field(default="", compare=False)


@dataclass
class EvidenceTarget:
    path: Path
    parent_fd: int
    name: str

    def close(self) -> None:
        if self.parent_fd >= 0:
            os.close(self.parent_fd)
            self.parent_fd = -1


@dataclass
class EvidencePublication:
    """A published result that remains rollback-capable until PASS is reported."""

    target: EvidenceTarget
    identity: tuple[int, int]
    active: bool = True
    committed: bool = False

    @property
    def path(self) -> Path:
        return self.target.path

    def rollback(self) -> None:
        if not self.active:
            return
        try:
            unlink_owned_file(self.target, self.target.name, self.identity)
            os.fsync(self.target.parent_fd)
        finally:
            self.active = False
            self.target.close()

    def commit(self) -> None:
        if not self.active:
            raise AcceptanceFailed("EB-11 evidence publication is already closed")
        try:
            require_evidence_parent_binding(self.target)
            metadata = os.stat(
                self.target.name,
                dir_fd=self.target.parent_fd,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or (metadata.st_dev, metadata.st_ino) != self.identity
            ):
                raise AcceptanceFailed("EB-11 evidence identity changed before PASS")
        except BaseException:
            self.rollback()
            raise
        self.committed = True

    def finish_report(self) -> None:
        """Release rollback ownership after the PASS line was fully flushed."""

        if not self.active or not self.committed:
            raise AcceptanceFailed("EB-11 evidence was not committed before PASS")
        descriptor = self.target.parent_fd
        self.target.parent_fd = -1
        self.active = False
        # The evidence was already inode-verified, fsynced and reported, so a
        # close interrupted here changes nothing a reader would see; process
        # exit reclaims the descriptor.
        with contextlib.suppress(OSError):
            os.close(descriptor)


def parse_arguments() -> Arguments:
    parser = argparse.ArgumentParser(
        description="EB-11 signed macOS App real-session acceptance",
    )
    parser.add_argument("--interactive-device-acceptance", action="store_true")
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--deployment-profile", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parsed = parser.parse_args()
    return Arguments(
        app=parsed.app,
        deployment_profile=parsed.deployment_profile,
        evidence=parsed.evidence,
        interactive_device_acceptance=parsed.interactive_device_acceptance,
    )


def require_no_symlink_components(path: Path, *, include_leaf: bool) -> None:
    parts = path.parts if include_leaf else path.parent.parts
    current = Path(parts[0])
    for part in parts[1:]:
        current /= part
        if current.is_symlink():
            raise AcceptanceFailed("EB-11 refuses a path with a symlink component")


class DeviceDriver:
    """How one host is observed. What is being observed does not vary.

    EB-11's definition is platform-neutral: sign in, re-check, log out and prove
    the old Profile is gone, scan again, restart and prove the same Profile came
    back, exit and prove none of our processes survive. Only the instruments are
    macOS-specific — AppleScript for the accessibility tree, `codesign` against
    a live PID, `lsof` for open files, `F_GETPATH` to prove an inode has no name.

    A second Windows runner would fork that definition, and the definition is
    the valuable part. So it stays in one file and the instruments sit here.
    """

    platform = ""

    def unavailable(self, capability: str) -> AcceptanceFailed:
        """Name the gap, not the host.

        `EB-11 formal App acceptance requires macOS` told an operator on Windows
        nothing about what was missing or what would close it.
        """
        return AcceptanceFailed(
            f"EB-11 cannot {capability} on {self.platform}: this observation has "
            "no implementation for this host yet"
        )

    def press(self, process_id: int, label: str) -> None:
        raise self.unavailable(f"press {label!r} through the accessibility tree")

    def read_release_identity(self, app: Path) -> SignedReleaseIdentity:
        raise self.unavailable("read the signed release identity")


class MacosDeviceDriver(DeviceDriver):
    platform = "darwin"

    def press(self, process_id: int, label: str) -> None:
        press(process_id, label)

    def read_release_identity(self, app: Path) -> SignedReleaseIdentity:
        return read_signed_release_identity(app)


class WindowsDeviceDriver(DeviceDriver):
    """The Windows instruments, as they arrive.

    Implemented: the release identity, because
    `build_release_package.py --platform windows` writes it. An NSIS package has
    no `Info.plist`, so the seven fields macOS keeps under the Developer ID seal
    are written to `release-identity.v1.json` in the payload instead — weaker,
    and recorded as such where it is written.

    Not yet implemented, each failing by name rather than as "requires macOS":
    driving the WebView2 accessibility tree, verifying a running PID's
    Authenticode signature, proving which Profile directory the browser has
    open, and proving a deleted Profile's file id has no remaining name.
    """

    platform = "win32"

    def read_release_identity(self, app: Path) -> SignedReleaseIdentity:
        identity_path = app / RELEASE_IDENTITY_NAME
        try:
            record = json.loads(identity_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise AcceptanceFailed("EB-11 signed release identity is unavailable") from error
        return release_identity_from_record(record)


DEVICE_DRIVERS: tuple[type[DeviceDriver], ...] = (MacosDeviceDriver, WindowsDeviceDriver)


def device_driver() -> DeviceDriver:
    for candidate in DEVICE_DRIVERS:
        if candidate.platform == sys.platform:
            return candidate()
    raise AcceptanceFailed(f"EB-11 has no device driver for {sys.platform}")


def require_device_boundary(arguments: Arguments) -> tuple[Path, Path]:
    # Selected rather than refused. A host without a driver still fails here;
    # a host with a partial one now fails at the specific observation it cannot
    # make, which is where the next piece of work is.
    driver = device_driver()
    if driver.platform != "darwin":
        raise driver.unavailable("run the full formal-App lifecycle")
    if not arguments.interactive_device_acceptance:
        raise AcceptanceFailed("EB-11 requires --interactive-device-acceptance")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise AcceptanceFailed("EB-11 requires an interactive console")
    if os.geteuid() == 0:
        raise AcceptanceFailed("EB-11 refuses to run as root")

    app = arguments.app
    if not app.is_absolute() or app.suffix.lower() != ".app":
        raise AcceptanceFailed("EB-11 App path must be one absolute .app")
    require_no_symlink_components(app, include_leaf=True)
    app = app.resolve(strict=True)
    if not app.is_dir():
        raise AcceptanceFailed("EB-11 App bundle is unavailable")

    evidence = arguments.evidence
    if not evidence.is_absolute() or evidence.suffix.lower() != ".json":
        raise AcceptanceFailed("EB-11 evidence path must be one absolute JSON file")
    require_no_symlink_components(evidence, include_leaf=True)
    if evidence.exists() or evidence.is_symlink():
        raise AcceptanceFailed("EB-11 refuses to overwrite evidence")
    if not evidence.parent.is_dir():
        raise AcceptanceFailed("EB-11 evidence parent is unavailable")
    evidence = require_evidence_outside_app(app, evidence)
    evidence = require_source_stable_evidence_path(evidence)
    return app, evidence


def require_evidence_outside_app(app: Path, evidence: Path) -> Path:
    resolved_app = app.resolve(strict=True)
    resolved_parent = evidence.parent.resolve(strict=True)
    resolved_evidence = resolved_parent / evidence.name
    resolved_app_data = APP_DATA.resolve(strict=False)
    if resolved_evidence == resolved_app or resolved_app in resolved_evidence.parents:
        raise AcceptanceFailed("EB-11 evidence must stay outside the App bundle")
    if resolved_evidence == resolved_app_data or resolved_app_data in resolved_evidence.parents:
        raise AcceptanceFailed("EB-11 evidence must stay outside production AppData")
    return resolved_evidence


def require_source_stable_evidence_path(evidence: Path) -> Path:
    """Keep a PASS artifact from becoming a new signed release input."""

    resolved = evidence.parent.resolve(strict=True) / evidence.name
    try:
        relative = resolved.relative_to(REPOSITORY_ROOT)
    except ValueError:
        return resolved
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", "--", relative.as_posix()],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if ignored.returncode == 0:
        return resolved
    if ignored.returncode == 1:
        raise AcceptanceFailed(
            "EB-11 evidence must not become a new release source inventory entry"
        )
    raise AcceptanceFailed("EB-11 evidence source inventory policy is unavailable")


def run_checked(
    command: list[str],
    *,
    capture: bool = False,
    timeout: float = 300.0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        timeout=timeout,
    )


def read_identity(app: Path) -> AppIdentity:
    information_path = app / "Contents" / "Info.plist"
    if information_path.is_symlink() or not information_path.is_file():
        raise AcceptanceFailed("EB-11 App Info.plist is unavailable")
    with information_path.open("rb") as source:
        information = plistlib.load(source)
    identifier = information.get("CFBundleIdentifier")
    version = information.get("CFBundleShortVersionString")
    executable = information.get("CFBundleExecutable")
    if identifier != APP_IDENTIFIER:
        raise AcceptanceFailed("EB-11 requires the production bundle identifier")
    if not isinstance(version, str) or not version:
        raise AcceptanceFailed("EB-11 App version is unavailable")
    if not isinstance(executable, str) or not executable or Path(executable).name != executable:
        raise AcceptanceFailed("EB-11 App executable identity is invalid")
    executable_path = app / "Contents" / "MacOS" / executable
    if (
        executable_path.is_symlink()
        or not executable_path.is_file()
        or not os.access(executable_path, os.X_OK)
    ):
        raise AcceptanceFailed("EB-11 App executable is unavailable")
    return AppIdentity(identifier, version, executable_path)


def read_packaged_json(app: Path, relative: Path) -> dict[str, object]:
    path = app / relative
    if path.is_symlink() or not path.is_file():
        raise AcceptanceFailed("EB-11 packaged runtime manifest is unavailable")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AcceptanceFailed("EB-11 packaged runtime manifest is invalid") from error
    if not isinstance(document, dict):
        raise AcceptanceFailed("EB-11 packaged runtime manifest is invalid")
    return document


def packaged_executable(root: Path, raw: object) -> Path:
    if not isinstance(raw, str) or not raw:
        raise AcceptanceFailed("EB-11 packaged runtime executable is invalid")
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        raise AcceptanceFailed("EB-11 packaged runtime executable is invalid")
    executable = root / relative
    try:
        executable.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as error:
        raise AcceptanceFailed("EB-11 packaged runtime executable escapes its root") from error
    if executable.is_symlink() or not executable.is_file() or not os.access(executable, os.X_OK):
        raise AcceptanceFailed("EB-11 packaged runtime executable is unavailable")
    return executable


def compiled_deployment_profile_root(executable: Path, deployment_profile: Path) -> Path:
    if not deployment_profile.is_absolute():
        raise AcceptanceFailed("EB-11 deployment Profile path must be absolute")
    require_no_symlink_components(deployment_profile, include_leaf=True)
    if not deployment_profile.is_file():
        raise AcceptanceFailed("EB-11 deployment Profile is unavailable")
    try:
        deployment = json.loads(deployment_profile.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AcceptanceFailed("EB-11 deployment Profile is invalid") from error
    expected_fields = {"profileId", "baseUrl", "allowedHosts"}
    if not isinstance(deployment, dict) or set(deployment) != expected_fields:
        raise AcceptanceFailed("EB-11 deployment Profile is invalid")
    profile_id = deployment.get("profileId")
    base_url = deployment.get("baseUrl")
    allowed_hosts = deployment.get("allowedHosts")
    if (
        not isinstance(profile_id, str)
        or not re.fullmatch(r"demo-[a-z0-9](?:[a-z0-9-]{0,41}[a-z0-9])?", profile_id)
        or not isinstance(base_url, str)
        or not isinstance(allowed_hosts, list)
        or not allowed_hosts
        or not all(isinstance(host, str) and host for host in allowed_hosts)
    ):
        raise AcceptanceFailed("EB-11 deployment Profile is invalid")
    manifest = {
        "version": DEMO_PROFILE_VERSION,
        "profile": DEMO_PROFILE_KIND,
        "profileId": profile_id,
        "baseUrl": base_url,
        "allowedHosts": allowed_hosts,
    }
    payload = json.dumps(
        manifest,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
    if encoded not in executable.read_bytes():
        raise AcceptanceFailed(
            "EB-11 App does not carry the expected signed deployment Profile"
        )
    return APP_DATA / "profiles" / profile_id / "embedded-browser-profiles"


def read_runtime_contract(app: Path, profile_root: Path) -> tuple[RuntimeContract, str]:
    executor_root = app / EXECUTOR_MANIFEST.parent
    executor_manifest = read_packaged_json(app, EXECUTOR_MANIFEST)
    browser_root = app / BROWSER_MANIFEST.parent
    browser_manifest = read_packaged_json(app, BROWSER_MANIFEST)
    build_id = executor_manifest.get("build_id")
    if not isinstance(build_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", build_id):
        raise AcceptanceFailed("EB-11 packaged Executor build identity is invalid")
    return (
        RuntimeContract(
            app_path=app,
            executor_path=packaged_executable(executor_root, executor_manifest.get("entrypoint")),
            browser_path=packaged_executable(browser_root, browser_manifest.get("executable")),
            profile_root=profile_root,
        ),
        build_id,
    )


def hash_field(digest: object, kind: bytes, relative: str, value: bytes = b"") -> None:
    encoded = relative.encode("utf-8")
    digest.update(kind)  # type: ignore[attr-defined]
    digest.update(len(encoded).to_bytes(8, "big"))  # type: ignore[attr-defined]
    digest.update(encoded)  # type: ignore[attr-defined]
    digest.update(len(value).to_bytes(8, "big"))  # type: ignore[attr-defined]
    digest.update(value)  # type: ignore[attr-defined]


def bundle_tree_digest(app: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    total_bytes = 0

    def visit(directory: Path) -> None:
        nonlocal total_bytes
        with os.scandir(directory) as entries:
            ordered = sorted(entries, key=lambda entry: entry.name)
        for entry in ordered:
            path = Path(entry.path)
            relative = path.relative_to(app).as_posix()
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                target = os.readlink(path)
                resolved = (path.parent / target).resolve(strict=True)
                try:
                    resolved.relative_to(app)
                except ValueError as error:
                    raise AcceptanceFailed("EB-11 App contains an escaping symlink") from error
                hash_field(digest, b"L", relative, os.fsencode(target))
            elif stat.S_ISDIR(metadata.st_mode):
                hash_field(digest, b"D", relative)
                visit(path)
            elif stat.S_ISREG(metadata.st_mode):
                hash_field(digest, b"F", relative, metadata.st_size.to_bytes(8, "big"))
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    while chunk := os.read(descriptor, 1024 * 1024):
                        digest.update(chunk)
                finally:
                    os.close(descriptor)
                total_bytes += metadata.st_size
            else:
                raise AcceptanceFailed("EB-11 App contains an unsupported file type")

    visit(app)
    return digest.hexdigest(), total_bytes


def verify_formal_app(app: Path, executor_build_id: str) -> ArtifactFacts:
    require_no_symlink_components(SIGNING_CONTRACT, include_leaf=True)
    with SIGNING_CONTRACT.open(encoding="utf-8") as source:
        signing_contract = json.load(source)
    expected_certificate = signing_contract.get("certificate")
    expected_team = signing_contract.get("teamId")
    if not isinstance(expected_certificate, str) or not isinstance(expected_team, str):
        raise AcceptanceFailed("EB-11 macOS signing contract is invalid")

    run_checked(["codesign", "--verify", "--deep", "--strict", os.fspath(app)])
    signature = run_checked(
        ["codesign", "--display", "--verbose=4", os.fspath(app)],
        capture=True,
    )
    details = f"{signature.stdout}\n{signature.stderr}"
    if (
        f"Authority={expected_certificate}" not in details
        or f"TeamIdentifier={expected_team}" not in details
        or "Signature=adhoc" in details
    ):
        raise AcceptanceFailed("EB-11 App does not match the release signing contract")
    cdhash_match = re.search(r"^CDHash=([0-9a-fA-F]+)$", details, re.MULTILINE)
    if cdhash_match is None:
        raise AcceptanceFailed("EB-11 App CDHash is unavailable")
    run_checked(["spctl", "--assess", "--type", "execute", "--verbose=4", os.fspath(app)])
    run_checked(["xcrun", "stapler", "validate", os.fspath(app)])
    tree_sha256, bundle_bytes = bundle_tree_digest(app)
    return ArtifactFacts(
        authority=expected_certificate,
        team_id=expected_team,
        bundle_cdhash=cdhash_match.group(1).lower(),
        bundle_tree_sha256=tree_sha256,
        bundle_bytes=bundle_bytes,
        executor_build_id=executor_build_id,
    )


def code_identity_from_details(
    details: str,
    *,
    authority: str,
    team_id: str,
) -> CodeIdentity:
    lines = details.splitlines()

    def one_value(prefix: str, pattern: str) -> str:
        values = [line.removeprefix(prefix) for line in lines if line.startswith(prefix)]
        if len(values) != 1 or re.fullmatch(pattern, values[0]) is None:
            raise AcceptanceFailed("EB-11 runtime code identity is invalid")
        return values[0]

    if f"Authority={authority}" not in lines or f"TeamIdentifier={team_id}" not in lines:
        raise AcceptanceFailed("EB-11 runtime code signer does not match the release")
    return CodeIdentity(
        identifier=one_value("Identifier=", r"[^\s=]{1,255}"),
        authority=authority,
        team_id=team_id,
        cdhash=one_value("CDHash=", r"[0-9a-fA-F]{40}").lower(),
    )


def signed_code_identity(
    executable: Path,
    *,
    authority: str,
    team_id: str,
) -> CodeIdentity:
    run_checked(
        ["codesign", "--verify", "--strict", "--verbose=4", os.fspath(executable)],
        capture=True,
    )
    signature = run_checked(
        ["codesign", "--display", "--verbose=4", os.fspath(executable)],
        capture=True,
    )
    return code_identity_from_details(
        f"{signature.stdout}\n{signature.stderr}",
        authority=authority,
        team_id=team_id,
    )


def bind_runtime_code_identities(
    contract: RuntimeContract,
    artifact: ArtifactFacts,
) -> RuntimeContract:
    return RuntimeContract(
        app_path=contract.app_path,
        executor_path=contract.executor_path,
        browser_path=contract.browser_path,
        profile_root=contract.profile_root,
        executor_identity=signed_code_identity(
            contract.executor_path,
            authority=artifact.authority,
            team_id=artifact.team_id,
        ),
        browser_identity=signed_code_identity(
            contract.browser_path,
            authority=artifact.authority,
            team_id=artifact.team_id,
        ),
    )


def read_signed_release_identity(app: Path) -> SignedReleaseIdentity:
    information_path = app / "Contents" / "Info.plist"
    try:
        with information_path.open("rb") as source:
            record = plistlib.load(source).get(RELEASE_IDENTITY_KEY)
    except (OSError, plistlib.InvalidFileException, AttributeError) as error:
        raise AcceptanceFailed("EB-11 signed release identity is unavailable") from error
    return release_identity_from_record(record)


def release_identity_from_record(record: object) -> SignedReleaseIdentity:
    """Validate the seven fields, wherever this platform carries them.

    Shared so a Windows package cannot be accepted on looser terms than a macOS
    one: the carrier differs, what has to be true of the contents does not.
    """
    required = {
        "architecture",
        "buildId",
        "deploymentProfileId",
        "schema",
        "sourceGitCommit",
        "sourceTreeSha256",
        "target",
    }
    if not isinstance(record, dict) or set(record) != required:
        raise AcceptanceFailed("EB-11 signed release identity is invalid")
    if record.get("schema") != RELEASE_IDENTITY_SCHEMA:
        raise AcceptanceFailed("EB-11 signed release identity version is invalid")

    def required_string(name: str) -> str:
        value = record.get(name)
        if not isinstance(value, str) or not value:
            raise AcceptanceFailed("EB-11 signed release identity is invalid")
        return value

    source_commit = required_string("sourceGitCommit")
    source_tree = required_string("sourceTreeSha256")
    build_id = required_string("buildId")
    if (
        not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", source_commit)
        or not re.fullmatch(r"[0-9a-f]{64}", source_tree)
        or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", build_id)
    ):
        raise AcceptanceFailed("EB-11 signed release identity is invalid")
    return SignedReleaseIdentity(
        source_git_commit=source_commit,
        source_tree_sha256=source_tree,
        executor_build_id=build_id,
        target=required_string("target"),
        architecture=required_string("architecture"),
        deployment_profile_id=required_string("deploymentProfileId"),
    )


def require_release_identity(
    release: SignedReleaseIdentity,
    *,
    artifact: ArtifactFacts,
    app_identity: AppIdentity,
    profile_root: Path,
    source: SourceFacts,
) -> None:
    machine = platform.machine().lower()
    expected_platform = {
        "arm64": ("macos-arm64", "aarch64"),
        "aarch64": ("macos-arm64", "aarch64"),
    }.get(machine)
    if expected_platform is None or (release.target, release.architecture) != expected_platform:
        raise AcceptanceFailed("EB-11 signed release target does not match this Mac")
    if app_identity.bundle_identifier != APP_IDENTIFIER or not app_identity.version:
        raise AcceptanceFailed("EB-11 signed release App identity is invalid")
    if release.source_tree_sha256 != source.tree_sha256:
        raise AcceptanceFailed("EB-11 App was not built from the current reviewed source tree")
    if release.source_git_commit != source.git_commit:
        try:
            ancestor = source_commit_is_ancestor(
                REPOSITORY_ROOT,
                release.source_git_commit,
                source.git_commit,
            )
        except RuntimeError as error:
            raise AcceptanceFailed(
                "EB-11 signed release source ancestry is unavailable"
            ) from error
        if not ancestor:
            raise AcceptanceFailed(
                "EB-11 App was not built from the current reviewed source history"
            )
    if release.executor_build_id != artifact.executor_build_id:
        raise AcceptanceFailed("EB-11 signed release does not match the packaged Executor")
    if release.deployment_profile_id != profile_root.parent.name:
        raise AcceptanceFailed("EB-11 signed release does not match the deployment Profile")


def verify_release_artifact(app: Path, deployment_profile: Path) -> VerifiedRelease:
    app_identity = read_identity(app)
    profile_root = compiled_deployment_profile_root(
        app_identity.executable_path,
        deployment_profile,
    )
    runtime_contract, executor_build_id = read_runtime_contract(app, profile_root)
    artifact = verify_formal_app(app, executor_build_id)
    runtime_contract = bind_runtime_code_identities(runtime_contract, artifact)
    release_identity = read_signed_release_identity(app)
    require_release_identity(
        release_identity,
        artifact=artifact,
        app_identity=app_identity,
        profile_root=profile_root,
        source=repository_source_facts(REPOSITORY_ROOT),
    )
    return VerifiedRelease(
        app_identity=app_identity,
        runtime_contract=runtime_contract,
        artifact=artifact,
        release_identity=release_identity,
        profile_root=profile_root,
    )


def apple_script(source: str, *, timeout: float = 30.0) -> str:
    result = subprocess.run(
        ["/usr/bin/osascript", "-"],
        input=source,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or "unknown AppleScript failure"
        raise AcceptanceFailed(f"EB-11 App UI automation failed: {message}")
    return result.stdout.strip()


def bundle_process_ids() -> set[int]:
    rendered = apple_script(
        f'''
tell application "System Events"
  set matches to every application process whose bundle identifier is "{APP_IDENTIFIER}"
  set ids to {{}}
  repeat with productProcess in matches
    set end of ids to unix id of productProcess
  end repeat
  return ids as text
end tell
'''
    )
    return {int(value) for value in re.findall(r"\d+", rendered)}


def accessibility_process_script(process_id: int, body: str) -> str:
    if process_id <= 0:
        raise AcceptanceFailed("EB-11 App process identity is invalid")
    return f'''
tell application "System Events"
  set matches to every application process whose unix id is {process_id}
  if (count of matches) is not 1 then return "__NO_PROCESS__"
  set productProcess to item 1 of matches
  if bundle identifier of productProcess is not "{APP_IDENTIFIER}" then return "__WRONG_APP__"
  set frontmost of productProcess to true
  tell productProcess
{body}
  end tell
end tell
'''


def process_snapshot() -> list[ProcessRecord]:
    completed = run_checked(
        ["/bin/ps", "-ww", "-axo", "pid=,ppid=,lstart=,command="],
        capture=True,
    )
    records: list[ProcessRecord] = []
    for line in completed.stdout.splitlines():
        fields = line.strip().split(maxsplit=7)
        if len(fields) != 8 or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        records.append(
            ProcessRecord(
                int(fields[0]),
                int(fields[1]),
                fields[7],
                " ".join(fields[2:7]),
            )
        )
    return records


def descendant_records(root_process_id: int, records: list[ProcessRecord]) -> list[ProcessRecord]:
    process_ids = {root_process_id}
    changed = True
    while changed:
        changed = False
        for record in records:
            if record.ppid in process_ids and record.pid not in process_ids:
                process_ids.add(record.pid)
                changed = True
    return [record for record in records if record.pid in process_ids]


def packaged_process_records(app: Path, records: list[ProcessRecord]) -> list[ProcessRecord]:
    executable_prefix = f"{app}{os.sep}Contents{os.sep}"
    return [
        record
        for record in records
        if record.pid != os.getpid() and record.command.startswith(executable_prefix)
    ]


def instance_process_records(
    app: Path,
    instance: ProcessRecord,
    records: list[ProcessRecord],
    *,
    reject_foreign: bool = True,
) -> list[ProcessRecord]:
    current_root = next(
        (record for record in records if record.pid == instance.pid),
        None,
    )
    if current_root != instance:
        return []
    scoped = descendant_records(instance.pid, records)
    scoped_ids = {record.pid for record in scoped}
    packaged = packaged_process_records(app, records)
    if instance.launch_nonce:
        for record in packaged:
            if record.pid not in scoped_ids and process_has_launch_nonce(
                record, instance.launch_nonce
            ):
                scoped_ids.add(record.pid)
        scoped = [record for record in records if record.pid in scoped_ids]
    foreign = [record for record in packaged if record.pid not in scoped_ids]
    if instance.launch_nonce and foreign:
        current = process_snapshot()
        foreign = [record for record in foreign if record in current]
    if reject_foreign and foreign:
        raise AcceptanceFailed("EB-11 detected another formal App instance during acceptance")
    return scoped


def nonce_owned_process_records(
    app: Path,
    nonce: str,
    records: list[ProcessRecord] | None = None,
) -> list[ProcessRecord]:
    snapshot = process_snapshot() if records is None else records
    return [
        record
        for record in packaged_process_records(app, snapshot)
        if process_has_launch_nonce(record, nonce)
    ]


def owned_process_records(
    app: Path,
    instance: ProcessRecord | None,
    *,
    reject_foreign: bool = True,
) -> list[ProcessRecord]:
    if instance is None:
        return []
    records = process_snapshot()
    rooted = instance_process_records(
        app,
        instance,
        records,
        reject_foreign=False,
    )
    nonce_owned = (
        nonce_owned_process_records(app, instance.launch_nonce, records)
        if instance.launch_nonce
        else []
    )
    scoped_by_pid = {record.pid: record for record in [*rooted, *nonce_owned]}
    if reject_foreign:
        foreign = [
            record
            for record in packaged_process_records(app, records)
            if record.pid not in scoped_by_pid
        ]
        if foreign:
            raise AcceptanceFailed("EB-11 detected another formal App instance during acceptance")
    return list(scoped_by_pid.values())


def still_running(expected: list[ProcessRecord]) -> list[ProcessRecord]:
    current = {record.pid: record for record in process_snapshot()}
    return [record for record in expected if current.get(record.pid) == record]


def command_runs(command: str, executable: Path) -> bool:
    rendered = os.fspath(executable)
    return command == rendered or command.startswith(f"{rendered} ")


def profile_argument(contract: RuntimeContract, record: ProcessRecord) -> Path | None:
    arguments = [
        match.group("value")
        for match in USER_DATA_DIRECTORY_PATTERN.finditer(record.command)
    ]
    expected_profile_pattern = re.compile(
        rf"{re.escape(os.fspath(contract.profile_root / 'douyin'))}{re.escape(os.sep)}"
        rf"{CANONICAL_PROFILE_ID_PATTERN}"
    )
    if len(arguments) != 1 or expected_profile_pattern.fullmatch(arguments[0]) is None:
        return None
    return Path(arguments[0])


def path_is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def require_private_directory_identity(path: Path) -> tuple[int, tuple[int, int]]:
    try:
        descriptor = open_absolute_directory(path)
        metadata = os.fstat(descriptor)
    except OSError as error:
        raise AcceptanceFailed("EB-11 App-owned Profile directory is unsafe") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != PRIVATE_DIRECTORY_MODE
        or metadata.st_uid != os.geteuid()
    ):
        os.close(descriptor)
        raise AcceptanceFailed("EB-11 App-owned Profile directory is not private")
    return descriptor, (metadata.st_dev, metadata.st_ino)


def read_process_open_paths(records: list[ProcessRecord]) -> list[Path] | None:
    process_ids = ",".join(str(record.pid) for record in records)
    try:
        rendered = run_checked(
            ["/usr/sbin/lsof", "-nP", "-Fn", "-p", process_ids],
            capture=True,
        ).stdout
    except subprocess.CalledProcessError as error:
        current = process_snapshot()
        if any(record not in current for record in records):
            return None
        raise AcceptanceFailed(
            "EB-11 could not audit stable Chromium open files"
        ) from error
    return [Path(line[1:]) for line in rendered.splitlines() if line.startswith("n/")]


def require_browser_profile_boundary(
    contract: RuntimeContract,
    browser: ProcessRecord,
    scoped_records: list[ProcessRecord],
) -> ProfileDirectoryBinding | None:
    candidate = profile_argument(contract, browser)
    if candidate is None:
        return None
    directories = (
        contract.profile_root,
        contract.profile_root / "douyin",
        candidate,
    )
    opened: list[int] = []
    first_identities: list[tuple[int, int]] = []
    try:
        for directory in directories:
            descriptor, identity = require_private_directory_identity(directory)
            opened.append(descriptor)
            first_identities.append(identity)

        browser_tree = descendant_records(browser.pid, scoped_records)
        opened_paths = read_process_open_paths(browser_tree)
        if opened_paths is None:
            return None
        if not any(path_is_within(path, candidate) for path in opened_paths):
            raise AcceptanceFailed("EB-11 Chromium did not open the App-owned Profile")
        if any(
            path_is_within(path, default_root)
            for path in opened_paths
            for default_root in DEFAULT_BROWSER_PROFILE_ROOTS
        ):
            raise AcceptanceFailed("EB-11 Chromium opened a default browser Profile")

        for directory, first_identity in zip(
            directories,
            first_identities,
            strict=True,
        ):
            reopened, second_identity = require_private_directory_identity(directory)
            try:
                if second_identity != first_identity:
                    raise AcceptanceFailed("EB-11 App-owned Profile identity changed")
            finally:
                os.close(reopened)
        if browser not in process_snapshot():
            raise AcceptanceFailed("EB-11 embedded Chromium process identity changed")
        binding = ProfileDirectoryBinding(
            path=candidate,
            parent_fd=opened[1],
            directory_fd=opened[2],
            parent_identity=first_identities[1],
            identity=first_identities[2],
        )
        opened = opened[:1]
        return binding
    finally:
        for descriptor in opened:
            os.close(descriptor)


def observe_runtime(
    contract: RuntimeContract,
    records: list[ProcessRecord],
    verified_profile_directories: tuple[Path, ...] = (),
    profile_bindings: tuple[ProfileDirectoryBinding, ...] = (),
) -> RuntimeObservation:
    executor_observed = any(
        command_runs(record.command, contract.executor_path) for record in records
    )
    browser_records = [
        record for record in records if command_runs(record.command, contract.browser_path)
    ]
    profile_directories = tuple(
        sorted(
            set(verified_profile_directories),
            key=os.fspath,
        )
    )

    return RuntimeObservation(
        executor_observed=executor_observed,
        embedded_browser_observed=bool(browser_records),
        app_owned_profile_observed=bool(profile_directories),
        profile_directories=profile_directories,
        profile_bindings=profile_bindings,
    )


def observe_instance_runtime(
    contract: RuntimeContract,
    instance: ProcessRecord,
) -> RuntimeObservation:
    records = process_snapshot()
    scoped = instance_process_records(contract.app_path, instance, records)
    if contract.executor_identity is None or contract.browser_identity is None:
        raise AcceptanceFailed("EB-11 packaged runtime code identity is unavailable")
    executor_records = [
        record for record in scoped if command_runs(record.command, contract.executor_path)
    ]
    browser_records = [
        record for record in scoped if command_runs(record.command, contract.browser_path)
    ]
    if len(executor_records) > 1 or len(browser_records) > 1:
        raise AcceptanceFailed("EB-11 observed duplicate packaged runtime processes")
    if browser_records:
        if len(executor_records) != 1:
            raise AcceptanceFailed("EB-11 Chromium did not descend from the packaged Executor")
        executor_tree = descendant_records(executor_records[0].pid, scoped)
        if browser_records[0] not in executor_tree:
            raise AcceptanceFailed("EB-11 Chromium did not descend from the packaged Executor")
    verified_executor_records = [
        record
        for record in executor_records
        if verify_runtime_process_identity(record, contract.executor_identity)
    ]
    verified_browser_records = [
        record
        for record in browser_records
        if verify_runtime_process_identity(record, contract.browser_identity)
    ]
    verified_bindings = tuple(
        binding
        for record in verified_browser_records
        if (
            binding := require_browser_profile_boundary(
                contract,
                record,
                scoped,
            )
        )
        is not None
    )
    transient_ids = {
        record.pid
        for record in [
            *executor_records,
            *browser_records,
        ]
        if record not in [*verified_executor_records, *verified_browser_records]
    }
    verified_scoped = [record for record in scoped if record.pid not in transient_ids]
    return observe_runtime(
        contract,
        verified_scoped,
        tuple(binding.path for binding in verified_bindings),
        verified_bindings,
    )


def verify_runtime_process_identity(
    record: ProcessRecord,
    expected: CodeIdentity,
) -> bool:
    if record not in process_snapshot():
        return False
    process_id = str(record.pid)
    try:
        run_checked(
            ["codesign", "--verify", "--strict", "--verbose=4", process_id],
            capture=True,
        )
        signature = run_checked(
            ["codesign", "--display", "--verbose=4", process_id],
            capture=True,
        )
    except subprocess.CalledProcessError as error:
        if record not in process_snapshot():
            return False
        raise AcceptanceFailed(
            "EB-11 packaged runtime process signature could not be verified"
        ) from error
    observed = code_identity_from_details(
        f"{signature.stdout}\n{signature.stderr}",
        authority=expected.authority,
        team_id=expected.team_id,
    )
    if record not in process_snapshot():
        return False
    if observed != expected:
        raise AcceptanceFailed("EB-11 packaged runtime process does not match the signed App")
    return True


class _RuntimeWatcher:
    """Samples the packaged runtime on its own thread for as long as it is open."""

    def __init__(self, contract: RuntimeContract, instance: ProcessRecord) -> None:
        self._contract = contract
        self._instance = instance
        self._stop = threading.Event()
        self._observation = RuntimeObservation()
        self._failure: BaseException | None = None
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                sample = observe_instance_runtime(self._contract, self._instance)
            except BaseException as error:  # 存下来，由 result() 在调用方线程上重抛
                with self._lock:
                    if self._failure is None:
                        self._failure = error
                return
            with self._lock:
                self._observation = self._observation.merge(sample)
            self._stop.wait(RUNTIME_SAMPLE_SECONDS)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=RUNTIME_WATCHER_JOIN_SECONDS)

    def result(self) -> RuntimeObservation:
        with self._lock:
            if self._failure is not None:
                raise self._failure
            return self._observation


@contextlib.contextmanager
def continuous_runtime_observation(
    contract: RuntimeContract,
    instance: ProcessRecord,
) -> Iterator[_RuntimeWatcher]:
    """Observe the packaged runtime *while* something slow happens in the caller.

    The samples used to be taken once per iteration of the same loop that calls
    `visible_ui_text()`, and that call walks the whole accessibility tree of the
    front window — by far the slowest step in the loop. The login-state recheck
    is carried out by the packaged Executor launching the embedded Chromium
    through Playwright, and that process is short-lived: on 2026-08-04 it started
    and exited inside one such gap, and `require_complete_runtime()` reported
    `did not observe the embedded Chromium` for an App that had done exactly what
    it was asked.

    Sampling faster inside that loop would not fix it — the loop is blocked in
    AppleScript, not sleeping. The observation has to run on its own thread, so
    that is what this does.
    """
    watcher = _RuntimeWatcher(contract, instance)
    watcher.start()
    try:
        yield watcher
    finally:
        watcher.close()


def require_complete_runtime(observation: RuntimeObservation) -> None:
    if not observation.executor_observed:
        raise AcceptanceFailed("EB-11 did not observe the packaged Executor")
    if not observation.embedded_browser_observed:
        raise AcceptanceFailed("EB-11 did not observe the embedded Chromium")
    if not observation.app_owned_profile_observed:
        raise AcceptanceFailed("EB-11 Chromium did not use the App-owned Profile")
    if len(observation.profile_directories) != 1:
        raise AcceptanceFailed("EB-11 did not observe exactly one App-owned Profile")


def require_same_profile_reuse(
    qr_open: RuntimeObservation,
    qr_recheck: RuntimeObservation,
    restarted: RuntimeObservation,
) -> None:
    """Bind QR confirmation and restart health to one exact Profile inode."""

    identities: list[tuple[Path, tuple[int, int], tuple[int, int]]] = []
    for observation in (qr_open, qr_recheck, restarted):
        require_complete_runtime(observation)
        if len(observation.profile_bindings) != 1:
            raise AcceptanceFailed(
                "EB-11 session reuse did not retain exactly one Profile identity"
            )
        binding = observation.profile_bindings[0]
        if (
            binding.parent_fd < 0
            or binding.directory_fd < 0
            or binding.path != observation.profile_directories[0]
        ):
            raise AcceptanceFailed("EB-11 session reuse Profile identity is unavailable")
        identities.append((binding.path, binding.parent_identity, binding.identity))
    if any(identity != identities[0] for identity in identities[1:]):
        raise AcceptanceFailed(
            "EB-11 QR login and restart did not reuse the same App-owned Profile"
        )


def require_observed_profile_unlinked(binding: ProfileDirectoryBinding) -> None:
    """Prove the observed directory inode has no surviving filesystem name."""

    if binding.parent_fd < 0 or binding.directory_fd < 0:
        raise AcceptanceFailed("EB-11 observed Profile identity handle is closed")
    parent = os.fstat(binding.parent_fd)
    profile = os.fstat(binding.directory_fd)
    if (parent.st_dev, parent.st_ino) != binding.parent_identity or (
        profile.st_dev,
        profile.st_ino,
    ) != binding.identity:
        raise AcceptanceFailed("EB-11 observed Profile identity changed")
    for name in os.listdir(binding.parent_fd):
        try:
            candidate = os.stat(name, dir_fd=binding.parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if (candidate.st_dev, candidate.st_ino) == binding.identity:
            raise AcceptanceFailed("EB-11 safe logout retained the old Profile inode")
    try:
        macos_fcntl = importlib.import_module("fcntl")
        raw_path = macos_fcntl.fcntl(
            binding.directory_fd,
            macos_fcntl.F_GETPATH,
            b"\0" * 1024,
        )
        current_path = Path(os.fsdecode(raw_path.split(b"\0", 1)[0]))
        current = current_path.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise AcceptanceFailed("EB-11 could not verify the removed Profile inode") from error
    if (current.st_dev, current.st_ino) == binding.identity:
        raise AcceptanceFailed("EB-11 safe logout retained the old Profile inode")


def require_safe_logout_cleanup(
    contract: RuntimeContract,
    instance: ProcessRecord,
    before_logout: RuntimeObservation,
) -> None:
    """Prove logout removed the exact Profile observed through Chromium argv.

    This deliberately does not read the current marker, browser storage, Cookie
    database, or Profile contents.  The old UUID comes from the packaged
    Chromium process used during the healthy recheck.  After the App publishes
    `missing`, neither that directory, its staged-removal name/lock, the current
    marker, nor a process still using the old `--user-data-dir` may remain.
    """

    require_complete_runtime(before_logout)
    if len(before_logout.profile_bindings) != 1:
        raise AcceptanceFailed("EB-11 logout did not retain the observed Profile identity")
    old_profile = before_logout.profile_directories[0]
    binding = before_logout.profile_bindings[0]
    if binding.path != old_profile:
        raise AcceptanceFailed("EB-11 logout Profile identity does not match its path")
    expected_parent = contract.profile_root / "douyin"
    if old_profile.parent != expected_parent:
        raise AcceptanceFailed("EB-11 observed Profile escaped the App-owned root")
    staged_profile = old_profile.with_name(f".removing-{old_profile.name}")
    profile_lease = old_profile.parent / f"{PROFILE_LEASE_FILE_PREFIX}{old_profile.name}"
    residual_paths = (
        old_profile,
        staged_profile,
        profile_lease,
        contract.profile_root / CURRENT_DOUYIN_PROFILE_FILE,
    )
    for path in residual_paths:
        try:
            os.lstat(path)
        except FileNotFoundError:
            continue
        raise AcceptanceFailed("EB-11 safe logout left the old App-owned Profile behind")
    require_observed_profile_unlinked(binding)

    records = process_snapshot()
    scoped = instance_process_records(contract.app_path, instance, records)
    if instance not in scoped:
        raise AcceptanceFailed("EB-11 formal App exited during safe logout")
    for record in scoped:
        arguments = [
            match.group("value")
            for match in USER_DATA_DIRECTORY_PATTERN.finditer(record.command)
        ]
        if os.fspath(old_profile) in arguments:
            raise AcceptanceFailed("EB-11 safe logout left a process using the old Profile")


def require_isolated_launch(app: Path) -> None:
    if bundle_process_ids() or packaged_process_records(app, process_snapshot()):
        raise AcceptanceFailed(
            "EB-11 refuses to share a running production App with another session"
        )


def process_has_launch_nonce(record: ProcessRecord, nonce: str) -> bool:
    try:
        rendered = run_checked(
            ["/bin/ps", "eww", "-p", str(record.pid), "-o", "command="],
            capture=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return f"{LAUNCH_NONCE_ENVIRONMENT}={nonce}" in rendered.split()


def verify_running_release_process(
    instance: ProcessRecord,
    release: VerifiedRelease,
) -> None:
    if instance not in process_snapshot():
        raise AcceptanceFailed("EB-11 formal App process identity changed")
    process_id = str(instance.pid)
    run_checked(
        ["codesign", "--verify", "--deep", "--strict", "--verbose=4", process_id],
        capture=True,
    )
    signature = run_checked(
        ["codesign", "--display", "--verbose=4", process_id],
        capture=True,
    )
    details = f"{signature.stdout}\n{signature.stderr}"
    expected = release.artifact
    if (
        f"Identifier={release.app_identity.bundle_identifier}" not in details.splitlines()
        or f"Authority={expected.authority}" not in details.splitlines()
        or f"TeamIdentifier={expected.team_id}" not in details.splitlines()
        or f"CDHash={expected.bundle_cdhash}" not in details.splitlines()
    ):
        raise AcceptanceFailed(
            "EB-11 running App process does not match the verified release"
        )
    if instance not in process_snapshot():
        raise AcceptanceFailed("EB-11 formal App process identity changed")


def launch_app(app: Path, executable_path: Path) -> ProcessRecord:
    nonce = secrets.token_urlsafe(24)
    identified: dict[tuple[int, str], ProcessRecord] = {}
    try:
        run_checked(
            [
                "/usr/bin/open",
                "-n",
                "-a",
                os.fspath(app),
                "--env",
                f"{LAUNCH_NONCE_ENVIRONMENT}={nonce}",
            ]
        )
        deadline = time.monotonic() + WINDOW_TIMEOUT_SECONDS
        expected_command = os.fspath(executable_path)
        while time.monotonic() < deadline:
            records = packaged_process_records(app, process_snapshot())
            main_records = [
                record
                for record in records
                if record.command == expected_command
                or record.command.startswith(f"{expected_command} ")
            ]
            for record in main_records:
                if process_has_launch_nonce(record, nonce):
                    identified[(record.pid, record.started_at)] = ProcessRecord(
                        record.pid,
                        record.ppid,
                        record.command,
                        record.started_at,
                        launch_nonce=nonce,
                    )
            own_records = list(identified.values())
            if len(own_records) == 1:
                instance = own_records[0]
                if len(main_records) != 1:
                    raise AcceptanceFailed("EB-11 launched alongside another App process")
                visible_ids = bundle_process_ids()
                if visible_ids and visible_ids != {instance.pid}:
                    raise AcceptanceFailed("EB-11 launched an ambiguous App process")
                instance_process_records(app, instance, process_snapshot())
                return instance
            if len(own_records) > 1:
                raise AcceptanceFailed("EB-11 launched more than one owned App process")
            time.sleep(POLL_SECONDS)
        raise AcceptanceFailed("EB-11 formal App process did not start")
    except (AcceptanceFailed, OSError, subprocess.SubprocessError, KeyboardInterrupt):
        if identified:
            for instance in identified.values():
                cleanup_owned_runtime(app, instance)
        else:
            cleanup_delayed_nonce_owned_runtime(app, nonce)
        raise


def cleanup_delayed_nonce_owned_runtime(app: Path, nonce: str) -> None:
    """Reclaim an App that LaunchServices materialises after launch was cancelled."""

    deadline = time.monotonic() + LAUNCH_CLEANUP_DISCOVERY_SECONDS
    while time.monotonic() < deadline:
        nonce_owned = nonce_owned_process_records(app, nonce)
        if nonce_owned:
            cleanup_owned_runtime(app, None, nonce_owned)
        time.sleep(POLL_SECONDS)
    quiet_polls = 0
    while quiet_polls < LAUNCH_CLEANUP_FINAL_QUIET_POLLS:
        residual = nonce_owned_process_records(app, nonce)
        if residual:
            cleanup_owned_runtime(app, None, residual)
            quiet_polls = 0
        else:
            quiet_polls += 1
        if quiet_polls < LAUNCH_CLEANUP_FINAL_QUIET_POLLS:
            time.sleep(POLL_SECONDS)


def wait_for_window(process_id: int) -> None:
    deadline = time.monotonic() + WINDOW_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        result = apple_script(
            accessibility_process_script(
                process_id,
                '    if (count of windows) > 0 then return "ready"\n    return "waiting"',
            )
        )
        if result == "ready":
            return
        if result in {"__NO_PROCESS__", "__WRONG_APP__"}:
            raise AcceptanceFailed("EB-11 formal App process identity changed")
        time.sleep(POLL_SECONDS)
    raise AcceptanceFailed("EB-11 formal App did not expose its normal main window")


def visible_ui_text(process_id: int) -> str:
    body = """
    if (count of windows) is 0 then return "__NO_WINDOW__"
    set collected to {}
    set allElements to entire contents of front window
    repeat with elementReference in allElements
      try
        set elementName to name of elementReference
        if elementName is not missing value and elementName is not "" then
          set end of collected to (elementName as text)
        end if
      end try
      try
        set elementValue to value of elementReference
        if elementValue is not missing value and elementValue is not "" then
          set renderedValue to elementValue as text
          if collected does not contain renderedValue then
            set end of collected to renderedValue
          end if
        end if
      end try
    end repeat
    return collected as text
"""
    rendered = apple_script(accessibility_process_script(process_id, body))
    if rendered in {"__NO_PROCESS__", "__WRONG_APP__", "__NO_WINDOW__"}:
        raise AcceptanceFailed("EB-11 formal App window disappeared")
    return rendered


def press(process_id: int, label: str) -> None:
    """Press one named control, refusing to mistake a disabled one for a click.

    `AXPress` on a disabled element does nothing, and a disabled element still
    carries its accessibility name — so matching on the name alone made "pressed
    it" and "pressed a greyed-out button" produce the identical result. All three
    controls this drives (`打开登录处理`, `我已处理，重新检查`, `安全注销`) carry
    `disabled={pending !== null}` in `PlatformSessions.tsx`, so that state is
    reached on every one of them while a previous action is still in flight.

    2026-08-04 is when it cost something: the run reported the App had been
    driven while the App had not moved at all. The enabled check is what makes
    a negative answer distinguishable from an unasked question.
    """
    escaped = label.replace('"', '\\"')
    body = f'''
    if (count of windows) is 0 then return "__NO_WINDOW__"
    set allElements to entire contents of front window
    repeat with elementReference in allElements
      try
        set elementName to name of elementReference
        if elementName is not missing value and (elementName as text) is "{escaped}" then
          set controlEnabled to true
          try
            set controlEnabled to (enabled of elementReference) as boolean
          end try
          if controlEnabled is false then return "disabled"
          perform action "AXPress" of elementReference
          return "pressed"
        end if
      end try
    end repeat
    return "not_found"
'''
    result = apple_script(accessibility_process_script(process_id, body))
    if result == "disabled":
        raise AcceptanceFailed(
            f"EB-11 App control is present but disabled, so it was not pressed: {label}"
        )
    if result != "pressed":
        raise AcceptanceFailed(f"EB-11 could not press the App control: {label}")


def wait_for_text(
    process_id: int,
    marker: str,
    *,
    timeout: float = WINDOW_TIMEOUT_SECONDS,
) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        latest = visible_ui_text(process_id)
        if UNAVAILABLE_CODE in latest:
            raise AcceptanceFailed(
                "EB-11 formal App exposed process_unavailable on the normal user path"
            )
        if marker in latest:
            return latest
        time.sleep(POLL_SECONDS)
    raise AcceptanceFailed(f"EB-11 App did not expose required UI state: {marker}")


def open_account_page(process_id: int) -> tuple[str, bool]:
    """Land on the account page and report whether a session is already there.

    Returns `(rendered_text, already_signed_in)`.

    This used to wait for `登录正常` and nothing else, which made the whole run
    depend on a session existing before it started. On a clean machine — the
    state a real user is in, and the one EB-17 exists to verify — the App shows
    `需要登录`, so the wait ran to its timeout and the run died with
    `did not expose required UI state: 登录正常` without ever offering a QR code.
    The lifecycle this script verifies (recheck → safe logout → rescan → restart
    reuse) still needs a session to start from; what changed is that the script
    now *observes* whether it has one instead of assuming it, and the caller
    establishes one by scanning when it does not.
    """
    deadline = time.monotonic() + WINDOW_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            press(process_id, ACCOUNT_PAGE_LABEL)
        except AcceptanceFailed:
            latest = visible_ui_text(process_id)
            if UNAVAILABLE_CODE in latest:
                raise AcceptanceFailed(
                    "EB-11 formal App exposed process_unavailable before account navigation"
                ) from None
            time.sleep(POLL_SECONDS)
            continue
        landing_deadline = time.monotonic() + ACTION_TIMEOUT_SECONDS
        while time.monotonic() < landing_deadline:
            latest = visible_ui_text(process_id)
            if UNAVAILABLE_CODE in latest:
                raise AcceptanceFailed(
                    "EB-11 formal App exposed process_unavailable on the normal user path"
                )
            if HEALTHY_LABEL in latest:
                return latest, True
            if LOGIN_REQUIRED_LABEL in latest:
                return latest, False
            time.sleep(POLL_SECONDS)
        raise AcceptanceFailed(
            "EB-11 account page settled on neither a signed-in nor a signed-out state"
        )
    raise AcceptanceFailed("EB-11 account navigation did not become ready")


def logout_current_session(process_id: int) -> str:
    press(process_id, LOGOUT_LABEL)
    wait_for_text(process_id, CONFIRM_LOGOUT_LABEL)
    press(process_id, CONFIRM_LOGOUT_LABEL)
    return wait_for_text(
        process_id,
        LOGIN_REQUIRED_LABEL,
        timeout=ACTION_TIMEOUT_SECONDS,
    )


def open_login_for_scan(
    process_id: int,
    runtime_contract: RuntimeContract,
    app_instance: ProcessRecord,
) -> tuple[str, RuntimeObservation]:
    press(process_id, OPEN_LOGIN_LABEL)
    deadline = time.monotonic() + ACTION_TIMEOUT_SECONDS
    observation = RuntimeObservation()
    try:
        # 采样与界面读取分开跑：`visible_ui_text()` 遍历整棵可访问性树，
        # 短命的 Chromium 会整个落在两次调用之间。
        with continuous_runtime_observation(runtime_contract, app_instance) as watcher:
            while time.monotonic() < deadline:
                latest = visible_ui_text(process_id)
                if UNAVAILABLE_CODE in latest:
                    raise AcceptanceFailed(
                        "EB-11 formal App exposed process_unavailable while opening QR login"
                    )
                if HEALTHY_LABEL in latest:
                    raise AcceptanceFailed(
                        "EB-11 QR login unexpectedly reused a session after safe logout"
                    )
                if any(marker in latest for marker in LOGIN_PROGRESS_MARKERS):
                    observation = observation.merge(watcher.result())
                    require_complete_runtime(observation)
                    return latest, observation
                time.sleep(POLL_SECONDS)
            observation = observation.merge(watcher.result())
        raise AcceptanceFailed("EB-11 formal App did not expose the real QR login flow")
    except BaseException:
        observation.close()
        raise


def confirm_scan_checkpoint() -> None:
    print("[EB-11] 请在正式 App 打开的内置运营浏览器中扫描抖音二维码，并在手机端完成确认。")
    confirmation = input(f"[EB-11] 登录完成后请输入 {SCAN_CHECKPOINT} 并回车：").strip()
    if confirmation != SCAN_CHECKPOINT:
        raise AcceptanceFailed("EB-11 real QR login confirmation was not exact")


def observed_revision(text: str) -> str:
    match = OBSERVED_AT_PATTERN.search(text)
    if match is None:
        raise AcceptanceFailed("EB-11 App did not expose one structured last-check time")
    return match.group(1).strip(" ,，")


def observed_time(text: str) -> datetime:
    revision = observed_revision(text)
    match = OBSERVED_REVISION_PATTERN.fullmatch(revision)
    if match is None:
        raise AcceptanceFailed("EB-11 App exposed an invalid last-check time")
    month = match.group("month") or match.group("month_cn")
    day = match.group("day") or match.group("day_cn")
    if month is None or day is None:
        raise AcceptanceFailed("EB-11 App exposed an invalid last-check date")
    hour = int(match.group("hour"))
    period = match.group("period")
    if period is not None:
        if not 1 <= hour <= 12:
            raise AcceptanceFailed("EB-11 App exposed an invalid 12-hour clock")
        if period == "上午":
            hour %= 12
        elif hour < 12:
            hour += 12
    try:
        return datetime(
            int(match.group("year")),
            int(month),
            int(day),
            hour,
            int(match.group("minute")),
            int(match.group("second") or "0"),
        )
    except ValueError:
        raise AcceptanceFailed("EB-11 App exposed an invalid last-check time") from None


def recheck_healthy_session(
    process_id: int,
    before_text: str,
    runtime_contract: RuntimeContract | None = None,
    app_instance: ProcessRecord | None = None,
) -> tuple[str, str, RuntimeObservation]:
    if (runtime_contract is None) != (app_instance is None):
        raise AcceptanceFailed("EB-11 runtime observation scope is incomplete")
    before = observed_revision(before_text)
    before_time = observed_time(before_text)
    observation = RuntimeObservation()
    try:
        time.sleep(1.1)
        # 复查由执行器经 Playwright 拉起内置 Chromium 完成，那个进程很短命；
        # 采样必须独立于下面这条含 `visible_ui_text()` 的慢循环。
        watching: contextlib.AbstractContextManager[_RuntimeWatcher | None]
        if runtime_contract is not None and app_instance is not None:
            watching = continuous_runtime_observation(runtime_contract, app_instance)
        else:
            watching = contextlib.nullcontext(None)
        with watching as watcher:
            press(process_id, RECHECK_LABEL)
            deadline = time.monotonic() + ACTION_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                latest = visible_ui_text(process_id)
                if UNAVAILABLE_CODE in latest:
                    raise AcceptanceFailed(
                        "EB-11 formal App exposed process_unavailable on the normal user path"
                    )
                if HEALTHY_LABEL in latest and "最近检查" in latest:
                    after = observed_revision(latest)
                    if observed_time(latest) > before_time:
                        if watcher is not None:
                            observation = observation.merge(watcher.result())
                            require_complete_runtime(observation)
                        return before, after, observation
                time.sleep(POLL_SECONDS)
            if watcher is not None:
                observation = observation.merge(watcher.result())
        raise AcceptanceFailed(
            "EB-11 session recheck did not publish a newer healthy visible revision"
        )
    except BaseException:
        observation.close()
        raise


def terminate_records(records: list[ProcessRecord], signal_number: int) -> None:
    for record in records:
        if not still_running([record]):
            continue
        try:
            os.kill(record.pid, signal_number)
        except ProcessLookupError:
            continue


def cleanup_owned_runtime(
    app: Path,
    instance: ProcessRecord | None,
    tracked: list[ProcessRecord] | None = None,
) -> None:
    expected = {record.pid: record for record in (tracked or [])}
    expected.update(
        (record.pid, record)
        for record in owned_process_records(app, instance, reject_foreign=False)
    )
    records = list(expected.values())
    if not records:
        return
    root_pid = instance.pid if instance is not None else None
    root = [record for record in records if record.pid == root_pid]
    descendants = [record for record in records if record.pid != root_pid]
    terminate_records(root + descendants, signal.SIGTERM)
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        residual = still_running(records)
        residual.extend(owned_process_records(app, instance, reject_foreign=False))
        if not {record.pid for record in residual}:
            return
        time.sleep(0.2)
    residual_by_pid = {
        record.pid: record
        for record in [
            *still_running(records),
            *owned_process_records(app, instance, reject_foreign=False),
        ]
    }
    terminate_records(list(residual_by_pid.values()), signal.SIGKILL)
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        residual = still_running(records)
        residual.extend(owned_process_records(app, instance, reject_foreign=False))
        residual_by_pid = {record.pid: record for record in residual}
        if not residual_by_pid:
            return
        terminate_records(list(residual_by_pid.values()), signal.SIGKILL)
        time.sleep(0.2)
    raise AcceptanceFailed("EB-11 could not clean its owned App process tree")


def quit_app(app: Path, instance: ProcessRecord) -> None:
    process_id = instance.pid
    tracked = owned_process_records(app, instance)
    if instance not in tracked:
        cleanup_owned_runtime(app, instance, tracked)
        raise AcceptanceFailed("EB-11 formal App exited before normal quit")
    try:
        result = apple_script(
            f'''
tell application "System Events"
  set matches to every application process whose unix id is {process_id}
  if (count of matches) is not 1 then return "not_running"
  set productProcess to item 1 of matches
  if bundle identifier of productProcess is not "{APP_IDENTIFIER}" then return "wrong_app"
  set frontmost of productProcess to true
  keystroke "q" using {{command down}}
  return "requested"
end tell
'''
        )
        if result != "requested":
            raise AcceptanceFailed("EB-11 could not request normal App quit")
        deadline = time.monotonic() + QUIT_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            residual = still_running(tracked)
            residual.extend(owned_process_records(app, instance))
            if not {record.pid for record in residual}:
                return
            time.sleep(POLL_SECONDS)
        raise AcceptanceFailed("EB-11 App quit left an Executor or Chromium process behind")
    except (AcceptanceFailed, OSError, subprocess.SubprocessError):
        cleanup_owned_runtime(app, instance, tracked)
        raise


def open_absolute_directory(path: Path) -> int:
    if not path.is_absolute():
        raise AcceptanceFailed("EB-11 directory path must be absolute")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(os.sep, directory_flags)
    try:
        for part in path.parts[1:]:
            next_descriptor = os.open(
                part,
                directory_flags,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def open_evidence_target(path: Path) -> EvidenceTarget:
    if not path.is_absolute() or path.name in {"", ".", ".."}:
        raise AcceptanceFailed("EB-11 evidence target is invalid")
    resolved_path = path.parent.resolve(strict=True) / path.name
    try:
        descriptor = open_absolute_directory(resolved_path.parent)
    except OSError as error:
        raise AcceptanceFailed("EB-11 evidence directory is unavailable") from error
    try:
        try:
            os.stat(resolved_path.name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise AcceptanceFailed("EB-11 refuses to overwrite evidence")
        return EvidenceTarget(
            path=resolved_path,
            parent_fd=descriptor,
            name=resolved_path.name,
        )
    except BaseException:
        os.close(descriptor)
        raise


def open_protected_evidence_target(app: Path, path: Path) -> EvidenceTarget:
    """Open evidence, then recheck the directory the held descriptor resolved to."""

    target = open_evidence_target(path)
    try:
        require_evidence_outside_app(app, target.path)
    except BaseException:
        target.close()
        raise
    return target


def require_evidence_parent_binding(target: EvidenceTarget) -> None:
    """Reopen the declared path and bind it to the already trusted directory FD."""

    if target.parent_fd < 0:
        raise AcceptanceFailed("EB-11 evidence directory handle is closed")
    try:
        current_descriptor = open_absolute_directory(target.path.parent)
    except OSError as error:
        raise AcceptanceFailed("EB-11 evidence directory path changed") from error
    try:
        expected = os.fstat(target.parent_fd)
        current = os.fstat(current_descriptor)
        if (expected.st_dev, expected.st_ino) != (current.st_dev, current.st_ino):
            raise AcceptanceFailed("EB-11 evidence directory path changed")
    finally:
        os.close(current_descriptor)


def unlink_owned_file(
    target: EvidenceTarget,
    name: str,
    identity: tuple[int, int] | None,
) -> None:
    if identity is None:
        return
    try:
        metadata = os.stat(
            name,
            dir_fd=target.parent_fd,
            follow_symlinks=False,
        )
        if stat.S_ISREG(metadata.st_mode) and (metadata.st_dev, metadata.st_ino) == identity:
            os.unlink(name, dir_fd=target.parent_fd)
    except FileNotFoundError:
        return


def write_evidence(
    target: EvidenceTarget,
    document: dict[str, object],
) -> tuple[int, int]:
    if target.parent_fd < 0:
        raise AcceptanceFailed("EB-11 evidence directory handle is closed")
    require_evidence_parent_binding(target)
    payload = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode()
    temporary = f".{target.name}.{secrets.token_hex(12)}.tmp"
    descriptor = -1
    identity: tuple[int, int] | None = None
    published = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            mode=0o600,
            dir_fd=target.parent_fd,
        )
        opened = os.fstat(descriptor)
        identity = (opened.st_dev, opened.st_ino)
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("EB-11 evidence write did not advance")
            offset += written
        os.fsync(descriptor)
        completed = os.fstat(descriptor)
        if (
            not stat.S_ISREG(completed.st_mode)
            or stat.S_IMODE(completed.st_mode) != 0o600
            or completed.st_size != len(payload)
            or (completed.st_dev, completed.st_ino) != identity
        ):
            raise AcceptanceFailed("EB-11 temporary evidence verification failed")
        os.close(descriptor)
        descriptor = -1

        require_evidence_parent_binding(target)
        os.link(
            temporary,
            target.name,
            src_dir_fd=target.parent_fd,
            dst_dir_fd=target.parent_fd,
            follow_symlinks=False,
        )
        published = True
        published_metadata = os.stat(
            target.name,
            dir_fd=target.parent_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(published_metadata.st_mode)
            or stat.S_IMODE(published_metadata.st_mode) != 0o600
            or published_metadata.st_size != len(payload)
            or (published_metadata.st_dev, published_metadata.st_ino) != identity
        ):
            raise AcceptanceFailed("EB-11 published evidence verification failed")
        os.unlink(temporary, dir_fd=target.parent_fd)
        os.fsync(target.parent_fd)
        require_evidence_parent_binding(target)
        return identity
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        if published:
            unlink_owned_file(target, target.name, identity)
        unlink_owned_file(target, temporary, identity)
        raise


def run_acceptance(arguments: Arguments) -> EvidencePublication:
    app, evidence = require_device_boundary(arguments)
    evidence_target = open_protected_evidence_target(app, evidence)
    owned_instance: ProcessRecord | None = None
    launch_attempted = False
    evidence_identity: tuple[int, int] | None = None
    runtime_observations: list[RuntimeObservation] = []
    try:
        try:
            verified_release = verify_release_artifact(app, arguments.deployment_profile)
            identity = verified_release.app_identity
            profile_root = verified_release.profile_root
            runtime_contract = verified_release.runtime_contract
            artifact = verified_release.artifact
            require_isolated_launch(app)
            launch_attempted = True
            owned_instance = launch_app(app, identity.executable_path)
            verify_running_release_process(owned_instance, verified_release)
            wait_for_window(owned_instance.pid)
            initial, already_signed_in = open_account_page(owned_instance.pid)
            if not already_signed_in:
                # 干净机的正常状态。先由操作者扫一次把登录态建立起来，之后的
                # 复查 → 安全注销 → 重扫 → 重启复用整条生命周期照常验证。
                # 这一步本身也是 EB-11 要求的「首次扫码登录」，不是绕过。
                print("[EB-11] 当前没有抖音登录态，先扫一次建立，随后照常验证整条生命周期。")
                initial, bootstrap_runtime = open_login_for_scan(
                    owned_instance.pid,
                    runtime_contract,
                    owned_instance,
                )
                runtime_observations.append(bootstrap_runtime)
                confirm_scan_checkpoint()
                _, _, bootstrap_recheck = recheck_healthy_session(
                    owned_instance.pid,
                    initial,
                    runtime_contract,
                    owned_instance,
                )
                runtime_observations.append(bootstrap_recheck)
                initial, already_signed_in = open_account_page(owned_instance.pid)
                if not already_signed_in:
                    raise AcceptanceFailed(
                        "EB-11 first scan did not leave the account healthy"
                    )
            first_before, first_after, first_runtime = recheck_healthy_session(
                owned_instance.pid,
                initial,
                runtime_contract,
                owned_instance,
            )
            runtime_observations.append(first_runtime)
            logged_out = logout_current_session(owned_instance.pid)
            if LOGIN_REQUIRED_LABEL not in logged_out or UNAVAILABLE_CODE in logged_out:
                raise AcceptanceFailed("EB-11 formal App did not publish the safe logout")
            require_safe_logout_cleanup(runtime_contract, owned_instance, first_runtime)
            login_started, qr_open_runtime = open_login_for_scan(
                owned_instance.pid,
                runtime_contract,
                owned_instance,
            )
            runtime_observations.append(qr_open_runtime)
            confirm_scan_checkpoint()
            qr_before, qr_after, qr_recheck_runtime = recheck_healthy_session(
                owned_instance.pid,
                login_started,
                runtime_contract,
                owned_instance,
            )
            runtime_observations.append(qr_recheck_runtime)
            verify_running_release_process(owned_instance, verified_release)
            quit_app(app, owned_instance)
            owned_instance = None
            launch_attempted = False

            require_isolated_launch(app)
            launch_attempted = True
            owned_instance = launch_app(app, identity.executable_path)
            verify_running_release_process(owned_instance, verified_release)
            wait_for_window(owned_instance.pid)
            restarted, reused_session = open_account_page(owned_instance.pid)
            if not reused_session or UNAVAILABLE_CODE in restarted:
                raise AcceptanceFailed("EB-11 formal App did not reuse the healthy session")
            restart_before, restart_after, restart_runtime = recheck_healthy_session(
                owned_instance.pid,
                restarted,
                runtime_contract,
                owned_instance,
            )
            runtime_observations.append(restart_runtime)
            require_same_profile_reuse(
                qr_open_runtime,
                qr_recheck_runtime,
                restart_runtime,
            )
            qr_runtime = qr_open_runtime.merge(qr_recheck_runtime)
            verify_running_release_process(owned_instance, verified_release)
            quit_app(app, owned_instance)
            owned_instance = None
            launch_attempted = False

            final_release = verify_release_artifact(app, arguments.deployment_profile)
            if final_release != verified_release:
                raise AcceptanceFailed("EB-11 formal App identity changed during acceptance")

            evidence_identity = write_evidence(
                evidence_target,
                {
                "schema": "eb-11.formal-app-session.v1",
                "acceptedAt": datetime.now(timezone.utc).isoformat(),
                "bundleIdentifier": identity.bundle_identifier,
                "appVersion": identity.version,
                "signingAuthority": artifact.authority,
                "signingTeamId": artifact.team_id,
                "bundleCdHash": artifact.bundle_cdhash,
                "bundleTreeSha256": artifact.bundle_tree_sha256,
                "bundleBytes": artifact.bundle_bytes,
                "executorBuildId": artifact.executor_build_id,
                "deploymentProfileId": profile_root.parent.name,
                "sourceGitCommit": verified_release.release_identity.source_git_commit,
                "sourceTreeSha256": verified_release.release_identity.source_tree_sha256,
                "releaseTarget": verified_release.release_identity.target,
                "releaseArchitecture": verified_release.release_identity.architecture,
                "developerIdSigned": True,
                "gatekeeperAccepted": True,
                "notarizationStapleValid": True,
                "normalAppPage": ACCOUNT_PAGE_LABEL,
                "normalAppAction": RECHECK_LABEL,
                "normalAppRecheckCount": 3,
                "visibleState": HEALTHY_LABEL,
                "firstVisibleObservedAtBefore": first_before,
                "firstVisibleObservedAtAfter": first_after,
                "qrVisibleObservedAtBefore": qr_before,
                "qrVisibleObservedAtAfter": qr_after,
                "restartVisibleObservedAtBefore": restart_before,
                "restartVisibleObservedAtAfter": restart_after,
                "restartSessionReused": True,
                "safeLogoutObserved": True,
                "realQrLoginConfirmed": True,
                "firstPackagedExecutorObserved": first_runtime.executor_observed,
                "firstEmbeddedChromiumObserved": (first_runtime.embedded_browser_observed),
                "firstAppOwnedProfileObserved": (first_runtime.app_owned_profile_observed),
                "qrPackagedExecutorObserved": qr_runtime.executor_observed,
                "qrEmbeddedChromiumObserved": qr_runtime.embedded_browser_observed,
                "qrAppOwnedProfileObserved": qr_runtime.app_owned_profile_observed,
                "restartPackagedExecutorObserved": restart_runtime.executor_observed,
                "restartEmbeddedChromiumObserved": (restart_runtime.embedded_browser_observed),
                "restartAppOwnedProfileObserved": (restart_runtime.app_owned_profile_observed),
                "ownedProcessTreeResidualCount": 0,
                "privateBrowserStateRead": False,
                },
            )
        finally:
            for observation in runtime_observations:
                observation.close()
            if launch_attempted:
                cleanup_owned_runtime(app, owned_instance)
        if evidence_identity is None:
            raise AcceptanceFailed("EB-11 evidence was not published")
        return EvidencePublication(evidence_target, evidence_identity)
    except BaseException:
        if evidence_identity is not None:
            unlink_owned_file(
                evidence_target,
                evidence_target.name,
                evidence_identity,
            )
        evidence_target.close()
        raise


def main() -> int:
    publication: EvidencePublication | None = None
    try:
        publication = run_acceptance(parse_arguments())
        publication.commit()
        print(
            f"[EB-11] PASS: formal App real-session evidence written to {publication.path}",
            flush=True,
        )
        publication.finish_report()
    except (AcceptanceFailed, OSError, subprocess.SubprocessError) as error:
        if publication is not None:
            publication.rollback()
        print(f"[EB-11] FAIL: {error}", file=sys.stderr)
        return 1
    except BaseException:
        if publication is not None:
            publication.rollback()
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
