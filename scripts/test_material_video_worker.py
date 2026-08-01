#!/usr/bin/env python3
"""Small process-boundary tests that do not replace the real frozen acceptance."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "workers/material_montage"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend/src"))

import build_material_video_worker_candidate as build_candidate_module  # noqa: E402
import gateway  # noqa: E402
import subtitle_font_assets  # noqa: E402
import webui_runtime  # noqa: E402
import worker_main  # noqa: E402
from automation_tool.executor.local_editing_worker import (  # noqa: E402
    LocalEditingWorkerFailureCode,
    LocalMaterialWorkerFailureCode,
    LocalMaterialWorkerStatus,
)
from automation_tool.executor.local_editing_worker_process import (  # noqa: E402
    LocalEditingRenderDiagnosticCode,
    LocalEditingRenderRejected,
    LocalMaterialOperationRejected,
)
from automation_tool.executor.material_probe import (  # noqa: E402
    MaterialFacts,
    ProbedMaterialKind,
)
from automation_tool.executor.smart_edit_generation import (  # noqa: E402
    SmartEditGenerationStage,
)
from automation_tool.executor.smart_edit_worker_process import (  # noqa: E402
    LocalSmartEditStagedJob,
)
from job_observation_bridge import (  # noqa: E402
    CANCEL_FILE,
    OBSERVATION_FILE,
    JobCancelled,
    ObservedTaskState,
)
from webui_runtime import (  # noqa: E402
    WebUiRejected,
    _native_path_for_upstream,
    _prepare_private_project,
    _private_config_document,
    default_subtitle_font_name,
)

UPSTREAM_WEBUI = ROOT / "vendor/moneyprinterturbo/webui"


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
    def test_local_editing_rejection_diagnostic_is_closed_and_path_free(self) -> None:
        error = LocalEditingRenderRejected(
            LocalEditingWorkerFailureCode.MATERIAL_UNAVAILABLE,
            LocalEditingRenderDiagnosticCode.REGISTRY_UNREADABLE,
        )
        output = io.StringIO()

        worker_main._report_local_editing_rejection(error, output)

        self.assertEqual(
            output.getvalue(),
            "Material video worker local editing rejected: registry_unreadable\n",
        )

    def test_local_material_rejection_diagnostic_is_closed_and_path_free(self) -> None:
        error = LocalMaterialOperationRejected(
            LocalMaterialWorkerFailureCode.SOURCE_NOT_AT_REST
        )
        output = io.StringIO()

        worker_main._report_local_material_rejection(error, output)

        self.assertEqual(
            output.getvalue(),
            "Material video worker material operation rejected: source_not_at_rest\n",
        )

    def test_legacy_gateway_bootstrap_still_starts_without_local_editing_tools(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="material-video-legacy-", dir=ROOT / ".local"
        ) as directory:
            asset_root = Path(directory) / "assets"
            asset_root.mkdir()
            document = {
                "assetRoot": str(asset_root),
                "bootstrapVersion": "1",
                "enableWebUi": False,
                "localSessionToken": "a" * 64,
                "protocolVersion": "1.0",
                "renderBrowser": None,
                "scriptModel": None,
                "workerKind": "python",
            }
            stream = io.StringIO(json.dumps(document, separators=(",", ":")) + "\n")
            output = io.StringIO()

            result = worker_main._gateway_process(stream, output)

            self.assertEqual(result, 0)
            ready = json.loads(output.getvalue())
            self.assertEqual(ready["event"], "worker.ready")
            self.assertEqual(ready["workerVersion"], gateway.WORKER_VERSION)

    def test_gateway_bootstrap_accepts_the_strict_local_editing_media_tools_extension(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="material-video-bootstrap-", dir=ROOT / ".local"
        ) as directory:
            root = Path(directory)
            asset_root = root / "assets"
            asset_root.mkdir()
            ffmpeg = root / "ffmpeg"
            ffprobe = root / "ffprobe"
            ffmpeg.touch()
            ffprobe.touch()
            document = {
                "assetRoot": str(asset_root),
                "bootstrapVersion": "1",
                "enableWebUi": False,
                "localSessionToken": "a" * 64,
                "mediaTools": {
                    "ffmpegPath": str(ffmpeg),
                    "ffprobePath": str(ffprobe),
                },
                "protocolVersion": "1.0",
                "renderBrowser": None,
                "scriptModel": None,
                "workerKind": "python",
            }

            parsed = gateway.parse_bootstrap(
                (json.dumps(document, separators=(",", ":")) + "\n").encode()
            )

            self.assertEqual(parsed.asset_root, asset_root)
            self.assertTrue(parsed.local_editing)

            document["mediaTools"] = {"ffmpegPath": str(ffmpeg)}
            with self.assertRaises(gateway.GatewayRejected):
                gateway.parse_bootstrap(
                    (json.dumps(document, separators=(",", ":")) + "\n").encode()
                )

    def test_gateway_dispatches_authenticated_material_import_without_echoing_path(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="material-video-import-", dir=ROOT / ".local"
        ) as directory:
            root = Path(directory)
            asset_root = root / "assets"
            asset_root.mkdir(mode=0o700)
            source = (root / "operator private source.mp4").resolve()
            source.write_bytes(b"source")
            ffmpeg = root / "ffmpeg"
            ffprobe = root / "ffprobe"
            for tool in (ffmpeg, ffprobe):
                tool.write_bytes(b"controlled executable")
                tool.chmod(0o700)
            token = bytes.fromhex("a1" * 32)
            material_id = str(uuid4())
            bootstrap = {
                "assetRoot": str(asset_root),
                "bootstrapVersion": "1",
                "enableWebUi": False,
                "localSessionToken": token.hex(),
                "mediaTools": {
                    "ffmpegPath": str(ffmpeg),
                    "ffprobePath": str(ffprobe),
                },
                "protocolVersion": "1.0",
                "renderBrowser": None,
                "scriptModel": None,
                "workerKind": "python",
            }
            message = b"automation-tool.video-worker-command.v1\0" + b"\0".join(
                part.encode()
                for part in (
                    "worker.material.import",
                    "python",
                    "1.0",
                    material_id,
                    str(source),
                )
            )
            proof = (
                "atvwc1."
                + base64.urlsafe_b64encode(hmac.digest(token, message, hashlib.sha256))
                .rstrip(b"=")
                .decode()
            )
            command = {
                "authenticationProof": proof,
                "command": "worker.material.import",
                "materialId": material_id,
                "protocolVersion": "1.0",
                "sourcePath": str(source),
                "workerKind": "python",
            }
            stream = io.StringIO(
                json.dumps(bootstrap, separators=(",", ":"))
                + "\n"
                + json.dumps(command, separators=(",", ":"))
                + "\n"
            )
            output = io.StringIO()
            facts = MaterialFacts(
                kind=ProbedMaterialKind.VIDEO,
                duration_ms=1000,
                width=720,
                height=1280,
                video_codec="h264",
                audio_codec=None,
                has_audio=False,
                audio_loudness_lufs=None,
                content_digest="ab" * 32,
            )

            with mock.patch(
                "automation_tool.executor.local_editing_worker_process.execute_local_material_import",
                return_value=facts,
            ) as execute:
                result = worker_main._gateway_process(stream, output)

            self.assertEqual(result, 0)
            events = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(
                [item["event"] for item in events],
                [
                    "worker.ready",
                    "worker.material.imported",
                ],
            )
            self.assertNotIn(str(source), output.getvalue())
            imported_command = execute.call_args.args[1]
            self.assertEqual(str(imported_command.material_id), material_id)
            self.assertEqual(imported_command.source_path, source)
            self.assertNotIn(str(source), repr(imported_command))

    def test_gateway_dispatches_authenticated_material_status_without_echoing_path(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="material-video-status-", dir=ROOT / ".local"
        ) as directory:
            root = Path(directory)
            asset_root = root / "assets"
            asset_root.mkdir(mode=0o700)
            ffmpeg = root / "ffmpeg"
            ffprobe = root / "ffprobe"
            for tool in (ffmpeg, ffprobe):
                tool.write_bytes(b"controlled executable")
                tool.chmod(0o700)
            token = bytes.fromhex("a2" * 32)
            material_id = str(uuid4())
            bootstrap = {
                "assetRoot": str(asset_root),
                "bootstrapVersion": "1",
                "enableWebUi": False,
                "localSessionToken": token.hex(),
                "mediaTools": {
                    "ffmpegPath": str(ffmpeg),
                    "ffprobePath": str(ffprobe),
                },
                "protocolVersion": "1.0",
                "renderBrowser": None,
                "scriptModel": None,
                "workerKind": "python",
            }
            message = b"automation-tool.video-worker-command.v1\0" + b"\0".join(
                part.encode()
                for part in (
                    "worker.material.status",
                    "python",
                    "1.0",
                    material_id,
                )
            )
            proof = (
                "atvwc1."
                + base64.urlsafe_b64encode(hmac.digest(token, message, hashlib.sha256))
                .rstrip(b"=")
                .decode()
            )
            command = {
                "authenticationProof": proof,
                "command": "worker.material.status",
                "materialId": material_id,
                "protocolVersion": "1.0",
                "workerKind": "python",
            }
            stream = io.StringIO(
                json.dumps(bootstrap, separators=(",", ":"))
                + "\n"
                + json.dumps(command, separators=(",", ":"))
                + "\n"
            )
            output = io.StringIO()

            with mock.patch(
                "automation_tool.executor.local_editing_worker_process.execute_local_material_status",
                return_value=LocalMaterialWorkerStatus.FILE_CHANGED,
            ) as execute:
                result = worker_main._gateway_process(stream, output)

            self.assertEqual(result, 0)
            events = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(
                [(item["event"], item.get("status")) for item in events],
                [("worker.ready", None), ("worker.material.status", "file_changed")],
            )
            self.assertEqual(str(execute.call_args.args[1].material_id), material_id)

    def test_gateway_holds_smart_edit_result_until_commit_or_timeout(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="material-video-smart-edit-", dir=ROOT / ".local"
        ) as directory:
            root = Path(directory)
            asset_root = root / "assets"
            asset_root.mkdir(mode=0o700)
            ffmpeg = root / "ffmpeg"
            ffprobe = root / "ffprobe"
            for tool in (ffmpeg, ffprobe):
                tool.write_bytes(b"controlled executable")
                tool.chmod(0o700)
            token = bytes.fromhex("a3" * 32)
            job_id = uuid4()
            model_key = "sk-" + "private" * 4
            bootstrap = {
                "assetRoot": str(asset_root),
                "bootstrapVersion": "1",
                "enableWebUi": False,
                "localSessionToken": token.hex(),
                "mediaTools": {
                    "ffmpegPath": str(ffmpeg),
                    "ffprobePath": str(ffprobe),
                },
                "protocolVersion": "1.0",
                "renderBrowser": None,
                "scriptModel": {
                    "apiKey": model_key,
                    "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "modelId": "qwen3.7-max-2026-06-08",
                    "sourceProvider": "bailian",
                    "upstreamProvider": "openai",
                },
                "workerKind": "python",
            }

            def command(name: str) -> str:
                message = b"automation-tool.video-worker-command.v1\0" + b"\0".join(
                    part.encode() for part in (name, "python", "1.0", str(job_id))
                )
                proof = (
                    "atvwc1."
                    + base64.urlsafe_b64encode(
                        hmac.digest(token, message, hashlib.sha256)
                    )
                    .rstrip(b"=")
                    .decode()
                )
                return (
                    json.dumps(
                        {
                            "authenticationProof": proof,
                            "command": name,
                            "jobId": str(job_id),
                            "protocolVersion": "1.0",
                            "workerKind": "python",
                        },
                        separators=(",", ":"),
                    )
                    + "\n"
                )

            prepared = threading.Event()

            class Output(io.StringIO):
                def write(self, value: str) -> int:
                    written = super().write(value)
                    if '"event":"worker.smart_edit.prepared"' in value:
                        prepared.set()
                    return written

            class Stream:
                def __init__(self) -> None:
                    self._bootstrap = (
                        json.dumps(bootstrap, separators=(",", ":")) + "\n"
                    )
                    self._commands = iter(
                        (
                            command("worker.smart_edit.start"),
                            command("worker.smart_edit.commit"),
                        )
                    )
                    self._first = True

                def readline(self, _limit: int = -1) -> str:
                    return self._bootstrap

                def __iter__(self) -> Stream:
                    return self

                def __next__(self) -> str:
                    if self._first:
                        self._first = False
                        return next(self._commands)
                    if not prepared.wait(timeout=5):
                        raise AssertionError("smart edit result was not prepared")
                    return next(self._commands)

            staging = asset_root / "private-smart-staging"
            staging.mkdir(mode=0o700)
            response = asset_root / "private-smart-result.json"
            response.write_bytes(b"{}")
            staged = LocalSmartEditStagedJob(
                job_id=job_id,
                job_root=staging,
                workspace_root=staging,
                response_path=response,
                result_digest="cd" * 32,
                narration_registrations=(),
            )

            def prepare(*_args: object, **kwargs: object) -> LocalSmartEditStagedJob:
                progress = kwargs["progress"]
                self.assertTrue(callable(progress))
                progress(SmartEditGenerationStage.PREPARING, 0)
                progress(SmartEditGenerationStage.COMPLETED, 1_000)
                return staged

            output = Output()
            with (
                mock.patch.object(
                    worker_main, "install_script_model", return_value="locked-model"
                ),
                mock.patch(
                    "automation_tool.executor.smart_edit_worker_process.prepare_smart_edit_job",
                    side_effect=prepare,
                ) as prepare_job,
                mock.patch(
                    "automation_tool.executor.smart_edit_worker_process.commit_smart_edit_job"
                ) as commit_job,
            ):
                result = worker_main._gateway_process(Stream(), output)

            self.assertEqual(result, 0)
            events = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(
                [event["event"] for event in events],
                [
                    "worker.ready",
                    "worker.smart_edit.progress",
                    "worker.smart_edit.progress",
                    "worker.smart_edit.prepared",
                    "worker.smart_edit.succeeded",
                ],
            )
            self.assertEqual(events[-1]["resultDigest"], "cd" * 32)
            prepare_job.assert_called_once()
            commit_job.assert_called_once()
            self.assertNotIn(model_key, output.getvalue())
            self.assertNotIn(str(asset_root), output.getvalue())

            job_id = uuid4()
            timeout_prepared = threading.Event()
            timeout_failed = threading.Event()

            class TimeoutOutput(io.StringIO):
                def write(self, value: str) -> int:
                    written = super().write(value)
                    if '"event":"worker.smart_edit.prepared"' in value:
                        timeout_prepared.set()
                    if '"event":"worker.smart_edit.failed"' in value:
                        timeout_failed.set()
                    return written

            class TimeoutStream:
                def __init__(self) -> None:
                    self._bootstrap = (
                        json.dumps(bootstrap, separators=(",", ":")) + "\n"
                    )
                    self._sent = False

                def readline(self, _limit: int = -1) -> str:
                    return self._bootstrap

                def __iter__(self) -> TimeoutStream:
                    return self

                def __next__(self) -> str:
                    if not self._sent:
                        self._sent = True
                        return command("worker.smart_edit.start")
                    if not timeout_prepared.wait(timeout=5):
                        raise AssertionError("smart edit result was not prepared")
                    if not timeout_failed.wait(timeout=5):
                        raise AssertionError("uncommitted smart edit did not expire")
                    raise StopIteration

            timeout_job = asset_root / "timeout-smart-job"
            timeout_workspace = timeout_job / "staging"
            timeout_workspace.mkdir(parents=True, mode=0o700)
            timeout_response = timeout_job / "result.json"
            timeout_response.write_bytes(b"{}")
            timeout_staged = LocalSmartEditStagedJob(
                job_id=job_id,
                job_root=timeout_job,
                workspace_root=timeout_workspace,
                response_path=timeout_response,
                result_digest="ef" * 32,
                narration_registrations=(),
            )

            def prepare_timeout(
                *_args: object, **kwargs: object
            ) -> LocalSmartEditStagedJob:
                progress = kwargs["progress"]
                self.assertTrue(callable(progress))
                progress(SmartEditGenerationStage.PREPARING, 0)
                progress(SmartEditGenerationStage.COMPLETED, 1_000)
                return timeout_staged

            timeout_output = TimeoutOutput()
            with (
                mock.patch.object(
                    worker_main, "install_script_model", return_value="locked-model"
                ),
                mock.patch.object(
                    worker_main, "SMART_EDIT_COMMIT_TIMEOUT_SECONDS", 0.01
                ),
                mock.patch(
                    "automation_tool.executor.smart_edit_worker_process.prepare_smart_edit_job",
                    side_effect=prepare_timeout,
                ),
            ):
                timeout_result = worker_main._gateway_process(
                    TimeoutStream(), timeout_output
                )

            self.assertEqual(timeout_result, 0)
            timeout_events = [
                json.loads(line) for line in timeout_output.getvalue().splitlines()
            ]
            self.assertEqual(
                [event["event"] for event in timeout_events],
                [
                    "worker.ready",
                    "worker.smart_edit.progress",
                    "worker.smart_edit.progress",
                    "worker.smart_edit.prepared",
                    "worker.smart_edit.failed",
                ],
            )
            self.assertEqual(timeout_events[-1]["failureCode"], "commit_failed")
            self.assertFalse(timeout_job.exists())
            self.assertNotIn(model_key, timeout_output.getvalue())
            self.assertNotIn(str(asset_root), timeout_output.getvalue())

    def test_gateway_does_not_turn_a_rejected_active_cancel_into_legacy_success(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="material-video-active-cancel-", dir=ROOT / ".local"
        ) as directory:
            root = Path(directory)
            asset_root = root / "assets"
            asset_root.mkdir(mode=0o700)
            ffmpeg = root / "ffmpeg"
            ffprobe = root / "ffprobe"
            for tool in (ffmpeg, ffprobe):
                tool.write_bytes(b"controlled executable")
                tool.chmod(0o700)
            token = bytes.fromhex("a4" * 32)
            job_id = uuid4()
            bootstrap = {
                "assetRoot": str(asset_root),
                "bootstrapVersion": "1",
                "enableWebUi": False,
                "localSessionToken": token.hex(),
                "mediaTools": {
                    "ffmpegPath": str(ffmpeg),
                    "ffprobePath": str(ffprobe),
                },
                "protocolVersion": "1.0",
                "renderBrowser": None,
                "scriptModel": None,
                "workerKind": "python",
            }
            message = b"automation-tool.video-worker-command.v1\0" + b"\0".join(
                part.encode()
                for part in ("worker.cancel", "python", "1.0", str(job_id))
            )
            proof = (
                "atvwc1."
                + base64.urlsafe_b64encode(hmac.digest(token, message, hashlib.sha256))
                .rstrip(b"=")
                .decode()
            )
            cancel = {
                "authenticationProof": proof,
                "command": "worker.cancel",
                "jobId": str(job_id),
                "protocolVersion": "1.0",
                "workerKind": "python",
            }
            stream = io.StringIO(
                json.dumps(bootstrap, separators=(",", ":"))
                + "\n"
                + json.dumps(cancel, separators=(",", ":"))
                + "\n"
            )

            class ActiveProtocol:
                def __init__(self, *_args: object) -> None:
                    pass

                def accept_command(self, _payload: bytes) -> object:
                    raise ValueError("active smart-edit command rejected")

                def has_active_operation(self) -> bool:
                    return True

            output = io.StringIO()
            with mock.patch(
                "automation_tool.executor.local_editing_worker.LocalEditingWorkerProtocol",
                ActiveProtocol,
            ):
                result = worker_main._gateway_process(stream, output)

            self.assertEqual(result, 0)
            events = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual([event["event"] for event in events], ["worker.ready"])

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

    def test_smart_edit_runtime_probe_is_fixed_and_path_free(self) -> None:
        stdout = io.StringIO()
        with (
            mock.patch.object(worker_main, "smart_edit_runtime_probe") as probe,
            contextlib.redirect_stdout(stdout),
        ):
            result = worker_main.main(["--probe-smart-edit-runtime"])

        self.assertEqual(result, 0)
        probe.assert_called_once_with()
        self.assertEqual(
            json.loads(stdout.getvalue()),
            {"status": "ready", "runtime": "smart_edit"},
        )

    def test_smart_edit_runtime_probe_hides_internal_failure(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(
                worker_main,
                "smart_edit_runtime_probe",
                side_effect=RuntimeError("/private/operator/model.onnx"),
            ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            result = worker_main.main(["--probe-smart-edit-runtime"])

        self.assertEqual(result, 70)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            stderr.getvalue(),
            "Material video worker smart-edit runtime is unavailable\n",
        )
        self.assertNotIn("operator", stderr.getvalue())

    def test_local_editing_cold_probe_includes_preview_and_smart_edit(self) -> None:
        with mock.patch.object(worker_main.importlib, "import_module") as imported:
            result = worker_main.dependency_probe("local-editing-runtime")

        self.assertEqual(
            [call.args[0] for call in imported.call_args_list],
            [
                "automation_tool.executor.local_editing_worker",
                "automation_tool.executor.local_editing_worker_process",
                "automation_tool.executor.local_material_preview",
                "automation_tool.executor.smart_edit_worker_process",
            ],
        )
        self.assertEqual(
            result,
            {"dependency": "local-editing-runtime", "status": "ready"},
        )

    def test_smart_edit_cold_import_does_not_load_motion_authoring_agent(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(ROOT / "backend/src")
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; "
                    "import automation_tool.executor.smart_edit_worker_process; "
                    "assert "
                    "'automation_tool.executor.motion_authoring.agent' "
                    "not in sys.modules"
                ),
            ],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "")

    def test_dependency_probe_reports_only_a_closed_failure_type(self) -> None:
        stdout = io.StringIO()
        with (
            mock.patch.object(
                worker_main,
                "dependency_probe",
                side_effect=RuntimeError("private path"),
            ),
            contextlib.redirect_stdout(stdout),
        ):
            result = worker_main.main(["--probe-dependency", "local-editing-runtime"])

        self.assertEqual(result, 70)
        self.assertEqual(
            json.loads(stdout.getvalue()),
            {
                "dependency": "local-editing-runtime",
                "failureType": "RuntimeError",
                "status": "unavailable",
            },
        )
        self.assertNotIn("private path", stdout.getvalue())


class MaterialVideoWorkerExcludedModulesTest(unittest.TestCase):
    """The frozen candidate must not carry modules no product path can reach."""

    def test_contract_declares_the_excluded_modules(self) -> None:
        contract = build_candidate_module.load_contract()
        excluded = build_candidate_module.excluded_modules(contract)
        self.assertIn("pyarrow", excluded)
        self.assertIn("tkinter", excluded)
        for required in ("pandas", "streamlit", "moviepy", "faster_whisper"):
            self.assertNotIn(required, excluded)

    def test_spec_reads_the_excluded_modules_from_the_contract(self) -> None:
        spec = (ROOT / "workers/material_montage/material-video-worker.spec").read_text(
            encoding="utf-8"
        )
        self.assertIn("excludedModules", spec)
        self.assertIn("excludes=excluded_modules", spec)
        self.assertNotIn("excludes=[]", spec)

    def test_candidate_carrying_an_excluded_module_is_rejected(self) -> None:
        contract = build_candidate_module.load_contract()
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate"
            (candidate / "_internal/pyarrow").mkdir(parents=True)
            (candidate / "_internal/pyarrow/__init__.py").write_bytes(b"")
            with self.assertRaisesRegex(
                build_candidate_module.MaterialVideoWorkerPackageError, "pyarrow"
            ):
                build_candidate_module.assert_excluded_modules_absent(
                    candidate, contract
                )

    def test_candidate_without_excluded_modules_is_accepted(self) -> None:
        contract = build_candidate_module.load_contract()
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate"
            (candidate / "_internal/pandas").mkdir(parents=True)
            build_candidate_module.assert_excluded_modules_absent(candidate, contract)


class MaterialVideoWorkerProductDependenciesTest(unittest.TestCase):
    def test_build_precreates_the_runtime_with_the_exact_interpreter(self) -> None:
        interpreter = Path("locked-python")
        runtime = Path("isolated-runtime")
        with mock.patch.object(build_candidate_module, "run") as run:
            build_candidate_module.create_locked_python_environment(
                interpreter,
                runtime,
            )

        run.assert_called_once_with(
            [str(interpreter), "-m", "venv", "--without-pip", str(runtime)],
            cwd=ROOT,
        )

    def test_build_resolves_the_exact_contract_python_before_uv_sync(self) -> None:
        contract = build_candidate_module.load_contract()
        version = contract["python"]["version"]
        environment = {"UV_PROJECT_ENVIRONMENT": "isolated-runtime"}
        with tempfile.TemporaryDirectory() as directory:
            interpreter = Path(directory) / "python.exe"
            interpreter.write_bytes(b"MZ")
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=f"{interpreter}\n",
                stderr="",
            )
            with mock.patch.object(
                build_candidate_module,
                "run",
                return_value=completed,
            ) as run:
                resolved = build_candidate_module.resolve_locked_python(
                    contract,
                    environment,
                )

        self.assertEqual(resolved, (version, interpreter))
        run.assert_called_once_with(
            ["uv", "python", "find", version],
            cwd=ROOT,
            environment=environment,
        )

    def test_local_editing_font_runtime_is_a_locked_product_dependency(self) -> None:
        contract = build_candidate_module.load_contract()

        self.assertEqual(
            build_candidate_module.product_dependencies(contract),
            {
                "brotli": "1.2.0",
                "fonttools": "4.63.0",
                "numpy": "2.4.4",
                "onnxruntime": "1.23.2",
            },
        )

    def test_smart_edit_runtime_is_frozen_with_its_locked_model_and_catalog(
        self,
    ) -> None:
        spec = (ROOT / "workers/material_montage/material-video-worker.spec").read_text(
            encoding="utf-8"
        )

        for required in (
            '"automation_tool.executor.smart_edit_worker_process"',
            '"onnxruntime"',
            "contracts/video/bailian-model-catalog.v1.json",
            "contracts/quality/silero-vad-runtime.v1.json",
            "speech/silero-vad",
        ):
            with self.subTest(required):
                self.assertIn(required, spec)

    def test_spec_exposes_backend_metadata_before_importing_product_fonts(self) -> None:
        spec = (ROOT / "workers/material_montage/material-video-worker.spec").read_text(
            encoding="utf-8"
        )

        metadata = spec.index("backend_metadata =")
        metadata_path = spec.index("sys.path.insert(0, str(Path(workpath)))")
        product_import = spec.index(
            "from automation_tool.executor.captions.fonts import"
        )

        self.assertLess(metadata, metadata_path)
        self.assertLess(metadata_path, product_import)

    def test_product_dependencies_must_be_part_of_the_probed_required_set(self) -> None:
        contract = build_candidate_module.load_contract()
        contract["dependencies"]["productRequired"]["fonttools"] = "0.0.0"

        with self.assertRaisesRegex(
            build_candidate_module.MaterialVideoWorkerPackageError,
            "产品依赖",
        ):
            build_candidate_module.product_dependencies(contract)


class MaterialVideoWorkerExcludedUpstreamResourcesTest(unittest.TestCase):
    """Upstream asset directories the product does not ship must never be frozen in."""

    def test_contract_declares_the_excluded_upstream_resources(self) -> None:
        contract = build_candidate_module.load_contract()
        excluded = build_candidate_module.excluded_upstream_resources(contract)
        self.assertIn("songs", excluded)
        for shipped in ("fonts", "public"):
            self.assertNotIn(shipped, excluded)

    def test_spec_ships_upstream_resources_without_the_excluded_ones(self) -> None:
        spec = (ROOT / "workers/material_montage/material-video-worker.spec").read_text(
            encoding="utf-8"
        )
        self.assertIn("excludedUpstreamResources", spec)
        self.assertNotIn('(str(upstream_root / "resource"), "upstream/resource")', spec)

    def test_candidate_carrying_an_excluded_upstream_resource_is_rejected(self) -> None:
        contract = build_candidate_module.load_contract()
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate"
            songs = candidate / "_internal/upstream/resource/songs"
            songs.mkdir(parents=True)
            (songs / "output000.mp3").write_bytes(b"")
            with self.assertRaisesRegex(
                build_candidate_module.MaterialVideoWorkerPackageError, "songs"
            ):
                build_candidate_module.assert_excluded_upstream_resources_absent(
                    candidate, contract
                )

    def test_candidate_without_excluded_upstream_resources_is_accepted(self) -> None:
        contract = build_candidate_module.load_contract()
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate"
            (candidate / "_internal/upstream/resource/fonts").mkdir(parents=True)
            build_candidate_module.assert_excluded_upstream_resources_absent(
                candidate, contract
            )


OFL_HEADLINE = "SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007"


def _name_table(entries: list[tuple[int, int, int, int, str]]) -> bytes:
    """Build a minimal sfnt `name` table so tests need no real 17 MB font."""
    records = b""
    strings = b""
    for platform, encoding, language, name_id, text in entries:
        raw = text.encode("utf-16-be") if platform in (0, 3) else text.encode("latin-1")
        records += struct.pack(
            ">HHHHHH", platform, encoding, language, name_id, len(raw), len(strings)
        )
        strings += raw
    return (
        struct.pack(">HHH", 0, len(entries), 6 + 12 * len(entries)) + records + strings
    )


def _synthetic_font(copyright_notice: str | None) -> bytes:
    entries = [(3, 1, 0x409, 1, "Test Face")]
    if copyright_notice is not None:
        entries.insert(0, (3, 1, 0x409, 0, copyright_notice))
    name = _name_table(sorted(entries, key=lambda entry: entry[3]))
    header = struct.pack(">IHHHH", 0x00010000, 1, 0, 0, 0)
    record = b"name" + struct.pack(">III", 0, 12 + 16, len(name))
    return header + record + name


def _synthetic_bundle() -> tuple[
    dict[str, bytes],
    tuple[subtitle_font_assets.BundledSubtitleFont, ...],
    subtitle_font_assets.PackagedLicenseNotice,
]:
    """A cleared bundle whose bytes the tests own, so no network or 33 MB is needed."""
    payloads: dict[str, bytes] = {}
    fonts = []
    for weight in ("Bold", "Regular"):
        packaged = f"TestSansCJKsc-{weight}.ttf"
        payload = _synthetic_font("© 2026 Test Foundry.")
        payloads[packaged] = payload
        fonts.append(
            subtitle_font_assets.BundledSubtitleFont(
                id=f"font-test-{weight.lower()}",
                packaged_name=packaged,
                source_url=(
                    f"{subtitle_font_assets.FONT_SOURCE_URL_PREFIX}Sans/OTF/"
                    f"SimplifiedChinese/TestSansCJKsc-{weight}.otf"
                ),
                upstream_file_name=f"TestSansCJKsc-{weight}.otf",
                sha256=hashlib.sha256(payload).hexdigest(),
                bytes=len(payload),
                license="OFL-1.1",
                attribution="© 2026 Test Foundry.",
            )
        )
    licence = f"{OFL_HEADLINE}\n".encode()
    payloads["TEST-LICENSE.txt"] = licence
    notice = subtitle_font_assets.PackagedLicenseNotice(
        packaged_name="TEST-LICENSE.txt",
        source_url=f"{subtitle_font_assets.FONT_SOURCE_URL_PREFIX}LICENSE",
        sha256=hashlib.sha256(licence).hexdigest(),
        bytes=len(licence),
    )
    return payloads, tuple(fonts), notice


def _write_bundled_fonts(
    candidate: Path,
    fonts: tuple[subtitle_font_assets.BundledSubtitleFont, ...],
    notice: subtitle_font_assets.PackagedLicenseNotice,
    payloads: dict[str, bytes],
) -> None:
    """Materialise exactly what a compliant candidate carries under resource/fonts."""
    directory = candidate / "_internal/upstream/resource/fonts"
    directory.mkdir(parents=True, exist_ok=True)
    for name in [font.packaged_name for font in fonts] + [notice.packaged_name]:
        (directory / name).write_bytes(payloads[name])


class SubtitleFontRightsTest(unittest.TestCase):
    """A font may only ship when the asset rights register actually clears it."""

    def test_registered_fonts_declare_a_locked_download_and_digest(self) -> None:
        fonts = subtitle_font_assets.bundled_subtitle_fonts()
        self.assertTrue(fonts)
        for font in fonts:
            self.assertEqual(font.license, "OFL-1.1")
            self.assertTrue(font.packaged_name.endswith((".ttf", ".ttc")))
            self.assertTrue(
                font.source_url.startswith(subtitle_font_assets.LOCKED_SOURCE_URL_PREFIXES)
            )
            self.assertRegex(font.sha256, r"^[0-9a-f]{64}$")
            self.assertGreater(font.bytes, 0)
            self.assertTrue(font.attribution)

    def test_no_font_binary_is_checked_into_the_repository(self) -> None:
        # A 33 MB binary in Git history is unremovable without rewriting history,
        # and every other large locked artifact (Chromium, ffmpeg) is fetched at
        # build time instead. The fonts follow that rule rather than being the
        # one exception.
        self.assertFalse((ROOT / "assets/fonts").exists())
        tracked = subprocess.run(
            ["git", "ls-files"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        for font in subtitle_font_assets.bundled_subtitle_fonts():
            self.assertEqual(
                [path for path in tracked if path.endswith(font.packaged_name)],
                [],
                "a cleared font must not be committed to the repository",
            )

    def test_no_registered_font_is_a_proprietary_system_face(self) -> None:
        registered = {
            font.packaged_name for font in subtitle_font_assets.bundled_subtitle_fonts()
        }
        for proprietary in (
            "MicrosoftYaHeiBold.ttc",
            "MicrosoftYaHeiNormal.ttc",
            "STHeitiLight.ttc",
            "STHeitiMedium.ttc",
        ):
            self.assertNotIn(proprietary, registered)

    def test_registry_rejects_a_font_missing_a_required_rights_field(self) -> None:
        rights = subtitle_font_assets.load_asset_rights()
        for entry in rights["entries"]:
            if entry.get("category") == "font":
                entry.pop("redistributionAllowed", None)
        with self.assertRaisesRegex(
            subtitle_font_assets.SubtitleFontRightsError, "redistributionAllowed"
        ):
            subtitle_font_assets.bundled_subtitle_fonts(rights)

    def test_registry_rejects_a_font_that_forbids_redistribution(self) -> None:
        rights = subtitle_font_assets.load_asset_rights()
        for entry in rights["entries"]:
            if entry.get("category") == "font":
                entry["redistributionAllowed"] = False
        with self.assertRaisesRegex(
            subtitle_font_assets.SubtitleFontRightsError, "redistribution"
        ):
            subtitle_font_assets.bundled_subtitle_fonts(rights)

    def test_registry_rejects_a_packaged_name_that_escapes_the_font_directory(
        self,
    ) -> None:
        rights = subtitle_font_assets.load_asset_rights()
        for entry in rights["entries"]:
            if entry.get("category") == "font":
                entry["packagedName"] = "../evil.ttf"
        with self.assertRaisesRegex(
            subtitle_font_assets.SubtitleFontRightsError, "packagedName"
        ):
            subtitle_font_assets.bundled_subtitle_fonts(rights)

    def test_registry_rejects_a_download_location_off_the_locked_upstream(self) -> None:
        rights = subtitle_font_assets.load_asset_rights()
        for entry in rights["entries"]:
            if entry.get("category") == "font":
                entry["sourceUrl"] = "https://example.invalid/NotoSansCJKsc-Bold.otf"
        with self.assertRaisesRegex(
            subtitle_font_assets.SubtitleFontRightsError, "sourceUrl"
        ):
            subtitle_font_assets.bundled_subtitle_fonts(rights)

    def test_licence_notice_travels_with_every_registered_font(self) -> None:
        notice = subtitle_font_assets.packaged_license_notice()
        self.assertNotIn("/", notice.packaged_name)
        self.assertTrue(
            notice.source_url.startswith(subtitle_font_assets.FONT_SOURCE_URL_PREFIX)
        )
        self.assertRegex(notice.sha256, r"^[0-9a-f]{64}$")

    def test_app_ships_the_same_licence_text_the_package_carries(self) -> None:
        shipped = (
            ROOT
            / "frontend/src/features/legal/third-party-software"
            / "license-texts/ofl-1.1.txt"
        )
        payload = shipped.read_bytes().replace(b"\r\n", b"\n")
        notice = subtitle_font_assets.packaged_license_notice()
        self.assertEqual(hashlib.sha256(payload).hexdigest(), notice.sha256)

    def test_default_subtitle_font_is_one_of_the_registered_fonts(self) -> None:
        registered = {
            font.packaged_name for font in subtitle_font_assets.bundled_subtitle_fonts()
        }
        self.assertIn(default_subtitle_font_name(), registered)


class SubtitleFontPayloadVerificationTest(unittest.TestCase):
    """Bytes are verified before use; there is no fallback face to fall back to."""

    def test_copyright_notice_is_read_from_the_font_name_table(self) -> None:
        payload = _synthetic_font("© 2026 Test Foundry.")
        self.assertEqual(
            subtitle_font_assets.font_copyright_notice(payload), "© 2026 Test Foundry."
        )

    def test_font_without_a_copyright_notice_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            subtitle_font_assets.SubtitleFontRightsError, "copyright"
        ):
            subtitle_font_assets.font_copyright_notice(_synthetic_font(None))

    def test_payload_with_drifted_bytes_is_rejected(self) -> None:
        _, fonts, _ = _synthetic_bundle()
        with self.assertRaisesRegex(
            subtitle_font_assets.SubtitleFontUnavailable, "digest"
        ):
            subtitle_font_assets.verify_font_payload(fonts[0], b"not the licensed font")

    def test_payload_whose_copyright_is_not_the_registered_one_is_rejected(
        self,
    ) -> None:
        payload = _synthetic_font("© 2026 Someone Else.")
        font = subtitle_font_assets.BundledSubtitleFont(
            id="font-test",
            packaged_name="TestSansCJKsc-Bold.ttf",
            source_url=f"{subtitle_font_assets.FONT_SOURCE_URL_PREFIX}x.otf",
            upstream_file_name="x.otf",
            sha256=hashlib.sha256(payload).hexdigest(),
            bytes=len(payload),
            license="OFL-1.1",
            attribution="© 2026 Test Foundry.",
        )
        with self.assertRaisesRegex(
            subtitle_font_assets.SubtitleFontUnavailable, "copyright"
        ):
            subtitle_font_assets.verify_font_payload(font, payload)


class SubtitleFontFetchTest(unittest.TestCase):
    """Fetching is fail-closed: no fallback font, no half-populated cache."""

    def test_locked_fetch_retries_a_transient_network_failure(self) -> None:
        payload = b"locked-font"
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = payload
        url = f"{subtitle_font_assets.FONT_SOURCE_URL_PREFIX}font.otf"

        with mock.patch.object(
            subtitle_font_assets.urllib.request,
            "urlopen",
            side_effect=[TimeoutError("temporary stall"), response],
        ) as urlopen:
            self.assertEqual(subtitle_font_assets._fetch_locked_url(url), payload)

        self.assertEqual(urlopen.call_count, 2)

    def test_locked_fetch_stops_after_the_bounded_attempts(self) -> None:
        url = f"{subtitle_font_assets.FONT_SOURCE_URL_PREFIX}font.otf"

        with (
            mock.patch.object(
                subtitle_font_assets.urllib.request,
                "urlopen",
                side_effect=TimeoutError("persistent stall"),
            ) as urlopen,
            self.assertRaisesRegex(
                subtitle_font_assets.SubtitleFontUnavailable,
                "persistent stall",
            ),
        ):
            subtitle_font_assets._fetch_locked_url(url)

        self.assertEqual(urlopen.call_count, subtitle_font_assets.FETCH_ATTEMPTS)

    def test_fetches_verifies_and_caches_every_cleared_font(self) -> None:
        payloads, fonts, notice = _synthetic_bundle()
        by_url = {font.source_url: payloads[font.packaged_name] for font in fonts}
        by_url[notice.source_url] = payloads[notice.packaged_name]
        calls: list[str] = []

        def fetch(url: str) -> bytes:
            calls.append(url)
            return by_url[url]

        with tempfile.TemporaryDirectory() as directory:
            first = subtitle_font_assets.ensure_subtitle_fonts(
                root=Path(directory), fonts=fonts, notice=notice, fetch=fetch
            )
            for name, payload in payloads.items():
                self.assertEqual((first / name).read_bytes(), payload)
            self.assertEqual(len(calls), 3)

            second = subtitle_font_assets.ensure_subtitle_fonts(
                root=Path(directory), fonts=fonts, notice=notice, fetch=fetch
            )
            self.assertEqual(first, second)
            self.assertEqual(len(calls), 3, "a warm cache must not refetch")

    def test_a_drifted_download_leaves_no_usable_cache(self) -> None:
        payloads, fonts, notice = _synthetic_bundle()

        def fetch(url: str) -> bytes:
            if url == fonts[0].source_url:
                return b"substituted font"
            return payloads[notice.packaged_name]

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(subtitle_font_assets.SubtitleFontUnavailable):
                subtitle_font_assets.ensure_subtitle_fonts(
                    root=Path(directory), fonts=fonts, notice=notice, fetch=fetch
                )
            self.assertFalse(
                (Path(directory) / subtitle_font_assets.CACHE_NAME).exists(),
                "a rejected download must not leave a partial font cache behind",
            )

    def test_an_unreachable_upstream_is_not_papered_over(self) -> None:
        _, fonts, notice = _synthetic_bundle()

        def fetch(url: str) -> bytes:
            raise OSError("network is unreachable")

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(subtitle_font_assets.SubtitleFontUnavailable):
                subtitle_font_assets.ensure_subtitle_fonts(
                    root=Path(directory), fonts=fonts, notice=notice, fetch=fetch
                )


class MaterialVideoWorkerExcludedUpstreamResourceFilesTest(unittest.TestCase):
    """Every font without redistribution clearance stays out of the release."""

    def test_contract_declares_the_excluded_upstream_resource_files(self) -> None:
        contract = build_candidate_module.load_contract()
        excluded = build_candidate_module.excluded_upstream_resource_files(contract)
        for proprietary in (
            "fonts/MicrosoftYaHeiBold.ttc",
            "fonts/MicrosoftYaHeiNormal.ttc",
            "fonts/STHeitiLight.ttc",
            "fonts/STHeitiMedium.ttc",
            "fonts/UTM Kabel KT.ttf",
        ):
            self.assertIn(proprietary, excluded)

    def test_spec_ships_upstream_resource_directories_file_by_file(self) -> None:
        spec = (ROOT / "workers/material_montage/material-video-worker.spec").read_text(
            encoding="utf-8"
        )
        self.assertIn("excludedUpstreamResourceFiles", spec)
        self.assertIn("bundled_subtitle_fonts", spec)

    def test_candidate_carrying_an_excluded_resource_file_is_rejected(self) -> None:
        contract = build_candidate_module.load_contract()
        for excluded in ("MicrosoftYaHeiBold.ttc", "UTM Kabel KT.ttf"):
            with (
                self.subTest(excluded=excluded),
                tempfile.TemporaryDirectory() as directory,
            ):
                candidate = Path(directory) / "candidate"
                fonts = candidate / "_internal/upstream/resource/fonts"
                fonts.mkdir(parents=True)
                (fonts / excluded).write_bytes(b"")
                with self.assertRaisesRegex(
                    build_candidate_module.MaterialVideoWorkerPackageError,
                    excluded,
                ):
                    build_candidate_module.assert_excluded_upstream_resource_files_absent(
                        candidate, contract
                    )

    def test_candidate_without_excluded_resource_files_is_accepted(self) -> None:
        contract = build_candidate_module.load_contract()
        payloads, fonts, notice = _synthetic_bundle()
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate"
            _write_bundled_fonts(candidate, fonts, notice, payloads)
            build_candidate_module.assert_excluded_upstream_resource_files_absent(
                candidate, contract
            )


class MaterialVideoWorkerBundledFontsTest(unittest.TestCase):
    """A candidate with no usable Chinese face renders subtitles as empty boxes."""

    def setUp(self) -> None:
        self.contract = build_candidate_module.load_contract()
        self.payloads, self.fonts, self.notice = _synthetic_bundle()
        self.default = self.fonts[0].packaged_name

    def _candidate(self, directory: str) -> Path:
        candidate = Path(directory) / "candidate"
        _write_bundled_fonts(candidate, self.fonts, self.notice, self.payloads)
        return candidate

    def _assert(self, candidate: Path) -> None:
        build_candidate_module.assert_bundled_subtitle_fonts_present(
            candidate,
            self.contract,
            fonts=self.fonts,
            notice=self.notice,
            default_font_name=self.default,
        )

    def test_candidate_missing_a_registered_font_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = self._candidate(directory)
            (candidate / "_internal/upstream/resource/fonts" / self.default).unlink()
            with self.assertRaisesRegex(
                build_candidate_module.MaterialVideoWorkerPackageError, self.default
            ):
                self._assert(candidate)

    def test_candidate_with_a_substituted_font_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = self._candidate(directory)
            (
                candidate / "_internal/upstream/resource/fonts" / self.default
            ).write_bytes(b"not the licensed font")
            with self.assertRaisesRegex(
                build_candidate_module.MaterialVideoWorkerPackageError, self.default
            ):
                self._assert(candidate)

    def test_candidate_whose_font_carries_another_copyright_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = self._candidate(directory)
            impostor = _synthetic_font("© 2026 Someone Else.")
            font = self.fonts[0]
            self.fonts = (
                subtitle_font_assets.BundledSubtitleFont(
                    id=font.id,
                    packaged_name=font.packaged_name,
                    source_url=font.source_url,
                    upstream_file_name=font.upstream_file_name,
                    sha256=hashlib.sha256(impostor).hexdigest(),
                    bytes=len(impostor),
                    license=font.license,
                    attribution=font.attribution,
                ),
            ) + self.fonts[1:]
            (
                candidate / "_internal/upstream/resource/fonts" / self.default
            ).write_bytes(impostor)
            with self.assertRaisesRegex(
                build_candidate_module.MaterialVideoWorkerPackageError, "版权"
            ):
                self._assert(candidate)

    def test_candidate_missing_the_font_licence_notice_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = self._candidate(directory)
            (
                candidate
                / "_internal/upstream/resource/fonts"
                / self.notice.packaged_name
            ).unlink()
            with self.assertRaisesRegex(
                build_candidate_module.MaterialVideoWorkerPackageError,
                self.notice.packaged_name,
            ):
                self._assert(candidate)

    def test_candidate_carrying_the_registered_fonts_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self._assert(self._candidate(directory))

    def test_the_real_register_is_what_the_audit_uses_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate"
            (candidate / "_internal/upstream/resource/fonts").mkdir(parents=True)
            with self.assertRaisesRegex(
                build_candidate_module.MaterialVideoWorkerPackageError,
                default_subtitle_font_name(),
            ):
                build_candidate_module.assert_bundled_subtitle_fonts_present(
                    candidate, self.contract
                )


class MaterialVideoWorkerDefaultSubtitleFontTest(unittest.TestCase):
    """Removing the upstream default is only safe once a new default is wired in."""

    def test_private_config_pins_the_subtitle_font_in_the_webui_section(self) -> None:
        document = _private_config_document(
            "[app]\nvalue = 1\n\n[ui]\nhide_log = false\n", "NotoSansCJKsc-Bold.ttf"
        )
        self.assertEqual(
            document,
            '[app]\nvalue = 1\n\n[ui]\nfont_name = "NotoSansCJKsc-Bold.ttf"\n'
            "hide_log = false\n",
        )

    def test_private_config_refuses_upstream_configuration_without_a_webui_section(
        self,
    ) -> None:
        with self.assertRaises(WebUiRejected):
            _private_config_document("[app]\nvalue = 1\n", "NotoSansCJKsc-Bold.ttf")

    def test_private_config_refuses_a_font_name_that_could_break_out_of_the_value(
        self,
    ) -> None:
        for hostile in ('a"\nhide_log = true', "a\nb.ttf", "", "../x.ttf"):
            with self.assertRaises(WebUiRejected):
                _private_config_document("[ui]\nhide_log = false\n", hostile)

    def test_upstream_default_subtitle_font_is_no_longer_shipped(self) -> None:
        contract = build_candidate_module.load_contract()
        excluded = build_candidate_module.excluded_upstream_resource_files(contract)
        self.assertIn("fonts/MicrosoftYaHeiBold.ttc", excluded)
        self.assertNotEqual(default_subtitle_font_name(), "MicrosoftYaHeiBold.ttc")


class MaterialVideoWorkerSubtitleFallbackTest(unittest.TestCase):
    """No render path may start a large model download the user cannot see.

    Upstream falls back to Whisper when the Edge subtitle timeline is missing,
    and that fallback resolves the model through Hugging Face, which downloads
    roughly 1.5 GB on first use with nothing on screen but a spinner. The
    package ships the code but no model, so the WebUI child runs with the
    Hugging Face offline switch and the fallback fails immediately instead.
    """

    def test_upstream_still_falls_back_to_a_downloaded_model(self) -> None:
        task = (ROOT / "vendor/moneyprinterturbo/app/services/task.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("fallback to whisper", task)
        subtitle = (
            ROOT / "vendor/moneyprinterturbo/app/services/subtitle.py"
        ).read_text(encoding="utf-8")
        self.assertIn("model_size_or_path=model_path", subtitle)

    def test_webui_child_cannot_start_a_hidden_model_download(self) -> None:
        self.assertEqual(webui_runtime.CHILD_ENVIRONMENT["HF_HUB_OFFLINE"], "1")

    def test_package_still_ships_no_speech_model(self) -> None:
        spec = (ROOT / "workers/material_montage/material-video-worker.spec").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("whisper-large", spec)
        self.assertNotIn("models/whisper", spec)


class MaterialVideoWorkerBackgroundMusicTest(unittest.TestCase):
    """A choice this release cannot honour must not stay on the product surface.

    The release ships no background music, so upstream's three-way "background
    music source" choice silently degrades to no music for every option. The
    private WebUI project is what the user actually opens, so the controls are
    removed there and replaced by a sentence that states the limit.
    """

    widget_keys = ("bgm_type_select", "bgm_volume_select", "custom_bgm_file_input")

    @classmethod
    def setUpClass(cls) -> None:
        cls._directory = tempfile.TemporaryDirectory()
        runtime_root = Path(cls._directory.name) / "runtime"
        runtime_root.mkdir()
        _prepare_private_project(runtime_root)
        cls.stylesheet = (runtime_root / "webui/styles.css").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._directory.cleanup()

    def test_release_ships_no_background_music_at_all(self) -> None:
        contract = build_candidate_module.load_contract()
        self.assertIn(
            "songs", build_candidate_module.excluded_upstream_resources(contract)
        )

    def test_upstream_still_uses_the_widget_keys_the_overlay_targets(self) -> None:
        source = (UPSTREAM_WEBUI / "Main.py").read_text(encoding="utf-8")
        for key in self.widget_keys:
            self.assertIn(f'key="{key}"', source)

    def test_private_project_removes_every_background_music_control(self) -> None:
        for key in self.widget_keys:
            self.assertIn(f"st-key-{key}", self.stylesheet)

    def test_private_project_states_that_this_release_has_no_background_music(
        self,
    ) -> None:
        self.assertIn(
            "当前版本不提供背景音乐素材，成片不会添加背景音乐。", self.stylesheet
        )

    def test_private_project_keeps_every_upstream_rule(self) -> None:
        upstream = (UPSTREAM_WEBUI / "styles.css").read_text(encoding="utf-8")
        self.assertTrue(self.stylesheet.startswith(upstream))


if __name__ == "__main__":
    unittest.main()
