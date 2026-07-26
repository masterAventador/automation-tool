#!/usr/bin/env python3
"""Execute every JavaScript runtime inside a built package. Do not just look at it.

On 2026-07-26 a signed, notarised, fully audited package shipped with two Node
runtimes that could not run a line of JavaScript. `release_assembly.py` signs
every Mach-O with `--options runtime`, and the signing contract declared
entitlements for `embedded-browser` only, so both `node` binaries got the
hardened runtime without `com.apple.security.cs.allow-jit`. V8 cannot allocate
write-then-execute pages without it and aborts during `Isolate::Init`.

Every gate on the path was green. `require_packaged_video_runtime` asks three
questions — is the directory there, is the file there, is it non-empty — and
**executes nothing**. The acceptance drivers built their own unsigned candidate
and tested that instead. The `node` inside the shipped package had never been
executed by any automation, ever.

The consequence was not subtle: the brand-motion video line and every browser
RPA path were dead in the product users installed, while the suite stayed green.

`node --version` is not enough, and that is the whole point: it prints and exits
before V8 initialises, so it succeeds on a binary that cannot run JavaScript.
This gate evaluates an actual expression.

Scope, stated rather than assumed: it finds Node runtimes by file name (`node`),
so a runtime shipped under another name would be missed. It prints every path it
executed, so an empty scan is visible instead of being read as success — and
`collect_runtime_failures` reports "found nothing" as a failure, because in a
package that is supposed to contain runtimes, finding none is the same bug
wearing a quieter coat.

The embedded Chromium is the other half, added in T104. Until then this gate
printed its own blind spot — eleven binaries carrying allow-jit, two of them
exercised, the largest JavaScript engine in the package among the nine it never
touched — and the only thing that would have caught a bad grant there was a
human scanning a QR code the day before a demo. `probe_embedded_browsers` now
launches it headless and evaluates an expression over CDP, which is the sole
call that returns a *value* from the renderer's V8. Measured on the 2026-07-26
package: it answers correctly, and a copy differing only in the renderer
helper's allow-jit entitlement fails this gate.

`--dump-dom` would have been simpler and does not work: on Chrome 149 it hangs,
and a control group established that it hangs identically on an unrelated
Chromium of the same version, so it is the flag and not the package.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

RUNTIME_FILE_NAMES = ("node",)
PROBE_EXPRESSION = "process.exit(0)"
PROBE_TIMEOUT_SECONDS = 60

EMBEDDED_BROWSER_DIRECTORY_NAME = "embedded-browser"
MACOS_DIRECTORY_NAME = "MacOS"
MACOS_FRAMEWORKS_DIRECTORY_NAME = "Frameworks"
DEVTOOLS_PORT_FILE_NAME = "DevToolsActivePort"
# Arrow functions and array methods, so the answer cannot come from anywhere
# but a live V8: a constant would survive being folded by something that never
# started an isolate.
BROWSER_PROBE_EXPRESSION = '[1,2,3].map(n => n * 2).join("-")'
BROWSER_PROBE_EXPECTED = "2-4-6"
BROWSER_LAUNCH_TIMEOUT_SECONDS = 90
BROWSER_IO_TIMEOUT = 30
_SIGNAL_NUMBERS = {member.value for member in signal.Signals}


@dataclass(frozen=True)
class RuntimeFailure:
    path: str
    returncode: int
    output: str


def find_runtime_candidates(bundle: Path) -> list[Path]:
    """Everything that carries a runtime's name, whatever state it is in.

    Deliberately unfiltered. The two filters that used to live in
    `find_javascript_runtimes` — executable bit and symlink — read like hygiene
    and behaved like silent drops: a package audit on 2026-07-26 shipped a
    non-executable `node` and a symlinked `node` past this gate and got
    `all 1 ... evaluate an expression` with exit 0. Whether such a file is a
    problem is a judgement for `collect_runtime_failures` to state out loud, not
    for a list comprehension to make disappear.
    """
    return [
        path
        for path in sorted(bundle.rglob("*"))
        if path.name in RUNTIME_FILE_NAMES and (path.is_file() or path.is_symlink())
    ]


def find_javascript_runtimes(bundle: Path) -> list[Path]:
    """The candidates this gate can actually execute."""
    return [
        path
        for path in find_runtime_candidates(bundle)
        if path.is_file() and not path.is_symlink() and os.access(path, os.X_OK)
    ]


def collect_runtime_failures(bundle: Path) -> list[RuntimeFailure]:
    """Every runtime that could not evaluate an expression, plus the empty case."""
    candidates = find_runtime_candidates(bundle)
    failures: list[RuntimeFailure] = []
    runnable: list[Path] = []
    for candidate in candidates:
        if candidate.is_symlink():
            failures.append(
                RuntimeFailure(
                    path=str(candidate),
                    returncode=0,
                    output=(
                        "this runtime is a symlink; a shipped runtime must be a "
                        "real file, and skipping it would hide whatever it points at"
                    ),
                )
            )
            continue
        if not os.access(candidate, os.X_OK):
            failures.append(
                RuntimeFailure(
                    path=str(candidate),
                    returncode=0,
                    output=(
                        "this runtime is not executable; the packaging step that "
                        "lost its mode bit has already broken it"
                    ),
                )
            )
            continue
        runnable.append(candidate)

    if failures:
        return failures

    runtimes = runnable
    if not runtimes:
        return [
            RuntimeFailure(
                path=str(bundle),
                returncode=0,
                output=(
                    "no JavaScript runtime was found in this package; a scan "
                    "that matches nothing must not be read as a pass"
                ),
            )
        ]

    # `failures` is still empty here — the pass above returns early otherwise.
    for runtime in runtimes:
        try:
            completed = subprocess.run(
                [os.fspath(runtime), "-e", PROBE_EXPRESSION],
                capture_output=True,
                check=False,
                timeout=PROBE_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError) as error:
            failures.append(RuntimeFailure(str(runtime), -1, f"{error}"))
            continue
        if completed.returncode != 0:
            streams = b"\n".join(
                stream for stream in (completed.stderr, completed.stdout) if stream
            )
            failures.append(
                RuntimeFailure(
                    path=str(runtime),
                    returncode=completed.returncode,
                    output=streams.decode("utf-8", "replace").strip()
                    or "the runtime produced no output",
                )
            )
    return failures


@dataclass(frozen=True)
class BrowserProbe:
    """What the browser probe proved, and which binaries it proved it with."""

    failures: tuple[RuntimeFailure, ...]
    executed: tuple[Path, ...]


def find_embedded_browsers(bundle: Path) -> list[Path]:
    """The browser processes under `embedded-browser/`, not their helpers.

    A Chrome for Testing tree holds five executables: the browser process in
    `<app>/Contents/MacOS`, and four helpers nested under `Frameworks/`. Only
    the first is launchable — the helpers are started *by* it with fork-time
    arguments, and running one directly proves nothing. They are reached the
    way production reaches them: as child processes of a real browser launch.
    """
    browsers: list[Path] = []
    for root in sorted(bundle.rglob(EMBEDDED_BROWSER_DIRECTORY_NAME)):
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.parent.name != MACOS_DIRECTORY_NAME:
                continue
            if MACOS_FRAMEWORKS_DIRECTORY_NAME in path.relative_to(root).parts:
                continue
            if path.is_file() or path.is_symlink():
                browsers.append(path)
    return browsers


def _read_devtools_port(user_data_dir: Path) -> int | None:
    """The port Chrome writes once its DevTools endpoint is actually listening."""
    port_file = user_data_dir / DEVTOOLS_PORT_FILE_NAME
    try:
        lines = port_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if len(lines) < 2 or not lines[0].strip():
        return None
    try:
        return int(lines[0].strip())
    except ValueError:
        return None


def _websocket_connect(url: str) -> tuple[socket.socket, bytes]:
    """A 13-byte-header WebSocket client, because CDP has no other door.

    `Runtime.evaluate` is the only CDP call that returns a *value* from the
    renderer's V8, and CDP only speaks it over a WebSocket. This is stdlib-only
    on purpose: the gate runs during packaging, before any virtualenv with
    Playwright in it is guaranteed to exist, and a gate that cannot run is a
    gate that gets skipped.
    """
    prefix = "ws://"
    if not url.startswith(prefix):
        raise RuntimeError(f"unexpected DevTools URL scheme: {url}")
    host_port, _, path = url[len(prefix) :].partition("/")
    host, _, port = host_port.partition(":")
    connection = socket.create_connection((host, int(port)), timeout=BROWSER_IO_TIMEOUT)
    connection.settimeout(BROWSER_IO_TIMEOUT)
    key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
    connection.sendall(
        (
            f"GET /{path} HTTP/1.1\r\n"
            f"Host: {host_port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
    )
    buffered = b""
    while b"\r\n\r\n" not in buffered:
        chunk = connection.recv(4096)
        if not chunk:
            raise RuntimeError("the browser closed the DevTools connection mid-handshake")
        buffered += chunk
    header, _, trailing = buffered.partition(b"\r\n\r\n")
    if b" 101 " not in header.split(b"\r\n")[0] + b" ":
        raise RuntimeError(f"DevTools refused the WebSocket upgrade: {header[:200]!r}")
    return connection, trailing


def _websocket_send(connection: socket.socket, payload: bytes) -> None:
    mask = secrets.token_bytes(4)
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", 0x81, 0x80 | length)
    elif length < 65536:
        header = struct.pack("!BBH", 0x81, 0x80 | 126, length)
    else:
        header = struct.pack("!BBQ", 0x81, 0x80 | 127, length)
    connection.sendall(
        header + mask + bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
    )


def _websocket_receive_text(connection: socket.socket, buffered: bytes) -> tuple[str, bytes]:
    def need(count: int, data: bytes) -> bytes:
        while len(data) < count:
            chunk = connection.recv(65536)
            if not chunk:
                raise RuntimeError("the browser closed the DevTools connection")
            data += chunk
        return data

    while True:
        buffered = need(2, buffered)
        opcode = buffered[0] & 0x0F
        length = buffered[1] & 0x7F
        offset = 2
        if length == 126:
            buffered = need(4, buffered)
            length = struct.unpack("!H", buffered[2:4])[0]
            offset = 4
        elif length == 127:
            buffered = need(10, buffered)
            length = struct.unpack("!Q", buffered[2:10])[0]
            offset = 10
        buffered = need(offset + length, buffered)
        payload = buffered[offset : offset + length]
        buffered = buffered[offset + length :]
        if opcode == 0x8:
            raise RuntimeError("the browser closed the DevTools connection")
        if opcode == 0x1:
            return payload.decode("utf-8", "replace"), buffered


def descendant_executables(pid: int) -> list[Path]:
    """Which binaries this launch actually ran, measured rather than assumed.

    The coverage line in `summarise_jit_grants` is only worth printing if it
    reports what happened. Chrome's helpers appear in `ps` under their full
    paths, so the process tree is the evidence: it distinguishes "the renderer
    ran" from "we launched something that usually starts a renderer".
    """
    listing = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,comm="], capture_output=True, check=False, text=True
    )
    rows: list[tuple[int, int, str]] = []
    for line in listing.stdout.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) < 3:
            continue
        try:
            rows.append((int(fields[0]), int(fields[1]), fields[2]))
        except ValueError:
            continue
    family = {pid}
    grew = True
    while grew:
        grew = False
        for child, parent, _ in rows:
            if parent in family and child not in family:
                family.add(child)
                grew = True
    return sorted({Path(command) for child, _, command in rows if child in family})


def _evaluate_over_devtools(port: int) -> str:
    """Ask a real page's V8 for a value, the way Playwright does."""
    with urllib.request.urlopen(
        f"http://127.0.0.1:{port}/json/list", timeout=BROWSER_IO_TIMEOUT
    ) as response:
        targets = json.load(response)
    pages = [target for target in targets if target.get("type") == "page"]
    if not pages:
        raise RuntimeError("the browser exposed no page target to evaluate in")
    connection, buffered = _websocket_connect(pages[0]["webSocketDebuggerUrl"])
    try:
        _websocket_send(
            connection,
            json.dumps(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": BROWSER_PROBE_EXPRESSION,
                        "returnByValue": True,
                    },
                }
            ).encode("utf-8"),
        )
        deadline = time.monotonic() + BROWSER_IO_TIMEOUT
        while time.monotonic() < deadline:
            text, buffered = _websocket_receive_text(connection, buffered)
            message = json.loads(text)
            if message.get("id") != 1:
                continue
            if "error" in message:
                raise RuntimeError(f"Runtime.evaluate failed: {message['error']}")
            result = message.get("result", {}).get("result", {})
            if "exceptionDetails" in message.get("result", {}):
                raise RuntimeError(
                    f"the expression threw: {message['result']['exceptionDetails']}"
                )
            return str(result.get("value"))
        raise RuntimeError("the browser never answered Runtime.evaluate")
    finally:
        connection.close()


