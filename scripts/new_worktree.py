#!/usr/bin/env python3
"""Create a worktree that is ready to work in, in seconds instead of minutes.

`git worktree add` is fast, but it leaves every submodule directory empty, and
`git submodule update --init` then has to materialise 879 MB — 581 MB of it
through git-lfs's smudge filter. Measured on 2026-07-26: over ten minutes, and
it has to be run serially because two of them at once will fight.

The main tree already holds a correct checkout at the locked commit. So copy
that instead of asking git to rebuild it. On APFS `cp -c` is a clone: new
inodes pointing at the same data blocks, marked shared. Measured on the same
879 MB: **0.9 seconds and 2 MB of disk**, and the result is a real directory,
not a link.

That last part is not cosmetic. Two cheaper-looking options are both wrong:

* a **symlink** is rejected on purpose — `motion_style_freezer.py:105` requires
  a real non-symlink directory, because vendor's trustworthiness rests on "this
  directory *is* that locked commit's checkout" and a link can point anywhere;
* a **hardlink** shares the inode, so a later write in one worktree writes
  through to the other. That is not a hypothetical: `cp -al` was used here on
  2026-07-26 and a subsequent `git submodule update` in one worktree corrupted
  the *main* tree's submodule — 155 staged deletions and a checkout at the
  wrong commit. The damage was blamed on shared `.git/modules` state until the
  layout was actually checked and found to be per-worktree already.

Filesystems without clone support (ext4, NTFS) fall back to a plain copy. That
is slower but correct; the fast path is an optimisation, never a requirement.

Usage:
    python3 scripts/new_worktree.py <name> [commit]
    python3 scripts/new_worktree.py ui-review origin/main
    python3 scripts/new_worktree.py ui-review origin/main --no-vendor
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKTREE_PARENT = "wt"


def gitdir_pointer(worktree_name: str, submodule_path: str) -> str:
    """The `.git` line git itself writes for a submodule inside a worktree.

    Two levels to climb out of `wt/<name>`, plus one per component of the
    submodule's own path.
    """
    parts = PurePosixPath(submodule_path).parts
    climb = "../" * (2 + len(parts))
    return f"gitdir: {climb}.git/worktrees/{worktree_name}/modules/{submodule_path}"


def clone_directory(source: Path, destination: Path) -> str:
    """Copy-on-write if the filesystem offers it, a plain copy if it does not.

    Returns which path it took, because a silent fallback is a minute slower and
    the operator should be able to see which one happened.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    cloned = subprocess.run(
        # `-p` keeps mtime, which `core.checkStat = minimal` then relies on.
        ["cp", "-c", "-p", "-R", os.fspath(source), os.fspath(destination)],
        capture_output=True,
        check=False,
    )
    if cloned.returncode == 0:
        return "clone"
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, symlinks=True)
    return "copy"


