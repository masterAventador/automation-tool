"""Strict worker observations for the App-owned material render-job ledger."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from threading import RLock
from typing import Any, Final
from uuid import RFC_4122, UUID

SCHEMA_VERSION: Final = 1
OBSERVATION_FILE: Final = "material-render-job-observation.json"
CANCEL_FILE: Final = "material-render-job-cancel-request"
MAX_SUBJECT_CHARACTERS: Final = 240
MAX_OBSERVATION_BYTES: Final = 64 * 1024


class JobCancelled(RuntimeError):
    """Internal cooperative cancellation signal."""


def _uuid4(value: str) -> str:
    parsed = UUID(str(value))
    if parsed.version != 4 or parsed.variant != RFC_4122:
        raise ValueError("invalid task identifier")
    return str(parsed)


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if not payload or len(payload) > MAX_OBSERVATION_BYTES:
        raise ValueError("invalid observation size")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class ObservedTaskState:
    """Delegates runtime behavior while emitting a bounded, path-free observation."""

    def __init__(self, delegate: Any, runtime_root: Path, output_root: Path):
        self._delegate = delegate
        self._runtime_root = runtime_root.resolve(strict=True)
        self._tasks_root = (self._runtime_root / "storage/tasks").resolve(strict=True)
        self._output_root = output_root.resolve(strict=True)
        self._render_job_id = _uuid4(self._runtime_root.parents[2].name)
        self._revision = 0
        self._active_task_id: str | None = None
        self._lock = RLock()

    def _cancel_requested(self) -> bool:
        marker = self._runtime_root / CANCEL_FILE
        return marker.is_file() and not marker.is_symlink()

    def _copy_output(self, task_id: str, values: dict[str, object]) -> str | None:
        videos = values.get("videos")
        if not isinstance(videos, list) or not videos or not isinstance(videos[0], str):
            return None
        source_candidate = Path(videos[0])
        if source_candidate.is_symlink():
            raise ValueError("invalid rendered output")
        source = source_candidate.resolve(strict=True)
        task_root = (self._tasks_root / task_id).resolve(strict=True)
        if source.parent != task_root or source.suffix.casefold() != ".mp4" or source.is_symlink():
            raise ValueError("invalid rendered output")
        destination_name = "material-result.mp4"
        temporary = self._output_root / ".material-result.tmp"
        destination = self._output_root / destination_name
        with source.open("rb") as reader, temporary.open("xb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, destination)
        return destination_name

    def _observe(self, task_id: str, state: int, progress: int, values: dict[str, object]) -> None:
        task_id = _uuid4(task_id)
        if self._active_task_id not in (None, task_id):
            raise ValueError("concurrent render task rejected")
        self._active_task_id = task_id
        progress = max(0, min(100, int(progress)))
        status = {4: "running", 1: "succeeded", -1: "failed"}.get(int(state))
        if status is None:
            raise ValueError("invalid task state")
        if self._cancel_requested():
            status = "cancelled"
        output_file = self._copy_output(task_id, values) if status == "succeeded" else None
        if status == "succeeded" and output_file is None:
            status = "failed"
        self._revision += 1
        subject = values.get("video_subject") or values.get("script") or "视频制作任务"
        if not isinstance(subject, str):
            subject = "视频制作任务"
        document: dict[str, object] = {
            "schemaVersion": SCHEMA_VERSION,
            "renderJobId": self._render_job_id,
            "workerTaskId": task_id,
            "revision": self._revision,
            "status": status,
            "progressPercent": 100 if status == "succeeded" else progress,
            "subject": subject[:MAX_SUBJECT_CHARACTERS],
            "outputFile": output_file,
            "failureCode": "generation_failed" if status == "failed" else None,
        }
        _atomic_json(self._runtime_root / OBSERVATION_FILE, document)
        if status == "cancelled":
            raise JobCancelled("render cancelled")

    def update_task(
        self, task_id: str, state: int = 4, progress: int = 0, **kwargs: object
    ) -> None:
        with self._lock:
            self._delegate.update_task(task_id, state=state, progress=progress, **kwargs)
            self._observe(task_id, state, progress, kwargs)

    def get_task(self, task_id: str) -> object:
        return self._delegate.get_task(task_id)

    def get_all_tasks(self, page: int, page_size: int) -> object:
        return self._delegate.get_all_tasks(page, page_size)

    def delete_task(self, task_id: str) -> None:
        _uuid4(task_id)
        raise PermissionError("App owns task deletion")


def install_job_observation_bridge(
    state_module: Any, runtime_root: Path, output_root: Path
) -> None:
    state_module.state = ObservedTaskState(state_module.state, runtime_root, output_root)
