#!/usr/bin/env python3
"""BM-04 HTML render sandbox boundary tests for the Node Worker.

Drives the real ``workers/motion_composition/worker.mjs`` process and proves
the ``worker.render.sandbox`` command enforces the render sandbox boundary:

- the sandbox spec is HMAC-bound to the command and strictly validated;
- the render process may only read the RenderJob workspace entry and the
  declared local assets (containment, symlink and traversal rejection);
- browser launch is isolated (private profile, popup block, no debug port,
  no discovery environment) and wall/CPU/memory budgets kill the process
  group; frame output is removed on every failure path.

Remote-request/file-URL/navigation/download/dialog interception against the
real embedded Chromium is exercised by ``scripts/run_bm_04_acceptance.py``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_motion_video_render_adapter import (
    COMMAND_DOMAIN,
    JOB_ID,
    LOCKED_MAJOR,
    PROTOCOL_VERSION,
    RENDER_JOB_PREFIX,
    TOKEN,
    WorkerSession,
    bootstrap_document,
    command_line,
    event_proof,
    expect_ready,
    poisoned_environment,
    read_invocations,
    render_browser_document,
    render_job_directories,
    write_decoy_browser,
    write_fake_browser,
)


def canonical_json(value: object) -> str:
    """The cross-language canonical form the sandbox HMAC binds to."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def sandbox_proof(job_id: str, sandbox: object) -> str:
    message = COMMAND_DOMAIN + b"\0".join(
        value.encode()
        for value in [
            "worker.render.sandbox",
            "node",
            PROTOCOL_VERSION,
            job_id,
            canonical_json(sandbox),
        ]
    )
    digest = hmac.digest(bytes.fromhex(TOKEN), message, hashlib.sha256)
    return "atvwc1." + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def sandbox_command_line(job_id: str, sandbox: object, proof: str | None = None) -> str:
    return json.dumps(
        {
            "authenticationProof": proof if proof is not None else sandbox_proof(job_id, sandbox),
            "command": "worker.render.sandbox",
            "jobId": job_id,
            "protocolVersion": PROTOCOL_VERSION,
            "sandbox": sandbox,
            "workerKind": "node",
        }
    )


def sandbox_spec(workspace: Path, **overrides: object) -> dict[str, object]:
    spec: dict[str, object] = {
        "allowedAssets": ["assets/style.css"],
        "entryHtml": "entry.html",
        "frameCount": 3,
        "maxCpuSeconds": 20,
        "maxDurationSeconds": 20,
        "maxMemoryMegabytes": 1024,
        "maxOutputBytes": 50_000_000,
        "workspace": str(workspace),
    }
    spec.update(overrides)
    return spec


def make_workspace(base: Path) -> Path:
    workspace = base / "workspace"
    (workspace / "assets").mkdir(parents=True)
    (workspace / "entry.html").write_text("<!doctype html><title>scene</title>")
    (workspace / "assets/style.css").write_text("body{background:#fff}")
    return workspace


def write_sandbox_fake(directory: Path, mode: str) -> tuple[Path, Path]:
    """A recording fake browser for resource-budget and hang scenarios.

    Honours ``--version``; in the headless phase either hangs before any CDP
    answer (``hang``), or answers ``Browser.getVersion`` and then burns CPU
    (``burn-cpu``) or holds a large allocation (``allocate-memory``).
    """
    directory.mkdir(parents=True, exist_ok=True)
    record = directory / "fake-record.log"
    product = json.dumps({"id": 1, "result": {"product": f"Chrome/{LOCKED_MAJOR}.0.7827.55"}})
    script = f"""#!{sys.executable}
import json, os, sys, time

with open({json.dumps(str(record))}, "a") as record:
    record.write("INVOCATION pid=%d\\n" % os.getpid())
    for argument in sys.argv[1:]:
        record.write("ARG %s\\n" % argument)
    for name, value in os.environ.items():
        record.write("ENV %s=%s\\n" % (name, value))
    record.write("END\\n")

if "--version" in sys.argv[1:]:
    print("Google Chrome for Testing {LOCKED_MAJOR}.0.7827.55")
    sys.exit(0)

mode = {json.dumps(mode)}
if mode == "hang":
    time.sleep(3600)

data = b""
while b"\\0" not in data:
    chunk = os.read(3, 65536)
    if not chunk:
        sys.exit(1)
    data += chunk
os.write(4, {product!r}.encode() + b"\\0")

if mode == "burn-cpu":
    while True:
        pass
if mode == "allocate-memory":
    hold = b"x" * (700 * 1024 * 1024)
    time.sleep(3600)
"""
    executable = directory / "fake-browser"
    executable.write_text(script)
    executable.chmod(0o755)
    return executable, record


