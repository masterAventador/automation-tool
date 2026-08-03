"""Lightweight private authoring workspace shared by isolated video runtimes."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Final, Never


class MotionAuthoringRejected(RuntimeError):
    """A closed-surface, containment or validation boundary was violated."""


class MotionAuthoringPersistenceError(RuntimeError):
    """A workspace write failed after entering the authoring transaction."""


def _reject(message: str) -> Never:
    raise MotionAuthoringRejected(f"motion authoring rejected: {message}")


_RESERVED_DEVICE_NAMES: Final = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{digit}" for digit in "123456789"}
    | {f"lpt{digit}" for digit in "123456789"}
)


def _validate_relative(path: object) -> str:
    """Return one portable, clean workspace-relative POSIX path."""

    if type(path) is not str or not path:
        _reject("path must be a non-empty string")
    if "\x00" in path or "\\" in path or path.startswith("/"):
        _reject("path must be a clean relative posix path")
    segments = path.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        _reject("path must not contain empty, current or parent segments")
    for segment in segments:
        if ":" in segment:
            _reject("path must not name an alternate data stream")
        if segment != segment.rstrip(" ."):
            _reject("path segment must not end with a dot or a space")
        if segment.split(".", 1)[0].casefold() in _RESERVED_DEVICE_NAMES:
            _reject("path must not name a reserved device")
    return path


def _require_no_case_collision(root: Path, relative: str) -> None:
    current = root
    for segment in relative.split("/"):
        if not current.is_dir():
            return
        folded = segment.casefold()
        for existing in current.iterdir():
            if existing.name != segment and existing.name.casefold() == folded:
                _reject("path collides with an existing entry that differs only by case")
        current = current / segment


class AuthoringWorkspace:
    """A private directory that permits writes only to contained portable paths."""

    def __init__(self, root: Path) -> None:
        if not isinstance(root, Path) or not root.is_absolute():
            _reject("workspace root must be an absolute path")
        if root.is_symlink() or not root.is_dir():
            _reject("workspace root must be a real, non-symlink directory")
        self._root = root.resolve(strict=True)
        self._seeded_assets = self.provided_assets()
        self._authored_targets: set[Path] = set()

    @property
    def root(self) -> Path:
        return self._root

    def resolve(self, relative: str) -> Path:
        clean = _validate_relative(relative)
        target = (self._root / clean).resolve()
        try:
            target.relative_to(self._root)
        except ValueError:
            _reject("path escapes the workspace")
        _require_no_case_collision(self._root, clean)
        return target

    def _write(self, relative: str, emit: Callable[[Path], object]) -> Path:
        try:
            target = self.resolve(relative)
            if target.relative_to(self._root).as_posix() not in self._seeded_assets:
                self._authored_targets.add(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink():
                _reject("refusing to write through a symlink")
            emit(target)
        except MotionAuthoringRejected:
            raise
        except OSError as error:
            self.rollback_authored_files()
            raise MotionAuthoringPersistenceError(
                "motion authoring workspace persistence failed"
            ) from error
        return target

    def write_text(self, relative: str, text: str) -> Path:
        return self._write(
            relative,
            lambda target: target.write_text(text, encoding="utf-8"),
        )

    def write_bytes(self, relative: str, payload: bytes) -> Path:
        if not isinstance(payload, (bytes, bytearray)):
            _reject("workspace bytes must be a bytes payload")
        return self._write(relative, lambda target: target.write_bytes(bytes(payload)))

    def rollback_authored_files(self) -> None:
        for target in self._authored_targets:
            try:
                if target.is_file() and not target.is_symlink():
                    target.unlink()
            except OSError:
                continue

    def read_text(self, relative: str) -> str:
        target = self.resolve(relative)
        if target.is_symlink() or not target.is_file():
            _reject("expected a regular file inside the workspace")
        return target.read_text(encoding="utf-8")

    def provided_assets(self) -> frozenset[str]:
        found: set[str] = set()
        for path in self._root.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            found.add(path.relative_to(self._root).as_posix())
        return frozenset(found)

    def seeded_assets(self) -> frozenset[str]:
        return self._seeded_assets


__all__ = [
    "AuthoringWorkspace",
    "MotionAuthoringPersistenceError",
    "MotionAuthoringRejected",
]