def _probe_one_browser(browser: Path) -> BrowserProbe:
    """Launch it headless, evaluate in a page, report what happened."""
    if browser.is_symlink() or not browser.is_file():
        return BrowserProbe(
            (
                RuntimeFailure(
                    str(browser),
                    0,
                    "this browser is a symlink; a shipped browser must be a real "
                    "file, and skipping it would hide whatever it points at",
                ),
            ),
            (),
        )
    if not os.access(browser, os.X_OK):
        return BrowserProbe(
            (
                RuntimeFailure(
                    str(browser),
                    0,
                    "this browser is not executable; the packaging step that lost "
                    "its mode bit has already broken it",
                ),
            ),
            (),
        )

    workspace = Path(tempfile.mkdtemp(prefix="automation-tool-jit-probe-"))
    user_data_dir = workspace / "profile"
    user_data_dir.mkdir()
    log = workspace / "browser.log"
    handle = log.open("wb")
    process = subprocess.Popen(
        [
            os.fspath(browser),
            # Headless is not optional here: this gate runs on a developer's
            # machine during packaging and must never take over the screen.
            "--headless",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            # So a failure carries the browser's own account of it.
            "--enable-logging=stderr",
            f"--user-data-dir={user_data_dir}",
            "--remote-debugging-port=0",
            "about:blank",
        ],
        stdout=handle,
        stderr=handle,
    )
    handle.close()
    try:
        deadline = time.monotonic() + BROWSER_LAUNCH_TIMEOUT_SECONDS
        port: int | None = None
        while time.monotonic() < deadline:
            port = _read_devtools_port(user_data_dir)
            if port is not None:
                break
            if process.poll() is not None:
                return BrowserProbe(
                    (
                        RuntimeFailure(
                            str(browser),
                            process.returncode,
                            f"{_describe_exit(process)}. {_describe_process_output(log)}",
                        ),
                    ),
                    (),
                )
            time.sleep(0.2)
        if port is None:
            return BrowserProbe(
                (
                    RuntimeFailure(
                        str(browser),
                        -1,
                        "the browser never opened a DevTools endpoint within "
                        f"{BROWSER_LAUNCH_TIMEOUT_SECONDS}s; it is running but "
                        "unable to serve a page. "
                        + _describe_process_output(log),
                    ),
                ),
                (),
            )

        try:
            value = _evaluate_over_devtools(port)
        except Exception as error:  # noqa: BLE001 - every failure is the answer
            return BrowserProbe(
                (
                    RuntimeFailure(
                        str(browser),
                        process.returncode if process.poll() is not None else -1,
                        f"{error}. {_describe_exit(process)}. "
                        + _describe_process_output(log),
                    ),
                ),
                (),
            )

        if value != BROWSER_PROBE_EXPECTED:
            return BrowserProbe(
                (
                    RuntimeFailure(
                        str(browser),
                        -1,
                        f"the browser evaluated {BROWSER_PROBE_EXPRESSION!r} to "
                        f"{value!r}, expected {BROWSER_PROBE_EXPECTED!r}",
                    ),
                ),
                (),
            )
        # Measured while it is still alive: once it exits, the helpers are gone
        # and the coverage claim would be a guess.
        executed = [browser, *descendant_executables(process.pid)]
        return BrowserProbe((), tuple(sorted(set(executed))))
    finally:
        _terminate_browser(process, workspace)


