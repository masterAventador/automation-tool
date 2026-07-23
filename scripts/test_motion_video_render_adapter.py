#!/usr/bin/env python3
"""BM-03 shared-Chromium render adapter boundary tests for the Node Worker.

Drives the real ``workers/motion_composition/worker.mjs`` process and proves:

- bootstrap only accepts one Rust-delivered ``renderBrowser`` channel
  (absolute regular executable, explicit Chromium major, bounded timeout);
- render verification launches the browser as an independent headless
  process inside a per-RenderJob temporary directory and cleans it up;
- upstream download, system browser discovery and cache fallbacks are
  disabled: discovery environment variables and caches are never consulted.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "workers/motion_composition/worker.mjs"
WORKER_VERSION = "0.7.68"
PROTOCOL_VERSION = "1.0"
EVENT_DOMAIN = b"automation-tool.video-worker-event.v1\0"
COMMAND_DOMAIN = b"automation-tool.video-worker-command.v1\0"
TOKEN = "b" * 64
JOB_ID = "7d444840-9dc0-41a2-bcd4-e15b02a4c51e"
LOCKED_MAJOR = 149
FAKE_VERSION_OUTPUT = f"Google Chrome for Testing {LOCKED_MAJOR}.0.7827.55"
RENDER_JOB_PREFIX = "automation-tool-renderjob-"


def node_executable() -> str:
    value = shutil.which("node")
    if value is None:
        raise AssertionError("development Node is unavailable")
    return value


def event_proof(event: str, detail: str) -> str:
    message = EVENT_DOMAIN + b"\0".join(
        value.encode() for value in [event, "node", PROTOCOL_VERSION, WORKER_VERSION, detail]
    )
    digest = hmac.digest(bytes.fromhex(TOKEN), message, hashlib.sha256)
    return "atvwp1." + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def command_proof(command: str, job_id: str) -> str:
    message = COMMAND_DOMAIN + b"\0".join(
        value.encode() for value in [command, "node", PROTOCOL_VERSION, job_id]
    )
    digest = hmac.digest(bytes.fromhex(TOKEN), message, hashlib.sha256)
    return "atvwc1." + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def command_line(command: str, job_id: str, proof: str | None = None) -> str:
    return json.dumps(
        {
            "authenticationProof": proof if proof is not None else command_proof(command, job_id),
            "command": command,
            "jobId": job_id,
            "protocolVersion": PROTOCOL_VERSION,
            "workerKind": "node",
        }
    )


def bootstrap_document(asset_root: str, render_browser: object) -> dict[str, object]:
    return {
        "assetRoot": asset_root,
        "bootstrapVersion": "1",
        "enableWebUi": False,
        "localSessionToken": TOKEN,
        "protocolVersion": PROTOCOL_VERSION,
        "renderBrowser": render_browser,
        "scriptModel": None,
        "workerKind": "node",
    }


def render_browser_document(
    executable: Path, major: int = LOCKED_MAJOR, timeout_seconds: int = 20
) -> dict[str, object]:
    return {
        "chromiumMajor": major,
        "executablePath": str(executable),
        "launchTimeoutSeconds": timeout_seconds,
    }


def poisoned_environment(decoy: Path) -> dict[str, str]:
    """A hostile environment: every legacy discovery channel points at a decoy."""
    environment = {
        "PATH": "",
        "HOME": str(decoy / "home"),
        "HYPERFRAMES_BROWSER_PATH": str(decoy / "decoy-browser"),
        "PRODUCER_HEADLESS_SHELL_PATH": str(decoy / "decoy-browser"),
        "PUPPETEER_CACHE_DIR": str(decoy / "puppeteer-cache"),
        "CHROME_PATH": str(decoy / "decoy-browser"),
    }
    if os.name == "nt":
        for name in ("SYSTEMROOT", "WINDIR"):
            if name in os.environ:
                environment[name] = os.environ[name]
    return environment


def write_decoy_browser(decoy: Path) -> Path:
    decoy.mkdir(parents=True, exist_ok=True)
    (decoy / "home").mkdir(exist_ok=True)
    record = decoy / "decoy-record.log"
    executable = decoy / "decoy-browser"
    executable.write_text(
        f"#!/bin/sh\necho invoked >> {json.dumps(str(record))}\n"
        f'echo "{FAKE_VERSION_OUTPUT}"\n'
    )
    executable.chmod(0o755)
    return record


def write_fake_browser(
    directory: Path,
    *,
    version_output: str = FAKE_VERSION_OUTPUT,
    product: str = f"Chrome/{LOCKED_MAJOR}.0.7827.55",
    garbage_protocol: bool = False,
    hang_seconds: int = 0,
) -> tuple[Path, Path]:
    """A recording fake browser speaking the `--remote-debugging-pipe` protocol.

    Logs argv/pid/env, honours `--version`, then answers one CDP request on
    file descriptor 4 and waits for the `Browser.close` request before exiting.
    """
    directory.mkdir(parents=True, exist_ok=True)
    record = directory / "fake-record.log"
    response = (
        b"not-a-cdp-response\0"
        if garbage_protocol
        else json.dumps({"id": 1, "result": {"product": product}}).encode() + b"\0"
    )
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
    print({json.dumps(version_output)})
    sys.exit(0)

time.sleep({hang_seconds})

def read_message():
    data = b""
    while b"\\0" not in data:
        chunk = os.read(3, 65536)
        if not chunk:
            sys.exit(1)
        data += chunk
    return data.split(b"\\0")[0]

read_message()
os.write(4, {response!r})
read_message()
sys.exit(0)
"""
    executable = directory / "fake-browser"
    executable.write_text(script)
    executable.chmod(0o755)
    return executable, record


