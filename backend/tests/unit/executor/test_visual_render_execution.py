"""LE-10 T6: bounded execution, ffprobe validation and atomic publication."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, cast
from uuid import uuid4

import pytest

from automation_tool.executor import visual_render_execution as execution
from automation_tool.executor.caption_overlay import (
    CaptionOverlayRejected,
    CaptionOverlayRejection,
    VisualCaptionOverlayBinding,
    VisualCaptionOverlaySet,
)
from automation_tool.executor.material_probe import (
    MaterialProbeRejected,
    MaterialProbeRejection,
    PackagedMediaTools,
    approve_source,
)
from automation_tool.executor.visual_render_execution import (
    VISUAL_RENDER_OUTPUT_FILENAME,
    VisualRenderExecutionRejected,
    VisualRenderExecutionRejection,
    VisualRenderReceipt,
    execute_visual_render,
)
from automation_tool.executor.visual_rendering import VisualRenderSourceBinding
from automation_tool.protocol.local_editing import SegmentSelectionMaterialKind
from automation_tool.protocol.local_rendering import (
    LocalEditingCaptionRenderCue,
    LocalEditingCaptionRenderPlan,
    LocalEditingCaptionRenderStyle,
    LocalEditingVisualRenderClip,
    LocalEditingVisualRenderPlan,
)


def _executable(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_bytes(b"#!/bin/sh\nexit 0\n")
    path.chmod(0o700)
    return path


def _tools(directory: Path) -> PackagedMediaTools:
    return PackagedMediaTools(
        ffprobe_path=_executable(directory, "ffprobe"),
        ffmpeg_path=_executable(directory, "ffmpeg"),
    )


def _request(
    tmp_path: Path,
    *,
    duration_ms: int = 1000,
    output_fps: int = 30,
) -> tuple[
    LocalEditingVisualRenderPlan,
    tuple[VisualRenderSourceBinding, ...],
    tuple[os.stat_result, ...],
]:
    material_id = uuid4()
    source = tmp_path / "source.png"
    source.write_bytes(b"source")
    _, approved = approve_source(source)
    plan = LocalEditingVisualRenderPlan(
        project_id=uuid4(),
        timeline_id=uuid4(),
        timeline_revision=1,
        output_width=720,
        output_height=1280,
        output_fps=output_fps,
        duration_ms=duration_ms,
        clips=(
            LocalEditingVisualRenderClip(
                sequence=1,
                material_id=material_id,
                kind=SegmentSelectionMaterialKind.IMAGE,
                start_ms=0,
                duration_ms=duration_ms,
                source_in_ms=None,
                source_out_ms=None,
                transition_kind=None,
                transition_duration_ms=None,
            ),
        ),
    )
    return (
        plan,
        (
            VisualRenderSourceBinding(
                material_id=material_id,
                kind=SegmentSelectionMaterialKind.IMAGE,
                source_path=source,
            ),
        ),
        (approved,),
    )


def _caption_plan(plan: LocalEditingVisualRenderPlan) -> LocalEditingCaptionRenderPlan:
    return LocalEditingCaptionRenderPlan(
        project_id=plan.project_id,
        timeline_id=plan.timeline_id,
        timeline_revision=plan.timeline_revision,
        output_width=plan.output_width,
        output_height=plan.output_height,
        output_fps=plan.output_fps,
        duration_ms=plan.duration_ms,
        style=LocalEditingCaptionRenderStyle(
            font_key="noto-sans-cjk-sc-bold",
            font_px=48,
            stroke_px=2,
            line_spacing=1.2,
        ),
        cues=(
            LocalEditingCaptionRenderCue(
                sequence=1,
                start_ms=0,
                duration_ms=plan.duration_ms,
                text="字幕",
            ),
        ),
    )


class _StoppingProcess:
    def __init__(self, *, already_stopped: bool = False, wait_times_out: bool = False) -> None:
        self.pid = 12345
        self.already_stopped = already_stopped
        self.wait_times_out = wait_times_out
        self.terminated = False
        self.killed = False
        self.wait_calls = 0

    def poll(self) -> int | None:
        return 0 if self.already_stopped else None

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        if self.wait_times_out and self.wait_calls == 1:
            raise subprocess.TimeoutExpired(
                "redacted",
                timeout if timeout is not None else 0.0,
            )
        return 0


def _valid_probe_payload(
    plan: LocalEditingVisualRenderPlan,
    *,
    size: int = 1,
) -> bytes:
    target_frames = (plan.duration_ms * plan.output_fps + 500) // 1000
    return json.dumps(
        {
            "streams": [
                {
                    "codec_name": "h264",
                    "codec_type": "video",
                    "width": plan.output_width,
                    "height": plan.output_height,
                    "avg_frame_rate": f"{plan.output_fps}/1",
                    "nb_read_frames": str(target_frames),
                }
            ],
            "format": {
                "duration": f"{target_frames / plan.output_fps:.6f}",
                "size": str(size),
            },
        }
    ).encode()


def test_validated_video_is_atomically_published_with_a_path_free_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _tools(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    plan, sources, approvals = _request(tmp_path)

    def run_process(argv: tuple[str, ...], **kwargs: object) -> execution._ProcessResult:
        if argv[0] == str(tools.ffmpeg_path):
            Path(argv[-1]).write_bytes(b"x" * 100)
            return execution._ProcessResult(returncode=0, stdout=b"")
        payload = {
            "streams": [
                {
                    "codec_name": "h264",
                    "codec_type": "video",
                    "width": 720,
                    "height": 1280,
                    "avg_frame_rate": "30/1",
                    "nb_read_frames": "30",
                }
            ],
            "format": {"duration": "1.000000", "size": "100"},
        }
        return execution._ProcessResult(returncode=0, stdout=json.dumps(payload).encode())

    monkeypatch.setattr(execution, "_run_bounded_process", run_process)

    result = execute_visual_render(
        tools,
        plan,
        sources,
        approvals,
        task,
    )

    assert isinstance(result, VisualRenderReceipt)
    assert result.frame_count == 30
    assert result.bytes_written == 100
    assert len(result.sha256) == 64
    assert (task / VISUAL_RENDER_OUTPUT_FILENAME).read_bytes() == b"x" * 100
    assert repr(result) == "VisualRenderReceipt(<redacted>)"
    assert [path.name for path in task.iterdir()] == [VISUAL_RENDER_OUTPUT_FILENAME]


def test_receipt_accepts_a_valid_single_frame_duration() -> None:
    receipt = VisualRenderReceipt(
        frame_count=1,
        width=720,
        height=1280,
        fps=12,
        duration_ms=83,
        bytes_written=1,
        sha256="0" * 64,
    )

    assert receipt.duration_ms == 83


def test_missing_tool_before_ffmpeg_has_a_fixed_tool_unavailable_code(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    plan, sources, approvals = _request(tmp_path)
    tools.ffmpeg_path.unlink()

    with pytest.raises(VisualRenderExecutionRejected) as raised:
        execute_visual_render(tools, plan, sources, approvals, task)

    assert raised.value.code is VisualRenderExecutionRejection.TOOL_UNAVAILABLE
    assert raised.value.__context__ is None
    assert list(task.iterdir()) == []


def test_missing_tool_before_ffprobe_has_a_fixed_tool_unavailable_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _tools(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    plan, sources, approvals = _request(tmp_path)

    def run_process(argv: tuple[str, ...], **kwargs: object) -> execution._ProcessResult:
        if argv[0] == str(tools.ffmpeg_path):
            Path(argv[-1]).write_bytes(b"x")
            tools.ffprobe_path.unlink()
            return execution._ProcessResult(returncode=0, stdout=b"")
        raise OSError("tool disappeared")

    monkeypatch.setattr(execution, "_run_bounded_process", run_process)

    with pytest.raises(VisualRenderExecutionRejected) as raised:
        execute_visual_render(tools, plan, sources, approvals, task)

    assert raised.value.code is VisualRenderExecutionRejection.TOOL_UNAVAILABLE
    assert raised.value.__context__ is None
    assert list(task.iterdir()) == []


def test_tool_removed_between_compilation_and_spawn_is_tool_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _tools(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    plan, sources, approvals = _request(tmp_path)

    def fail_to_spawn(*args: object, **kwargs: object) -> execution._ProcessResult:
        tools.ffmpeg_path.unlink()
        raise FileNotFoundError(os.fspath(tools.ffmpeg_path))

    monkeypatch.setattr(execution, "_run_bounded_process", fail_to_spawn)

    with pytest.raises(VisualRenderExecutionRejected) as raised:
        execute_visual_render(tools, plan, sources, approvals, task)

    assert raised.value.code is VisualRenderExecutionRejection.TOOL_UNAVAILABLE
    assert raised.value.__context__ is None
    assert list(task.iterdir()) == []


def test_non_finite_probe_duration_is_output_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _tools(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    plan, sources, approvals = _request(tmp_path)

    def run_process(argv: tuple[str, ...], **kwargs: object) -> execution._ProcessResult:
        if argv[0] == str(tools.ffmpeg_path):
            Path(argv[-1]).write_bytes(b"x")
            return execution._ProcessResult(returncode=0, stdout=b"")
        payload = {
            "streams": [
                {
                    "codec_name": "h264",
                    "codec_type": "video",
                    "width": 720,
                    "height": 1280,
                    "avg_frame_rate": "30/1",
                    "nb_read_frames": "30",
                }
            ],
            "format": {"duration": "NaN", "size": "1"},
        }
        return execution._ProcessResult(returncode=0, stdout=json.dumps(payload).encode())

    monkeypatch.setattr(execution, "_run_bounded_process", run_process)

    with pytest.raises(VisualRenderExecutionRejected) as raised:
        execute_visual_render(tools, plan, sources, approvals, task)

    assert raised.value.code is VisualRenderExecutionRejection.OUTPUT_INVALID
    assert raised.value.__context__ is None
    assert list(task.iterdir()) == []


def test_oversized_probe_output_has_a_fixed_output_too_large_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _tools(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    plan, sources, approvals = _request(tmp_path)
    calls = 0

    def run_process(argv: tuple[str, ...], **kwargs: object) -> execution._ProcessResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            Path(argv[-1]).write_bytes(b"x")
            return execution._ProcessResult(returncode=0, stdout=b"")
        raise execution._ProcessOutputTooLarge

    monkeypatch.setattr(execution, "_run_bounded_process", run_process)

    with pytest.raises(VisualRenderExecutionRejected) as raised:
        execute_visual_render(tools, plan, sources, approvals, task)

    assert raised.value.code is VisualRenderExecutionRejection.OUTPUT_TOO_LARGE
    assert list(task.iterdir()) == []


def test_bounded_process_stops_a_live_process_as_soon_as_stdout_exceeds_the_limit() -> None:
    with pytest.raises(execution._ProcessOutputTooLarge):
        execution._run_bounded_process(
            (
                sys.executable,
                "-c",
                "import os\nwhile True: os.write(1, b'x' * 65536)",
            ),
            timeout_seconds=1.0,
            cancel_requested=None,
            stdout_limit_bytes=1024,
        )


def test_bounded_process_stops_a_live_process_when_its_output_file_exceeds_the_limit(
    tmp_path: Path,
) -> None:
    output = tmp_path / "working.mp4"

    with pytest.raises(execution._ProcessOutputTooLarge):
        execution._run_bounded_process(
            (
                sys.executable,
                "-c",
                (
                    "import os,sys\n"
                    "stream=open(sys.argv[1], 'wb', buffering=0)\n"
                    "while True: os.write(stream.fileno(), b'x' * 65536)"
                ),
                os.fspath(output),
            ),
            timeout_seconds=1.0,
            cancel_requested=None,
            output_path=output,
            output_limit_bytes=1024,
        )


def test_bounded_process_returns_exit_status_with_no_captured_output() -> None:
    result = execution._run_bounded_process(
        (sys.executable, "-c", "raise SystemExit(3)"),
        timeout_seconds=5.0,
        cancel_requested=None,
    )

    assert result == execution._ProcessResult(returncode=3, stdout=b"")


def test_bounded_process_without_stdout_capture_does_not_allocate_temp_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tempfile,
        "TemporaryFile",
        lambda: (_ for _ in ()).throw(PermissionError("temp unavailable")),
    )

    result = execution._run_bounded_process(
        (sys.executable, "-c", "raise SystemExit(0)"),
        timeout_seconds=5.0,
        cancel_requested=None,
    )

    assert result == execution._ProcessResult(returncode=0, stdout=b"")


def test_bounded_process_returns_small_captured_output() -> None:
    result = execution._run_bounded_process(
        (sys.executable, "-c", "import os; os.write(1, b'ok')"),
        timeout_seconds=5.0,
        cancel_requested=None,
        stdout_limit_bytes=2,
    )

    assert result == execution._ProcessResult(returncode=0, stdout=b"ok")


def test_bounded_process_rejects_output_already_oversized_when_process_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CompletedProcess:
        def poll(self) -> int:
            return 0

        def wait(self, timeout: float | None = None) -> int:
            return 0

    def completed_process(*args: object, **kwargs: object) -> CompletedProcess:
        stdout = cast(BinaryIO, kwargs["stdout"])
        stdout.write(b"oversized")
        stdout.flush()
        return CompletedProcess()

    monkeypatch.setattr(subprocess, "Popen", completed_process)

    with pytest.raises(execution._ProcessOutputTooLarge):
        execution._run_bounded_process(
            ("packaged-tool",),
            timeout_seconds=5.0,
            cancel_requested=None,
            stdout_limit_bytes=1,
        )


@pytest.mark.parametrize(
    ("timeout_seconds", "cancel_requested", "expected"),
    [
        (5.0, lambda: True, execution._ProcessCancelled),
        (0.0, None, execution._ProcessTimedOut),
    ],
)
def test_bounded_process_stops_on_cancel_or_timeout(
    timeout_seconds: float,
    cancel_requested: Callable[[], bool] | None,
    expected: type[Exception],
) -> None:
    with pytest.raises(expected):
        execution._run_bounded_process(
            (sys.executable, "-c", "import time; time.sleep(30)"),
            timeout_seconds=timeout_seconds,
            cancel_requested=cancel_requested,
        )


def test_stop_process_is_a_noop_for_an_already_stopped_process() -> None:
    process = _StoppingProcess(already_stopped=True)

    execution._stop_process(process)  # type: ignore[arg-type]

    assert process.wait_calls == 0


def test_stop_process_escalates_from_term_to_kill_on_posix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _StoppingProcess(wait_times_out=True)
    signals: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(
        os,
        "killpg",
        lambda pid, sig: signals.append((pid, sig)),
        raising=False,
    )

    execution._stop_process(process)  # type: ignore[arg-type]

    assert signals == [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)]
    assert process.wait_calls == 2


def test_stop_process_uses_native_terminate_and_kill_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _StoppingProcess(wait_times_out=True)
    monkeypatch.setattr(os, "name", "nt")

    execution._stop_process(process)  # type: ignore[arg-type]

    assert process.terminated is True
    assert process.killed is True
    assert process.wait_calls == 2


def test_source_changed_during_render_is_rejected_and_every_new_file_is_cleaned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _tools(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    plan, sources, approvals = _request(tmp_path)

    def run_process(argv: tuple[str, ...], **kwargs: object) -> execution._ProcessResult:
        Path(argv[-1]).write_bytes(b"x")
        sources[0].source_path.write_bytes(b"changed")
        return execution._ProcessResult(returncode=0, stdout=b"")

    monkeypatch.setattr(execution, "_run_bounded_process", run_process)

    with pytest.raises(VisualRenderExecutionRejected) as raised:
        execute_visual_render(tools, plan, sources, approvals, task)

    assert raised.value.code is VisualRenderExecutionRejection.SOURCE_CHANGED
    assert raised.value.__context__ is None
    assert list(task.iterdir()) == []


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (execution._ProcessTimedOut, VisualRenderExecutionRejection.TIMED_OUT),
        (execution._ProcessCancelled, VisualRenderExecutionRejection.CANCELLED),
    ],
)
def test_process_control_failure_has_a_fixed_code_and_cleans_working_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: type[Exception],
    expected: VisualRenderExecutionRejection,
) -> None:
    tools = _tools(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    plan, sources, approvals = _request(tmp_path)

    def run_process(argv: tuple[str, ...], **kwargs: object) -> execution._ProcessResult:
        Path(argv[-1]).write_bytes(b"partial")
        raise failure

    monkeypatch.setattr(execution, "_run_bounded_process", run_process)

    with pytest.raises(VisualRenderExecutionRejected) as raised:
        execute_visual_render(tools, plan, sources, approvals, task)

    assert raised.value.code is expected
    assert list(task.iterdir()) == []


def test_keyboard_interrupt_is_preserved_while_working_output_is_cleaned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _tools(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    plan, sources, approvals = _request(tmp_path)

    def run_process(argv: tuple[str, ...], **kwargs: object) -> execution._ProcessResult:
        Path(argv[-1]).write_bytes(b"partial")
        raise KeyboardInterrupt

    monkeypatch.setattr(execution, "_run_bounded_process", run_process)

    with pytest.raises(KeyboardInterrupt):
        execute_visual_render(tools, plan, sources, approvals, task)

    assert list(task.iterdir()) == []


def test_an_existing_final_output_is_never_overwritten(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _tools(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    final = task / VISUAL_RENDER_OUTPUT_FILENAME
    final.write_bytes(b"existing")
    plan, sources, approvals = _request(tmp_path)
    monkeypatch.setattr(
        execution,
        "_run_bounded_process",
        lambda *args, **kwargs: pytest.fail("an existing final must reject before execution"),
    )

    with pytest.raises(VisualRenderExecutionRejected) as raised:
        execute_visual_render(tools, plan, sources, approvals, task)

    assert raised.value.code is VisualRenderExecutionRejection.OUTPUT_EXISTS
    assert final.read_bytes() == b"existing"


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        ("ffmpeg_failure", VisualRenderExecutionRejection.PROCESS_FAILED),
        ("ffprobe_failure", VisualRenderExecutionRejection.PROCESS_FAILED),
        ("missing_output", VisualRenderExecutionRejection.OUTPUT_INVALID),
        ("oversized_output", VisualRenderExecutionRejection.OUTPUT_TOO_LARGE),
    ],
)
def test_render_or_probe_failure_never_publishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutate: str,
    expected: VisualRenderExecutionRejection,
) -> None:
    tools = _tools(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    plan, sources, approvals = _request(tmp_path)
    if mutate == "oversized_output":
        monkeypatch.setattr(execution, "VISUAL_RENDER_MAX_OUTPUT_BYTES", 0)
    calls = 0

    def run_process(argv: tuple[str, ...], **kwargs: object) -> execution._ProcessResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            if mutate != "missing_output":
                Path(argv[-1]).write_bytes(b"x")
            return execution._ProcessResult(
                returncode=1 if mutate == "ffmpeg_failure" else 0,
                stdout=b"",
            )
        return execution._ProcessResult(
            returncode=1 if mutate == "ffprobe_failure" else 0,
            stdout=b"{}",
        )

    monkeypatch.setattr(execution, "_run_bounded_process", run_process)

    with pytest.raises(VisualRenderExecutionRejected) as raised:
        execute_visual_render(tools, plan, sources, approvals, task)

    assert raised.value.code is expected
    assert list(task.iterdir()) == []


def test_empty_ffmpeg_output_is_rejected_before_ffprobe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _tools(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    plan, sources, approvals = _request(tmp_path)
    calls = 0

    def run_process(argv: tuple[str, ...], **kwargs: object) -> execution._ProcessResult:
        nonlocal calls
        calls += 1
        if calls > 1:
            pytest.fail("an empty output must be rejected before ffprobe")
        return execution._ProcessResult(returncode=0, stdout=b"")

    monkeypatch.setattr(execution, "_run_bounded_process", run_process)

    with pytest.raises(VisualRenderExecutionRejected) as raised:
        execute_visual_render(tools, plan, sources, approvals, task)

    assert raised.value.code is VisualRenderExecutionRejection.OUTPUT_INVALID
    assert raised.value.__context__ is None
    assert calls == 1
    assert list(task.iterdir()) == []


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        json.dumps({"streams": [], "format": {}}).encode(),
        json.dumps({"streams": [1], "format": {}}).encode(),
        json.dumps(
            {
                "streams": [
                    {
                        "codec_name": "h264",
                        "codec_type": "video",
                        "width": 720,
                        "height": 1280,
                        "avg_frame_rate": "30/1",
                        "nb_read_frames": None,
                    }
                ],
                "format": {"duration": "1.0", "size": "1"},
            }
        ).encode(),
        json.dumps(
            {
                "streams": [
                    {
                        "codec_name": "h264",
                        "codec_type": "video",
                        "width": 720,
                        "height": 1280,
                        "avg_frame_rate": "30/1",
                        "nb_read_frames": "0",
                    }
                ],
                "format": {"duration": "1.0", "size": "1"},
            }
        ).encode(),
        json.dumps(
            {
                "streams": [
                    {
                        "codec_name": "h264",
                        "codec_type": "video",
                        "width": 720,
                        "height": 1280,
                        "avg_frame_rate": "30/1",
                        "nb_read_frames": "30",
                    }
                ],
                "format": {"duration": 1, "size": "1"},
            }
        ).encode(),
        json.dumps(
            {
                "streams": [
                    {
                        "codec_name": "h264",
                        "codec_type": "video",
                        "width": 720,
                        "height": 1280,
                        "avg_frame_rate": "0/0",
                        "nb_read_frames": "30",
                    }
                ],
                "format": {"duration": "1.0", "size": "1"},
            }
        ).encode(),
        json.dumps(
            {
                "streams": [
                    {
                        "codec_name": "hevc",
                        "codec_type": "video",
                        "width": 720,
                        "height": 1280,
                        "avg_frame_rate": "30/1",
                        "nb_read_frames": "30",
                    }
                ],
                "format": {"duration": "1.0", "size": "1"},
            }
        ).encode(),
        json.dumps(
            {
                "streams": [
                    {
                        "codec_name": "h264",
                        "codec_type": "video",
                        "width": 720,
                        "height": 1280,
                        "avg_frame_rate": "30/1",
                        "nb_read_frames": "29",
                    }
                ],
                "format": {"duration": "1.0", "size": "1"},
            }
        ).encode(),
        json.dumps(
            {
                "streams": [
                    {
                        "codec_name": "h264",
                        "codec_type": "video",
                        "width": 720,
                        "height": 1280,
                        "avg_frame_rate": "30/1",
                        "nb_read_frames": "30",
                    }
                ],
                "format": {"duration": "1.1", "size": "1"},
            }
        ).encode(),
    ],
)
def test_malformed_or_mismatched_probe_facts_are_rejected_without_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
) -> None:
    tools = _tools(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    plan, sources, approvals = _request(tmp_path)
    calls = 0

    def run_process(argv: tuple[str, ...], **kwargs: object) -> execution._ProcessResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            Path(argv[-1]).write_bytes(b"x")
            return execution._ProcessResult(returncode=0, stdout=b"")
        return execution._ProcessResult(returncode=0, stdout=payload)

    monkeypatch.setattr(execution, "_run_bounded_process", run_process)

    with pytest.raises(VisualRenderExecutionRejected) as raised:
        execute_visual_render(tools, plan, sources, approvals, task)

    assert raised.value.code is VisualRenderExecutionRejection.OUTPUT_INVALID
    assert raised.value.__context__ is None
    assert list(task.iterdir()) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("frame_count", True),
        ("frame_count", 0),
        ("width", 127),
        ("width", 721),
        ("height", 4097),
        ("height", 1279),
        ("fps", 11),
        ("duration_ms", 0),
        ("bytes_written", 0),
        ("sha256", "PRIVATE_PATH"),
    ],
)
def test_receipt_rebuild_rejects_invalid_values_with_no_private_diagnostics(
    field: str,
    value: object,
) -> None:
    values: dict[str, object] = {
        "frame_count": 30,
        "width": 720,
        "height": 1280,
        "fps": 30,
        "duration_ms": 1000,
        "bytes_written": 1,
        "sha256": "0" * 64,
    }
    values[field] = value

    with pytest.raises(VisualRenderExecutionRejected) as raised:
        VisualRenderReceipt(**values)  # type: ignore[arg-type]

    assert raised.value.code is VisualRenderExecutionRejection.OUTPUT_INVALID
    assert str(raised.value) == "visual render execution rejected"
    assert "PRIVATE_PATH" not in repr(raised.value)


@pytest.mark.parametrize("bad_directory", ["relative", 123, None])
def test_task_directory_must_be_an_absolute_path_value(
    tmp_path: Path,
    bad_directory: object,
) -> None:
    tools = _tools(tmp_path)
    plan, sources, approvals = _request(tmp_path)

    with pytest.raises(VisualRenderExecutionRejected) as raised:
        execute_visual_render(
            tools,
            plan,
            sources,
            approvals,
            bad_directory,  # type: ignore[arg-type]
        )

    assert raised.value.code is VisualRenderExecutionRejection.INVALID_REQUEST


@pytest.mark.parametrize("shape", ["missing", "file", "symlink", "aliased_parent"])
def test_task_directory_must_resolve_to_itself_as_a_real_directory(
    tmp_path: Path,
    shape: str,
) -> None:
    tools = _tools(tmp_path)
    plan, sources, approvals = _request(tmp_path)
    task = tmp_path / "task"
    if shape == "file":
        task.write_bytes(b"not a directory")
    elif shape == "symlink":
        real = tmp_path / "real"
        real.mkdir()
        task.symlink_to(real, target_is_directory=True)
    elif shape == "aliased_parent":
        real = tmp_path / "real"
        real.mkdir()
        (real / "child").mkdir()
        alias = tmp_path / "alias"
        alias.symlink_to(real, target_is_directory=True)
        task = alias / "child"

    with pytest.raises(VisualRenderExecutionRejected) as raised:
        execute_visual_render(tools, plan, sources, approvals, task)

    assert raised.value.code is VisualRenderExecutionRejection.WORKSPACE_UNUSABLE
    assert raised.value.__context__ is None


@pytest.mark.parametrize(
    ("sources_value", "approvals_value"),
    [
        ([], ()),
        ((), ()),
        ((object(),), (object(),)),
    ],
)
def test_source_bindings_and_approvals_are_rebuilt_as_matching_tuples(
    tmp_path: Path,
    sources_value: object,
    approvals_value: object,
) -> None:
    tools = _tools(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    plan, _, _ = _request(tmp_path)

    with pytest.raises(VisualRenderExecutionRejected) as raised:
        execute_visual_render(
            tools,
            plan,
            sources_value,  # type: ignore[arg-type]
            approvals_value,  # type: ignore[arg-type]
            task,
        )

    assert raised.value.code is VisualRenderExecutionRejection.INVALID_REQUEST
    assert list(task.iterdir()) == []


def test_cancelled_before_workspace_allocation_publishes_nothing(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    plan, sources, approvals = _request(tmp_path)

    with pytest.raises(VisualRenderExecutionRejected) as raised:
        execute_visual_render(
            tools,
            plan,
            sources,
            approvals,
            task,
            cancel_requested=lambda: True,
        )

    assert raised.value.code is VisualRenderExecutionRejection.CANCELLED
    assert list(task.iterdir()) == []


@pytest.mark.parametrize(
    ("link_failure", "expected"),
    [
        (FileExistsError, VisualRenderExecutionRejection.OUTPUT_EXISTS),
        (PermissionError, VisualRenderExecutionRejection.WORKSPACE_UNUSABLE),
    ],
)
def test_atomic_publication_race_has_a_fixed_code_and_cleans_the_work_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    link_failure: type[OSError],
    expected: VisualRenderExecutionRejection,
) -> None:
    tools = _tools(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    plan, sources, approvals = _request(tmp_path)
    calls = 0

    def run_process(argv: tuple[str, ...], **kwargs: object) -> execution._ProcessResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            Path(argv[-1]).write_bytes(b"x")
            return execution._ProcessResult(returncode=0, stdout=b"")
        return execution._ProcessResult(returncode=0, stdout=_valid_probe_payload(plan))

    def fail_link(source: Path, destination: Path) -> None:
        if link_failure is FileExistsError:
            destination.write_bytes(b"winner")
        raise link_failure("publication failed")

    monkeypatch.setattr(execution, "_run_bounded_process", run_process)
    monkeypatch.setattr(os, "link", fail_link)

    with pytest.raises(VisualRenderExecutionRejected) as raised:
        execute_visual_render(tools, plan, sources, approvals, task)

    assert raised.value.code is expected
    assert raised.value.__context__ is None
    expected_names = [VISUAL_RENDER_OUTPUT_FILENAME] if link_failure is FileExistsError else []
    assert [path.name for path in task.iterdir()] == expected_names


@pytest.mark.parametrize("mutation_point", ["probe", "digest"])
def test_output_identity_drift_is_rejected_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation_point: str,
) -> None:
    tools = _tools(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    plan, sources, approvals = _request(tmp_path)
    calls = 0

    def run_process(argv: tuple[str, ...], **kwargs: object) -> execution._ProcessResult:
        nonlocal calls
        calls += 1
        output = Path(argv[-1])
        if calls == 1:
            output.write_bytes(b"x")
            return execution._ProcessResult(returncode=0, stdout=b"")
        if mutation_point == "probe":
            output.write_bytes(b"changed")
        return execution._ProcessResult(returncode=0, stdout=_valid_probe_payload(plan))

    original_sha256 = execution._sha256

    def mutate_during_digest(path: Path) -> str:
        digest = original_sha256(path)
        path.write_bytes(b"changed")
        return digest

    monkeypatch.setattr(execution, "_run_bounded_process", run_process)
    if mutation_point == "digest":
        monkeypatch.setattr(execution, "_sha256", mutate_during_digest)

    with pytest.raises(VisualRenderExecutionRejected) as raised:
        execute_visual_render(tools, plan, sources, approvals, task)

    assert raised.value.code is VisualRenderExecutionRejection.OUTPUT_INVALID
    assert list(task.iterdir()) == []


@pytest.mark.parametrize("caption_drift", ["none", "rewrite", "delete", "symlink"])
def test_caption_files_are_identity_checked_and_always_cleaned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caption_drift: str,
) -> None:
    tools = _tools(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    plan, sources, approvals = _request(tmp_path)
    caption_plan = _caption_plan(plan)
    caption_path = task / "caption-0001.png"
    target_frames = (plan.duration_ms * plan.output_fps + 500) // 1000

    def render_overlays(
        requested: LocalEditingCaptionRenderPlan,
        destination: Path,
    ) -> VisualCaptionOverlaySet:
        assert requested is caption_plan
        assert destination == task
        caption_path.write_bytes(b"png")
        return VisualCaptionOverlaySet(
            project_id=plan.project_id,
            timeline_id=plan.timeline_id,
            timeline_revision=plan.timeline_revision,
            output_width=plan.output_width,
            output_height=plan.output_height,
            output_fps=plan.output_fps,
            duration_ms=plan.duration_ms,
            target_frames=target_frames,
            captions=(
                VisualCaptionOverlayBinding(
                    sequence=1,
                    start_frame=0,
                    end_frame=target_frames,
                    source_path=caption_path,
                ),
            ),
        )

    calls = 0

    def run_process(argv: tuple[str, ...], **kwargs: object) -> execution._ProcessResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            Path(argv[-1]).write_bytes(b"x")
            if caption_drift == "rewrite":
                caption_path.write_bytes(b"changed")
            elif caption_drift == "delete":
                caption_path.unlink()
            elif caption_drift == "symlink":
                caption_path.unlink()
                caption_path.symlink_to(sources[0].source_path)
            return execution._ProcessResult(returncode=0, stdout=b"")
        return execution._ProcessResult(returncode=0, stdout=_valid_probe_payload(plan))

    monkeypatch.setattr(execution, "render_caption_overlay_set", render_overlays)
    monkeypatch.setattr(execution, "_run_bounded_process", run_process)

    if caption_drift != "none":
        with pytest.raises(VisualRenderExecutionRejected) as raised:
            execute_visual_render(
                tools,
                plan,
                sources,
                approvals,
                task,
                caption_plan=caption_plan,
            )
        assert raised.value.code is VisualRenderExecutionRejection.SOURCE_CHANGED
        assert list(task.iterdir()) == []
    else:
        receipt = execute_visual_render(
            tools,
            plan,
            sources,
            approvals,
            task,
            caption_plan=caption_plan,
        )
        assert receipt.frame_count == target_frames
        assert [path.name for path in task.iterdir()] == [VISUAL_RENDER_OUTPUT_FILENAME]


def test_caption_render_rejection_has_a_fixed_caption_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _tools(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    plan, sources, approvals = _request(tmp_path)

    def reject_caption(*args: object, **kwargs: object) -> VisualCaptionOverlaySet:
        raise CaptionOverlayRejected(CaptionOverlayRejection.RENDER_FAILED)

    monkeypatch.setattr(execution, "render_caption_overlay_set", reject_caption)

    with pytest.raises(VisualRenderExecutionRejected) as raised:
        execute_visual_render(
            tools,
            plan,
            sources,
            approvals,
            task,
            caption_plan=_caption_plan(plan),
        )

    assert raised.value.code is VisualRenderExecutionRejection.CAPTION_FAILED
    assert raised.value.__context__ is None
    assert list(task.iterdir()) == []


def test_invalid_caption_identity_is_invalid_request_and_generated_png_is_cleaned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _tools(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    plan, sources, approvals = _request(tmp_path)
    caption_path = task / "caption-0001.png"

    def mismatched_overlays(*args: object, **kwargs: object) -> VisualCaptionOverlaySet:
        caption_path.write_bytes(b"png")
        return VisualCaptionOverlaySet(
            project_id=uuid4(),
            timeline_id=plan.timeline_id,
            timeline_revision=plan.timeline_revision,
            output_width=plan.output_width,
            output_height=plan.output_height,
            output_fps=plan.output_fps,
            duration_ms=plan.duration_ms,
            target_frames=30,
            captions=(
                VisualCaptionOverlayBinding(
                    sequence=1,
                    start_frame=0,
                    end_frame=30,
                    source_path=caption_path,
                ),
            ),
        )

    monkeypatch.setattr(execution, "render_caption_overlay_set", mismatched_overlays)

    with pytest.raises(VisualRenderExecutionRejected) as raised:
        execute_visual_render(
            tools,
            plan,
            sources,
            approvals,
            task,
            caption_plan=_caption_plan(plan),
        )

    assert raised.value.code is VisualRenderExecutionRejection.INVALID_REQUEST
    assert list(task.iterdir()) == []


def test_workspace_allocation_failure_has_a_fixed_workspace_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _tools(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    plan, sources, approvals = _request(tmp_path)
    monkeypatch.setattr(
        tempfile,
        "mkstemp",
        lambda **kwargs: (_ for _ in ()).throw(PermissionError("denied")),
    )

    with pytest.raises(VisualRenderExecutionRejected) as raised:
        execute_visual_render(tools, plan, sources, approvals, task)

    assert raised.value.code is VisualRenderExecutionRejection.WORKSPACE_UNUSABLE
    assert raised.value.__context__ is None
    assert list(task.iterdir()) == []


def test_workspace_descriptor_close_failure_removes_the_allocated_work_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _tools(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    plan, sources, approvals = _request(tmp_path)
    original_close = os.close
    calls = 0

    def fail_close(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("close denied")
        original_close(descriptor)

    monkeypatch.setattr(os, "close", fail_close)

    with pytest.raises(VisualRenderExecutionRejected) as raised:
        execute_visual_render(tools, plan, sources, approvals, task)

    assert raised.value.code is VisualRenderExecutionRejection.WORKSPACE_UNUSABLE
    assert raised.value.__context__ is None
    assert calls == 2
    assert list(task.iterdir()) == []


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (
            MaterialProbeRejected(MaterialProbeRejection.UNREADABLE),
            VisualRenderExecutionRejection.SOURCE_CHANGED,
        ),
        (PermissionError("denied"), VisualRenderExecutionRejection.WORKSPACE_UNUSABLE),
        (RuntimeError("internal"), VisualRenderExecutionRejection.PROCESS_FAILED),
    ],
)
def test_internal_dependency_failures_are_reduced_to_fixed_path_free_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected: VisualRenderExecutionRejection,
) -> None:
    tools = _tools(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    plan, sources, approvals = _request(tmp_path)

    def fail_compile(*args: object, **kwargs: object) -> object:
        raise failure

    if isinstance(failure, MaterialProbeRejected):
        monkeypatch.setattr(execution, "_checked_sources", fail_compile)
    else:
        monkeypatch.setattr(execution, "compile_visual_ffmpeg_command", fail_compile)

    with pytest.raises(VisualRenderExecutionRejected) as raised:
        execute_visual_render(tools, plan, sources, approvals, task)

    assert raised.value.code is expected
    assert str(raised.value) == "visual render execution rejected"
    assert raised.value.__context__ is None
    assert list(task.iterdir()) == []


def test_sha256_read_failure_is_output_invalid(tmp_path: Path) -> None:
    with pytest.raises(VisualRenderExecutionRejected) as raised:
        execution._sha256(tmp_path)

    assert raised.value.code is VisualRenderExecutionRejection.OUTPUT_INVALID
    assert raised.value.__context__ is None


@pytest.mark.parametrize("shape", ["missing", "directory", "symlink"])
def test_output_stat_requires_a_real_regular_file(tmp_path: Path, shape: str) -> None:
    path = tmp_path / "output.mp4"
    if shape == "directory":
        path.mkdir()
    elif shape == "symlink":
        target = tmp_path / "target.mp4"
        target.write_bytes(b"x")
        path.symlink_to(target)

    with pytest.raises(VisualRenderExecutionRejected) as raised:
        execution._stat_file(path)

    assert raised.value.code is VisualRenderExecutionRejection.OUTPUT_INVALID
    assert raised.value.__context__ is None