def _describe_exit(process: subprocess.Popen) -> str:
    """`exit -9` is a number the reader has to decode. Decode it here.

    A negative return code is a signal, and on a real package the signal is the
    diagnosis: a Chromium whose renderer helper carries the hardened runtime
    without allow-jit takes the whole browser down with SIGKILL and prints
    nothing at all. Left as "-9" that reads like a generic crash.
    """
    code = process.poll()
    if code is None:
        return "the browser was still running"
    if code < 0:
        name = signal.Signals(-code).name if -code in _SIGNAL_NUMBERS else "unknown"
        return f"the browser was killed by signal {-code} ({name})"
    return f"the browser exited with status {code}"


def _describe_process_output(log: Path) -> str:
    """Whatever the browser said, because the incident lost exactly this.

    On 2026-07-26 `capture_output=True` swallowed the abort message and the
    real cause — V8 failing inside `Isolate::Init` — had to be recovered by a
    human re-running the command by hand.

    The browser writes to a file rather than a pipe for two reasons, both
    measured: a pipe can only be drained after the process exits, so a browser
    that hangs takes its own explanation down with it; and an undrained pipe
    fills at 64 KB and deadlocks the very process under test.
    """
    try:
        text = log.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return "the browser produced no readable output"
    return text or "the browser produced no output"


