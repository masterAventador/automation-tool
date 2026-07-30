from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from automation_tool.executor import adaptive_frame_extraction
from automation_tool.executor.adaptive_frame_extraction import (
    AdaptiveFrameRejection,
    BoundedFfmpegOutput,
)

_WRITER = """
from pathlib import Path
import sys
import time

output = Path(sys.argv[1])
size = int(sys.argv[2])
linger = float(sys.argv[3])
exit_code = int(sys.argv[4])
marker = Path(sys.argv[5])
(output / "frame-000001.jpg").write_bytes(b"x" * size)
if linger:
    time.sleep(linger)
marker.write_text("finished", encoding="ascii")
raise SystemExit(exit_code)
"""


def _writer(
    size: int,
    marker: Path,
    *,
    linger: float = 0,
    exit_code: int = 0,
) -> Callable[[Path], list[str]]:
    def build(output: Path) -> list[str]:
        return [
            sys.executable,
            "-c",
            _WRITER,
            str(output),
            str(size),
            str(linger),
            str(exit_code),
            str(marker),
        ]

    return build


def _run(
    build_argv: Callable[[Path], list[str]],
    *,
    seconds: float = 2,
    limit: int = 64,
) -> BoundedFfmpegOutput | AdaptiveFrameRejection:
    return adaptive_frame_extraction._run_bounded_ffmpeg(
        build_argv,
        seconds=seconds,
        output_limit_bytes=limit,
    )


def test_bounded_ffmpeg_accepts_the_exact_limit_and_reads_output_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "finished"
    observed_popen: dict[str, Any] = {}
    real_popen = subprocess.Popen

    def recording_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        observed_popen.update(kwargs)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(
        "automation_tool.executor.adaptive_frame_extraction.subprocess.Popen",
        recording_popen,
    )

    result = _run(_writer(64, marker))

    assert isinstance(result, BoundedFfmpegOutput)
    assert result.files == (("frame-000001.jpg", b"x" * 64),)
    assert marker.read_text(encoding="ascii") == "finished"
    assert observed_popen["stdin"] is subprocess.DEVNULL
    assert observed_popen["stdout"] is subprocess.DEVNULL
    assert observed_popen["stderr"] is subprocess.DEVNULL


def test_bounded_ffmpeg_rejects_one_byte_over_the_limit(tmp_path: Path) -> None:
    result = _run(_writer(65, tmp_path / "finished"))

    assert result is AdaptiveFrameRejection.OUTPUT_LIMIT_EXCEEDED


def test_bounded_ffmpeg_kills_a_writer_as_soon_as_it_exceeds_the_limit(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "must-not-finish"

    result = _run(_writer(65, marker, linger=5), seconds=3)

    assert result is AdaptiveFrameRejection.OUTPUT_LIMIT_EXCEEDED
    assert not marker.exists()


def test_bounded_ffmpeg_kills_and_reaps_a_timed_out_process(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-finish"

    result = _run(_writer(8, marker, linger=5), seconds=0.1)

    assert result is AdaptiveFrameRejection.TIMED_OUT
    assert not marker.exists()


def test_bounded_ffmpeg_kills_and_reaps_when_monitoring_is_interrupted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawned: list[subprocess.Popen[bytes]] = []
    real_popen = subprocess.Popen

    def recording_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        process: subprocess.Popen[bytes] = real_popen(*args, **kwargs)
        spawned.append(process)
        return process

    def monitoring_failure(
        _workspace: Path,
    ) -> tuple[int, tuple[Path, ...]] | AdaptiveFrameRejection:
        raise RuntimeError("monitoring failed")

    monkeypatch.setattr(
        "automation_tool.executor.adaptive_frame_extraction.subprocess.Popen",
        recording_popen,
    )
    monkeypatch.setattr(adaptive_frame_extraction, "_measure_output", monitoring_failure)

    with pytest.raises(RuntimeError, match="monitoring failed"):
        _run(_writer(8, tmp_path / "must-not-finish", linger=1))

    assert len(spawned) == 1
    assert spawned[0].returncode is not None
    assert spawned[0].returncode != 0


def test_failed_tool_reports_an_unusable_workspace_before_bad_media(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_open = Path.open

    def refuse_workspace_probe(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name == adaptive_frame_extraction._WORKSPACE_PROBE_NAME:
            raise OSError("workspace is full")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", refuse_workspace_probe)

    result = _run(_writer(8, tmp_path / "finished", exit_code=1))

    assert result is AdaptiveFrameRejection.WORKSPACE_UNUSABLE


def test_cleanup_failure_cannot_replace_an_undecodable_verdict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_rmtree = shutil.rmtree

    def cleanup_failure(*args: Any, **kwargs: Any) -> None:
        real_rmtree(*args, **kwargs)
        raise OSError("cleanup failed")

    monkeypatch.setattr(
        "automation_tool.executor.adaptive_frame_extraction.shutil.rmtree",
        cleanup_failure,
    )

    result = _run(_writer(8, tmp_path / "finished", exit_code=1))

    assert result is AdaptiveFrameRejection.UNDECODABLE