def write_windows_hanging_browser(
    directory: Path, *, version_output: str = FAKE_VERSION_OUTPUT
) -> tuple[Path, Path]:
    if os.name != "nt":
        raise AssertionError("Windows fake browser requested on another platform")
    rustc = shutil.which("rustc")
    if rustc is None:
        raise AssertionError("rustc is unavailable")
    directory.mkdir(parents=True, exist_ok=True)
    executable = directory / "fake-browser.exe"
    record = directory / "fake-record.log"
    source = directory / "fake-browser.rs"
    source.write_text(
        f"""
use std::{{env, fs, process::Command, thread, time::Duration}};

fn main() {{
    let arguments = env::args().skip(1).collect::<Vec<_>>();
    if arguments.iter().any(|value| value == "--version") {{
        println!({json.dumps(version_output)});
        return;
    }}
    if arguments.iter().any(|value| value == "--child") {{
        loop {{ thread::sleep(Duration::from_secs(1)); }}
    }}
    let child = Command::new(env::current_exe().unwrap())
        .arg("--child")
        .spawn()
        .unwrap();
    fs::write(
        {json.dumps(str(record))},
        format!("{{}} {{}}\\n", std::process::id(), child.id()),
    ).unwrap();
    loop {{ thread::sleep(Duration::from_secs(1)); }}
}}
""",
        encoding="utf-8",
    )
    subprocess.run(
        [rustc, str(source), "-o", str(executable)],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return executable, record


def write_windows_cdp_browser(
    directory: Path,
    *,
    product: str = f"Chrome/{LOCKED_MAJOR}.0.7827.55",
    garbage_protocol: bool = False,
) -> tuple[Path, Path]:
    """Compile a PE fake that speaks CDP on inherited CRT descriptors 3/4."""
    if os.name != "nt":
        raise AssertionError("Windows CDP fake browser requested on another platform")
    rustc = shutil.which("rustc")
    if rustc is None:
        raise AssertionError("rustc is unavailable")
    directory.mkdir(parents=True, exist_ok=True)
    executable = directory / "fake-browser.exe"
    record = directory / "fake-record.log"
    source = directory / "fake-browser.rs"
    response = (
        b"not-a-cdp-response\0"
        if garbage_protocol
        else json.dumps({"id": 1, "result": {"product": product}}).encode() + b"\0"
    )
    close_response = json.dumps({"id": 2, "result": {}}).encode() + b"\0"
    source.write_text(
        f"""
use std::{{env, ffi::c_void, fs, process, thread, time::Duration}};

extern "C" {{
    fn _read(fd: i32, buffer: *mut c_void, count: u32) -> i32;
    fn _write(fd: i32, buffer: *const c_void, count: u32) -> i32;
}}

fn read_message() {{
    let mut byte = [0_u8; 1];
    loop {{
        let count = unsafe {{ _read(3, byte.as_mut_ptr().cast(), 1) }};
        if count != 1 {{
            process::exit(2);
        }}
        if byte[0] == 0 {{
            return;
        }}
    }}
}}

fn write_message(message: &[u8]) {{
    let mut offset = 0;
    while offset < message.len() {{
        let count = unsafe {{
            _write(
                4,
                message[offset..].as_ptr().cast(),
                (message.len() - offset) as u32,
            )
        }};
        if count <= 0 {{
            process::exit(3);
        }}
        offset += count as usize;
    }}
}}

fn main() {{
    let mut invocation = format!("INVOCATION pid={{}}\\n", process::id());
    for argument in env::args().skip(1) {{
        invocation.push_str(&format!("ARG {{argument}}\\n"));
    }}
    for (name, value) in env::vars() {{
        invocation.push_str(&format!("ENV {{name}}={{value}}\\n"));
    }}
    invocation.push_str("END\\n");
    fs::write({json.dumps(str(record))}, invocation).unwrap();
    read_message();
    write_message(&{list(response)!r});
    if {str(garbage_protocol).lower()} {{
        loop {{ thread::sleep(Duration::from_secs(1)); }}
    }}
    read_message();
    write_message(&{list(close_response)!r});
    loop {{ thread::sleep(Duration::from_secs(1)); }}
}}
""",
        encoding="utf-8",
    )
    subprocess.run(
        [rustc, str(source), "-o", str(executable)],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return executable, record


def windows_process_exists(process_id: int) -> bool:
    if os.name != "nt":
        raise AssertionError("Windows process query requested on another platform")
    import ctypes

    query_limited_information = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
        query_limited_information, False, process_id
    )
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        return bool(
            ctypes.windll.kernel32.GetExitCodeProcess(  # type: ignore[attr-defined]
                handle, ctypes.byref(exit_code)
            )
        ) and exit_code.value == 259
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]


