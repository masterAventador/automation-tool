"""T4 real-cloud acceptance for the exact one-shot Executor composition.

Opt-in only. It generates one tiny local MP4, runs the same production
OSS/IMS/reconciliation/output-import composition selected by
``--execute-video-editing``, verifies the imported film, and relies on that
composition's terminal cleanup policy to delete both temporary OSS objects.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx2
import pytest
from automation_tool.executor.video_editing import (
    EditingExecutionRequest,
    execute_video_editing,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CREDENTIAL_PATH = Path(
    os.environ.get(
        "AUTOMATION_TOOL_ALIYUN_CREDENTIALS",
        os.fspath(REPOSITORY_ROOT / ".local/secrets/aliyun-video-editing.json"),
    )
)

pytestmark = pytest.mark.skipif(
    os.environ.get("AUTOMATION_TOOL_REAL_CLOUD") != "1" or not CREDENTIAL_PATH.is_file(),
    reason="real T4 editing acceptance requires explicit opt-in and local credentials",
)


def _generate_input(path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None
    subprocess.run(
        [
            ffmpeg,
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=orange:s=128x128:d=2",
            "-pix_fmt",
            "yuv420p",
            "-y",
            os.fspath(path),
        ],
        check=True,
        capture_output=True,
    )


@pytest.mark.asyncio
async def test_real_one_shot_composition_imports_a_film_and_cleans_cloud_objects(
    tmp_path: Path,
) -> None:
    credential = json.loads(CREDENTIAL_PATH.read_text(encoding="utf-8"))
    child_credential = {
        key: credential[key] for key in ("accessKeyId", "accessKeySecret", "region", "ossBucket")
    }
    input_directory = tmp_path / "input"
    output_directory = tmp_path / "output"
    state_directory = tmp_path / "state"
    input_directory.mkdir()
    output_directory.mkdir()
    state_directory.mkdir(mode=0o700)
    artifact_id = str(uuid4())
    project_id = str(uuid4())
    timeline_id = str(uuid4())
    editing_job_id = str(uuid4())
    source = input_directory / f"{artifact_id}.mp4"
    _generate_input(source)
    request = EditingExecutionRequest.model_validate(
        {
            "schemaVersion": 1,
            "executionMode": "submit",
            "credential": child_credential,
            "editingJobId": editing_job_id,
            "projectId": project_id,
            "timeline": {
                "timelineId": timeline_id,
                "projectId": project_id,
                "revision": 1,
                "durationMs": 2_000,
                "tracks": [
                    {
                        "trackId": "visual-main",
                        "kind": "visual",
                        "clips": [
                            {
                                "clipId": "clip-1",
                                "startMs": 0,
                                "durationMs": 2_000,
                                "sourceArtifactId": artifact_id,
                                "text": None,
                                "transitionIn": None,
                            }
                        ],
                    }
                ],
                "createdAt": datetime.now(UTC).isoformat(),
            },
            "assets": [
                {
                    "artifactId": artifact_id,
                    "path": os.fspath(source),
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "sizeBytes": source.stat().st_size,
                    "extension": ".mp4",
                }
            ],
            "inputDirectory": os.fspath(input_directory),
            "outputDirectory": os.fspath(output_directory),
            "stateDirectory": os.fspath(state_directory),
            "outputWidth": 128,
            "outputHeight": 128,
        }
    )

    events: list[tuple[str, str, int]] = []

    async def record_response(response: httpx2.Response) -> None:
        error_code = ""
        if response.status_code >= 400:
            body = (await response.aread()).decode("utf-8", errors="ignore")
            matched = re.search(r"<Code>([A-Za-z0-9]+)</Code>", body)
            error_code = "" if matched is None else matched.group(1)
        events.append(
            (
                response.request.method,
                response.request.headers.get("x-acs-action", error_code or "oss"),
                response.status_code,
            )
        )

    async with httpx2.AsyncClient(
        timeout=60.0,
        event_hooks={"response": [record_response]},
    ) as client:
        result = await execute_video_editing(
            request,
            client=client,
            poll_interval_seconds=5.0,
        )

    assert result.status == "succeeded", (result.status, result.failure_code, events)
    assert result.editing_job_id == editing_job_id
    assert result.output_path is not None
    output = Path(result.output_path)
    assert output.parent == output_directory
    assert output.is_file()
    assert hashlib.sha256(output.read_bytes()).hexdigest() == result.output_sha256
    assert output.stat().st_size == result.output_size_bytes