def _terminate_browser(process: subprocess.Popen, workspace: Path) -> None:
    """Close the whole tree. A leaked Chrome is a cost the next run pays."""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=BROWSER_IO_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=BROWSER_IO_TIMEOUT)
    # Scoped to this run's unique profile path, so it can only ever match the
    # children of the browser this function started.
    subprocess.run(
        ["pkill", "-f", os.fspath(workspace)], capture_output=True, check=False
    )
    shutil.rmtree(workspace, ignore_errors=True)


def probe_embedded_browsers(bundle: Path) -> BrowserProbe:
    """Make the package's Chromium evaluate an expression, or say why it cannot.

    This is the half of the 2026-07-26 lesson that was left undone. Two `node`
    binaries were made to prove themselves; the embedded Chromium — the largest
    JavaScript engine in the package, and the one every RPA path depends on —
    was only ever looked at. Its `allow-jit` grant was a claim nobody checked,
    and the only thing that would have caught a bad one was a human scanning a
    QR code the day before a demo.
    """
    browsers = find_embedded_browsers(bundle)
    if not browsers:
        return BrowserProbe(
            (
                RuntimeFailure(
                    str(bundle),
                    0,
                    "no embedded browser was found in this package; a scan that "
                    "matches nothing must not be read as a pass",
                ),
            ),
            (),
        )
    failures: list[RuntimeFailure] = []
    executed: list[Path] = []
    for browser in browsers:
        probe = _probe_one_browser(browser)
        failures.extend(probe.failures)
        executed.extend(probe.executed)
    return BrowserProbe(tuple(failures), tuple(sorted(set(executed))))