def declared_submodules(root: Path) -> list[str]:
    """Submodule paths as `.gitmodules` declares them — never a hand-kept list."""
    listed = subprocess.run(
        ["git", "config", "--file", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$"],
        cwd=root,
        capture_output=True,
        check=False,
        text=True,
    )
    if listed.returncode != 0:
        return []
    return [line.split(" ", 1)[1] for line in listed.stdout.splitlines() if " " in line]


def install_submodule(root: Path, name: str, submodule_path: str) -> str:
    """Give the worktree its own real checkout, and its own git metadata."""
    worktree = root / WORKTREE_PARENT / name
    destination = worktree / submodule_path

    if destination.exists() and any(destination.iterdir()):
        return "already present"

    if destination.exists():
        destination.rmdir()
    method = clone_directory(root / submodule_path, destination)

    # git gives every worktree its own module directory. A brand new worktree
    # does not have one yet, so clone the main tree's and point the working copy
    # at it — sharing one would put two checkouts behind a single index.
    shared = root / ".git" / "modules" / submodule_path
    private = root / ".git" / "worktrees" / name / "modules" / submodule_path
    if shared.is_dir() and not private.exists():
        clone_directory(shared, private)
        # Edit the config file directly. Neither `cwd=private` nor `--git-dir`
        # works here: the copy still carries the source tree's relative
        # `core.worktree`, and git chdirs to it before doing anything at all —
        # so every form that opens the repository fails before it can fix the
        # very setting that is breaking it. `--file` never opens a repository.
        for key, value in (
            ("core.worktree", os.fspath(destination)),
            # Cloning gives every file a new inode, which invalidates the index's
            # stat cache; git then re-reads each file and runs the clean filter.
            # For an LFS-tracked path whose blob is stored as content rather than
            # as a pointer, that round-trip does not reproduce the blob, and 68
            # untouched files report as modified — enough for
            # `check_third_party_sources.py` to refuse the tree as dirty.
            # `minimal` compares mtime and size and ignores inode, so the copy
            # is recognised for what it is: the same bytes.
            ("core.checkStat", "minimal"),
        ):
            subprocess.run(
                ["git", "config", "--file", os.fspath(private / "config"), key, value],
                check=True,
                capture_output=True,
            )

    (destination / ".git").write_text(
        gitdir_pointer(name, submodule_path) + "\n", encoding="utf-8"
    )
    return method


def verify(root: Path, name: str, submodules: list[str]) -> list[str]:
    """What has to be true before this worktree is worth working in."""
    worktree = root / WORKTREE_PARENT / name
    problems: list[str] = []
    for submodule_path in submodules:
        installed = worktree / submodule_path
        if installed.is_symlink():
            problems.append(
                f"{submodule_path} is a symlink; the vendor gates refuse those"
            )
            continue
        if not installed.is_dir() or not any(installed.iterdir()):
            problems.append(f"{submodule_path} is empty")
            continue
        expected = subprocess.run(
            ["git", "ls-tree", "HEAD", submodule_path],
            cwd=worktree,
            capture_output=True,
            check=False,
            text=True,
        ).stdout.split()
        actual = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=installed,
            capture_output=True,
            check=False,
            text=True,
        ).stdout.strip()
        if len(expected) >= 3 and expected[2] != actual:
            problems.append(
                f"{submodule_path} is at {actual[:9]} but this commit pins "
                f"{expected[2][:9]}"
            )
        # `check_third_party_sources.py` refuses a dirty submodule outright, so
        # a worktree that reports one is not usable no matter how fast it was
        # produced. This is the check that caught the stat-cache problem; the
        # structural checks above were all green while the tree was unusable.
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=installed,
            capture_output=True,
            check=False,
            text=True,
        ).stdout.strip()
        if dirty:
            count = len(dirty.splitlines())
            problems.append(
                f"{submodule_path} reports {count} modified files; upstream "
                "source must be clean"
            )
    return problems


def run(command: list[str], cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name")
    parser.add_argument("commit", nargs="?", default="origin/main")
    parser.add_argument(
        "--no-vendor",
        action="store_true",
        help="skip submodules entirely; correct for lines that never read them",
    )
    parser.add_argument("--branch", help="create this branch instead of detaching")
    arguments = parser.parse_args()

    root = REPOSITORY_ROOT
    worktree = root / WORKTREE_PARENT / arguments.name
    if worktree.exists():
        raise SystemExit(f"{worktree} already exists")

    add = ["git", "worktree", "add"]
    if arguments.branch:
        add += ["-b", arguments.branch]
    run([*add, os.fspath(worktree), arguments.commit], root)

    if arguments.no_vendor:
        print("  vendor skipped by request")
        submodules: list[str] = []
    else:
        submodules = declared_submodules(root)
        for submodule_path in submodules:
            method = install_submodule(root, arguments.name, submodule_path)
            print(f"  {submodule_path}: {method}")

    print("  backend venv…")
    run(["uv", "sync", "--locked"], worktree / "backend")
    print("  frontend node_modules…")
    run(["pnpm", "install", "--frozen-lockfile", "--reporter=silent"], worktree / "frontend")

    problems = verify(root, arguments.name, submodules)
    if problems:
        raise SystemExit("worktree is not usable:\n  " + "\n  ".join(problems))
    print(f"{worktree} is ready")


if __name__ == "__main__":
    main()
