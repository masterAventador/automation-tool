#!/usr/bin/env python3
"""Shared startup-gate preparation for production-composed desktop E2E Apps.

Every `control-plane-e2e` acceptance App compiles the production startup gate,
so it refuses to mount the workbench unless all four of these hold:

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

Point (4) is where the three families differ, because `option_env!` on the origin
exists only under `control-plane-e2e`. Builds without that feature call the
product default, 127.0.0.1:8765, and a harness for them has to own that exact
endpoint rather than reserve a free one:

* `control-plane-e2e` drivers pick a reserved port and bring up their own
  Control Plane, because their subject usually *is* Control Plane state;
* `video-studio-e2e` gets the complete lifecycle harness — real isolated
  PostgreSQL, the production Alembic chain and the production Uvicorn service on
  the product origin — because those Apps read and write real task data;
* plain `desktop-e2e` gets `desktop_e2e_startup_harness`, which serves the same
  production `create_app` on the same origin without a database. The gate asks
  that service two things and nothing else — `/health` and `/version` — and
  neither touches persistence, so a PostgreSQL container per driver would add
  minutes to every run of a family that stores nothing there.

Nothing here terminates an existing or unknown process to free a port. Lifecycle
cleanup stops only a `Popen` handle this module's harness successfully started.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Final
from uuid import uuid4

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT: Final = REPOSITORY_ROOT / "scripts"
FRONTEND_ROOT: Final = REPOSITORY_ROOT / "frontend"
PACKAGE_JSON: Final = FRONTEND_ROOT / "package.json"

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from acceptance_postgres import WINDOWS_POSTGRES_ROOT_ENVIRONMENT  # noqa: E402
from embedded_browser_archives import (  # noqa: E402
    MACOS_ARM64_ARCHIVE,
    archive_path,
)

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

# The origin the product itself is compiled to call. `control-plane-e2e` is the
# only feature that turns it into an `option_env!`; every other build — the
# release, and every plain `desktop-e2e` acceptance App — reaches exactly here.
# A harness for those builds cannot pick a free port, so it must own this one,
# which is also why it refuses rather than reclaims when something holds it.
PRODUCT_CONTROL_PLANE_PORT: Final = 8765
VIDEO_STUDIO_DRIVER_ENVIRONMENT_NAMES: Final = frozenset(
    {
        WINDOWS_POSTGRES_ROOT_ENVIRONMENT,
        "AUTOMATION_TOOL_BM08_EVIDENCE_VIDEO",
        "AUTOMATION_TOOL_IM05_WORKER",
    }
)

LOCKED_BROWSER_ARCHIVES: Final = {
    "macos-arm64": archive_path(MACOS_ARM64_ARCHIVE),
}

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
    if sys.platform == "darwin" and machine in {"arm64", "aarch64"}:
        return "macos-arm64"
    if sys.platform == "win32":
        return "windows-x86_64"
    raise DesktopPrerequisiteRejected(
        f"the embedded browser has no distribution target for {sys.platform}"
    )


def embedded_browser_cache(target_id: str | None = None) -> Path:
    """Where the staged browser lives — the one cache, shared with the release.

    This used to name a directory only the desktop drivers wrote to, while a
    release unpacked its own copy elsewhere. Both now read
    `embedded_browser_staging_cache`, so this has to answer for that location
    or it would report on a directory nothing writes any more.
    """
    from embedded_browser_staging_cache import cache_name
    from video_runtime_cache import cache_root

    return cache_root() / cache_name(target_id or release_target_id())


def build_embedded_browser_cache(target_id: str | None = None) -> Path:
    """Build the verified distribution once and keep it outside the target tree.

    Rebuilding it per driver would cost minutes each; keeping it inside
    `target/debug` would let `cargo clean` silently change what the startup gate
    sees. The cache is content-verified on every use, so a damaged one is
    rejected rather than staged.
    """
    from embedded_browser_staging_cache import (
        EmbeddedBrowserStagingUnavailable,
        ensure_staged_browser,
    )

    resolved = target_id or release_target_id()
    try:
        destination = ensure_staged_browser(target_id=resolved)
    except EmbeddedBrowserStagingUnavailable as error:
        raise DesktopPrerequisiteRejected(str(error)) from error
    return destination


def verify_embedded_browser(tree: Path, target_id: str | None = None) -> None:
    from build_embedded_browser_distribution import (
        DistributionRejected,
        verify_distribution,
    )

    try:
        verify_distribution(staging=tree, target_id=target_id or release_target_id())
    except DistributionRejected as error:
        # Naming the remedy, because the reason alone is a dead end. On
        # 2026-07-27 the staging contract changed at 21:29 the previous evening
        # to exclude Widevine while the cached tree here had been built that
        # morning; verification refused it correctly and then said only that
        # the records differ. Thirty-six acceptance drivers reach this line and
        # every one of them was stuck from that moment, with nothing to run and
        # nothing anywhere reporting it — none of them is in a gate.
        raise DesktopPrerequisiteRejected(
            f"the embedded browser distribution at {tree} failed verification: "
            f"{error}. If the contract moved under a cached tree, rebuild it "
            "with build_embedded_browser_cache (it re-stages from the locked "
            "archive and verifies the result)."
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

    from build_embedded_browser_distribution import (
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

_EXECUTOR_FIXED_INPUTS: Final = (
    "backend/automation-tool-executor.spec",
    "backend/pyproject.toml",
    "backend/uv.lock",
    "scripts/silero_vad_assets.py",
    "scripts/video_runtime_cache.py",
)
_EXECUTOR_CONTRACT_ROOTS: Final = ("contracts/protocol",)


def _executor_spec_resources(source: str) -> tuple[str, ...]:
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise DesktopPrerequisiteRejected(
            "the Executor spec cannot be parsed"
        ) from error
    silero_contract: str | None = None
    motion_resources: tuple[str, ...] | None = None
    for statement in tree.body:
        if not isinstance(statement, ast.Assign):
            continue
        names = {
            target.id for target in statement.targets if isinstance(target, ast.Name)
        }
        if "silero_vad_contract_source" in names:
            value = statement.value
            if (
                isinstance(value, ast.BinOp)
                and isinstance(value.op, ast.Div)
                and isinstance(value.right, ast.Constant)
                and isinstance(value.right.value, str)
            ):
                silero_contract = value.right.value
        if "motion_authoring_resources" in names and isinstance(
            statement.value, (ast.List, ast.Tuple)
        ):
            resources = tuple(
                item.value
                for item in statement.value.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            )
            if len(resources) == len(statement.value.elts) and resources:
                motion_resources = resources
    if silero_contract is not None and motion_resources is not None:
        return (silero_contract, *motion_resources)
    raise DesktopPrerequisiteRejected("the Executor spec resource inventory is invalid")


_IGNORED_EXECUTOR_SOURCE_PARTS: Final = frozenset({"__pycache__"})
_IGNORED_EXECUTOR_SOURCE_SUFFIXES: Final = frozenset({".pyc", ".pyo"})


def _executor_input_paths(repository_root: Path) -> tuple[Path, ...]:
    """Return every repository file whose bytes can change the frozen Executor."""
    source_root = repository_root / "backend/src"
    if not source_root.is_dir():
        raise DesktopPrerequisiteRejected(
            f"the Executor source tree is missing ({source_root})"
        )
    inputs = {
        path
        for path in source_root.rglob("*")
        if path.is_file()
        and not _IGNORED_EXECUTOR_SOURCE_PARTS.intersection(
            path.relative_to(source_root).parts
        )
        and path.suffix not in _IGNORED_EXECUTOR_SOURCE_SUFFIXES
    }
    if not inputs:
        raise DesktopPrerequisiteRejected(
            f"the Executor source tree contains no build inputs ({source_root})"
        )

    for relative in _EXECUTOR_FIXED_INPUTS:
        path = repository_root / relative
        if not path.is_file():
            raise DesktopPrerequisiteRejected(
                f"the Executor package input is missing ({relative})"
            )
        inputs.add(path)

    for relative in _EXECUTOR_CONTRACT_ROOTS:
        contract_root = repository_root / relative
        if not contract_root.is_dir():
            raise DesktopPrerequisiteRejected(
                f"the Executor contract tree is missing ({relative})"
            )
        inputs.update(path for path in contract_root.rglob("*") if path.is_file())

    spec_path = repository_root / _EXECUTOR_FIXED_INPUTS[0]
    for relative in _executor_spec_resources(spec_path.read_text(encoding="utf-8")):
        resource = Path(relative)
        if resource.is_absolute() or ".." in resource.parts:
            raise DesktopPrerequisiteRejected(
                f"the Executor spec names an unsafe package resource ({relative})"
            )
        path = repository_root / resource
        if not path.is_file():
            raise DesktopPrerequisiteRejected(
                f"the Executor spec package resource is missing ({relative})"
            )
        inputs.add(path)

    return tuple(
        sorted(inputs, key=lambda path: path.relative_to(repository_root).as_posix())
    )


def executor_package_input_digest(repository_root: Path | None = None) -> str:
    """Hash source, build configuration and contracts that feed PyInstaller."""
    root = REPOSITORY_ROOT if repository_root is None else repository_root
    digest = hashlib.sha256()
    for path in _executor_input_paths(root):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, byteorder="big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, byteorder="big"))
        digest.update(content)
    return digest.hexdigest()


def executor_package_cache_key(
    build_id: str,
    *,
    repository_root: Path | None = None,
) -> str:
    """Name a cached package by its semantic build id and exact frozen inputs."""
    digest = executor_package_input_digest(repository_root=repository_root)
    return f"{build_id}-inputs-v1-{digest}"


def ensure_signed_executor_package(build_id: str = SHARED_EXECUTOR_BUILD_ID) -> Path:
    """Build the signed PyInstaller Executor once per build id and input digest.

    The gate calls `validate_installed_package()`, so an App data directory
    without a signed package reports `executor_unavailable`. The PyInstaller
    build takes minutes; running it once per driver would dominate a serial run
    of the whole layer.
    """
    cached = EXECUTOR_PACKAGE_CACHE_ROOT / executor_package_cache_key(build_id)
    if (cached / EXECUTOR_MANIFEST_NAME).is_file() and (
        cached / EXECUTOR_MANIFEST_SIGNATURE_NAME
    ).is_file():
        return cached
    from run_e4_07_acceptance import build_signed_executor

    cached.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="automation-tool-executor-", dir=cached.parent
    ) as workspace:
        package_source = build_signed_executor(Path(workspace), build_id=build_id)
        shutil.rmtree(cached, ignore_errors=True)
        shutil.move(os.fspath(package_source), os.fspath(cached))
    return cached


def install_signed_executor_package(
    *,
    build_id: str = SHARED_EXECUTOR_BUILD_ID,
    resource_root: Path = DEBUG_APP_RESOURCE_ROOT,
) -> Path:
    """Install the cached signed package in the App's real resource layout."""
    package_source = ensure_signed_executor_package(build_id)
    from run_e4_14_acceptance import install_executor_package

    return install_executor_package(package_source, resource_root=resource_root)


