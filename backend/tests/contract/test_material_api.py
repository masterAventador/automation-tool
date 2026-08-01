"""LE-06 T3: installation-scoped material registration, queries and descriptions."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from automation_tool.control_plane import create_app
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.materials import (
    InvalidMaterialQuery,
    MaterialAlreadyRegistered,
    MaterialDescriptionProtected,
    MaterialInUse,
    MaterialNotFound,
    MaterialPersistenceUnavailable,
    MaterialService,
    MaterialSnapshotConflict,
    SmartEditMaterialWriteback,
)
from automation_tool.control_plane.domain import (
    DescriptionSource,
    InstallationId,
    Material,
    MaterialId,
    MaterialKind,
)
from automation_tool.control_plane.infrastructure.database import Database

NOW = datetime(2026, 7, 30, 8, 9, 10, 123_456, tzinfo=UTC)
INSTALLATION_ID = InstallationId.new()
OTHER_INSTALLATION_ID = InstallationId.new()
DIGEST = "a1b2c3d4" * 8
OTHER_DIGEST = "9f8e7d6c" * 8
MATERIAL_ID = "00000000-0000-4000-8000-000000000001"
NARRATION_ID = "00000000-0000-4000-8000-000000000002"

VALID_PAYLOAD: dict[str, object] = {
    "materialId": MATERIAL_ID,
    "kind": "video",
    "durationMs": 185_000,
    "width": 1920,
    "height": 1080,
    "contentDigest": DIGEST,
    "hasAudio": True,
    "audioLoudnessLufs": -14.5,
    "hasSpeech": True,
    "speechSegmentsMs": [[1_200, 4_800], [6_000, 9_500]],
    "speechTranscript": "今天我们去露营",
    "shotBoundariesMs": [0, 3_200, 15_000],
    "aiDescription": "一段露营视频",
    "aiTags": ["户外", "露营"],
    "descriptionSource": "ai",
    "describedAt": "2026-07-30T08:09:10.123456Z",
}


class MemoryMaterialRepository:
    def __init__(self) -> None:
        self.materials: dict[MaterialId, tuple[InstallationId, Material]] = {}
        self.failure: Exception | None = None
        self.in_use: set[MaterialId] = set()

    async def save(
        self,
        material: Material,
        installation_id: InstallationId,
    ) -> None:
        if self.failure is not None:
            raise self.failure
        if material.material_id in self.materials or any(
            owner == installation_id and stored.content_digest == material.content_digest
            for owner, stored in self.materials.values()
        ):
            raise MaterialAlreadyRegistered
        self.materials[material.material_id] = (installation_id, material)

    async def get(
        self,
        material_id: MaterialId,
        installation_id: InstallationId,
    ) -> Material:
        if self.failure is not None:
            raise self.failure
        try:
            owner, material = self.materials[material_id]
        except KeyError:
            raise MaterialNotFound from None
        if owner != installation_id:
            raise MaterialNotFound
        return material

    async def find_by_digest(
        self,
        content_digest: str,
        installation_id: InstallationId,
    ) -> Material | None:
        if self.failure is not None:
            raise self.failure
        return next(
            (
                material
                for owner, material in self.materials.values()
                if owner == installation_id and material.content_digest == content_digest
            ),
            None,
        )

    async def list_page(
        self,
        *,
        installation_id: InstallationId,
        before_material_id: MaterialId | None,
        limit: int,
    ) -> tuple[Material, ...]:
        if self.failure is not None:
            raise self.failure
        visible = sorted(
            (
                material
                for owner, material in self.materials.values()
                if owner == installation_id
                and (
                    before_material_id is None
                    or material.material_id.uuid < before_material_id.uuid
                )
            ),
            key=lambda material: material.material_id.uuid,
            reverse=True,
        )
        return tuple(visible[:limit])

    async def delete(
        self,
        material_id: MaterialId,
        installation_id: InstallationId,
    ) -> None:
        if self.failure is not None:
            raise self.failure
        await self.get(material_id, installation_id)
        if material_id in self.in_use:
            raise MaterialInUse
        del self.materials[material_id]

    async def update_user_description(
        self,
        material: Material,
        installation_id: InstallationId,
    ) -> None:
        if self.failure is not None:
            raise self.failure
        await self.get(material.material_id, installation_id)
        self.materials[material.material_id] = (installation_id, material)

    async def update_ai_understanding(
        self,
        material: Material,
        installation_id: InstallationId,
    ) -> None:
        if self.failure is not None:
            raise self.failure
        stored = await self.get(material.material_id, installation_id)
        if stored.description_source is DescriptionSource.USER:
            raise MaterialDescriptionProtected
        self.materials[material.material_id] = (installation_id, material)

    async def update_speech_analysis(
        self,
        material: Material,
        installation_id: InstallationId,
    ) -> None:
        if self.failure is not None:
            raise self.failure
        await self.get(material.material_id, installation_id)
        self.materials[material.material_id] = (installation_id, material)

    async def apply_smart_edit_writeback(
        self,
        writeback: SmartEditMaterialWriteback,
        installation_id: InstallationId,
    ) -> tuple[Material, ...]:
        if self.failure is not None:
            raise self.failure
        working = dict(self.materials)
        changed: list[Material] = []
        for analysis in writeback.analyses:
            stored = working.get(analysis.material_id)
            if stored is None or stored[0] != installation_id:
                raise MaterialNotFound
            current = stored[1]
            if current.content_digest != analysis.content_digest:
                raise MaterialSnapshotConflict
            if analysis.description_source is DescriptionSource.USER:
                if (
                    current.description_source is not DescriptionSource.USER
                    or current.ai_description != analysis.ai_description
                    or current.ai_tags != analysis.ai_tags
                    or current.shot_boundaries_ms != analysis.shot_boundaries_ms
                    or current.described_at != analysis.described_at
                ):
                    raise MaterialSnapshotConflict
                updated = current
            else:
                if current.description_source is DescriptionSource.USER:
                    raise MaterialDescriptionProtected
                if analysis.ai_description is None:
                    updated = current
                else:
                    assert analysis.described_at is not None
                    updated = current.with_ai_understanding(
                        analysis.ai_description,
                        analysis.ai_tags,
                        analysis.shot_boundaries_ms,
                        analysis.described_at,
                    )
            updated = updated.with_speech_analysis(
                has_speech=analysis.has_speech,
                speech_segments_ms=analysis.speech_segments_ms,
                speech_transcript=analysis.speech_transcript,
            )
            working[updated.material_id] = (installation_id, updated)
            changed.append(updated)
        for narration in writeback.narrations:
            existing = working.get(narration.material_id)
            if existing is not None:
                if existing != (installation_id, narration):
                    raise MaterialAlreadyRegistered
            elif any(
                owner == installation_id and stored.content_digest == narration.content_digest
                for owner, stored in working.values()
            ):
                raise MaterialAlreadyRegistered
            else:
                working[narration.material_id] = (installation_id, narration)
        self.materials = working
        return (*changed, *writeback.narrations)


def material_client(
    repository: MemoryMaterialRepository | None = None,
    *,
    installation_id: InstallationId = INSTALLATION_ID,
) -> tuple[TestClient, MemoryMaterialRepository]:
    resolved = repository or MemoryMaterialRepository()
    app = create_app(
        database=None,
        material_service=MaterialService(repository=resolved),
    )
    app.dependency_overrides[require_current_installation_access] = lambda: installation_id
    return TestClient(app), resolved


def make_material(
    *,
    material_id: str = MATERIAL_ID,
    content_digest: str = DIGEST,
    description: str = "一段露营视频",
    source: DescriptionSource = DescriptionSource.AI,
) -> Material:
    return Material(
        material_id=MaterialId.parse(material_id),
        kind=MaterialKind.VIDEO,
        duration_ms=185_000,
        width=1920,
        height=1080,
        content_digest=content_digest,
        has_audio=True,
        audio_loudness_lufs=-14.5,
        has_speech=True,
        speech_segments_ms=((1_200, 4_800), (6_000, 9_500)),
        speech_transcript="今天我们去露营",
        shot_boundaries_ms=(0, 3_200, 15_000),
        ai_description=description,
        ai_tags=("户外", "露营") if source is DescriptionSource.AI else (),
        description_source=source,
        described_at=NOW if source is DescriptionSource.AI else None,
    )


def expected_snapshot(material: Material) -> dict[str, object]:
    return {
        "materialId": str(material.material_id),
        "kind": material.kind.value,
        "durationMs": material.duration_ms,
        "width": material.width,
        "height": material.height,
        "contentDigest": material.content_digest,
        "hasAudio": material.has_audio,
        "audioLoudnessLufs": material.audio_loudness_lufs,
        "hasSpeech": material.has_speech,
        "speechSegmentsMs": [list(segment) for segment in material.speech_segments_ms],
        "speechTranscript": material.speech_transcript,
        "shotBoundariesMs": list(material.shot_boundaries_ms),
        "aiDescription": material.ai_description,
        "aiTags": list(material.ai_tags),
        "descriptionSource": material.description_source.value,
        "describedAt": (
            None
            if material.described_at is None
            else material.described_at.isoformat().replace("+00:00", "Z")
        ),
    }


def smart_edit_writeback_payload(
    *,
    content_digest: str = DIGEST,
) -> dict[str, object]:
    return {
        "analyses": [
            {
                "materialId": MATERIAL_ID,
                "contentDigest": content_digest,
                "hasSpeech": True,
                "speechSegmentsMs": [[500, 3_000]],
                "speechTranscript": "更新后的原声内容",
                "shotBoundariesMs": [0, 8_000],
                "aiDescription": "更新后的素材理解",
                "aiTags": ["产品", "特写"],
                "descriptionSource": "ai",
                "describedAt": "2026-08-01T08:00:00Z",
            }
        ],
        "narrations": [
            {
                "materialId": NARRATION_ID,
                "contentDigest": OTHER_DIGEST,
                "durationMs": 1_200,
                "speechTranscript": "生成的旁白内容",
            }
        ],
    }


def test_openapi_exposes_material_library_list_and_constrained_delete() -> None:
    schema = create_app(database=None).openapi()
    collection = schema["paths"]["/api/v1/editing-materials"]
    detail = schema["paths"]["/api/v1/editing-materials/{material_id}"]
    library = schema["paths"]["/api/v1/editing-materials/library"]
    description = schema["paths"]["/api/v1/editing-materials/{material_id}/description"]

    assert set(collection) == {"get", "post"}
    assert set(detail) == {"delete", "get"}
    assert set(library) == {"get"}
    assert set(description) == {"put"}
    assert collection["post"]["operationId"] == "registerEditingMaterial"
    assert collection["get"]["operationId"] == "findEditingMaterialByDigest"
    assert detail["get"]["operationId"] == "getEditingMaterial"
    assert detail["delete"]["operationId"] == "deleteEditingMaterial"
    assert library["get"]["operationId"] == "listEditingMaterials"
    assert description["put"]["operationId"] == "updateEditingMaterialDescription"
    for operation in (
        collection["post"],
        collection["get"],
        detail["get"],
        detail["delete"],
        library["get"],
        description["put"],
    ):
        assert operation["security"] == [{"AppSession": []}]


def test_material_library_pages_without_cross_installation_visibility() -> None:
    repository = MemoryMaterialRepository()
    owner_client, _ = material_client(repository)
    other_client, _ = material_client(repository, installation_id=OTHER_INSTALLATION_ID)
    material_ids = [
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000002",
        "00000000-0000-4000-8000-000000000003",
    ]
    for index, material_id in enumerate(material_ids, start=1):
        payload = {
            **VALID_PAYLOAD,
            "materialId": material_id,
            "contentDigest": f"{index:064x}",
        }
        assert owner_client.post("/api/v1/editing-materials", json=payload).status_code == 201
    foreign = {
        **VALID_PAYLOAD,
        "materialId": "00000000-0000-4000-8000-000000000004",
        "contentDigest": f"{4:064x}",
    }
    assert other_client.post("/api/v1/editing-materials", json=foreign).status_code == 201

    first = owner_client.get("/api/v1/editing-materials/library", params={"limit": 2})
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    assert [item["materialId"] for item in first.json()["items"]] == material_ids[::-1][:2]
    cursor = first.json()["nextCursor"]
    assert isinstance(cursor, str)
    assert not any(token in cursor for token in ("/", "\\", "private"))

    second = owner_client.get(
        "/api/v1/editing-materials/library",
        params={"limit": 2, "cursor": cursor},
    )
    assert second.status_code == 200
    assert [item["materialId"] for item in second.json()["items"]] == material_ids[:1]
    assert second.json()["nextCursor"] is None
    assert str(foreign["materialId"]) not in first.text + second.text


def test_material_library_rejects_invalid_paging_and_delete_is_constrained() -> None:
    client, repository = material_client()
    registered = client.post("/api/v1/editing-materials", json=VALID_PAYLOAD)
    assert registered.status_code == 201
    material_id = MaterialId.parse(registered.json()["materialId"])

    for params in (
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"cursor": "private/invalid"},
    ):
        response = client.get("/api/v1/editing-materials/library", params=params)
        assert response.status_code == 422
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["error"]["code"] == "validation"
        assert "private" not in response.text

    repository.in_use.add(material_id)
    refused = client.delete(f"/api/v1/editing-materials/{material_id}")
    assert refused.status_code == 409
    assert refused.headers["cache-control"] == "no-store"
    assert refused.json()["error"]["code"] == "material_in_use"
    assert material_id in repository.materials

    repository.in_use.clear()
    deleted = client.delete(f"/api/v1/editing-materials/{material_id}")
    assert deleted.status_code == 204
    assert deleted.headers["cache-control"] == "no-store"
    assert deleted.content == b""
    missing = client.delete(f"/api/v1/editing-materials/{material_id}")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "material_not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "private/invalid",
        "A" * 257,
        base64.urlsafe_b64encode(
            json.dumps(
                {"materialId": "00000000-0000-4000-8000-000000000001", "extra": 1},
                separators=(",", ":"),
            ).encode("ascii")
        )
        .rstrip(b"=")
        .decode("ascii"),
        base64.urlsafe_b64encode(
            b'{"materialId":"00000000-0000-4000-8000-000000000001",'
            b'"materialId":"00000000-0000-4000-8000-000000000002"}'
        )
        .rstrip(b"=")
        .decode("ascii"),
        base64.urlsafe_b64encode(b'{"materialId":"not-a-material"}').rstrip(b"=").decode("ascii"),
    ],
)
async def test_material_library_cursor_is_canonical_and_fail_closed(cursor: str) -> None:
    service = MaterialService(repository=MemoryMaterialRepository())

    with pytest.raises(InvalidMaterialQuery):
        await service.list(
            installation_id=INSTALLATION_ID,
            cursor=cursor,
            limit=10,
        )


def test_registration_and_both_queries_return_only_the_domain_snapshot() -> None:
    client, repository = material_client()

    registered = client.post("/api/v1/editing-materials", json=VALID_PAYLOAD)

    assert registered.status_code == 201
    assert registered.headers["cache-control"] == "no-store"
    owner, stored = next(iter(repository.materials.values()))
    assert owner == INSTALLATION_ID
    assert registered.json() == expected_snapshot(stored)
    assert set(registered.json()) == set(expected_snapshot(stored))
    assert not {"path", "filePath", "sourcePath", "localPath", "fileUri"} & set(registered.json())

    by_id = client.get(f"/api/v1/editing-materials/{stored.material_id}")
    by_digest = client.get(
        "/api/v1/editing-materials",
        params={"contentDigest": stored.content_digest},
    )
    for loaded in (by_id, by_digest):
        assert loaded.status_code == 200
        assert loaded.headers["cache-control"] == "no-store"
        assert loaded.json() == registered.json()


@pytest.mark.parametrize(
    "path_field",
    ["path", "filePath", "sourcePath", "localPath", "fileUri"],
)
def test_registration_rejects_every_path_shaped_field(path_field: str) -> None:
    client, repository = material_client()

    response = client.post(
        "/api/v1/editing-materials",
        json={**VALID_PAYLOAD, path_field: "/Users/private/露营.mp4"},
    )

    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["error"]["code"] == "validation"
    assert "private" not in response.text
    assert repository.materials == {}


def test_user_description_roundtrip_permanently_protects_against_ai() -> None:
    client, repository = material_client()
    registered = client.post("/api/v1/editing-materials", json=VALID_PAYLOAD)
    material_id = registered.json()["materialId"]

    user_write = client.put(
        f"/api/v1/editing-materials/{material_id}/description",
        json={"source": "user", "description": "用户自己写的露营记录"},
    )
    assert user_write.status_code == 200
    assert user_write.json()["aiDescription"] == "用户自己写的露营记录"
    assert user_write.json()["aiTags"] == []
    assert user_write.json()["descriptionSource"] == "user"
    assert user_write.json()["describedAt"] is None

    ai_write = client.put(
        f"/api/v1/editing-materials/{material_id}/description",
        json={
            "source": "ai",
            "description": "模型试图覆盖",
            "tags": ["不应写入"],
            "shotBoundariesMs": [0, 12_000, 30_000],
            "describedAt": "2026-07-30T09:10:11.654321Z",
        },
    )
    assert ai_write.status_code == 409
    assert ai_write.json()["error"]["code"] == "material_description_protected"

    stored = next(iter(repository.materials.values()))[1]
    assert stored.ai_description == "用户自己写的露营记录"
    assert stored.description_source is DescriptionSource.USER
    assert stored.shot_boundaries_ms == (0, 3_200, 15_000)
    assert "模型试图覆盖" not in repr(stored)


def test_ai_description_roundtrip_returns_the_actual_stored_snapshot() -> None:
    client, _ = material_client()
    registered = client.post("/api/v1/editing-materials", json=VALID_PAYLOAD)
    material_id = registered.json()["materialId"]

    ai_write = client.put(
        f"/api/v1/editing-materials/{material_id}/description",
        json={
            "source": "ai",
            "description": "模型更新后的露营描述",
            "tags": ["更新", "露营"],
            "shotBoundariesMs": [0, 8_000, 27_000],
            "describedAt": "2026-07-30T09:10:11.654321Z",
        },
    )

    assert ai_write.status_code == 200
    assert ai_write.json()["aiDescription"] == "模型更新后的露营描述"
    assert ai_write.json()["aiTags"] == ["更新", "露营"]
    assert ai_write.json()["shotBoundariesMs"] == [0, 8_000, 27_000]
    assert ai_write.json()["descriptionSource"] == "ai"
    assert ai_write.json()["describedAt"] == "2026-07-30T09:10:11.654321Z"


def test_domain_invalid_description_is_the_same_fixed_validation_error() -> None:
    client, _ = material_client()
    registered = client.post("/api/v1/editing-materials", json=VALID_PAYLOAD)
    material_id = registered.json()["materialId"]

    response = client.put(
        f"/api/v1/editing-materials/{material_id}/description",
        json={"source": "user", "description": " "},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation"
    assert "Request validation failed" in response.text


def test_materials_and_descriptions_are_isolated_by_installation() -> None:
    repository = MemoryMaterialRepository()
    owner_client, _ = material_client(repository)
    other_client, _ = material_client(repository, installation_id=OTHER_INSTALLATION_ID)

    owner_registered = owner_client.post("/api/v1/editing-materials", json=VALID_PAYLOAD)
    assert owner_registered.status_code == 201
    other_payload = {
        **VALID_PAYLOAD,
        "materialId": "00000000-0000-4000-8000-000000000002",
    }
    other_registered = other_client.post("/api/v1/editing-materials", json=other_payload)
    assert other_registered.status_code == 201

    hidden_detail = other_client.get(f"/api/v1/editing-materials/{MATERIAL_ID}")
    other_by_digest = other_client.get(
        "/api/v1/editing-materials",
        params={"contentDigest": DIGEST},
    )
    hidden_update = other_client.put(
        f"/api/v1/editing-materials/{MATERIAL_ID}/description",
        json={"source": "user", "description": "越权修改"},
    )

    assert hidden_detail.status_code == 404
    assert hidden_update.status_code == 404
    assert other_by_digest.status_code == 200
    assert other_by_digest.json()["materialId"] == other_payload["materialId"]
    assert "越权修改" not in repr(repository.materials)


@pytest.mark.parametrize(
    "payload",
    [
        {**VALID_PAYLOAD, "privateFutureField": "private"},
        {**VALID_PAYLOAD, "materialId": "private-invalid"},
        {**VALID_PAYLOAD, "kind": "gif"},
        {**VALID_PAYLOAD, "durationMs": "185000"},
        {**VALID_PAYLOAD, "hasAudio": 1},
        {**VALID_PAYLOAD, "audioLoudnessLufs": -71.0},
        {**VALID_PAYLOAD, "speechSegmentsMs": [[4_800, 1_200]]},
        {**VALID_PAYLOAD, "contentDigest": "private-invalid"},
        {**VALID_PAYLOAD, "descriptionSource": "user"},
    ],
)
def test_invalid_registration_values_fail_closed(payload: dict[str, object]) -> None:
    client, repository = material_client()

    response = client.post("/api/v1/editing-materials", json=payload)

    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["error"]["code"] == "validation"
    assert "private" not in response.text
    assert repository.materials == {}


def test_invalid_description_union_and_queries_fail_closed() -> None:
    client, _ = material_client()
    invalid_responses = (
        client.get("/api/v1/editing-materials"),
        client.get(
            "/api/v1/editing-materials",
            params={"contentDigest": "private-invalid"},
        ),
        client.put(
            f"/api/v1/editing-materials/{MATERIAL_ID}/description",
            json={"source": "user", "description": "用户文本", "tags": ["private"]},
        ),
        client.put(
            f"/api/v1/editing-materials/{MATERIAL_ID}/description",
            json={"source": "ai", "description": "模型文本"},
        ),
        client.put(
            f"/api/v1/editing-materials/{MATERIAL_ID}/description",
            json={
                "source": "ai",
                "description": "模型文本",
                "tags": [],
                "shotBoundariesMs": [],
                "describedAt": "2026-07-30T09:10:11.654321Z",
            },
        ),
        client.put(
            f"/api/v1/editing-materials/{MATERIAL_ID}/description",
            json={
                "source": "robot",
                "description": "private",
                "tags": [],
                "describedAt": "2026-07-30T09:10:11.654321Z",
            },
        ),
    )
    for response in invalid_responses:
        assert response.status_code == 422
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["error"]["code"] == "validation"
        assert "private" not in response.text


@pytest.mark.parametrize(
    "invalid_described_at",
    [
        0,
        "0",
        "2026-07-30",
        "2026-07-30T09:10:11",
        "2026-07-30T09:10Z",
        "2026-07-30T09:10:11+0800",
        "2026-07-30T09:10:11,1Z",
    ],
)
def test_registration_and_ai_update_reject_non_aware_datetime_wire_values(
    invalid_described_at: object,
) -> None:
    client, repository = material_client()

    rejected_registration = client.post(
        "/api/v1/editing-materials",
        json={**VALID_PAYLOAD, "describedAt": invalid_described_at},
    )

    assert rejected_registration.status_code == 422
    assert rejected_registration.json()["error"]["code"] == "validation"
    assert repository.materials == {}

    registered = client.post("/api/v1/editing-materials", json=VALID_PAYLOAD)
    material_id = registered.json()["materialId"]
    before = repository.materials.copy()
    rejected_update = client.put(
        f"/api/v1/editing-materials/{material_id}/description",
        json={
            "source": "ai",
            "description": "不应写入的模型描述",
            "tags": [],
            "describedAt": invalid_described_at,
        },
    )

    assert rejected_update.status_code == 422
    assert rejected_update.json()["error"]["code"] == "validation"
    assert repository.materials == before


def test_not_found_duplicate_unavailable_auth_and_missing_service_are_stable() -> None:
    client, repository = material_client()
    for response in (
        client.get("/api/v1/editing-materials/private-invalid"),
        client.get(f"/api/v1/editing-materials/{MaterialId.new()}"),
        client.get(
            "/api/v1/editing-materials",
            params={"contentDigest": OTHER_DIGEST},
        ),
    ):
        assert response.status_code == 404
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["error"]["code"] == "material_not_found"
        assert "private" not in response.text

    repository.failure = MaterialAlreadyRegistered()
    duplicate = client.post("/api/v1/editing-materials", json=VALID_PAYLOAD)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "material_already_registered"

    repository.failure = MaterialPersistenceUnavailable()
    unavailable = client.get(
        "/api/v1/editing-materials",
        params={"contentDigest": DIGEST},
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["error"] == {
        "code": "material_persistence_unavailable",
        "message": "Material persistence is unavailable",
        "retryable": True,
        "requestId": unavailable.headers["x-request-id"],
    }

    repository.failure = InvalidMaterialQuery()
    invalid_scope = client.get(f"/api/v1/editing-materials/{MaterialId.new()}")
    assert invalid_scope.status_code == 422
    assert invalid_scope.json()["error"]["code"] == "validation"
    invalid_digest_scope = client.get(
        "/api/v1/editing-materials",
        params={"contentDigest": DIGEST},
    )
    assert invalid_digest_scope.status_code == 422
    assert invalid_digest_scope.json()["error"]["code"] == "validation"

    no_auth = TestClient(create_app(database=None)).get(
        "/api/v1/editing-materials",
        params={"contentDigest": DIGEST},
    )
    assert no_auth.status_code == 401
    assert no_auth.json()["error"]["code"] == "installation_access_denied"

    app = create_app(database=None)
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID
    missing_service = TestClient(app).get(
        "/api/v1/editing-materials",
        params={"contentDigest": DIGEST},
    )
    assert missing_service.status_code == 503
    assert missing_service.json()["error"]["code"] == "editing_materials_unavailable"


@pytest.mark.asyncio
async def test_service_rejects_foreign_types_before_the_repository() -> None:
    repository = MemoryMaterialRepository()
    material = make_material()
    repository.materials[material.material_id] = (INSTALLATION_ID, material)
    service = MaterialService(repository=repository)
    foreign = object()

    with pytest.raises(InvalidMaterialQuery):
        await service.register(
            installation_id=foreign,
            material=make_material(),
        )
    with pytest.raises(InvalidMaterialQuery):
        await service.register(
            installation_id=INSTALLATION_ID,
            material=foreign,
        )
    with pytest.raises(InvalidMaterialQuery):
        await service.get(
            installation_id=foreign,
            material_id=MATERIAL_ID,
        )
    with pytest.raises(InvalidMaterialQuery):
        await service.find_by_digest(
            installation_id=INSTALLATION_ID,
            content_digest=foreign,  # type: ignore[arg-type]
        )
    with pytest.raises(InvalidMaterialQuery):
        await service.find_by_digest(
            installation_id=INSTALLATION_ID,
            content_digest="not-a-digest",
        )
    with pytest.raises(InvalidMaterialQuery):
        await service.list(
            installation_id=foreign,
            cursor=None,
            limit=10,
        )
    with pytest.raises(InvalidMaterialQuery):
        await service.list(
            installation_id=INSTALLATION_ID,
            cursor=foreign,  # type: ignore[arg-type]
            limit=10,
        )
    with pytest.raises(InvalidMaterialQuery):
        await service.list(
            installation_id=INSTALLATION_ID,
            cursor=None,
            limit=True,
        )
    with pytest.raises(MaterialNotFound):
        await service.delete(
            installation_id=INSTALLATION_ID,
            material_id=foreign,  # type: ignore[arg-type]
        )
    with pytest.raises(InvalidMaterialQuery):
        await service.update_understanding(
            installation_id=INSTALLATION_ID,
            material_id=MATERIAL_ID,
            source=foreign,
            description="不会写入",
            tags=(),
            shot_boundaries_ms=None,
            described_at=None,
        )
    with pytest.raises(InvalidMaterialQuery):
        await service.update_understanding(
            installation_id=INSTALLATION_ID,
            material_id=MATERIAL_ID,
            source=DescriptionSource.USER,
            description="不会写入",
            tags=("user 不接受标签",),
            shot_boundaries_ms=None,
            described_at=None,
        )
    with pytest.raises(InvalidMaterialQuery):
        await service.update_understanding(
            installation_id=INSTALLATION_ID,
            material_id=MATERIAL_ID,
            source=DescriptionSource.AI,
            description="不会写入",
            tags=(),
            shot_boundaries_ms=(),
            described_at=None,
        )


@pytest.mark.asyncio
async def test_service_detects_a_user_write_that_wins_before_the_return_read() -> None:
    class ConcurrentUserRepository(MemoryMaterialRepository):
        async def update_ai_understanding(
            self,
            material: Material,
            installation_id: InstallationId,
        ) -> None:
            stored = await self.get(material.material_id, installation_id)
            self.materials[material.material_id] = (
                installation_id,
                stored.with_user_description("并发写入的用户描述"),
            )

    repository = ConcurrentUserRepository()
    material = make_material()
    repository.materials[material.material_id] = (INSTALLATION_ID, material)
    service = MaterialService(repository=repository)

    with pytest.raises(MaterialDescriptionProtected):
        await service.update_understanding(
            installation_id=INSTALLATION_ID,
            material_id=MATERIAL_ID,
            source=DescriptionSource.AI,
            description="模型稍早写入的描述",
            tags=("模型",),
            shot_boundaries_ms=(0, 8_000),
            described_at=NOW,
        )
    assert repository.materials[material.material_id][1].ai_description == "并发写入的用户描述"
    assert repository.materials[material.material_id][1].shot_boundaries_ms == (
        0,
        3_200,
        15_000,
    )


def test_smart_edit_writeback_atomically_updates_analysis_and_registers_narration() -> None:
    client, repository = material_client()
    original = make_material()
    repository.materials[original.material_id] = (INSTALLATION_ID, original)
    payload = smart_edit_writeback_payload()

    response = client.post(
        "/api/v1/editing-materials/smart-edit-writebacks",
        json=payload,
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    materials = response.json()["materials"]
    assert [value["materialId"] for value in materials] == [MATERIAL_ID, NARRATION_ID]
    assert materials[0]["aiDescription"] == "更新后的素材理解"
    assert materials[0]["speechTranscript"] == "更新后的原声内容"
    assert materials[1]["kind"] == "audio"
    assert materials[1]["speechSegmentsMs"] == [[0, 1_200]]
    assert "relativePath" not in response.text
    assert "prompt" not in response.text

    retry = client.post(
        "/api/v1/editing-materials/smart-edit-writebacks",
        json=payload,
    )
    assert retry.status_code == 200
    assert retry.json() == response.json()
    assert len(repository.materials) == 2


def test_smart_edit_writeback_snapshot_conflict_rolls_back_the_narration() -> None:
    client, repository = material_client()
    original = make_material()
    repository.materials[original.material_id] = (INSTALLATION_ID, original)

    response = client.post(
        "/api/v1/editing-materials/smart-edit-writebacks",
        json=smart_edit_writeback_payload(content_digest="c" * 64),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "material_snapshot_conflict"
    assert repository.materials == {original.material_id: (INSTALLATION_ID, original)}


def test_smart_edit_writeback_cannot_overwrite_a_user_description() -> None:
    client, repository = material_client()
    original = make_material(
        description="用户刚刚改写的描述",
        source=DescriptionSource.USER,
    )
    repository.materials[original.material_id] = (INSTALLATION_ID, original)

    response = client.post(
        "/api/v1/editing-materials/smart-edit-writebacks",
        json=smart_edit_writeback_payload(),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "material_description_protected"
    assert repository.materials == {original.material_id: (INSTALLATION_ID, original)}


def test_smart_edit_writeback_rejects_worker_paths_and_unknown_fields() -> None:
    client, repository = material_client()
    original = make_material()
    repository.materials[original.material_id] = (INSTALLATION_ID, original)
    payload = smart_edit_writeback_payload()
    narrations = payload["narrations"]
    assert isinstance(narrations, list)
    assert isinstance(narrations[0], dict)
    narrations[0]["relativePath"] = "voiceover/sentence-0001.wav"

    response = client.post(
        "/api/v1/editing-materials/smart-edit-writebacks",
        json=payload,
    )

    assert response.status_code == 422
    assert repository.materials == {original.material_id: (INSTALLATION_ID, original)}


def test_smart_edit_writeback_cannot_reach_another_installations_material() -> None:
    repository = MemoryMaterialRepository()
    owner_client, _ = material_client(repository)
    outsider_client, _ = material_client(
        repository,
        installation_id=OTHER_INSTALLATION_ID,
    )
    original = make_material()
    repository.materials[original.material_id] = (INSTALLATION_ID, original)

    response = outsider_client.post(
        "/api/v1/editing-materials/smart-edit-writebacks",
        json=smart_edit_writeback_payload(),
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "material_not_found"
    assert repository.materials == {original.material_id: (INSTALLATION_ID, original)}
    assert owner_client.get(f"/api/v1/editing-materials/{MATERIAL_ID}").status_code == 200


def test_application_factory_wires_the_real_repository() -> None:
    database = Database.from_url(
        "postgresql+asyncpg://private:private@127.0.0.1:1/private",
        connect_timeout_seconds=0.05,
    )
    app = create_app(database=database)
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION_ID

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/editing-materials",
            params={"contentDigest": DIGEST},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "material_persistence_unavailable"
    assert response.json()["error"]["retryable"] is True
    assert "private" not in response.text
