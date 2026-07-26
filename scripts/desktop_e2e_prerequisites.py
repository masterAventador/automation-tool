#!/usr/bin/env python3
"""Shared startup-gate preparation for the `control-plane-e2e` desktop E2E layer.

Every acceptance App in this layer compiles the production startup gate, so it
refuses to mount the workbench unless all four of these hold:

1. the compile-time action authorization triple is baked into the binary —
   `startup_environment_state()` reads it first and returns
   `ConfigurationRequired` before it ever looks at the installed package;
2. a verified embedded-browser distribution sits in the resource directory the
   App runs from, or the gate reports `browser_component_missing` (EB-08);
3. a signed Local Executor package is installed under the App data directory;
4. the Control Plane the binary was *compiled* to call is listening — the origin
   is an `option_env!`, not a runtime setting.

Between 2026-07-22 (`199a021`) and 2026-07-26 no driver in this layer supplied
(1), and none ever supplied (2), so the entire layer was blocked before its first
assertion while 27 task ledgers recorded a pass. Keeping the preparation here —
rather than as a paragraph copied into 30 drivers — is what makes the next change
to the gate reach all of them at once.

Nothing here ever terminates a process or frees a port that is in use.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Final

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT: Final = REPOSITORY_ROOT / "scripts"
FRONTEND_ROOT: Final = REPOSITORY_ROOT / "frontend"
PACKAGE_JSON: Final = FRONTEND_ROOT / "package.json"

# `tauri build --debug --no-bundle` produces a bare executable and Tauri treats
# the directory holding it as the resource directory, which is where the release
# resolver looks for the packaged embedded browser.
DEBUG_APP_RESOURCE_ROOT: Final = FRONTEND_ROOT / "src-tauri" / "target" / "debug"
EMBEDDED_BROWSER_DIRECTORY: Final = "embedded-browser"
DISTRIBUTION_MANIFEST_NAME: Final = "distribution-manifest.v1.json"

# EB-09 moved `BrowserProfileStore` to a new root and declared the development-era
# `browser-profiles` neither migrated nor read. Drivers that assert on the
# operations Profile have to name the root the App actually writes, and naming it
# once here keeps the next rename from silently splitting into N stale copies.
# The single definition lives in `frontend/src-tauri/src/browser_profiles.rs`.
OPERATIONS_PROFILE_ROOT: Final = "embedded-browser-profiles"
CURRENT_DOUYIN_PROFILE_FILE: Final = "current-douyin-profile-v1"

# Project-owned, traceable and outside every published port of this repository
# (Control Plane 8765, Vite 1420, PostgreSQL 5432/5433) as well as the loopback
# ephemeral range the drivers hand to PostgreSQL and WebDriver.
CONTROL_PLANE_PORT_RANGE: Final = range(18765, 18865)

CACHE_ROOT: Final = REPOSITORY_ROOT / ".local" / "desktop-e2e"
EMBEDDED_BROWSER_CACHE_ROOT: Final = CACHE_ROOT / "embedded-browser"
EXECUTOR_PACKAGE_CACHE_ROOT: Final = CACHE_ROOT / "executor-package"
EXECUTOR_MANIFEST_NAME: Final = "executor-manifest.v1.json"
EXECUTOR_MANIFEST_SIGNATURE_NAME: Final = "executor-manifest.v1.sig"
# The package contents are identical for every driver in this layer, so one
# cached build serves all of them; a driver that needs task-specific contents
# still builds its own.
SHARED_EXECUTOR_BUILD_ID: Final = "desktop-e2e-startup-gate"

# The development fixture the debug build's `executor_verifying_key` accepts.
# Identical to the value H8-16E/H8-16F/P9-03/P9-04 already compile in; it is a
# test signer, never a release key.
ACTION_AUTHORIZATION_PUBLIC_KEY: Final = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
# The local action floor. `ExecutorLedger` raises a Task's own interval to
# `max(stored, effective)`, so a large value here silently throttles every driver
# that performs more than one action, and the failure looks like the product's.
# 1 second is the smallest value the validator accepts, exercises the same
# rate-limit code path, and is what H8-16F — the only full user-journey driver —
# already compiles in.
LOCAL_ACTION_MINIMUM_INTERVAL_SECONDS: Final = "1"
LOCAL_ACTION_TASK_LIMIT: Final = "20"

LOCKED_BROWSER_ARCHIVES: Final = {
    "macos-arm64": (
        REPOSITORY_ROOT
        / ".local/embedded-browser-video-studio/eb-03-cache/chrome-mac-arm64.zip"
    ),
    "macos-x86_64": REPOSITORY_ROOT / ".local/eb-mac-x64/chrome-mac-x64.zip",
}

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

_reserved_control_plane_port: int | None = None


class DesktopPrerequisiteRejected(RuntimeError):
    """An acceptance App would start without something the startup gate needs."""


# --------------------------------------------------------------------------- #
# Compile-time gate inputs
# --------------------------------------------------------------------------- #


def control_plane_origin(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def startup_gate_environment(
    environment: Mapping[str, str], *, control_plane_port: int
) -> dict[str, str]:
    """Return a copy of `environment` carrying every compile-time gate input.

    Callers pass the isolated environment they already assembled (database
    credentials, bootstrap token, environment id); this only adds the four values
    `tauri build` bakes into the binary through `option_env!`. Setting a subset
    still produces a blocked App, which is why they are set together.
    """
    prepared = dict(environment)
    prepared.update(
        {
            "AUTOMATION_TOOL_CONTROL_PLANE_E2E_ORIGIN": control_plane_origin(
                control_plane_port
            ),
            "AUTOMATION_TOOL_ACTION_AUTHORIZATION_PUBLIC_KEY": (
                ACTION_AUTHORIZATION_PUBLIC_KEY
            ),
            "AUTOMATION_TOOL_LOCAL_ACTION_MINIMUM_INTERVAL_SECONDS": (
                LOCAL_ACTION_MINIMUM_INTERVAL_SECONDS
            ),
            "AUTOMATION_TOOL_LOCAL_ACTION_TASK_LIMIT": LOCAL_ACTION_TASK_LIMIT,
        }
    )
    return prepared


# --------------------------------------------------------------------------- #
# Ports
# --------------------------------------------------------------------------- #


def port_is_free(port: int) -> bool:
    """True when nothing holds this loopback port, including in `TIME_WAIT`.

    `SO_REUSEADDR` is deliberately not set: a port whose previous listener has
    not finished closing is treated as taken so the reservation moves on instead
    of racing it.
    """
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError:
            return False
    with socket.socket() as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", port)) != 0


def reserve_control_plane_port() -> int:
    """Reserve one project-owned, verified-free Control Plane port per process.

    23 drivers used to pin 8765 and hard-fail when it was taken, so a serial run
    lost 20 of them to `Errno 48` in under a second without ever building an App.
    Occupied ports are skipped, never reclaimed: the owner may belong to another
    project. Drivers that import one another share one Control Plane, so the
    reservation is made once per process.
    """
    global _reserved_control_plane_port
    if _reserved_control_plane_port is not None:
        return _reserved_control_plane_port
    for port in CONTROL_PLANE_PORT_RANGE:
        if port_is_free(port):
            _reserved_control_plane_port = port
            return port
    raise DesktopPrerequisiteRejected(
        "no free Control Plane port in the automation-tool range "
        f"{CONTROL_PLANE_PORT_RANGE.start}-{CONTROL_PLANE_PORT_RANGE.stop - 1}; "
        "stop a previous acceptance run instead of reusing an occupied port"
    )


def require_reserved_port_still_free(port: int) -> None:
    """Re-check a reserved port right before a server binds it."""
    if not port_is_free(port):
        raise DesktopPrerequisiteRejected(
            f"the reserved Control Plane port {port} is no longer free"
        )


# --------------------------------------------------------------------------- #
# Embedded browser distribution
# --------------------------------------------------------------------------- #


def release_target_id() -> str:
    """Mirror `embedded_browser_authority::release_target_id()`."""
    machine = os.uname().machine.lower() if hasattr(os, "uname") else "x86_64"
    if sys.platform == "darwin":
        return "macos-arm64" if machine in {"arm64", "aarch64"} else "macos-x86_64"
    if sys.platform == "win32":
        return "windows-x86_64"
    raise DesktopPrerequisiteRejected(
        f"the embedded browser has no distribution target for {sys.platform}"
    )


def embedded_browser_cache(target_id: str | None = None) -> Path:
    return EMBEDDED_BROWSER_CACHE_ROOT / (target_id or release_target_id())


def build_embedded_browser_cache(target_id: str | None = None) -> Path:
    """Build the verified distribution once and keep it outside the target tree.

    Rebuilding it per driver would cost minutes each; keeping it inside
    `target/debug` would let `cargo clean` silently change what the startup gate
    sees. The cache is content-verified on every use, so a damaged one is
    rejected rather than staged.
    """
    from build_embedded_browser_distribution import (  # noqa: PLC0415
        build_distribution_manifest,
    )
    from build_embedded_chromium_staging import (  # noqa: PLC0415
        load_staging_contract,
        sha256_file,
    )
    from build_embedded_chromium_staging import build_staging  # noqa: PLC0415

    resolved = target_id or release_target_id()
    archive = LOCKED_BROWSER_ARCHIVES.get(resolved)
    if archive is None or not archive.is_file():
        raise DesktopPrerequisiteRejected(
            f"the locked Chromium archive for {resolved} is not downloaded "
            f"({archive}); build_embedded_browser_distribution needs it"
        )
    destination = embedded_browser_cache(resolved)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="automation-tool-embedded-browser-", dir=destination.parent
    ) as workspace:
        staging = Path(workspace) / "distribution"
        contract = load_staging_contract(
            REPOSITORY_ROOT / "contracts/browser/embedded-chromium-staging.v1.json"
        )
        build_staging(
            contract=contract,
            target_id=resolved,
            archive_path=archive,
            archive_sha256=sha256_file(archive),
            output=staging,
        )
        build_distribution_manifest(staging=staging, target_id=resolved)
        shutil.rmtree(destination, ignore_errors=True)
        shutil.move(os.fspath(staging), os.fspath(destination))
    return destination


def verify_embedded_browser(tree: Path, target_id: str | None = None) -> None:
    from build_embedded_browser_distribution import (  # noqa: PLC0415
        DistributionRejected,
        verify_distribution,
    )

    try:
        verify_distribution(staging=tree, target_id=target_id or release_target_id())
    except DistributionRejected as error:
        raise DesktopPrerequisiteRejected(
            f"the embedded browser distribution at {tree} failed verification: "
            f"{error}"
        ) from error


def stage_embedded_browser(
    *,
    cache: Path | None = None,
    resource_root: Path = DEBUG_APP_RESOURCE_ROOT,
    target_id: str | None = None,
) -> Path:
    """Install the verified embedded browser where the acceptance App reads it.

    The App resolves it through the same authority the release uses, so without
    this the startup gate reports `browser_component_missing` and every spec in
    the run fails on the diagnostics page instead of its own subject. An already
    staged tree that still verifies is kept, because copying ~340 MB per driver
    is the dominant cost of a serial run.
    """
    resolved_target = target_id or release_target_id()
    source = cache if cache is not None else embedded_browser_cache(resolved_target)
    destination = resource_root / EMBEDDED_BROWSER_DIRECTORY

    if not (source / DISTRIBUTION_MANIFEST_NAME).is_file():
        raise DesktopPrerequisiteRejected(
            f"no embedded browser distribution at {source}; build one with "
            "scripts/build_embedded_browser_distribution.py "
            "(build_embedded_browser_cache does it from the locked archive)"
        )
    verify_embedded_browser(source, resolved_target)

    if (destination / DISTRIBUTION_MANIFEST_NAME).is_file():
        try:
            verify_embedded_browser(destination, resolved_target)
        except DesktopPrerequisiteRejected:
            shutil.rmtree(destination, ignore_errors=True)
        else:
            return destination

    from build_embedded_browser_distribution import (  # noqa: PLC0415
        install_distribution,
    )

    shutil.rmtree(destination, ignore_errors=True)
    resource_root.mkdir(parents=True, exist_ok=True)
    try:
        install_distribution(staging=source, destination=destination)
        verify_embedded_browser(destination, resolved_target)
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return destination


def remove_staged_embedded_browser(
    *, resource_root: Path = DEBUG_APP_RESOURCE_ROOT
) -> None:
    """Leave the resource root without a browser component, on purpose.

    H8-16E asserts the blocked diagnostics page, so it needs the component
    genuinely absent. The resource root is shared by every acceptance App and
    survives runs, so a driver that depends on the component being missing has to
    say so instead of inheriting whatever the previous run left there.
    """
    shutil.rmtree(resource_root / EMBEDDED_BROWSER_DIRECTORY, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Signed Local Executor package
# --------------------------------------------------------------------------- #


def ensure_signed_executor_package(build_id: str = SHARED_EXECUTOR_BUILD_ID) -> Path:
    """Build the signed PyInstaller Executor once per build id and cache it.

    The gate calls `validate_installed_package()`, so an App data directory
    without a signed package reports `executor_unavailable`. The PyInstaller
    build takes minutes; running it once per driver would dominate a serial run
    of the whole layer.
    """
    cached = EXECUTOR_PACKAGE_CACHE_ROOT / build_id
    if (cached / EXECUTOR_MANIFEST_NAME).is_file() and (
        cached / EXECUTOR_MANIFEST_SIGNATURE_NAME
    ).is_file():
        return cached
    from run_e4_07_acceptance import build_signed_executor  # noqa: PLC0415

    cached.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="automation-tool-executor-", dir=cached.parent
    ) as workspace:
        package_source = build_signed_executor(Path(workspace), build_id=build_id)
        shutil.rmtree(cached, ignore_errors=True)
        shutil.move(os.fspath(package_source), os.fspath(cached))
    return cached


def install_signed_executor_package(
    private_app_data: Path, *, build_id: str = SHARED_EXECUTOR_BUILD_ID
) -> Path:
    """Install the cached signed package where the debug build looks for it."""
    package_source = ensure_signed_executor_package(build_id)
    from run_e4_14_acceptance import install_executor_package  # noqa: PLC0415

    return install_executor_package(package_source, private_app_data)


# --------------------------------------------------------------------------- #
# One call every driver makes
# --------------------------------------------------------------------------- #


def prepare_startup_gate(
    private_app_data: Path,
    *,
    build_id: str = SHARED_EXECUTOR_BUILD_ID,
    embedded_browser: bool = True,
    executor_package: bool = True,
    resource_root: Path = DEBUG_APP_RESOURCE_ROOT,
) -> None:
    """Put the acceptance App's local prerequisites in place before it builds.

    `embedded_browser=False` is only for drivers whose subject *is* the blocked
    page. `executor_package=False` is for drivers that build their own package
    with task-specific contents.
    """
    if embedded_browser:
        stage_embedded_browser(resource_root=resource_root)
    else:
        remove_staged_embedded_browser(resource_root=resource_root)
    if executor_package:
        install_signed_executor_package(private_app_data, build_id=build_id)


def terminate_app_process_tree(app_process: subprocess.Popen[bytes]) -> None:
    """Stop an acceptance App run and everything the run started.

    `pnpm test:*-tauri` is a chain — pnpm launches WebdriverIO, WebdriverIO
    launches `tauri-driver`, and `tauri-driver` launches the App — and signalling
    only the pnpm process leaves the rest reparented to init. The orphaned App
    then rewrites the isolated App data directory its driver just deleted, so the
    next run of the same driver dies in under a second on "Refusing to reuse an
    existing … App data directory" and reads like a product failure.

    Drivers therefore start the chain in its own session and stop that session
    here. Nothing outside the session this driver created is ever signalled.
    """
    if app_process.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(app_process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with suppress(subprocess.TimeoutExpired):
            app_process.wait(timeout=10)
        return
    group = _process_group(app_process)
    _stop(app_process, group, signal.SIGTERM)
    try:
        app_process.wait(timeout=10)
        return
    except subprocess.TimeoutExpired:
        pass
    _stop(app_process, group, signal.SIGKILL)
    app_process.wait(timeout=5)


def _process_group(app_process: subprocess.Popen[bytes]) -> int | None:
    """The session the driver started, or `None` where the platform has none."""
    if not hasattr(os, "getpgid") or not hasattr(os, "killpg"):
        return None
    try:
        group = os.getpgid(app_process.pid)
    except OSError:
        return None
    # A process that never got its own session shares the driver's group, and
    # signalling that group would take the driver down with it.
    return group if group != os.getpgid(0) else None


def _stop(
    app_process: subprocess.Popen[bytes],
    group: int | None,
    number: int,
) -> None:
    if group is not None:
        try:
            os.killpg(group, number)
            return
        except ProcessLookupError:
            return
        except OSError:
            pass
    if number == signal.SIGKILL:
        app_process.kill()
    else:
        app_process.terminate()


def private_app_data_directory(identifier: str) -> Path:
    """The App data directory Tauri derives from a bundle identifier."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / identifier
    if sys.platform == "win32":
        roaming = os.environ.get("APPDATA")
        if roaming is None:
            raise DesktopPrerequisiteRejected("Windows roaming AppData is unavailable")
        return Path(roaming) / identifier
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
    return base / identifier


