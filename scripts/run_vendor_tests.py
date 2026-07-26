#!/usr/bin/env python3
"""Run an upstream vendor test command in an isolated `.local/` checkout.

Two upstream test paths are known to write beside their inputs. Hyperframes'
regression update mode replaces `packages/producer/tests/*/output/compiled.html`;
MoneyPrinterTurbo's image preprocessing derives an adjacent `.mp4` path from
the input image. Running either command from the checked-out submodule violates
the repository's read-only vendor policy.

This entrypoint makes an independent local clone and index under `.local/` first,
while reusing the source repository's object store read-only. It never installs
dependencies, downloads source, or writes through the submodule's Git metadata.

Known write-producing suites must be invoked through this entrypoint, for
example:

* ``python scripts/run_vendor_tests.py hyperframes -- bun run --cwd
  packages/producer test:regression:update``
* ``python scripts/run_vendor_tests.py moneyprinterturbo --
  /path/to/python-with-upstream-deps -m unittest discover -s test``
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
VENDOR_ROOT = REPOSITORY_ROOT / "vendor"
LOCAL_TEST_ROOT = REPOSITORY_ROOT / ".local" / "vendor-tests"


def extract_archive(
    archive: Path,
    destination: Path,
    *,
    platform_name: str | None = None,
) -> None:
    """Extract Git source safely, with Git-for-Windows symlink semantics.

    Creating a real symlink on Windows commonly requires Developer Mode or an
    elevated token. Git's `core.symlinks=false` checkout behavior is the useful
    fallback for source fixtures: materialize the link target as regular file
    content, preserving the committed bytes without needing symlink privilege.
    """
    destination.mkdir(parents=True, exist_ok=True)
    platform_name = platform_name or os.name
    with tarfile.open(archive) as bundle:
        if platform_name != "nt":
            bundle.extractall(destination, filter="data")
            return
        for member in bundle.getmembers():
            filtered = tarfile.data_filter(member, destination)
            if filtered is None:
                continue
            if filtered.issym():
                target = destination / filtered.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(filtered.linkname, encoding="utf-8", newline="")
                continue
            bundle.extract(filtered, destination, filter="data")


def _git_status(source: Path) -> str:
    completed = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=source,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"cannot inspect vendor source {source}: {detail}")
    return completed.stdout.strip()


def _git_head(source: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"cannot inspect vendor revision {source}: {detail}")
    return completed.stdout.strip()


def _locked_revision(vendor: str) -> str:
    lock_path = (
        REPOSITORY_ROOT / "contracts" / "quality" / "third-party-sources.v1.json"
    )
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot read vendor source lock: {error}") from error
    sources = lock.get("sources") if isinstance(lock, dict) else None
    if not isinstance(sources, list):
        raise RuntimeError("vendor source lock has no sources list")
    path = f"vendor/{vendor}"
    revisions = [
        source.get("commit")
        for source in sources
        if isinstance(source, dict) and source.get("path") == path
    ]
    if len(revisions) != 1 or not isinstance(revisions[0], str):
        raise RuntimeError(f"vendor source lock does not pin {path}")
    return revisions[0]


def materialize_repository(
    source: Path,
    destination: Path,
    revision: str,
    *,
    platform_name: str | None = None,
) -> None:
    """Create an isolated Git checkout while borrowing objects read-only."""
    if destination.exists():
        if any(destination.iterdir()):
            raise RuntimeError(f"isolated destination is not empty: {destination}")
        destination.rmdir()
    destination.parent.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment["GIT_LFS_SKIP_SMUDGE"] = "1"
    completed = subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--shared",
            "--no-checkout",
            os.fspath(source),
            os.fspath(destination),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"cannot clone vendor source {source}: {detail}")

    platform_name = platform_name or os.name
    # Some locked Hyperframes blobs are real snapshot bytes even though a
    # later `.gitattributes` revision marks their paths as LFS. The developer's
    # global LFS clean filter would immediately report those exact committed
    # bytes as dirty. This disposable checkout compares raw committed bytes and
    # never smudges/downloads LFS objects.
    for key, value in (
        ("filter.lfs.process", ""),
        ("filter.lfs.clean", "cat"),
        ("filter.lfs.smudge", "cat"),
        ("filter.lfs.required", "false"),
    ):
        subprocess.run(
            ["git", "config", "--local", key, value],
            cwd=destination,
            capture_output=True,
            text=True,
            check=True,
        )
    if platform_name == "nt":
        subprocess.run(
            ["git", "config", "core.symlinks", "false"],
            cwd=destination,
            capture_output=True,
            text=True,
            check=True,
        )
    source_origin = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=source,
        capture_output=True,
        text=True,
        check=False,
    )
    if source_origin.returncode == 0 and source_origin.stdout.strip():
        subprocess.run(
            ["git", "remote", "set-url", "origin", source_origin.stdout.strip()],
            cwd=destination,
            capture_output=True,
            text=True,
            check=True,
        )
    checked_out = subprocess.run(
        ["git", "checkout", "--quiet", "--detach", revision],
        cwd=destination,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if checked_out.returncode != 0:
        detail = checked_out.stderr.strip() or checked_out.stdout.strip()
        raise RuntimeError(f"cannot check out vendor revision {revision}: {detail}")


def run_in_isolation(
    source: Path,
    local_root: Path,
    command: Sequence[str],
    *,
    expected_revision: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run `command` against an isolated locked checkout below `local_root`."""
    source = source.resolve()
    if not command:
        raise ValueError("a vendor test command is required")
    dirty = _git_status(source)
    if dirty:
        raise RuntimeError(f"vendor source must be clean before testing:\n{dirty}")
    revision_before = _git_head(source)
    if expected_revision is not None and revision_before != expected_revision:
        raise RuntimeError(
            f"vendor source HEAD {revision_before} does not match locked "
            f"commit {expected_revision}"
        )

    local_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f"{source.name}-",
        dir=local_root,
    ) as scratch:
        isolated = Path(scratch) / "source"
        materialize_repository(
            source,
            isolated,
            revision_before,
        )
        completed = subprocess.run(
            list(command),
            cwd=isolated,
            capture_output=True,
            text=True,
            check=False,
        )
        dirty_after = _git_status(source)
        if dirty_after:
            raise RuntimeError(
                "vendor source changed during isolated test; the command escaped "
                f"its .local working directory:\n{dirty_after}"
            )
        revision_after = _git_head(source)
        if revision_after != revision_before:
            raise RuntimeError(
                "vendor source revision changed during isolated test: "
                f"{revision_before} -> {revision_after}"
            )
        return completed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "vendor",
        choices=("hyperframes", "moneyprinterturbo"),
        help="read-only upstream source to clone into an isolated checkout",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="command to run in the isolated source; place it after --",
    )
    arguments = parser.parse_args(argv)
    command = list(arguments.command)
    if command[:1] == ["--"]:
        command.pop(0)
    if not command:
        parser.error("a command is required after --")

    source = (VENDOR_ROOT / arguments.vendor).resolve()
    try:
        source.relative_to(VENDOR_ROOT.resolve())
        completed = run_in_isolation(
            source,
            LOCAL_TEST_ROOT,
            command,
            expected_revision=_locked_revision(arguments.vendor),
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"isolated vendor test failed: {error}")
        return 1
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
