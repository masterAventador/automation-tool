#!/usr/bin/env python3
"""What each gate needs before it can run, and the command that produces it.

Some gates in this repository cannot run on a freshly cloned tree. Each one
needs a build artifact that no runner produces and that nothing declares:

* `scripts/test_motion_catalog_release.py` needs the BM-12 staged catalog;
* `cargo test` needs `frontend/dist`, because `generate_context!` reads it;
* `cargo test --test motion_authoring_runtime` needs the motion Worker package
  installed where a debug App resolves its resources from;
* `scripts/run_cq_03_acceptance.py` needs an EB-16 release package.
* video-line WebdriverIO drivers need the verified embedded-browser cache that
  their shared startup harness stages into the debug App.

The cost of that is not the missing file. It is that a new machine running the
full suite goes red for reasons unrelated to any change, everyone learns the red
is meaningless, and a gate that is genuinely broken sits in the noise. This
repository already has one guard that stayed red for weeks inside that noise.

Two rules this module exists to keep:

**A missing prerequisite is never a skip.** It is either produced or reported.
Nine `#[test]` functions here once returned early when an environment variable
was absent, and libtest printed "1 passed" for work that never happened. A gate
that goes quiet when its inputs are missing is worse than no gate, because it
also spends the budget that would have bought a real one.

**The remedy is derived from the producer, never written next to it.**
`motion_authoring_runtime.rs` told the reader to run
`scripts/prepare_video_runtime.py`; that script exits 0 and writes to the
per-machine build cache, never to the path the test reads. Following the
instruction exactly changed nothing, twice. Here the message is generated from
the same `producer` field that `--ensure` executes, so a wrong pointer is a
wrong command, and `scripts/test_gate_prerequisites.py` runs the cheap ones.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]


class PrerequisiteMissing(RuntimeError):
    """A gate's input is absent and the gate must say so rather than proceed."""


@dataclass(frozen=True)
class Prerequisite:
    """One gate, one artifact it needs, and one command that produces it."""

    name: str
    gate: str
    """The command that goes red without this artifact. Documentation only."""
    produces: tuple[str, ...]
    """Repository-relative paths that must all exist. Never absolute."""
    producer: tuple[str, ...]
    """The argv `--ensure` runs. `{python}` is replaced by this interpreter."""
    automatic: bool
    """Whether a gate may run the producer itself instead of reporting."""
    why: str
    cwd: str = "."
    """Where `producer` runs, relative to the repository root."""
    caveat: str = ""
    """What a human has to know before running a non-automatic producer."""

    def resolved_producer(self) -> list[str]:
        return [
            sys.executable if part == "{python}" else part for part in self.producer
        ]

    def command_line(self) -> str:
        rendered = shlex.join(
            ["python3" if part == "{python}" else part for part in self.producer]
        )
        if self.cwd in ("", "."):
            return rendered
        return f"(cd {self.cwd} && {rendered})"

    def missing(self) -> list[Path]:
        return [
            REPOSITORY_ROOT / relative
            for relative in self.produces
            if not (REPOSITORY_ROOT / relative).exists()
        ]


def _motion_worker_paths() -> tuple[str, ...]:
    """Where a debug `cargo test` resolves the motion Worker from.

    Derived from the same contract `frontend/src-tauri/tests/
    motion_authoring_runtime.rs` reads, because the whole defect here was two
    places computing one path and one of them being wrong. A debug binary built
    by `tauri build --debug --no-bundle` treats the directory holding it as its
    resource root, which is `target/debug` -- the same root
    `scripts/desktop_e2e_prerequisites.py` seeds the embedded browser into.
    """
    import json

    contract = json.loads(
        (
            REPOSITORY_ROOT / "contracts/quality/release-package-resources.v1.json"
        ).read_text(encoding="utf-8")
    )
    worker = next(
        resource
        for resource in contract["resources"]
        if resource["name"] == "motion-video-worker"
    )
    installed = "/".join(["frontend/src-tauri/target/debug", *worker["installedParts"]])
    return (installed, *(f"{installed}/{name}" for name in worker["requiredFiles"]))