def prepare_private_app_data(identifier: str) -> Path:
    """Create the private App data directory the Executor package installs into."""
    directory = private_app_data_directory(identifier)
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        directory.chmod(stat.S_IRWXU)
    return directory


# --------------------------------------------------------------------------- #
# Which drivers belong to this layer
# --------------------------------------------------------------------------- #


def control_plane_e2e_drivers() -> tuple[Path, ...]:
    """Every `scripts/run_*.py` that launches a `control-plane-e2e` App.

    Derived from `frontend/package.json` rather than kept by hand, so a new
    acceptance App shows up here the moment it is declared.
    """
    scripts = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))["scripts"]
    builds = {
        name
        for name, command in scripts.items()
        if name.startswith("build:tauri:") and "control-plane-e2e" in command
    }
    tokens = builds | {
        name
        for name, command in scripts.items()
        if any(re.search(rf"\b{re.escape(build)}\b", command) for build in builds)
    }
    return tuple(
        sorted(
            path
            for path in SCRIPTS_ROOT.glob("run_*.py")
            if any(token in path.read_text(encoding="utf-8") for token in tokens)
        )
    )


def main() -> int:
    """Build the caches every driver in this layer needs, once."""
    browser = build_embedded_browser_cache()
    verify_embedded_browser(browser)
    print(f"embedded browser distribution cached at {browser}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
