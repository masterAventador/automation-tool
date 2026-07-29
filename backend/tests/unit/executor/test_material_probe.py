from __future__ import annotations

import os
from pathlib import Path

import pytest

from automation_tool.executor.material_probe import (
    MAX_TOOL_PATH_CHARACTERS,
    MaterialProbeRejected,
    MaterialProbeRejection,
    PackagedMediaTools,
)


def _executable(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    path.chmod(0o755)
    return path


@pytest.fixture
def tools(tmp_path: Path) -> PackagedMediaTools:
    return PackagedMediaTools(
        ffprobe_path=_executable(tmp_path, "ffprobe"),
        ffmpeg_path=_executable(tmp_path, "ffmpeg"),
    )


def _rejection(excinfo: pytest.ExceptionInfo[MaterialProbeRejected]) -> MaterialProbeRejection:
    return excinfo.value.rejection


class TestPackagedMediaToolsAcceptance:
    def test_accepts_a_pair_of_regular_executables(self, tools: PackagedMediaTools) -> None:
        assert tools.ffprobe_path.name == "ffprobe"
        assert tools.ffmpeg_path.name == "ffmpeg"


class TestPackagedMediaToolsRejectsUnsafePaths:
    """One case per disjunct: a merged case lets a never-true term keep full coverage."""

    def test_rejects_a_non_path_value(self, tmp_path: Path) -> None:
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=str(_executable(tmp_path, "ffprobe")),  # type: ignore[arg-type]
                ffmpeg_path=_executable(tmp_path, "ffmpeg"),
            )
        assert _rejection(excinfo) is MaterialProbeRejection.UNSAFE_PATH

    def test_rejects_a_relative_path(self, tmp_path: Path) -> None:
        _executable(tmp_path, "ffprobe")
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=Path("ffprobe"),
                ffmpeg_path=_executable(tmp_path, "ffmpeg"),
            )
        assert _rejection(excinfo) is MaterialProbeRejection.UNSAFE_PATH

    def test_rejects_a_path_longer_than_the_limit(self, tmp_path: Path) -> None:
        overlong = tmp_path / ("a" * (MAX_TOOL_PATH_CHARACTERS + 1))
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=overlong,
                ffmpeg_path=_executable(tmp_path, "ffmpeg"),
            )
        assert _rejection(excinfo) is MaterialProbeRejection.UNSAFE_PATH

    def test_a_path_at_the_length_limit_passes_the_length_guard(self, tmp_path: Path) -> None:
        """The endpoint itself is allowed: the limit rejects longer, not equal.

        No file can exist at this length, so the proof is *which* reason comes
        back — reaching the filesystem check means the length guard let it by.
        Without this, moving `>` to `>=` changes no test.
        """
        at_limit = Path("/" + "a" * (MAX_TOOL_PATH_CHARACTERS - 1))
        assert len(os.fspath(at_limit)) == MAX_TOOL_PATH_CHARACTERS
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=at_limit,
                ffmpeg_path=_executable(tmp_path, "ffmpeg"),
            )
        assert _rejection(excinfo) is MaterialProbeRejection.UNREADABLE

    def test_rejects_a_path_holding_a_control_character(self, tmp_path: Path) -> None:
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=tmp_path / "ff\x01probe",
                ffmpeg_path=_executable(tmp_path, "ffmpeg"),
            )
        assert _rejection(excinfo) is MaterialProbeRejection.UNSAFE_PATH

    def test_rejects_a_path_holding_a_bidi_override(self, tmp_path: Path) -> None:
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=tmp_path / "ff‮probe",
                ffmpeg_path=_executable(tmp_path, "ffmpeg"),
            )
        assert _rejection(excinfo) is MaterialProbeRejection.UNSAFE_PATH

    def test_rejects_a_path_whose_parent_is_a_symlink(self, tmp_path: Path) -> None:
        real = tmp_path / "real"
        real.mkdir()
        _executable(real, "ffprobe")
        link = tmp_path / "link"
        link.symlink_to(real, target_is_directory=True)
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=link / "ffprobe",
                ffmpeg_path=_executable(tmp_path, "ffmpeg"),
            )
        assert _rejection(excinfo) is MaterialProbeRejection.UNSAFE_PATH

    def test_rejects_a_symlinked_tool_itself(self, tmp_path: Path) -> None:
        link = tmp_path / "ffprobe-link"
        link.symlink_to(_executable(tmp_path, "ffprobe"))
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=link,
                ffmpeg_path=_executable(tmp_path, "ffmpeg"),
            )
        assert _rejection(excinfo) is MaterialProbeRejection.UNSAFE_PATH


