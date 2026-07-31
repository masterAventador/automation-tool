"""Private durable intent storage for one native video-editing workspace.

The provider writes ``prepared`` before its single IMS submission and replaces
that record with the acknowledged vendor JobId afterwards.  Keeping this tiny
record beside the retained RenderJob workspace lets a later signed Executor
resume polling without submitting the cloud job again.
"""

from __future__ import annotations

import contextlib
import importlib
import os
import stat
import tempfile
from contextlib import AbstractContextManager
from pathlib import Path
from types import TracebackType
from typing import Annotated, Final, Literal, Never, Protocol, Self, cast, final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from automation_tool.control_plane.domain.aliyun_ims_editing_provider import (
    AliyunEditingIntent,
    AliyunEditingIntentState,
)
from automation_tool.control_plane.domain.resource_ids import ArtifactId
from automation_tool.control_plane.domain.video_editing import (
    EditingFailureCode,
    EditingJobId,
    EditingJobStatus,
)

_FILE_NAME: Final = "aliyun-editing-intent.checkpoint"
_LOCK_FILE_NAME: Final = "aliyun-editing-execution-lock.checkpoint"
_MAX_BYTES: Final = 64 * 1024


class InvalidAliyunEditingIntentStore(ValueError):
    def __init__(self) -> None:
        super().__init__("Aliyun editing intent storage is unavailable")


def _reject() -> Never:
    raise InvalidAliyunEditingIntentStore


class _WindowsFileLockApi(Protocol):
    LK_LOCK: int
    LK_UNLCK: int

    def locking(self, file_descriptor: int, mode: int, byte_count: int) -> None: ...


def _windows_file_lock_api() -> _WindowsFileLockApi:
    return cast(
        _WindowsFileLockApi,
        importlib.import_module("msvcrt"),
    )


class _PersistedIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_version: Literal[1] = Field(alias="schemaVersion")
    editing_job_id: str = Field(alias="editingJobId")
    request_hash: Annotated[str, Field(alias="requestHash", pattern=r"^[0-9a-f]{64}$")]
    state: Literal["prepared", "dispatched", "uncertain"]
    vendor_job_id: Annotated[
        str | None,
        Field(alias="vendorJobId", pattern=r"^[A-Za-z0-9-]{8,128}$"),
    ]
    status: Literal[
        "queued",
        "running",
        "paused",
        "cancelling",
        "succeeded",
        "failed",
        "cancelled",
        "outcome_uncertain",
    ]
    failure_code: (
        Literal[
            "invalid_input",
            "dependency_unavailable",
            "resource_exhausted",
            "editing_failed",
        ]
        | None
    ) = Field(alias="failureCode")
    output_artifact_ids: Annotated[
        list[str],
        Field(alias="outputArtifactIds", max_length=64),
    ]


@final
class _ExecutionLease(AbstractContextManager["_ExecutionLease"]):
    __slots__ = ("_descriptor",)

    def __init__(self, directory: Path) -> None:
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(directory / _LOCK_FILE_NAME, flags, 0o600)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or (os.name != "nt" and stat.S_IMODE(metadata.st_mode) & 0o077)
            ):
                os.close(descriptor)
                _reject()
            if os.name == "nt":
                if metadata.st_size == 0:
                    os.write(descriptor, b"\0")
                    os.fsync(descriptor)
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt = _windows_file_lock_api()
                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX)
        except BaseException:
            if "descriptor" in locals():
                with contextlib.suppress(OSError):
                    os.close(descriptor)
            raise
        self._descriptor = descriptor

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exception_type, exception, traceback
        try:
            if os.name == "nt":
                os.lseek(self._descriptor, 0, os.SEEK_SET)
                msvcrt = _windows_file_lock_api()
                msvcrt.locking(self._descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._descriptor, fcntl.LOCK_UN)
        finally:
            os.close(self._descriptor)


@final
class FileAliyunEditingIntentStore:
    """One strict, atomically replaced intent document per private workspace."""

    __slots__ = ("_directory", "_path")

    def __init__(self, directory: Path) -> None:
        if not isinstance(directory, Path) or not directory.is_absolute():
            _reject()
        try:
            metadata = directory.lstat()
        except OSError:
            _reject()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or directory.is_symlink()
            or (os.name != "nt" and stat.S_IMODE(metadata.st_mode) & 0o077)
        ):
            _reject()
        self._directory = directory
        self._path = directory / _FILE_NAME

    def execution_lease(self) -> AbstractContextManager[object]:
        return _ExecutionLease(self._directory)

    async def load(self, editing_job_id: EditingJobId) -> AliyunEditingIntent | None:
        if not isinstance(editing_job_id, EditingJobId):
            _reject()
        intent = self._load_document()
        if intent is None:
            return None
        if intent.editing_job_id != editing_job_id:
            _reject()
        return intent

    async def save(self, intent: AliyunEditingIntent) -> None:
        if not isinstance(intent, AliyunEditingIntent):
            _reject()
        existing = self._load_document()
        if existing is not None and existing.editing_job_id != intent.editing_job_id:
            _reject()
        document = _PersistedIntent.model_validate(
            {
                "schema_version": 1,
                "editing_job_id": str(intent.editing_job_id),
                "request_hash": intent.request_hash,
                "state": intent.state.value,
                "vendor_job_id": intent.vendor_job_id,
                "status": intent.status.value,
                "failure_code": (
                    None if intent.failure_code is None else intent.failure_code.value
                ),
                "output_artifact_ids": [str(value) for value in intent.output_artifact_ids],
            }
        )
        payload = document.model_dump_json(by_alias=True).encode()
        if not 0 < len(payload) <= _MAX_BYTES:
            _reject()
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".aliyun-editing-intent-",
            suffix=".tmp",
            dir=self._directory,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
            _sync_directory(self._directory)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    async def load_all(self) -> tuple[AliyunEditingIntent, ...]:
        intent = self._load_document()
        return () if intent is None else (intent,)

    async def load_by_vendor_job_id(self, vendor_job_id: str) -> AliyunEditingIntent | None:
        if type(vendor_job_id) is not str or not vendor_job_id:
            _reject()
        intent = self._load_document()
        if intent is None or intent.vendor_job_id != vendor_job_id:
            return None
        return intent

    def _load_document(self) -> AliyunEditingIntent | None:
        try:
            metadata = self._path.lstat()
        except FileNotFoundError:
            return None
        except OSError:
            _reject()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or self._path.is_symlink()
            or metadata.st_nlink != 1
            or not 0 < metadata.st_size <= _MAX_BYTES
            or (os.name != "nt" and stat.S_IMODE(metadata.st_mode) & 0o077)
        ):
            _reject()
        try:
            payload = self._path.read_bytes()
            document = _PersistedIntent.model_validate_json(payload)
            editing_job_id = EditingJobId.parse(document.editing_job_id)
            output_artifact_ids = tuple(
                ArtifactId.parse(value) for value in document.output_artifact_ids
            )
            if len(set(output_artifact_ids)) != len(output_artifact_ids):
                _reject()
            return AliyunEditingIntent(
                editing_job_id=editing_job_id,
                request_hash=document.request_hash,
                state=AliyunEditingIntentState(document.state),
                vendor_job_id=document.vendor_job_id,
                status=EditingJobStatus(document.status),
                failure_code=(
                    None
                    if document.failure_code is None
                    else EditingFailureCode(document.failure_code)
                ),
                output_artifact_ids=output_artifact_ids,
            )
        except (OSError, ValidationError, TypeError, ValueError):
            _reject()


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "FileAliyunEditingIntentStore",
    "InvalidAliyunEditingIntentStore",
]
