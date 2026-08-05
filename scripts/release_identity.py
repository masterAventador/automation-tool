#!/usr/bin/env python3
"""Derive the source identity that a formal release signature attests to."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

# The file a Windows package carries its release identity in, at the install
# root. macOS keeps the same facts under the Developer ID seal in `Info.plist`
# and needs no name; Windows has no plist, so this name is written by the
# release builder, declared by the bundler configuration and read back by the
# EB-11 runner. Three modules, one definition — the three of them disagreeing
# is exactly the shape where the package looks complete and the runner cannot
# find the file.
PACKAGED_IDENTITY_NAME = "release-identity.v1.json"
POST_ACCEPTANCE_LEDGER_PREFIX = b"docs/development/"
POST_ACCEPTANCE_LEDGER_PATHS = frozenset(
    {
        b"docs/demo-sprint-roadmap.md",
        b"docs/development-roadmap.md",
        b"docs/embedded-browser-video-studio-roadmap.md",
    }
)


class ReleaseIdentityRejected(RuntimeError):
    """The repository cannot provide one stable release source identity."""


@dataclass(frozen=True)
class SourceFacts:
    git_commit: str
    tree_sha256: str


def _git(repository: Path, *arguments: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "-C", os.fspath(repository), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise ReleaseIdentityRejected("release source identity is unavailable") from error


def source_commit_is_ancestor(
    repository: Path,
    ancestor: str,
    descendant: str,
) -> bool:
    """Keep provenance meaningful while an exact source digest carries identity.

    A formal App is built before its acceptance ledger can truthfully be marked
    complete.  The final task commit therefore advances ``HEAD`` even though the
    release-input digest is unchanged.  Accept that transition only when the
    embedded commit remains on the current history; the exact source bytes are
    still bound independently by ``tree_sha256``.
    """

    for commit in (ancestor, descendant):
        if len(commit) not in {40, 64} or any(
            character not in "0123456789abcdef" for character in commit
        ):
            raise ReleaseIdentityRejected("release source commit identity is invalid")
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                os.fspath(repository),
                "merge-base",
                "--is-ancestor",
                ancestor,
                descendant,
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ReleaseIdentityRejected("release source ancestry is unavailable") from error
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise ReleaseIdentityRejected("release source ancestry is unavailable")


def _hash_field(digest: hashlib._Hash, kind: bytes, name: bytes, value: bytes) -> None:
    for field in (kind, name, value):
        digest.update(len(field).to_bytes(8, "big"))
        digest.update(field)


def _is_release_input(name: bytes) -> bool:
    """Exclude ledgers whose required post-acceptance update is not App input."""

    return not (
        name.startswith(POST_ACCEPTANCE_LEDGER_PREFIX)
        or name in POST_ACCEPTANCE_LEDGER_PATHS
    )


def _source_names(root: Path, *, exclude_post_acceptance_ledgers: bool) -> list[bytes]:
    return sorted(
        name
        for name in _git(
            root,
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ).split(b"\0")
        if name and (not exclude_post_acceptance_ledgers or _is_release_input(name))
    )


def _index_entries(root: Path) -> dict[bytes, tuple[bytes, bytes]]:
    entries: dict[bytes, tuple[bytes, bytes]] = {}
    for entry in _git(root, "ls-files", "--stage", "-z", "--cached").split(b"\0"):
        if not entry:
            continue
        try:
            header, name = entry.split(b"\t", 1)
            mode, object_id, stage = header.split()
        except ValueError as error:
            raise ReleaseIdentityRejected("release source index is invalid") from error
        if stage != b"0" or name in entries:
            raise ReleaseIdentityRejected("release source index is unresolved")
        entries[name] = (mode, object_id)
    return entries


def _require_visible_index(root: Path) -> None:
    """Reject index flags that can make the mandatory clean-tree gate lie."""

    for entry in _git(root, "ls-files", "-v", "-z", "--cached").split(b"\0"):
        if not entry:
            continue
        tag = entry[:1]
        # `ls-files -v` lowercases an ordinary tag for assume-unchanged;
        # `S` is skip-worktree. Either can hide changed third-party bytes from
        # `git status`, so a release source may use neither one.
        if tag == b"S" or tag.islower():
            raise ReleaseIdentityRejected(
                "release source index visibility flags are forbidden"
            )


def _git_path(root: Path, name: str) -> Path:
    rendered = Path(os.fsdecode(_git(root, "rev-parse", "--git-path", name).strip()))
    if not rendered.is_absolute():
        rendered = root / rendered
    return rendered.resolve(strict=True)


def _require_inventoried_symlink_target(
    *,
    root: Path,
    path: Path,
    target: str,
    names: list[bytes],
) -> None:
    """Keep every followed symlink byte inside the reviewed source inventory."""

    if Path(target).is_absolute():
        raise ReleaseIdentityRejected(
            "release source symlink target must be relative and inventoried"
        )
    try:
        resolved = (path.parent / target).resolve(strict=True)
        relative = resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as error:
        raise ReleaseIdentityRejected(
            "release source symlink escapes the inventoried repository"
        ) from error

    inventory = {Path(os.fsdecode(name)) for name in names}
    if resolved.is_dir():
        represented = any(relative in candidate.parents for candidate in inventory)
    else:
        represented = relative in inventory
    if not represented:
        raise ReleaseIdentityRejected(
            "release source symlink target is outside the source inventory"
        )


def repository_source_facts(
    repository: Path,
    *,
    _exclude_post_acceptance_ledgers: bool = True,
) -> SourceFacts:
    root = repository.resolve(strict=True)
    top_level = Path(os.fsdecode(_git(root, "rev-parse", "--show-toplevel").strip())).resolve(
        strict=True
    )
    if top_level != root:
        raise ReleaseIdentityRejected("release source root is not the Git worktree root")
    commit = os.fsdecode(_git(root, "rev-parse", "--verify", "HEAD").strip())
    if len(commit) not in {40, 64} or any(character not in "0123456789abcdef" for character in commit):
        raise ReleaseIdentityRejected("release source commit identity is invalid")

    _require_visible_index(root)
    names = _source_names(
        root,
        exclude_post_acceptance_ledgers=_exclude_post_acceptance_ledgers,
    )
    if not names:
        raise ReleaseIdentityRejected("release source inventory is empty")

    index_entries = _index_entries(root)

    digest = hashlib.sha256(b"automation-tool.release-source.v2\0")
    for raw_name in names:
        relative = Path(os.fsdecode(raw_name))
        if relative.is_absolute() or ".." in relative.parts:
            raise ReleaseIdentityRejected("release source inventory contains an unsafe path")
        path = root / relative
        indexed = index_entries.get(raw_name)
        if indexed is not None and indexed[0] == b"160000":
            object_id = indexed[1]
            if not path.is_dir() or path.is_symlink():
                raise ReleaseIdentityRejected("release source submodule is not initialized")
            try:
                checked_out = _git(path, "rev-parse", "--verify", "HEAD").strip()
                dirty = _git(
                    path,
                    "status",
                    "--porcelain=v1",
                    "--untracked-files=all",
                ).strip()
            except ReleaseIdentityRejected as error:
                raise ReleaseIdentityRejected(
                    "release source submodule is unavailable"
                ) from error
            if checked_out != object_id:
                raise ReleaseIdentityRejected("release source submodule commit drifted")
            if dirty:
                raise ReleaseIdentityRejected("release source submodule is dirty")
            submodule = repository_source_facts(
                path,
                _exclude_post_acceptance_ledgers=False,
            )
            if os.fsencode(submodule.git_commit) != object_id:
                raise ReleaseIdentityRejected("release source submodule commit drifted")
            _hash_field(
                digest,
                b"gitlink",
                raw_name,
                object_id + b"\0" + os.fsencode(submodule.tree_sha256),
            )
            continue
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            if indexed is None:
                raise ReleaseIdentityRejected(
                    "release source inventory changed while it was read"
                ) from None
            _hash_field(digest, b"missing", raw_name, b"")
            continue
        if stat.S_ISREG(metadata.st_mode):
            kind = b"executable" if metadata.st_mode & 0o111 else b"file"
            try:
                contents = path.read_bytes()
            except OSError as error:
                raise ReleaseIdentityRejected(
                    "release source inventory changed while it was read"
                ) from error
            _hash_field(digest, kind, raw_name, contents)
        elif stat.S_ISLNK(metadata.st_mode):
            try:
                rendered_target = os.readlink(path)
            except OSError as error:
                raise ReleaseIdentityRejected(
                    "release source inventory changed while it was read"
                ) from error
            _require_inventoried_symlink_target(
                root=root,
                path=path,
                target=rendered_target,
                names=names,
            )
            target = os.fsencode(rendered_target)
            _hash_field(digest, b"symlink", raw_name, target)
        else:
            raise ReleaseIdentityRejected("release source inventory contains an unsupported entry")
    return SourceFacts(git_commit=commit, tree_sha256=digest.hexdigest())


def _remove_snapshot_entry(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
        shutil.rmtree(path)
    else:
        path.unlink()


def _copy_snapshot_entry(source: Path, target: Path) -> None:
    metadata = source.lstat()
    _remove_snapshot_entry(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if stat.S_ISREG(metadata.st_mode):
        shutil.copy2(source, target, follow_symlinks=False)
    elif stat.S_ISLNK(metadata.st_mode):
        target.symlink_to(os.readlink(source))
    else:
        raise ReleaseIdentityRejected(
            "release source inventory contains an unsupported entry"
        )


def _synchronize_clean_submodule(source: Path, target: Path) -> None:
    """Use the verified checkout bytes, not a clone filter's reconstructed bytes."""

    _require_visible_index(source)
    names = [
        name
        for name in _git(source, "ls-files", "-z", "--cached").split(b"\0")
        if name
    ]
    for raw_name in names:
        if not raw_name:
            continue
        relative = Path(os.fsdecode(raw_name))
        if relative.is_absolute() or ".." in relative.parts:
            raise ReleaseIdentityRejected("release source submodule inventory is unsafe")
        _copy_snapshot_entry(source / relative, target / relative)
    try:
        # Preserve the verified source index and its stat cache. copy2 kept
        # mtime/size and checkStat=minimal ignores only the new inode. No
        # assume-unchanged or skip-worktree bit is introduced, so the mandatory
        # third-party source gate can still see later byte drift.
        shutil.copy2(_git_path(source, "index"), _git_path(target, "index"))
        origin = os.fsdecode(_git(source, "remote", "get-url", "origin").strip())
        if not origin:
            raise ReleaseIdentityRejected("release source submodule origin is unavailable")
        _git(target, "remote", "set-url", "origin", origin)
        _git(target, "config", "core.checkStat", "minimal")
    except (OSError, subprocess.SubprocessError) as error:
        raise ReleaseIdentityRejected("release source submodule snapshot is unavailable") from error


