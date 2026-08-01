"""The material persistence boundary's failure vocabulary.

Six outcomes, because a caller has six different things to do about them: a
duplicate is either the same file arriving twice or the same identifier reused,
and neither gets better on a retry; a missing row is a question that got an
answer; a material used by an immutable timeline cannot be deleted; a
description a person has taken over is a refusal aimed at one caller and nobody
else; an unavailable database is worth retrying; and a rejected argument is a
bug upstairs. Collapsing them into one exception makes every caller guess, and
leaves the REST layer above answering 409, 404 and 503 with the same status.

Every message is a fixed string and no constructor takes an argument, so nothing
reaching a log through one of these can carry a connection string, a content
digest or a private path -- and `raise ... ("detail")` is a `TypeError` at the
call site rather than a leak. The digest matters here specifically: it is
derived from a file on the user's own disk, and a duplicate-import refusal is
exactly the moment something would be tempted to name it.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from automation_tool.control_plane.domain import (
    DescriptionSource,
    InstallationId,
    Material,
    MaterialId,
    MaterialKind,
)
from automation_tool.control_plane.domain.resource_ids import InvalidResourceId

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}\Z")
_CURSOR_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,256}$")


class _MaterialPersistenceFailure(RuntimeError):
    message = "Material persistence failed"

    def __init__(self) -> None:
        super().__init__(self.message)


class MaterialAlreadyRegistered(_MaterialPersistenceFailure):
    message = "Material is already registered"


class MaterialNotFound(_MaterialPersistenceFailure):
    message = "Material was not found"


class MaterialInUse(_MaterialPersistenceFailure):
    message = "Material is used by a timeline"


class MaterialDescriptionProtected(_MaterialPersistenceFailure):
    """A describe pass tried to write over a description a person owns.

    Deliberately not `MaterialNotFound`, even though both surface as an update
    that matched nothing. The material is right there; what changed is who owns
    the field. A caller told "not found" stops retrying and may conclude the
    material was deleted, and the REST layer above answers 404 where 409 is the
    honest reply.

    **Contract for anything that writes a model-generated description** -- this
    is written here rather than in a task note because this is the file such a
    caller imports, and a note lives in a document nobody will have open:

    1. **This is a normal terminal outcome, not an error.** It means the user
       has taken the field over and this model result is discarded. Catch it
       explicitly and record that; do **not** re-queue the work as a failure.
       `description_source = USER` is terminal, so a retry can never succeed.
    2. **Do not fold it into `MaterialNotFound`.** That one means the material
       is gone, so stop. This one means the material is fine and this one field
       is no longer yours to write.
    3. **Discard the complete understanding result.** Description, tags,
       timestamp and shot boundaries come from one model response. A current
       `USER` snapshot is refused before persistence; a stale AI snapshot is
       refused by the SQL predicate. Neither path may keep the new shot
       boundaries while discarding only the model text.

    A caller may hold a snapshot from before the model call, and even the
    shorter load-to-UPDATE window inside `MaterialService` can race a user edit.
    The database predicate is required for both cases; this is not merely a
    theoretical interleaving.
    """

    message = "Material description is owned by the user"


class MaterialDataRejected(_MaterialPersistenceFailure):
    message = "Material data is rejected"


class MaterialPersistenceUnavailable(_MaterialPersistenceFailure):
    message = "Material persistence is unavailable"


class MaterialSnapshotConflict(_MaterialPersistenceFailure):
    message = "Material snapshot has changed"


class InvalidMaterialQuery(ValueError):
    def __init__(self) -> None:
        super().__init__("Material query is invalid")


@dataclass(frozen=True, slots=True)
class MaterialListBoundary:
    material_id: MaterialId


@dataclass(frozen=True, slots=True)
class MaterialListPage:
    items: tuple[Material, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True, repr=False)
class SmartEditMaterialAnalysisWriteback:
    """One path-free, digest-bound Worker analysis snapshot."""

    material_id: MaterialId
    content_digest: str = field(repr=False)
    has_speech: bool
    speech_segments_ms: tuple[tuple[int, int], ...] = field(repr=False)
    speech_transcript: str | None = field(repr=False)
    shot_boundaries_ms: tuple[int, ...] = field(repr=False)
    ai_description: str | None = field(repr=False)
    ai_tags: tuple[str, ...] = field(repr=False)
    description_source: DescriptionSource
    described_at: datetime | None = field(repr=False)

    def __post_init__(self) -> None:
        validation: Material | None = None
        with suppress(Exception):
            validation = Material.register(
                material_id=self.material_id,
                kind=MaterialKind.VIDEO,
                duration_ms=max(
                    1,
                    max((end for _start, end in self.speech_segments_ms), default=0),
                    max(self.shot_boundaries_ms, default=0) + 1,
                ),
                width=1,
                height=1,
                content_digest=self.content_digest,
                has_audio=self.has_speech,
                audio_loudness_lufs=None,
                has_speech=self.has_speech,
                speech_segments_ms=self.speech_segments_ms,
                speech_transcript=self.speech_transcript,
                shot_boundaries_ms=self.shot_boundaries_ms,
                ai_description=self.ai_description,
                ai_tags=self.ai_tags,
                description_source=self.description_source,
                described_at=self.described_at,
            )
        if validation is None:
            raise InvalidMaterialQuery from None

    def __repr__(self) -> str:
        return "SmartEditMaterialAnalysisWriteback(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class SmartEditMaterialWriteback:
    """Every material mutation from one generated draft, committed together."""

    analyses: tuple[SmartEditMaterialAnalysisWriteback, ...] = field(repr=False)
    narrations: tuple[Material, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.analyses, tuple)
            or not 1 <= len(self.analyses) <= 32
            or not all(
                isinstance(value, SmartEditMaterialAnalysisWriteback) for value in self.analyses
            )
            or not isinstance(self.narrations, tuple)
            or len(self.narrations) > 32
            or not all(self._valid_narration(value) for value in self.narrations)
        ):
            raise InvalidMaterialQuery
        analysis_ids = tuple(value.material_id for value in self.analyses)
        narration_ids = tuple(value.material_id for value in self.narrations)
        narration_digests = tuple(value.content_digest for value in self.narrations)
        if (
            len(set(analysis_ids)) != len(analysis_ids)
            or len(set(narration_ids)) != len(narration_ids)
            or len(set(narration_digests)) != len(narration_digests)
            or not set(analysis_ids).isdisjoint(narration_ids)
            or not {value.content_digest for value in self.analyses}.isdisjoint(narration_digests)
        ):
            raise InvalidMaterialQuery

    @staticmethod
    def _valid_narration(value: object) -> bool:
        return (
            isinstance(value, Material)
            and value.kind is MaterialKind.AUDIO
            and value.duration_ms is not None
            and value.has_audio
            and value.has_speech
            and value.speech_segments_ms == ((0, value.duration_ms),)
            and value.speech_transcript is not None
            and not value.shot_boundaries_ms
            and value.ai_description is None
            and not value.ai_tags
            and value.description_source is DescriptionSource.AI
            and value.described_at is None
        )

    def __repr__(self) -> str:
        return "SmartEditMaterialWriteback(<redacted>)"


class MaterialRepository(Protocol):
    async def save(
        self,
        material: Material,
        installation_id: InstallationId,
    ) -> None: ...

    async def get(
        self,
        material_id: MaterialId,
        installation_id: InstallationId,
    ) -> Material: ...

    async def find_by_digest(
        self,
        content_digest: str,
        installation_id: InstallationId,
    ) -> Material | None: ...

    async def list_page(
        self,
        *,
        installation_id: InstallationId,
        before_material_id: MaterialId | None,
        limit: int,
    ) -> tuple[Material, ...]: ...

    async def delete(
        self,
        material_id: MaterialId,
        installation_id: InstallationId,
    ) -> None: ...

    async def update_user_description(
        self,
        material: Material,
        installation_id: InstallationId,
    ) -> None: ...

    async def update_ai_understanding(
        self,
        material: Material,
        installation_id: InstallationId,
    ) -> None: ...

    async def update_speech_analysis(
        self,
        material: Material,
        installation_id: InstallationId,
    ) -> None: ...

    async def apply_smart_edit_writeback(
        self,
        writeback: SmartEditMaterialWriteback,
        installation_id: InstallationId,
    ) -> tuple[Material, ...]: ...


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _encode_cursor(boundary: MaterialListBoundary) -> str:
    payload = json.dumps(
        {"materialId": str(boundary.material_id)},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _decode_cursor(value: object) -> MaterialListBoundary:
    if not isinstance(value, str) or _CURSOR_PATTERN.fullmatch(value) is None:
        raise InvalidMaterialQuery
    boundary: MaterialListBoundary | None = None
    try:
        decoded = base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))
        if base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != value:
            raise ValueError("noncanonical cursor")
        payload = json.loads(decoded.decode("ascii"), object_pairs_hook=_unique_object)
        if not isinstance(payload, dict) or set(payload) != {"materialId"}:
            raise ValueError("invalid cursor object")
        boundary = MaterialListBoundary(material_id=MaterialId.parse(payload["materialId"]))
        if _encode_cursor(boundary) != value:
            raise ValueError("noncanonical cursor payload")
    except (binascii.Error, InvalidResourceId, TypeError, UnicodeDecodeError, ValueError):
        boundary = None
    if boundary is None:
        raise InvalidMaterialQuery
    return boundary


class MaterialService:
    """Installation-scoped material registration, lookup and understanding writes."""

    def __init__(self, *, repository: MaterialRepository) -> None:
        self._repository = repository

    @staticmethod
    def _require_installation(installation_id: object) -> InstallationId:
        if not isinstance(installation_id, InstallationId):
            raise InvalidMaterialQuery
        return installation_id

    async def register(
        self,
        *,
        installation_id: InstallationId,
        material: Material,
    ) -> Material:
        owner = self._require_installation(installation_id)
        if not isinstance(material, Material):
            raise InvalidMaterialQuery
        await self._repository.save(material, owner)
        return material

    async def get(
        self,
        *,
        installation_id: InstallationId,
        material_id: str,
    ) -> Material:
        owner = self._require_installation(installation_id)
        try:
            parsed_material_id = MaterialId.parse(material_id)
        except (InvalidResourceId, TypeError):
            parsed_material_id = None
        if parsed_material_id is None:
            raise MaterialNotFound
        return await self._repository.get(parsed_material_id, owner)

    async def find_by_digest(
        self,
        *,
        installation_id: InstallationId,
        content_digest: str,
    ) -> Material:
        owner = self._require_installation(installation_id)
        if not isinstance(content_digest, str) or _SHA256_PATTERN.fullmatch(content_digest) is None:
            raise InvalidMaterialQuery
        material = await self._repository.find_by_digest(
            content_digest,
            owner,
        )
        if material is None:
            raise MaterialNotFound
        return material

    async def list(
        self,
        *,
        installation_id: InstallationId,
        cursor: str | None,
        limit: int,
    ) -> MaterialListPage:
        owner = self._require_installation(installation_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidMaterialQuery
        boundary = None if cursor is None else _decode_cursor(cursor)
        materials = await self._repository.list_page(
            installation_id=owner,
            before_material_id=None if boundary is None else boundary.material_id,
            limit=limit + 1,
        )
        items = materials[:limit]
        next_cursor = (
            _encode_cursor(MaterialListBoundary(material_id=items[-1].material_id))
            if len(materials) > limit
            else None
        )
        return MaterialListPage(items=items, next_cursor=next_cursor)

    async def delete(
        self,
        *,
        installation_id: InstallationId,
        material_id: str,
    ) -> None:
        owner = self._require_installation(installation_id)
        try:
            parsed_material_id = MaterialId.parse(material_id)
        except (InvalidResourceId, TypeError):
            parsed_material_id = None
        if parsed_material_id is None:
            raise MaterialNotFound
        await self._repository.delete(parsed_material_id, owner)

    async def update_understanding(
        self,
        *,
        installation_id: InstallationId,
        material_id: str,
        source: DescriptionSource,
        description: str,
        tags: tuple[str, ...],
        shot_boundaries_ms: tuple[int, ...] | None,
        described_at: datetime | None,
    ) -> Material:
        owner = self._require_installation(installation_id)
        if not isinstance(source, DescriptionSource):
            raise InvalidMaterialQuery
        current = await self.get(
            installation_id=owner,
            material_id=material_id,
        )
        if source is DescriptionSource.USER:
            if tags or shot_boundaries_ms is not None or described_at is not None:
                raise InvalidMaterialQuery
            changed = current.with_user_description(description)
            await self._repository.update_user_description(changed, owner)
        else:
            if (
                described_at is None
                or not isinstance(shot_boundaries_ms, tuple)
                or not shot_boundaries_ms
            ):
                raise InvalidMaterialQuery
            if current.description_source is DescriptionSource.USER:
                raise MaterialDescriptionProtected
            changed = current.with_ai_understanding(
                description,
                tags,
                shot_boundaries_ms,
                described_at,
            )
            await self._repository.update_ai_understanding(changed, owner)
        stored = await self._repository.get(changed.material_id, owner)
        if source is DescriptionSource.AI and stored.description_source is DescriptionSource.USER:
            raise MaterialDescriptionProtected
        return stored

    async def update_speech_analysis(
        self,
        *,
        installation_id: InstallationId,
        material_id: str,
        has_speech: bool,
        speech_segments_ms: tuple[tuple[int, int], ...],
        speech_transcript: str | None,
    ) -> Material:
        """Atomically replace the complete path-free speech-analysis result."""

        owner = self._require_installation(installation_id)
        current = await self.get(
            installation_id=owner,
            material_id=material_id,
        )
        changed = current.with_speech_analysis(
            has_speech=has_speech,
            speech_segments_ms=speech_segments_ms,
            speech_transcript=speech_transcript,
        )
        await self._repository.update_speech_analysis(changed, owner)
        return await self._repository.get(changed.material_id, owner)

    async def apply_smart_edit_writeback(
        self,
        *,
        installation_id: InstallationId,
        writeback: SmartEditMaterialWriteback,
    ) -> tuple[Material, ...]:
        """Persist one generated draft's analyses and narrations atomically."""

        owner = self._require_installation(installation_id)
        if not isinstance(writeback, SmartEditMaterialWriteback):
            raise InvalidMaterialQuery
        return await self._repository.apply_smart_edit_writeback(writeback, owner)


__all__ = [
    "InvalidMaterialQuery",
    "MaterialAlreadyRegistered",
    "MaterialDataRejected",
    "MaterialDescriptionProtected",
    "MaterialInUse",
    "MaterialListBoundary",
    "MaterialListPage",
    "MaterialNotFound",
    "MaterialPersistenceUnavailable",
    "MaterialRepository",
    "MaterialService",
    "MaterialSnapshotConflict",
    "SmartEditMaterialAnalysisWriteback",
    "SmartEditMaterialWriteback",
]