class WorkerSession:
    """One live worker process with line-based stdin/stdout access."""

    def __init__(self, bootstrap: dict[str, object], environment: dict[str, str]):
        self.process = subprocess.Popen(
            [node_executable(), str(WORKER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        self.send_line(json.dumps(bootstrap))

    def send_line(self, line: str) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()

    def read_event(self) -> dict[str, object]:
        assert self.process.stdout is not None
        line = self.process.stdout.readline()
        assert line, "worker closed stdout before emitting an event"
        return json.loads(line)

    def finish(self) -> tuple[int, str]:
        assert self.process.stdin is not None
        self.process.stdin.close()
        try:
            code = self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            raise
        stderr = self.process.stderr.read() if self.process.stderr else ""
        return code, stderr


def expect_ready(session: WorkerSession) -> None:
    event = session.read_event()
    assert event["event"] == "worker.ready", event
    detail = str(event["port"])
    assert event["authenticationProof"] == event_proof("worker.ready", detail)


def run_rejected_bootstrap(bootstrap: dict[str, object], environment: dict[str, str]) -> str:
    session = WorkerSession(bootstrap, environment)
    code, stderr = session.finish()
    assert code == 65, f"expected bootstrap rejection, got exit {code}: {stderr}"
    assert stderr == "Motion composition worker bootstrap is rejected\n", stderr
    return stderr


def render_job_directories(job_id: str) -> list[Path]:
    return [
        path
        for path in Path(tempfile.gettempdir()).iterdir()
        if path.name.startswith(f"{RENDER_JOB_PREFIX}{job_id}")
    ]


def read_invocations(record: Path) -> list[dict[str, object]]:
    invocations: list[dict[str, object]] = []
    if not record.exists():
        return invocations
    current: dict[str, object] | None = None
    for line in record.read_text().splitlines():
        if line.startswith("INVOCATION pid="):
            current = {"pid": int(line.split("=", 1)[1]), "arguments": [], "environment": {}}
            invocations.append(current)
        elif line.startswith("ARG ") and current is not None:
            current["arguments"].append(line[4:])
        elif line.startswith("ENV ") and current is not None and "=" in line[4:]:
            name, value = line[4:].split("=", 1)
            current["environment"][name] = value
    return invocations


def test_bootstrap_requires_render_browser_key(assets: Path, decoy: Path) -> None:
    bootstrap = bootstrap_document(str(assets), None)
    del bootstrap["renderBrowser"]
    run_rejected_bootstrap(bootstrap, poisoned_environment(decoy))


def test_bootstrap_rejects_invalid_render_browser(assets: Path, decoy: Path) -> None:
    if os.name == "nt":
        executable = Path(sys.executable).resolve(strict=True)
        link = None
    else:
        executable, _ = write_fake_browser(assets / "valid")
        link = assets / "browser-link"
        link.symlink_to(executable)
    missing = assets / "missing-browser"
    plain = assets / ("plain-file.exe" if os.name == "nt" else "plain-file")
    plain.write_text("not executable")
    secret_relative = "secret-relative/browser"
    invalid_documents: list[object] = [
        render_browser_document(Path(secret_relative)),
        render_browser_document(missing),
        render_browser_document(plain),
        render_browser_document(executable, major=99),
        render_browser_document(executable, major=1000),
        render_browser_document(executable, major="149"),
        render_browser_document(executable, timeout_seconds=0),
        render_browser_document(executable, timeout_seconds=61),
        {**render_browser_document(executable), "downloadFallback": True},
        {"executablePath": str(executable)},
        str(executable),
    ]
    if link is not None:
        invalid_documents.append(render_browser_document(link))
    for document in invalid_documents:
        stderr = run_rejected_bootstrap(
            bootstrap_document(str(assets), document), poisoned_environment(decoy)
        )
        assert secret_relative not in stderr
        assert str(assets) not in stderr


def test_null_render_browser_refuses_render_without_discovery(assets: Path, decoy: Path) -> None:
    decoy_record = write_decoy_browser(decoy)
    session = WorkerSession(bootstrap_document(str(assets), None), poisoned_environment(decoy))
    expect_ready(session)
    session.send_line(command_line("worker.render.verify", JOB_ID))
    event = session.read_event()
    assert event["event"] == "worker.render.failed", event
    assert event["reasonCode"] == "render_browser_unavailable", event
    assert event["jobId"] == JOB_ID
    assert event["authenticationProof"] == event_proof(
        "worker.render.failed", f"{JOB_ID}\0render_browser_unavailable"
    )
    code, _ = session.finish()
    assert code == 0
    assert not decoy_record.exists(), "discovery fallback consulted the decoy browser"


def test_render_rejects_chromium_major_mismatch(assets: Path, decoy: Path) -> None:
    if os.name == "nt":
        executable, record = write_windows_cdp_browser(
            assets / "mismatch",
            product="Chrome/150.0.1.1",
        )
    else:
        executable, record = write_fake_browser(
            assets / "mismatch", version_output="Google Chrome for Testing 150.0.1.1"
        )
    session = WorkerSession(
        bootstrap_document(str(assets), render_browser_document(executable)),
        poisoned_environment(decoy),
    )
    expect_ready(session)
    session.send_line(command_line("worker.render.verify", JOB_ID))
    event = session.read_event()
    assert event["event"] == "worker.render.failed", event
    assert event["reasonCode"] == "chromium_major_mismatch", event
    code, _ = session.finish()
    assert code == 0
    if os.name == "nt":
        invocations = read_invocations(record)
        assert len(invocations) == 1, invocations
        assert "--remote-debugging-pipe" in invocations[0]["arguments"]
    else:
        invocations = read_invocations(record)
        assert len(invocations) == 1, "mismatched browser must not be launched headless"
        assert invocations[0]["arguments"] == ["--version"]
    assert not render_job_directories(JOB_ID), "render job directory must be removed"


def test_render_verified_headless_job_isolation_and_cleanup(assets: Path, decoy: Path) -> None:
    decoy_record = write_decoy_browser(decoy)
    if os.name == "nt":
        executable, record = write_windows_cdp_browser(assets / "healthy")
    else:
        executable, record = write_fake_browser(assets / "healthy")
    session = WorkerSession(
        bootstrap_document(str(assets), render_browser_document(executable)),
        poisoned_environment(decoy),
    )
    expect_ready(session)
    session.send_line(command_line("worker.render.verify", JOB_ID))
    event = session.read_event()
    assert event["event"] == "worker.render.verified", event
    assert event["jobId"] == JOB_ID
    assert event["chromiumMajor"] == LOCKED_MAJOR
    assert event["workerVersion"] == WORKER_VERSION
    assert event["authenticationProof"] == event_proof(
        "worker.render.verified", f"{JOB_ID}\0{LOCKED_MAJOR}"
    )
    code, _ = session.finish()
    assert code == 0

    invocations = read_invocations(record)
    expected_invocations = 1 if os.name == "nt" else 2
    assert len(invocations) == expected_invocations, invocations
    if os.name != "nt":
        assert invocations[0]["arguments"] == ["--version"]
    headless = invocations[-1]
    arguments = headless["arguments"]
    assert "--headless" in arguments, arguments
    assert "--remote-debugging-pipe" in arguments, arguments
    profile = [value for value in arguments if value.startswith("--user-data-dir=")]
    crashes = [value for value in arguments if value.startswith("--crash-dumps-dir=")]
    assert len(profile) == 1 and len(crashes) == 1, arguments
    assert RENDER_JOB_PREFIX + JOB_ID in profile[0]
    assert RENDER_JOB_PREFIX + JOB_ID in crashes[0]
    assert arguments[-1] == "about:blank"
    assert not any(value.startswith("--remote-debugging-port") for value in arguments), (
        "the render browser must never open a listening debug port"
    )
    environment = headless["environment"]
    for name in (
        "HYPERFRAMES_BROWSER_PATH",
        "PRODUCER_HEADLESS_SHELL_PATH",
        "PUPPETEER_CACHE_DIR",
        "CHROME_PATH",
    ):
        assert name not in environment, f"{name} leaked into the render browser"
    assert RENDER_JOB_PREFIX + JOB_ID in environment.get("HOME", ""), environment.get("HOME")
    assert not render_job_directories(JOB_ID), "render job directory must be removed"
    assert not decoy_record.exists(), "discovery fallback consulted the decoy browser"


def test_render_rejects_invalid_headless_protocol(assets: Path, decoy: Path) -> None:
    if os.name == "nt":
        executable, _ = write_windows_cdp_browser(
            assets / "badprotocol", garbage_protocol=True
        )
    else:
        executable, _ = write_fake_browser(
            assets / "badprotocol", garbage_protocol=True
        )
    session = WorkerSession(
        bootstrap_document(str(assets), render_browser_document(executable)),
        poisoned_environment(decoy),
    )
    expect_ready(session)
    session.send_line(command_line("worker.render.verify", JOB_ID))
    event = session.read_event()
    assert event["event"] == "worker.render.failed", event
    assert event["reasonCode"] == "render_protocol_invalid", event
    code, _ = session.finish()
    assert code == 0
    assert not render_job_directories(JOB_ID)


def test_render_rejects_headless_major_drift(assets: Path, decoy: Path) -> None:
    """The version probe and the running headless browser must agree."""
    if os.name == "nt":
        executable, _ = write_windows_cdp_browser(
            assets / "drift", product="Chrome/150.0.1.1"
        )
    else:
        executable, _ = write_fake_browser(
            assets / "drift", product="Chrome/150.0.1.1"
        )
    session = WorkerSession(
        bootstrap_document(str(assets), render_browser_document(executable)),
        poisoned_environment(decoy),
    )
    expect_ready(session)
    session.send_line(command_line("worker.render.verify", JOB_ID))
    event = session.read_event()
    assert event["event"] == "worker.render.failed", event
    assert event["reasonCode"] == "chromium_major_mismatch", event
    code, _ = session.finish()
    assert code == 0
    assert not render_job_directories(JOB_ID)


def test_render_rejects_replaced_executable(assets: Path, decoy: Path) -> None:
    if os.name == "nt":
        directory = assets / "replaced"
        directory.mkdir()
        executable = directory / "fake-browser.exe"
        shutil.copyfile(sys.executable, executable)
    else:
        executable, _ = write_fake_browser(assets / "replaced")
    session = WorkerSession(
        bootstrap_document(str(assets), render_browser_document(executable)),
        poisoned_environment(decoy),
    )
    expect_ready(session)
    executable.unlink()
    session.send_line(command_line("worker.render.verify", JOB_ID))
    event = session.read_event()
    assert event["event"] == "worker.render.failed", event
    assert event["reasonCode"] == "render_browser_unusable", event
    code, _ = session.finish()
    assert code == 0
    assert not render_job_directories(JOB_ID)


def test_render_timeout_kills_the_browser_process_group(assets: Path, decoy: Path) -> None:
    if os.name == "nt":
        executable, record = write_windows_hanging_browser(assets / "hang")
    else:
        executable, record = write_fake_browser(assets / "hang", hang_seconds=30)
    session = WorkerSession(
        bootstrap_document(
            str(assets), render_browser_document(executable, timeout_seconds=1)
        ),
        poisoned_environment(decoy),
    )
    expect_ready(session)
    started = time.monotonic()
    session.send_line(command_line("worker.render.verify", JOB_ID))
    event = session.read_event()
    assert time.monotonic() - started < 10, "timeout must not wait for the hanging browser"
    assert event["event"] == "worker.render.failed", event
    assert event["reasonCode"] == "render_timeout", event
    code, _ = session.finish()
    assert code == 0
    if os.name == "nt":
        parent, child = [
            int(value) for value in record.read_text(encoding="utf-8").split()
        ]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if not windows_process_exists(parent) and not windows_process_exists(child):
                break
            time.sleep(0.05)
        else:
            raise AssertionError("hanging render browser tree survived the timeout")
        assert not render_job_directories(JOB_ID)
        return
    hanging = read_invocations(record)[-1]["pid"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(hanging, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(hanging, 9)
        raise AssertionError("hanging render browser survived the timeout")
    assert not render_job_directories(JOB_ID)


def test_forged_render_command_is_ignored(assets: Path, decoy: Path) -> None:
    if os.name == "nt":
        directory = assets / "forged"
        directory.mkdir()
        executable = directory / "fake-browser.exe"
        shutil.copyfile(sys.executable, executable)
        record = None
    else:
        executable, record = write_fake_browser(assets / "forged")
    session = WorkerSession(
        bootstrap_document(str(assets), render_browser_document(executable)),
        poisoned_environment(decoy),
    )
    expect_ready(session)
    session.send_line(
        command_line("worker.render.verify", JOB_ID, proof="atvwc1.forged-proof-value")
    )
    session.send_line(command_line("worker.cancel", JOB_ID))
    event = session.read_event()
    assert event["event"] == "worker.cancelled", event
    code, _ = session.finish()
    assert code == 0
    if record is not None:
        assert not read_invocations(record), "forged render command must never touch the browser"


def main() -> int:
    if os.name == "nt":
        tests = [
            test_bootstrap_requires_render_browser_key,
            test_bootstrap_rejects_invalid_render_browser,
            test_null_render_browser_refuses_render_without_discovery,
            test_render_rejects_chromium_major_mismatch,
            test_render_verified_headless_job_isolation_and_cleanup,
            test_render_rejects_invalid_headless_protocol,
            test_render_rejects_headless_major_drift,
            test_render_rejects_replaced_executable,
            test_render_timeout_kills_the_browser_process_group,
            test_forged_render_command_is_ignored,
        ]
    else:
        tests = [
            test_bootstrap_requires_render_browser_key,
            test_bootstrap_rejects_invalid_render_browser,
            test_null_render_browser_refuses_render_without_discovery,
            test_render_rejects_chromium_major_mismatch,
            test_render_verified_headless_job_isolation_and_cleanup,
            test_render_rejects_invalid_headless_protocol,
            test_render_rejects_headless_major_drift,
            test_render_rejects_replaced_executable,
            test_render_timeout_kills_the_browser_process_group,
            test_forged_render_command_is_ignored,
        ]
    for test in tests:
        with tempfile.TemporaryDirectory(prefix="automation-tool-bm03-") as directory:
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
    print("BM-03 render adapter boundary tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