def materialize_repository_snapshot(
    repository: Path,
    destination: Path,
    *,
    expected: SourceFacts,
) -> Path:
    """Copy the reviewed source into a detached checkout used by the compiler."""

    root = repository.resolve(strict=True)
    if destination.exists() or destination.is_symlink():
        raise ReleaseIdentityRejected("release source snapshot destination already exists")
    if repository_source_facts(root) != expected:
        raise ReleaseIdentityRejected("release sources changed before snapshot creation")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                "git",
                "clone",
                "--quiet",
                "--no-hardlinks",
                "--no-checkout",
                os.fspath(root),
                os.fspath(destination),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )
        _git(destination, "checkout", "--quiet", "--detach", expected.git_commit)
        checkout_names = set(
            _source_names(destination, exclude_post_acceptance_ledgers=False)
        )
        names = _source_names(root, exclude_post_acceptance_ledgers=True)
        name_set = set(names)
        index_entries = _index_entries(root)
        # The detached checkout reflects HEAD, while the reviewed source may
        # include a staged deletion or rename. Mirror its index, then remove
        # obsolete release inputs before copying the current bytes. Excluded
        # post-acceptance ledgers intentionally remain at HEAD.
        shutil.copy2(_git_path(root, "index"), _git_path(destination, "index"))
        for raw_name in checkout_names - name_set:
            if _is_release_input(raw_name):
                _remove_snapshot_entry(destination / Path(os.fsdecode(raw_name)))
        for raw_name in names:
            relative = Path(os.fsdecode(raw_name))
            source = root / relative
            target = destination / relative
            indexed = index_entries.get(raw_name)
            if indexed is not None and indexed[0] == b"160000":
                _remove_snapshot_entry(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                module_git_directory = destination / ".git" / "modules" / relative
                module_git_directory.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    [
                        "git",
                        "clone",
                        "--quiet",
                        "--no-hardlinks",
                        "--no-checkout",
                        f"--separate-git-dir={module_git_directory}",
                        os.fspath(source),
                        os.fspath(target),
                    ],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=120,
                )
                _git(target, "checkout", "--quiet", "--detach", os.fsdecode(indexed[1]))
                # Hyperframes carries Git LFS-managed fixtures. A local clone can
                # finish checkout with a mixture of pointers and smudged bytes,
                # depending on filter scheduling. The reviewed source checkout
                # has already passed the clean-submodule gate, so copy its exact
                # tracked bytes over the clone before verifying the snapshot.
                _synchronize_clean_submodule(source, target)
                continue
            try:
                source.lstat()
            except FileNotFoundError:
                _remove_snapshot_entry(target)
                continue
            _copy_snapshot_entry(source, target)
        if repository_source_facts(destination) != expected:
            raise ReleaseIdentityRejected("materialized release source identity changed")
        return destination
    except BaseException:
        if destination.exists() and not destination.is_symlink():
            shutil.rmtree(destination)
        raise