def expect_sandbox_failure(
    session: WorkerSession, line: str, reason: str, job_id: str = JOB_ID
) -> None:
    session.send_line(line)
    session.send_line(command_line("worker.cancel", job_id))
    event = session.read_event()
    assert event["event"] == "worker.render.failed", event
    assert event["reasonCode"] == reason, event
    assert event["jobId"] == job_id, event
    assert event["authenticationProof"] == event_proof(
        "worker.render.failed", f"{job_id}\0{reason}"
    ), event
    cancelled = session.read_event()
    assert cancelled["event"] == "worker.cancelled", cancelled


def assert_workspace_untouched(workspace: Path) -> None:
    assert not (workspace / "frames").exists(), "sandbox failure must not leave a frames directory"


def wait_for_process_exit(pid: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    os.kill(pid, 9)
    raise AssertionError("sandbox browser process survived the kill path")


def test_sandbox_rejects_invalid_spec(assets: Path, decoy: Path) -> None:
    executable, record = write_fake_browser(assets / "valid")
    workspace = make_workspace(assets)
    session = WorkerSession(
        bootstrap_document(str(assets), render_browser_document(executable)),
        poisoned_environment(decoy),
    )
    expect_ready(session)
    missing_key = sandbox_spec(workspace)
    del missing_key["frameCount"]
    secret_relative = "secret-traversal"
    invalid_specs: list[object] = [
        missing_key,
        {**sandbox_spec(workspace), "shell": True},
        sandbox_spec(Path("relative/workspace")),
        sandbox_spec(workspace, entryHtml="/absolute/entry.html"),
        sandbox_spec(workspace, entryHtml=f"../{secret_relative}/entry.html"),
        sandbox_spec(workspace, entryHtml="a\\b.html"),
        sandbox_spec(workspace, entryHtml=""),
        sandbox_spec(workspace, allowedAssets="assets/style.css"),
        sandbox_spec(workspace, allowedAssets=[f"../{secret_relative}.css"]),
        sandbox_spec(workspace, allowedAssets=["./style.css"]),
        sandbox_spec(workspace, allowedAssets=[f"asset-{index}.css" for index in range(129)]),
        sandbox_spec(workspace, frameCount=0),
        sandbox_spec(workspace, frameCount=601),
        sandbox_spec(workspace, frameCount="3"),
        sandbox_spec(workspace, maxDurationSeconds=0),
        sandbox_spec(workspace, maxDurationSeconds=301),
        sandbox_spec(workspace, maxCpuSeconds=0),
        sandbox_spec(workspace, maxCpuSeconds=301),
        sandbox_spec(workspace, maxMemoryMegabytes=64),
        sandbox_spec(workspace, maxMemoryMegabytes=8193),
        sandbox_spec(workspace, maxOutputBytes=0),
        "not-an-object",
        None,
    ]
    for spec in invalid_specs:
        expect_sandbox_failure(
            session, sandbox_command_line(JOB_ID, spec), "render_sandbox_invalid"
        )
    code, stderr = session.finish()
    assert code == 0
    assert secret_relative not in stderr
    assert not read_invocations(record), "invalid sandbox spec must never launch the browser"
    assert not render_job_directories(JOB_ID)
    assert_workspace_untouched(workspace)


def test_sandbox_rejects_workspace_violations(assets: Path, decoy: Path) -> None:
    executable, record = write_fake_browser(assets / "valid")
    outside = assets / "outside-secret.css"
    outside.write_text("secret{}")

    def run_case(prepare: object, reason: str = "render_workspace_invalid") -> None:
        base = assets / f"case-{run_case.counter}"
        run_case.counter += 1
        workspace = make_workspace(base)
        spec = prepare(workspace)
        session = WorkerSession(
            bootstrap_document(str(assets), render_browser_document(executable)),
            poisoned_environment(decoy),
        )
        expect_ready(session)
        expect_sandbox_failure(session, sandbox_command_line(JOB_ID, spec), reason)
        code, _ = session.finish()
        assert code == 0

    run_case.counter = 0

    def missing_workspace(workspace: Path) -> dict[str, object]:
        return sandbox_spec(workspace / "missing")

    def symlinked_workspace(workspace: Path) -> dict[str, object]:
        link = workspace.parent / "workspace-link"
        link.symlink_to(workspace)
        return sandbox_spec(link)

    def missing_entry(workspace: Path) -> dict[str, object]:
        (workspace / "entry.html").unlink()
        return sandbox_spec(workspace)

    def symlinked_entry(workspace: Path) -> dict[str, object]:
        (workspace / "entry.html").unlink()
        (workspace / "entry.html").symlink_to(outside)
        return sandbox_spec(workspace)

    def missing_asset(workspace: Path) -> dict[str, object]:
        return sandbox_spec(workspace, allowedAssets=["assets/missing.css"])

    def escaping_asset(workspace: Path) -> dict[str, object]:
        (workspace / "assets/escape.css").symlink_to(outside)
        return sandbox_spec(workspace, allowedAssets=["assets/escape.css"])

    def preexisting_frames(workspace: Path) -> dict[str, object]:
        (workspace / "frames").mkdir()
        return sandbox_spec(workspace)

    for prepare in (
        missing_workspace,
        symlinked_workspace,
        missing_entry,
        symlinked_entry,
        missing_asset,
        escaping_asset,
        preexisting_frames,
    ):
        run_case(prepare)
    assert not read_invocations(record), "workspace violations must never launch the browser"
    assert not render_job_directories(JOB_ID)


def test_sandbox_null_render_browser_refuses_without_discovery(assets: Path, decoy: Path) -> None:
    decoy_record = write_decoy_browser(decoy)
    workspace = make_workspace(assets)
    session = WorkerSession(bootstrap_document(str(assets), None), poisoned_environment(decoy))
    expect_ready(session)
    expect_sandbox_failure(
        session,
        sandbox_command_line(JOB_ID, sandbox_spec(workspace)),
        "render_browser_unavailable",
    )
    code, _ = session.finish()
    assert code == 0
    assert not decoy_record.exists(), "discovery fallback consulted the decoy browser"
    assert_workspace_untouched(workspace)


def test_sandbox_forged_or_tampered_command_is_ignored(assets: Path, decoy: Path) -> None:
    executable, record = write_fake_browser(assets / "forged")
    workspace = make_workspace(assets)
    session = WorkerSession(
        bootstrap_document(str(assets), render_browser_document(executable)),
        poisoned_environment(decoy),
    )
    expect_ready(session)
    spec = sandbox_spec(workspace)
    tampered = sandbox_spec(workspace, frameCount=600)
    unbound_message = COMMAND_DOMAIN + b"\0".join(
        value.encode()
        for value in ["worker.render.sandbox", "node", PROTOCOL_VERSION, JOB_ID]
    )
    unbound_proof = "atvwc1." + base64.urlsafe_b64encode(
        hmac.digest(bytes.fromhex(TOKEN), unbound_message, hashlib.sha256)
    ).rstrip(b"=").decode()
    forged_lines = [
        sandbox_command_line(JOB_ID, spec, proof="atvwc1.forged-proof-value"),
        sandbox_command_line(JOB_ID, tampered, proof=sandbox_proof(JOB_ID, spec)),
        sandbox_command_line(JOB_ID, spec, proof=unbound_proof),
    ]
    for line in forged_lines:
        session.send_line(line)
    session.send_line(command_line("worker.cancel", JOB_ID))
    event = session.read_event()
    assert event["event"] == "worker.cancelled", event
    code, _ = session.finish()
    assert code == 0
    assert not read_invocations(record), "forged sandbox command must never touch the browser"
    assert_workspace_untouched(workspace)


def test_sandbox_rejects_chromium_major_mismatch(assets: Path, decoy: Path) -> None:
    executable, record = write_fake_browser(
        assets / "mismatch", version_output="Google Chrome for Testing 150.0.1.1"
    )
    workspace = make_workspace(assets)
    session = WorkerSession(
        bootstrap_document(str(assets), render_browser_document(executable)),
        poisoned_environment(decoy),
    )
    expect_ready(session)
    expect_sandbox_failure(
        session, sandbox_command_line(JOB_ID, sandbox_spec(workspace)), "chromium_major_mismatch"
    )
    code, _ = session.finish()
    assert code == 0
    invocations = read_invocations(record)
    assert len(invocations) == 1, "mismatched browser must not be launched headless"
    assert invocations[0]["arguments"] == ["--version"]
    assert not render_job_directories(JOB_ID)
    assert_workspace_untouched(workspace)


def test_sandbox_isolation_flags_and_wall_timeout(assets: Path, decoy: Path) -> None:
    executable, record = write_sandbox_fake(assets / "hang", "hang")
    workspace = make_workspace(assets)
    session = WorkerSession(
        bootstrap_document(str(assets), render_browser_document(executable)),
        poisoned_environment(decoy),
    )
    expect_ready(session)
    started = time.monotonic()
    expect_sandbox_failure(
        session,
        sandbox_command_line(JOB_ID, sandbox_spec(workspace, maxDurationSeconds=1)),
        "render_timeout",
    )
    assert time.monotonic() - started < 15, "wall budget must not wait for the hanging browser"
    code, _ = session.finish()
    assert code == 0
    invocations = read_invocations(record)
    assert len(invocations) == 2, invocations
    assert invocations[0]["arguments"] == ["--version"]
    headless = invocations[1]
    arguments = headless["arguments"]
    for required in (
        "--headless",
        "--remote-debugging-pipe",
        "--use-mock-keychain",
        "--block-new-web-contents",
    ):
        assert required in arguments, (required, arguments)
    assert not any(value.startswith("--remote-debugging-port") for value in arguments), (
        "the sandbox browser must never open a listening debug port"
    )
    profile = [value for value in arguments if value.startswith("--user-data-dir=")]
    assert len(profile) == 1 and RENDER_JOB_PREFIX + JOB_ID in profile[0], arguments
    assert str(workspace) not in " ".join(arguments), (
        "the workspace must be reached over CDP navigation, not the command line"
    )
    environment = headless["environment"]
    for name in (
        "HYPERFRAMES_BROWSER_PATH",
        "PRODUCER_HEADLESS_SHELL_PATH",
        "PUPPETEER_CACHE_DIR",
        "CHROME_PATH",
    ):
        assert name not in environment, f"{name} leaked into the sandbox browser"
    assert RENDER_JOB_PREFIX + JOB_ID in environment.get("HOME", ""), environment.get("HOME")
    wait_for_process_exit(headless["pid"])
    assert not render_job_directories(JOB_ID)
    assert_workspace_untouched(workspace)


def run_resource_budget_case(
    assets: Path, decoy: Path, mode: str, spec_overrides: dict[str, object]
) -> None:
    executable, record = write_sandbox_fake(assets / mode, mode)
    workspace = make_workspace(assets)
    session = WorkerSession(
        bootstrap_document(str(assets), render_browser_document(executable)),
        poisoned_environment(decoy),
    )
    expect_ready(session)
    started = time.monotonic()
    expect_sandbox_failure(
        session,
        sandbox_command_line(JOB_ID, sandbox_spec(workspace, **spec_overrides)),
        "render_resource_exceeded",
    )
    assert time.monotonic() - started < 25, "resource budget must interrupt the busy browser"
    code, _ = session.finish()
    assert code == 0
    wait_for_process_exit(read_invocations(record)[-1]["pid"])
    assert not render_job_directories(JOB_ID)
    assert_workspace_untouched(workspace)


def test_sandbox_cpu_budget_kills_the_process_group(assets: Path, decoy: Path) -> None:
    run_resource_budget_case(
        assets, decoy, "burn-cpu", {"maxCpuSeconds": 1, "maxDurationSeconds": 30}
    )


def test_sandbox_memory_budget_kills_the_process_group(assets: Path, decoy: Path) -> None:
    run_resource_budget_case(
        assets, decoy, "allocate-memory", {"maxMemoryMegabytes": 256, "maxDurationSeconds": 30}
    )


def main() -> int:
    tests = [
        test_sandbox_rejects_invalid_spec,
        test_sandbox_rejects_workspace_violations,
        test_sandbox_null_render_browser_refuses_without_discovery,
        test_sandbox_forged_or_tampered_command_is_ignored,
        test_sandbox_rejects_chromium_major_mismatch,
        test_sandbox_isolation_flags_and_wall_timeout,
        test_sandbox_cpu_budget_kills_the_process_group,
        test_sandbox_memory_budget_kills_the_process_group,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory(prefix="automation-tool-bm04-") as directory:
            base = Path(directory).resolve(strict=True)
            assets = base / "assets"
            assets.mkdir()
            decoy = base / "decoy"
            decoy.mkdir()
            test(assets, decoy)
            print(f"PASS {test.__name__}")
    leftovers = [
        path
        for path in Path(tempfile.gettempdir()).iterdir()
        if path.name.startswith(RENDER_JOB_PREFIX)
    ]
    assert not leftovers, f"render job directories leaked: {leftovers}"
    print("BM-04 render sandbox boundary tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
