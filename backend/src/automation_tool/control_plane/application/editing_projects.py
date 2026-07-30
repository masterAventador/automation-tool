"""The editing-project persistence boundary's failure vocabulary.

Four outcomes, because a caller has four different things to do about them: a
duplicate is the caller's own doing and retrying will never help; a missing row
is a question that got an answer; an unavailable database is worth retrying; and
a rejected argument is a bug upstairs. Collapsing them into one exception makes
every caller guess, and leaves the REST layer above answering 409, 404 and 503
with the same status.

Every message is a fixed string and no constructor takes an argument, so nothing
reaching a log through one of these can carry a connection string, a stored
value or a private path -- and `raise ... ("detail")` is a `TypeError` at the
call site rather than a leak.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from automation_tool.control_plane.domain import (
    CaptionStyle,
    EditingProject,
    EditingProjectId,
    InstallationId,
    InvalidResourceId,
    OutputSpec,
)

_CURSOR_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,256}$")


class _EditingProjectPersistenceFailure(RuntimeError):
    message = "Editing project persistence failed"

    def __init__(self) -> None:
        super().__init__(self.message)


class EditingProjectAlreadyRegistered(_EditingProjectPersistenceFailure):
    message = "Editing project is already registered"


class EditingProjectNotFound(_EditingProjectPersistenceFailure):
    message = "Editing project was not found"


class EditingProjectDataRejected(_EditingProjectPersistenceFailure):
    message = "Editing project data is rejected"


class EditingProjectPersistenceUnavailable(_EditingProjectPersistenceFailure):
    message = "Editing project persistence is unavailable"


class InvalidEditingProjectQuery(ValueError):
    def __init__(self) -> None:
        super().__init__("Editing project query is invalid")


@dataclass(frozen=True, slots=True)
class EditingProjectListBoundary:
    created_at: datetime
    project_id: EditingProjectId


@dataclass(frozen=True, slots=True)
class EditingProjectListPage:
    items: tuple[EditingProject, ...]
    next_cursor: str | None


class EditingProjectRepository(Protocol):
    async def save(
        self,
        project: EditingProject,
        installation_id: InstallationId,
    ) -> None: ...

    async def get(
        self,
        project_id: EditingProjectId,
        installation_id: InstallationId,
    ) -> EditingProject: ...

    async def list_page(
        self,
        *,
        installation_id: InstallationId,
        before_created_at: datetime | None,
        before_project_id: EditingProjectId | None,
        limit: int,
    ) -> tuple[EditingProject, ...]: ...


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _utc_text(value: datetime) -> str:
    normalized = value.astimezone(UTC)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _encode_cursor(boundary: EditingProjectListBoundary) -> str:
    payload = json.dumps(
        {
            "createdAt": _utc_text(boundary.created_at),
            "projectId": str(boundary.project_id),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _decode_cursor(value: object) -> EditingProjectListBoundary:
    if not isinstance(value, str) or _CURSOR_PATTERN.fullmatch(value) is None:
        raise InvalidEditingProjectQuery
    boundary: EditingProjectListBoundary | None = None
    try:
        decoded = base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))
        if base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != value:
            raise ValueError("noncanonical cursor")
        payload = json.loads(decoded.decode("ascii"), object_pairs_hook=_unique_object)
        if not isinstance(payload, dict) or set(payload) != {"createdAt", "projectId"}:
            raise ValueError("invalid cursor object")
        project_id = EditingProjectId.parse(payload["projectId"])
        created_text = payload["createdAt"]
        if not isinstance(created_text, str) or not created_text.endswith("Z"):
            raise ValueError("invalid cursor timestamp")
        created_at = datetime.fromisoformat(created_text.replace("Z", "+00:00"))
        boundary = EditingProjectListBoundary(
            created_at=created_at,
            project_id=project_id,
        )
        if _encode_cursor(boundary) != value:
            raise ValueError("noncanonical cursor payload")
    except (binascii.Error, InvalidResourceId, UnicodeDecodeError, ValueError):
        boundary = None
    if boundary is None:
        raise InvalidEditingProjectQuery
    return boundary


class EditingProjectService:
    def __init__(
        self,
        *,
        repository: EditingProjectRepository,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._clock = clock

    async def create(
        self,
        *,
        installation_id: InstallationId,
        title: str,
        output: OutputSpec,
        caption_style: CaptionStyle,
    ) -> EditingProject:
        if not isinstance(installation_id, InstallationId):
            raise InvalidEditingProjectQuery
        project = EditingProject(
            project_id=EditingProjectId.new(),
            title=title,
            output=output,
            caption_style=caption_style,
            created_at=self._clock(),
        )
        await self._repository.save(project, installation_id)
        return project

    async def get(
        self,
        *,
        installation_id: InstallationId,
        project_id: str,
    ) -> EditingProject:
        if not isinstance(installation_id, InstallationId):
            raise InvalidEditingProjectQuery
        try:
            parsed_project_id = EditingProjectId.parse(project_id)
        except InvalidResourceId:
            parsed_project_id = None
        if parsed_project_id is None:
            raise EditingProjectNotFound
        return await self._repository.get(
            parsed_project_id,
            installation_id,
        )

    async def list(
        self,
        *,
        installation_id: InstallationId,
        cursor: str | None,
        limit: int,
    ) -> EditingProjectListPage:
        if (
            not isinstance(installation_id, InstallationId)
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            raise InvalidEditingProjectQuery
        boundary = None if cursor is None else _decode_cursor(cursor)
        projects = await self._repository.list_page(
            installation_id=installation_id,
            before_created_at=None if boundary is None else boundary.created_at,
            before_project_id=None if boundary is None else boundary.project_id,
            limit=limit + 1,
        )
        items = projects[:limit]
        next_cursor = (
            _encode_cursor(
                EditingProjectListBoundary(
                    created_at=items[-1].created_at,
                    project_id=items[-1].project_id,
                )
            )
            if len(projects) > limit
            else None
        )
        return EditingProjectListPage(items=items, next_cursor=next_cursor)


__all__ = [
    "EditingProjectAlreadyRegistered",
    "EditingProjectDataRejected",
    "EditingProjectListBoundary",
    "EditingProjectListPage",
    "EditingProjectNotFound",
    "EditingProjectPersistenceUnavailable",
    "EditingProjectRepository",
    "EditingProjectService",
    "InvalidEditingProjectQuery",
]
