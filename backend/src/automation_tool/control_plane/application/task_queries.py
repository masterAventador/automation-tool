"""Installation-scoped Task snapshot queries and opaque keyset cursors."""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from automation_tool.control_plane.application.tasks import TaskRecord
from automation_tool.control_plane.domain import InstallationId, InvalidResourceId, TaskId

_CURSOR_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,256}$")


class InvalidTaskQuery(ValueError):
    def __init__(self) -> None:
        super().__init__("Task query is invalid")


class TaskNotFound(LookupError):
    def __init__(self) -> None:
        super().__init__("Task is unavailable")


@dataclass(frozen=True, slots=True)
class TaskListBoundary:
    updated_at: datetime
    task_id: TaskId


@dataclass(frozen=True, slots=True)
class TaskListPage:
    items: tuple[TaskRecord, ...]
    next_cursor: str | None


class TaskQueryRepository(Protocol):
    async def get(
        self,
        *,
        task_id: TaskId,
        installation_id: InstallationId,
    ) -> TaskRecord | None: ...

    async def list_page(
        self,
        *,
        installation_id: InstallationId,
        before_updated_at: datetime | None,
        before_task_id: TaskId | None,
        limit: int,
    ) -> tuple[TaskRecord, ...]: ...


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _utc_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise InvalidTaskQuery
    normalized = value.astimezone(UTC)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _encode_cursor(boundary: TaskListBoundary) -> str:
    payload = json.dumps(
        {"taskId": str(boundary.task_id), "updatedAt": _utc_text(boundary.updated_at)},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _decode_cursor(value: object) -> TaskListBoundary:
    if not isinstance(value, str) or _CURSOR_PATTERN.fullmatch(value) is None:
        raise InvalidTaskQuery
    boundary: TaskListBoundary | None = None
    try:
        decoded = base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))
        if base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != value:
            raise ValueError("noncanonical cursor")
        payload = json.loads(decoded.decode("ascii"), object_pairs_hook=_unique_object)
        if not isinstance(payload, dict) or set(payload) != {"taskId", "updatedAt"}:
            raise ValueError("invalid cursor object")
        task_id = TaskId.parse(payload["taskId"])
        updated_text = payload["updatedAt"]
        if not isinstance(updated_text, str) or not updated_text.endswith("Z"):
            raise ValueError("invalid cursor timestamp")
        updated_at = datetime.fromisoformat(updated_text.replace("Z", "+00:00"))
        boundary = TaskListBoundary(updated_at=updated_at, task_id=task_id)
        if _encode_cursor(boundary) != value:
            raise ValueError("noncanonical cursor payload")
    except (binascii.Error, InvalidResourceId, UnicodeDecodeError, ValueError):
        boundary = None
    if boundary is None:
        raise InvalidTaskQuery
    return boundary


class TaskQueryService:
    def __init__(self, *, repository: TaskQueryRepository) -> None:
        self._repository = repository

    async def get(
        self,
        *,
        installation_id: InstallationId,
        task_id: str,
    ) -> TaskRecord:
        if not isinstance(installation_id, InstallationId):
            raise InvalidTaskQuery
        try:
            parsed_task_id = TaskId.parse(task_id)
        except InvalidResourceId:
            parsed_task_id = None
        if parsed_task_id is None:
            raise TaskNotFound
        record = await self._repository.get(
            task_id=parsed_task_id,
            installation_id=installation_id,
        )
        if record is None:
            raise TaskNotFound
        return record

    async def list(
        self,
        *,
        installation_id: InstallationId,
        cursor: str | None,
        limit: int,
    ) -> TaskListPage:
        if (
            not isinstance(installation_id, InstallationId)
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            raise InvalidTaskQuery
        boundary = None if cursor is None else _decode_cursor(cursor)
        records = await self._repository.list_page(
            installation_id=installation_id,
            before_updated_at=None if boundary is None else boundary.updated_at,
            before_task_id=None if boundary is None else boundary.task_id,
            limit=limit + 1,
        )
        items = records[:limit]
        next_cursor = (
            _encode_cursor(
                TaskListBoundary(updated_at=items[-1].updated_at, task_id=items[-1].task_id)
            )
            if len(records) > limit
            else None
        )
        return TaskListPage(items=items, next_cursor=next_cursor)


__all__ = [
    "InvalidTaskQuery",
    "TaskListBoundary",
    "TaskListPage",
    "TaskNotFound",
    "TaskQueryRepository",
    "TaskQueryService",
]