class TestPackagedMediaToolsRejectsUnusableTools:
    def test_rejects_a_path_component_the_filesystem_cannot_name(self, tmp_path: Path) -> None:
        """A component over `NAME_MAX` must still come back as a rejection.

        `Path.is_symlink()` only swallows `ENOENT`/`ENOTDIR`/`EBADF`/`ELOOP`
        (`pathlib._IGNORED_ERRNOS`), so `ENAMETOOLONG` propagates. Left
        uncaught it escapes as a bare `OSError` — callers catching
        `MaterialProbeRejected` miss it, and its message carries the full
        private path.

        The total length here stays well inside the limit, so the earlier
        length guard cannot shadow this the way it does for a 4097-character
        single component.
        """
        victim = tmp_path / ("a" * 300)
        assert len(os.fspath(victim)) < MAX_TOOL_PATH_CHARACTERS
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=victim,
                ffmpeg_path=_executable(tmp_path, "ffmpeg"),
            )
        assert _rejection(excinfo) is MaterialProbeRejection.UNREADABLE
        assert str(tmp_path) not in str(excinfo.value)

    def test_rejects_a_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=tmp_path / "absent",
                ffmpeg_path=_executable(tmp_path, "ffmpeg"),
            )
        assert _rejection(excinfo) is MaterialProbeRejection.UNREADABLE

    def test_rejects_a_directory(self, tmp_path: Path) -> None:
        directory = tmp_path / "ffprobe"
        directory.mkdir()
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=directory,
                ffmpeg_path=_executable(tmp_path, "ffmpeg"),
            )
        assert _rejection(excinfo) is MaterialProbeRejection.UNREADABLE

    def test_rejects_a_file_without_the_execute_bit(self, tmp_path: Path) -> None:
        plain = tmp_path / "ffprobe"
        plain.write_text("#!/bin/sh\n", encoding="ascii")
        plain.chmod(0o644)
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=plain,
                ffmpeg_path=_executable(tmp_path, "ffmpeg"),
            )
        assert _rejection(excinfo) is MaterialProbeRejection.UNREADABLE


class TestPackagedMediaToolsGuardsBothTools:
    """The second tool has to be guarded too: a guard with no rejecting case is not a guard."""

    def test_rejects_an_unsafe_ffmpeg_path(self, tmp_path: Path) -> None:
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=_executable(tmp_path, "ffprobe"),
                ffmpeg_path=Path("ffmpeg"),
            )
        assert _rejection(excinfo) is MaterialProbeRejection.UNSAFE_PATH

    def test_rejects_an_unusable_ffmpeg(self, tmp_path: Path) -> None:
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=_executable(tmp_path, "ffprobe"),
                ffmpeg_path=tmp_path / "absent",
            )
        assert _rejection(excinfo) is MaterialProbeRejection.UNREADABLE


class TestPackagedMediaToolsRevalidation:
    def test_revalidate_accepts_while_the_tools_remain(self, tools: PackagedMediaTools) -> None:
        tools.revalidate()

    def test_revalidate_rejects_after_the_tool_is_removed(self, tools: PackagedMediaTools) -> None:
        os.unlink(tools.ffprobe_path)
        with pytest.raises(MaterialProbeRejected) as excinfo:
            tools.revalidate()
        assert _rejection(excinfo) is MaterialProbeRejection.UNREADABLE


class TestPackagedMediaToolsLeaksNoPath:
    def test_repr_reveals_no_path(self, tools: PackagedMediaTools) -> None:
        rendered = repr(tools)
        assert rendered == "PackagedMediaTools(<redacted>)"
        assert str(tools.ffprobe_path) not in rendered

    def test_rejection_message_reveals_no_path(self, tmp_path: Path) -> None:
        secret = tmp_path / "operator-private-name"
        with pytest.raises(MaterialProbeRejected) as excinfo:
            PackagedMediaTools(
                ffprobe_path=secret,
                ffmpeg_path=_executable(tmp_path, "ffmpeg"),
            )
        assert "operator-private-name" not in str(excinfo.value)
        assert str(tmp_path) not in str(excinfo.value)