def binaries_granted_jit(bundle: Path) -> list[Path]:
    """Every code node in the package carrying `com.apple.security.cs.allow-jit`."""
    granted: list[Path] = []
    for path in sorted(bundle.rglob("*")):
        if not path.is_file() or path.is_symlink() or not os.access(path, os.X_OK):
            continue
        shown = subprocess.run(
            ["codesign", "-d", "--entitlements", "-", os.fspath(path)],
            capture_output=True,
            check=False,
        )
        if b"allow-jit" in shown.stdout or b"allow-jit" in shown.stderr:
            granted.append(path)
    return granted


def format_jit_grant_summary(
    *, granted: Iterable[Path], exercised: Iterable[Path]
) -> str:
    """Report the JIT surface as covered and uncovered, by name.

    The audit that broke this gate found eleven binaries carrying allow-jit in
    the shipped package while the gate exercised two. `exercised 2 of 11` was
    true and nearly useless: it took building a deliberately broken package to
    learn that the embedded Chromium was one of the nine. So the uncovered ones
    are named. A number is a fact; a name is a lead.
    """
    granted_paths = list(granted)
    ran = {os.path.realpath(path) for path in exercised}
    covered = [path for path in granted_paths if os.path.realpath(path) in ran]
    missed = [path for path in granted_paths if os.path.realpath(path) not in ran]
    summary = (
        f"{len(granted_paths)} binaries are granted allow-jit; this gate "
        f"exercised {len(covered)} of them by evaluating an expression."
    )
    if not missed:
        return summary + " None are left unexercised."
    listed = "\n".join(f"      {path}" for path in missed)
    return (
        summary
        + f" {len(missed)} not exercised — a JIT grant that never runs is a "
        f"claim nobody checked:\n{listed}"
    )


def summarise_jit_grants(bundle: Path, *, exercised: Iterable[Path]) -> str:
    """Say how much of the package's JIT surface this gate actually ran."""
    return format_jit_grant_summary(
        granted=binaries_granted_jit(bundle), exercised=exercised
    )


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: check_packaged_javascript_runtimes.py <bundle>")
    bundle = Path(sys.argv[1]).resolve()
    runtimes = find_javascript_runtimes(bundle)
    for runtime in runtimes:
        print(f"  executing {runtime}")
    failures = collect_runtime_failures(bundle)

    for browser in find_embedded_browsers(bundle):
        print(f"  evaluating {BROWSER_PROBE_EXPRESSION} in {browser}")
    browser_probe = probe_embedded_browsers(bundle)
    failures.extend(browser_probe.failures)

    if failures:
        detail = "\n".join(
            f"  {failure.path}\n    exit {failure.returncode}: {failure.output}"
            for failure in failures
        )
        raise SystemExit(
            "packaged JavaScript runtime check failed: a runtime in this "
            f"package cannot evaluate an expression.\n{detail}"
        )
    print(f"all {len(runtimes)} packaged JavaScript runtimes evaluate an expression")
    print(
        f"the embedded browser evaluated {BROWSER_PROBE_EXPRESSION} to "
        f"{BROWSER_PROBE_EXPECTED!r} using {len(browser_probe.executed)} binaries"
    )
    exercised = [*runtimes, *browser_probe.executed]
    print(f"  {summarise_jit_grants(bundle, exercised=exercised)}")


if __name__ == "__main__":
    main()
