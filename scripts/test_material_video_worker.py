#!/usr/bin/env python3
"""Small process-boundary tests that do not replace the real frozen acceptance."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "workers/material_montage"))

import worker_main  # noqa: E402
from job_observation_bridge import (  # noqa: E402
    CANCEL_FILE,
    OBSERVATION_FILE,
    JobCancelled,
    ObservedTaskState,
)
from webui_runtime import _native_path_for_upstream  # noqa: E402


class MemoryStateFixture:
    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, object]] = {}

    def update_task(
        self, task_id: str, state: int, progress: int, **kwargs: object
    ) -> None:
        self.tasks[task_id] = {"state": state, "progress": progress, **kwargs}

    def get_task(self, task_id: str) -> object:
        return self.tasks.get(task_id)

    def get_all_tasks(self, page: int, page_size: int) -> object:
        return list(self.tasks.values()), len(self.tasks)

    def delete_task(self, task_id: str) -> None:
        self.tasks.pop(task_id, None)


class MaterialVideoWorkerBoundaryTest(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows extended-path boundary")
    def test_webui_normalizes_canonical_windows_paths_before_upstream_use(self) -> None:
        self.assertEqual(
            str(_native_path_for_upstream(Path(r"\\?\C:\workspace\job"))),
            r"C:\workspace\job",
        )
        self.assertEqual(
            str(
                _native_path_for_upstream(
                    Path(r"\\?\UNC\server.example\share\workspace")
                )
            ),
            r"\\server.example\share\workspace",
        )

    def test_job_observations_are_bounded_path_free_and_copy_only_final_video(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="im07-observation-") as directory:
            render_job_id = str(uuid4())
            job_root = Path(directory) / render_job_id
            runtime_root = job_root / "work/.automation-tool-webui/capability"
            output_root = job_root / "outputs"
            task_id = str(uuid4())
            task_root = runtime_root / "storage/tasks" / task_id
            task_root.mkdir(parents=True)
            output_root.mkdir()
            video = task_root / "final-1.mp4"
            video.write_bytes(b"verified-video")
            delegate = MemoryStateFixture()
            bridge = ObservedTaskState(delegate, runtime_root, output_root)

            bridge.update_task(task_id, state=4, progress=25, video_subject="雨后空气")
            running = json.loads((runtime_root / OBSERVATION_FILE).read_text())
            self.assertEqual(running["renderJobId"], render_job_id)
            self.assertEqual(running["status"], "running")
            self.assertEqual(running["progressPercent"], 25)
            self.assertNotIn(str(runtime_root), json.dumps(running))

            bridge.update_task(task_id, state=1, progress=100, videos=[str(video)])
            succeeded = json.loads((runtime_root / OBSERVATION_FILE).read_text())
            self.assertEqual(succeeded["status"], "succeeded")
            self.assertEqual(succeeded["outputFile"], "material-result.mp4")
            self.assertEqual(
                (output_root / "material-result.mp4").read_bytes(), b"verified-video"
            )
            with self.assertRaises(PermissionError):
                bridge.delete_task(task_id)

            (runtime_root / CANCEL_FILE).touch()
            with self.assertRaises(JobCancelled):
                bridge.update_task(task_id, state=4, progress=75)
            cancelled = json.loads((runtime_root / OBSERVATION_FILE).read_text())
            self.assertEqual(cancelled["status"], "cancelled")

    def test_job_observation_rejects_concurrent_or_outside_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix="im07-reject-") as directory:
            job_root = Path(directory) / str(uuid4())
            runtime_root = job_root / "work/.automation-tool-webui/capability"
            output_root = job_root / "outputs"
            first = str(uuid4())
            (runtime_root / "storage/tasks" / first).mkdir(parents=True)
            output_root.mkdir()
            bridge = ObservedTaskState(MemoryStateFixture(), runtime_root, output_root)
            bridge.update_task(first, state=4, progress=1)
            with self.assertRaisesRegex(ValueError, "concurrent"):
                bridge.update_task(str(uuid4()), state=4, progress=1)

            outside = Path(directory) / "outside.mp4"
            outside.write_bytes(b"outside")
            with self.assertRaisesRegex(ValueError, "rendered output"):
                bridge.update_task(first, state=1, progress=100, videos=[str(outside)])

    def test_rejects_missing_or_unknown_commands_without_loading_runtime(self) -> None:
        for arguments in (["--unknown"], ["--probe", "extra"]):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = worker_main.main(arguments)
            self.assertEqual(result, 64)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(
                stderr.getvalue(), "Material video worker command is required\n"
            )

    def test_rejects_missing_bootstrap_without_starting_gateway(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = worker_main.main([], io.StringIO(""))
        self.assertEqual(result, 64)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            stderr.getvalue(), "Material video worker command is required\n"
        )

    def test_dependency_probe_rejects_non_startup_dependency(self) -> None:
        with self.assertRaisesRegex(ValueError, "not part of the startup set"):
            worker_main.dependency_probe("litellm")


if __name__ == "__main__":
    unittest.main()