# --------------------------------------------------------------------------- #
# One call every driver makes
# --------------------------------------------------------------------------- #


def prepare_startup_gate(
    _private_app_data: Path,
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
        install_signed_executor_package(build_id=build_id, resource_root=resource_root)


# --------------------------------------------------------------------------- #
# The plain `desktop-e2e` family: gate inputs plus the product's own origin
# --------------------------------------------------------------------------- #


class _HealthControlPlane:
    """A Control Plane this harness started, and only that one.

    Held as an object rather than a bare thread so cleanup can never be aimed at
    a listener some other process owns: `stop` acts on the server instance this
    module created and returns once its thread has actually left.
    """

    def __init__(self, server: object, thread: object) -> None:
        self._server = server
        self._thread = thread

    def stop(self) -> None:
        self._server.should_exit = True  # type: ignore[attr-defined]
        self._thread.join(timeout=15)  # type: ignore[attr-defined]
        if self._thread.is_alive():  # type: ignore[attr-defined]
            raise DesktopPrerequisiteRejected(
                "the acceptance Control Plane did not stop; it still holds "
                f"127.0.0.1:{PRODUCT_CONTROL_PLANE_PORT}"
            )


def _start_health_control_plane(*, port: int) -> _HealthControlPlane:
    """Serve the production Control Plane app the startup gate calls.

    `create_app(database=None)` is a supported production configuration, not a
    stub: the routes, the response models and the redaction middleware are the
    real ones, and `/health` skips the connection probe exactly as it does for a
    deployment that configured no database. Anything narrower would answer the
    App's `/version` compatibility comparison wrongly and leave the gate closed
    for a reason that looks like a product defect.
    """
    # Imported lazily: cache-only and staging-only users of this module must not
    # need the backend's dependencies on the import path.
    import uvicorn

    backend_source = REPOSITORY_ROOT / "backend" / "src"
    if str(backend_source) not in sys.path:
        sys.path.insert(0, str(backend_source))
    from automation_tool.control_plane.bootstrap.app import create_app

    server = uvicorn.Server(
        uvicorn.Config(
            create_app(database=None),
            host="127.0.0.1",
            port=port,
            access_log=False,
            log_level="critical",
        )
    )
    thread = threading.Thread(
        target=server.run,
        name="automation-tool-desktop-e2e-health",
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if server.started:
            return _HealthControlPlane(server, thread)
        if not thread.is_alive():
            break
        time.sleep(0.05)
    server.should_exit = True
    thread.join(timeout=10)
    raise DesktopPrerequisiteRejected(
        f"the acceptance Control Plane did not start on 127.0.0.1:{port}"
    )


def require_product_origin_released(port: int) -> None:
    """Fail if the origin this harness bound is still accepting connections."""
    from run_i2_13_acceptance import require_port_closed

    require_port_closed(port)


@contextmanager
def desktop_e2e_startup_harness(
    private_app_data: Path,
    *,
    environment: Mapping[str, str],
    resource_root: Path = DEBUG_APP_RESOURCE_ROOT,
) -> Iterator[dict[str, str]]:
    """Yield the environment one plain `desktop-e2e` acceptance App needs.

    The returned mapping is what the driver must build *and* run with: three of
    the four values are read by `tauri build` through `option_env!`, so passing
    them only to the WDIO run produces a binary that reports
    "本地执行器动作配置缺失" and never mounts the workbench — which reads as a
    broken product rather than a driver that prepared half of its inputs.

    The caller's mapping is copied, never mutated: several drivers in this family
    assemble an isolated update feed, TLS material or signing keys first and then
    reuse that same dictionary for their own cleanup assertions.
    """
    if not port_is_free(PRODUCT_CONTROL_PLANE_PORT):
        raise DesktopPrerequisiteRejected(
            "this App is compiled to call "
            f"{control_plane_origin(PRODUCT_CONTROL_PLANE_PORT)}, but that port is "
            "occupied; stop its owner instead of reusing or terminating it"
        )
    prepare_startup_gate(private_app_data, resource_root=resource_root)
    prepared = startup_gate_environment(
        environment, control_plane_port=PRODUCT_CONTROL_PLANE_PORT
    )
    server = _start_health_control_plane(port=PRODUCT_CONTROL_PLANE_PORT)
    try:
        yield prepared
    finally:
        server.stop()
        require_product_origin_released(PRODUCT_CONTROL_PLANE_PORT)


def _video_studio_environment(
    environment: Mapping[str, str],
    *,
    database_port: int,
    development_database_port: int,
) -> dict[str, str]:
    """Overlay one isolated database without dropping driver-specific inputs."""
    database_name = "automation_tool_video_studio"
    database_password = secrets.token_hex(24)
    prepared = {
        key: value
        for key, value in environment.items()
        if not key.startswith("AUTOMATION_TOOL_")
        or key in VIDEO_STUDIO_DRIVER_ENVIRONMENT_NAMES
    }
    prepared.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "unused_video_studio_dev",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": secrets.token_hex(24),
            "AUTOMATION_TOOL_DEV_DB_NAME": "unused_video_studio_dev",
            "AUTOMATION_TOOL_DEV_DB_PORT": str(development_database_port),
            "AUTOMATION_TOOL_TEST_DB_USER": database_name,
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": database_password,
            "AUTOMATION_TOOL_TEST_DB_NAME": database_name,
            "AUTOMATION_TOOL_TEST_DB_PORT": str(database_port),
            "AUTOMATION_TOOL_DATABASE_URL": (
                f"postgresql+asyncpg://{database_name}:{database_password}"
                f"@127.0.0.1:{database_port}/{database_name}"
            ),
        }
    )
    return startup_gate_environment(
        prepared,
        control_plane_port=PRODUCT_CONTROL_PLANE_PORT,
    )