def _video_e2e_browser_paths() -> tuple[str, ...]:
    """The cache manifest the shared video WDIO startup harness consumes."""
    from desktop_e2e_prerequisites import (
        DISTRIBUTION_MANIFEST_NAME,
        DesktopPrerequisiteRejected,
        embedded_browser_cache,
        release_target_id,
    )

    try:
        target_id = release_target_id()
    except DesktopPrerequisiteRejected:
        target_id = f"unsupported-{sys.platform}"
    manifest = embedded_browser_cache(target_id) / DISTRIBUTION_MANIFEST_NAME
    return (manifest.relative_to(REPOSITORY_ROOT).as_posix(),)


PREREQUISITES: Final[tuple[Prerequisite, ...]] = (
    Prerequisite(
        name="offline-motion-catalog",
        gate="scripts/test_motion_catalog_release.py",
        produces=(".local/offline-motion-deps/catalog",),
        producer=("{python}", "scripts/build_offline_motion_catalog.py"),
        automatic=False,
        why=(
            "BM-14 rebuilds the release twice from the BM-12 staged catalog and "
            "requires the two to be byte-identical, so the staged tree is the "
            "input, not something the test can synthesise"
        ),
        caveat=(
            "downloads digest-pinned artifacts from jsdelivr, "
            "gstatic and cdnjs, so it needs network; each file is retried up to "
            "four times and already-cached files are skipped"
        ),
    ),
    Prerequisite(
        name="frontend-dist",
        gate="pnpm test:rust (and every other cargo test in src-tauri)",
        produces=("frontend/dist",),
        producer=("pnpm", "build"),
        cwd="frontend",
        automatic=True,
        why=(
            "tauri::generate_context! reads frontendDist at compile time, so "
            "without it cargo test does not fail an assertion, it fails to "
            "build: 'the frontendDist configuration is set to ../dist but this "
            "path doesn't exist'"
        ),
    ),
    Prerequisite(
        name="motion-authoring-runtime",
        gate="cargo test --test motion_authoring_runtime",
        produces=_motion_worker_paths(),
        producer=(
            "{python}",
            "scripts/prepare_video_runtime.py",
            "--only",
            "motion-video-worker",
            "--install-into",
            "frontend/src-tauri/target/debug",
        ),
        automatic=True,
        why=(
            "the test reads the assembled Worker package rather than the "
            "developer-local catalog, on purpose: it used to return early when "
            "the catalog was absent and reported a pass on machines that had "
            "never built it"
        ),
        caveat=(
            "the first run builds the Worker and caches it per machine; later "
            "runs only copy it, and --only keeps ffmpeg out of the build"
        ),
    ),
    Prerequisite(
        name="eb-16-release-package",
        gate="scripts/run_cq_03_acceptance.py",
        produces=(
            ".local/release/cargo-target/release/bundle/macos/自动化运营工具.app",
        ),
        producer=("{python}", "scripts/build_release_package.py"),
        automatic=False,
        why=(
            "CQ-03 runs three business lines against the one Chromium inside a "
            "current production package; a browser resolved from the historical "
            "EB-16 acceptance directory would not be testing the thing that ships"
        ),
        caveat=(
            "macOS only, tens of minutes, and it signs and notarises with the "
            "Developer ID identity — this is deliberately not automatic"
        ),
    ),
    Prerequisite(
        name="video-e2e-embedded-browser",
        gate=(
            "video-line WebdriverIO drivers using scripts/desktop_e2e_prerequisites.py"
        ),
        produces=_video_e2e_browser_paths(),
        producer=("{python}", "scripts/desktop_e2e_prerequisites.py"),
        automatic=True,
        why=(
            "the shared startup harness stages only a verified platform cache; "
            "without its distribution manifest the App is blocked before any "
            "video spec reaches its first assertion"
        ),
        caveat=(
            "builds the platform cache from the already-downloaded locked "
            "Chromium archive and verifies every file; it does not download "
            "the archive"
        ),
    ),
)


