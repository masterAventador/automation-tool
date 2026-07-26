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

Scope, stated rather than assumed: it finds runtimes by file name (`node`). A
runtime shipped under another name would be missed. It prints every path it
executed, so an empty scan is visible instead of being read as success — and
`collect_runtime_failures` reports "found nothing" as a failure, because in a
package that is supposed to contain runtimes, finding none is the same bug
wearing a quieter coat.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

RUNTIME_FILE_NAMES = ("node",)
PROBE_EXPRESSION = "process.exit(0)"
PROBE_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class RuntimeFailure:
    path: str
    returncode: int
    output: str


def find_javascript_runtimes(bundle: Path) -> list[Path]:
    found = [
        path
        for path in sorted(bundle.rglob("*"))
        if path.name in RUNTIME_FILE_NAMES
        and path.is_file()
        and not path.is_symlink()
        and os.access(path, os.X_OK)
    ]
    return found


def collect_runtime_failures(bundle: Path) -> list[RuntimeFailure]:
    """Every runtime that could not evaluate an expression, plus the empty case."""
    runtimes = find_javascript_runtimes(bundle)
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

    failures: list[RuntimeFailure] = []
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


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: check_packaged_javascript_runtimes.py <bundle>")
    bundle = Path(sys.argv[1]).resolve()
    runtimes = find_javascript_runtimes(bundle)
    for runtime in runtimes:
        print(f"  executing {runtime}")
    failures = collect_runtime_failures(bundle)
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


if __name__ == "__main__":
    main()