def _terminate_owned_control_plane(server: subprocess.Popen[bytes]) -> None:
    """Stop only the Control Plane process this harness started."""
    if server.poll() is not None:
        return
    server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait(timeout=5)


@contextmanager
def video_studio_startup_harness(
    private_app_data: Path,
    *,
    environment: Mapping[str, str],
    resource_root: Path = DEBUG_APP_RESOURCE_ROOT,
    demo_environment_id: str | None = None,
    demo_bootstrap_public_key: str | None = None,
) -> Iterator[dict[str, str]]:
    """Yield the complete environment around one real video-studio App run.

    Unlike `control-plane-e2e`, the `video-studio-e2e` Rust feature always uses
    the production default origin, so this harness must own 127.0.0.1:8765.
    An existing listener is never terminated or reused. The isolated PostgreSQL
    service, production Alembic chain and production Uvicorn Control Plane stay
    alive for the caller's build and WDIO run, then are removed even when server
    startup or the acceptance itself fails.
    """
    if not port_is_free(PRODUCT_CONTROL_PLANE_PORT):
        raise DesktopPrerequisiteRejected(
            "video-studio-e2e is compiled to call http://127.0.0.1:8765, "
            "but that port is occupied; stop its owner instead of reusing or "
            "terminating it"
        )

    # Import lazily: the helpers load backend acceptance dependencies, while
    # cache/staging-only users of this module must remain stdlib-only.
    from acceptance_postgres import managed_test_postgres
    from run_e4_14_acceptance import start_control_plane
    from run_i2_13_acceptance import (
        BACKEND_ROOT,
        compose_command,
        require_port_closed,
        unused_loopback_port,
    )

    prepare_startup_gate(private_app_data, resource_root=resource_root)
    database_port = unused_loopback_port()
    development_database_port = unused_loopback_port()
    while development_database_port == database_port:
        development_database_port = unused_loopback_port()
    prepared = _video_studio_environment(
        environment,
        database_port=database_port,
        development_database_port=development_database_port,
    )
    if (demo_environment_id is None) != (demo_bootstrap_public_key is None):
        raise DesktopPrerequisiteRejected(
            "video-studio Demo environment and bootstrap key must be supplied together"
        )
    if demo_environment_id is not None and demo_bootstrap_public_key is not None:
        prepared.update(
            {
                "AUTOMATION_TOOL_DEMO_ENVIRONMENT_ID": demo_environment_id,
                "AUTOMATION_TOOL_DEMO_BOOTSTRAP_PUBLIC_KEY": demo_bootstrap_public_key,
            }
        )
    project_name = f"automation-tool-video-studio-{uuid4()}"
    compose = compose_command(project_name)
    server: subprocess.Popen[bytes] | None = None
    control_plane_started = False

    try:
        with managed_test_postgres(
            compose=compose,
            database_port=database_port,
            environment=prepared,
            repository_root=REPOSITORY_ROOT,
        ):
            subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                check=True,
                cwd=BACKEND_ROOT,
                env=prepared,
            )
            server = start_control_plane(
                port=PRODUCT_CONTROL_PLANE_PORT,
                environment=prepared,
            )
            control_plane_started = True
            try:
                yield prepared
            finally:
                _terminate_owned_control_plane(server)
                server = None
    finally:
        if server is not None:
            _terminate_owned_control_plane(server)
        if control_plane_started:
            require_port_closed(PRODUCT_CONTROL_PLANE_PORT)
        require_port_closed(database_port)


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