def by_name(name: str) -> Prerequisite:
    for prerequisite in PREREQUISITES:
        if prerequisite.name == name:
            return prerequisite
    known = ", ".join(sorted(item.name for item in PREREQUISITES)) or "<none>"
    raise KeyError(f"no prerequisite named {name!r}; declared: {known}")


def explain(prerequisite: Prerequisite, missing: list[Path]) -> str:
    absent = ", ".join(path.relative_to(REPOSITORY_ROOT).as_posix() for path in missing)
    lines = [
        f"{prerequisite.gate} cannot run: {absent} is missing.",
        f"  why: {prerequisite.why}",
        f"  produce it with: {prerequisite.command_line()}",
    ]
    # `--ensure` refuses a non-automatic producer on purpose, so offering it
    # there would hand the reader a second command that does not work.
    if prerequisite.automatic:
        lines.append(
            f"  or: python3 scripts/gate_prerequisites.py --ensure {prerequisite.name}"
        )
    if prerequisite.caveat:
        lines.append(f"  note: {prerequisite.caveat}")
    return "\n".join(lines)


def require(name: str) -> None:
    """Raise with a runnable remedy when a gate's input is absent.

    Never returns a "skipped" outcome: the caller either has its input or gets
    an exception naming the command that produces it.
    """
    prerequisite = by_name(name)
    missing = prerequisite.missing()
    if missing:
        raise PrerequisiteMissing(explain(prerequisite, missing))


def ensure(name: str) -> None:
    """Run the declared producer, then require the artifact to actually exist.

    The post-check is the point. `prepare_video_runtime.py` exits 0 while
    writing nothing to the path its caller needed, so a producer's exit status
    is not evidence that the prerequisite is satisfied.
    """
    prerequisite = by_name(name)
    if not prerequisite.missing():
        return
    if not prerequisite.automatic:
        raise PrerequisiteMissing(
            f"{prerequisite.name} is not produced automatically.\n"
            + explain(prerequisite, prerequisite.missing())
        )
    completed = subprocess.run(
        prerequisite.resolved_producer(),
        cwd=REPOSITORY_ROOT / prerequisite.cwd,
        check=False,
    )
    still_missing = prerequisite.missing()
    if completed.returncode != 0:
        raise PrerequisiteMissing(
            f"{prerequisite.command_line()} exited {completed.returncode} and did "
            f"not produce {prerequisite.name}"
        )
    if still_missing:
        raise PrerequisiteMissing(
            f"{prerequisite.command_line()} exited 0 without producing "
            + ", ".join(
                path.relative_to(REPOSITORY_ROOT).as_posix() for path in still_missing
            )
            + " — the remedy is wrong, not the machine"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--ensure", metavar="NAME")
    arguments = parser.parse_args()

    if arguments.ensure:
        names = (
            [item.name for item in PREREQUISITES if item.automatic]
            if arguments.ensure == "all"
            else [arguments.ensure]
        )
        for name in names:
            print(f"[prerequisites] ensuring {name}", flush=True)
            ensure(name)
        print("[prerequisites] done")
        return 0

    if arguments.list or arguments.check:
        unsatisfied = 0
        for prerequisite in PREREQUISITES:
            missing = prerequisite.missing()
            mark = "MISSING" if missing else "ok     "
            unsatisfied += bool(missing)
            print(f"{mark} {prerequisite.name}  ({prerequisite.gate})")
            if arguments.list or missing:
                print(f"        needs: {', '.join(prerequisite.produces)}")
                print(f"        produce: {prerequisite.command_line()}")
                if prerequisite.caveat:
                    print(f"        note: {prerequisite.caveat}")
        if arguments.check and unsatisfied:
            print(f"\n{unsatisfied} prerequisite(s) missing")
            return 1
        return 0

    parser.print_help()
    return 0


__all__ = [
    "PREREQUISITES",
    "Prerequisite",
    "PrerequisiteMissing",
    "REPOSITORY_ROOT",
    "by_name",
    "ensure",
    "explain",
    "require",
]


if __name__ == "__main__":
    raise SystemExit(main())
