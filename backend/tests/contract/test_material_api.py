"""LE-06 T3: installation-scoped material registration, queries and descriptions."""

from __future__ import annotations

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
    MaterialNotFound,
    MaterialPersistenceUnavailable,
    MaterialService,
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

    async def save_for_installation(
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

    async def get_for_installation(
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

    async def find_by_digest_for_installation(
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

    async def update_description_for_installation(
        self,
        material: Material,
        installation_id: InstallationId,
    ) -> None:
        if self.failure is not None:
            raise self.failure
        stored = await self.get_for_installation(material.material_id, installation_id)
        if (
            material.description_source is DescriptionSource.AI
            and stored.description_source is DescriptionSource.USER
        ):
            raise MaterialDescriptionProtected
        self.materials[material.material_id] = (installation_id, material)


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


def test_openapi_exposes_only_registration_queries_and_description_update() -> None:
    schema = create_app(database=None).openapi()
    collection = schema["paths"]["/api/v1/editing-materials"]
    detail = schema["paths"]["/api/v1/editing-materials/{material_id}"]
    description = schema["paths"]["/api/v1/editing-materials/{material_id}/description"]

    assert set(collection) == {"get", "post"}
    assert set(detail) == {"get"}
    assert set(description) == {"put"}
    assert collection["post"]["operationId"] == "registerEditingMaterial"
    assert collection["get"]["operationId"] == "findEditingMaterialByDigest"
    assert detail["get"]["operationId"] == "getEditingMaterial"
    assert description["put"]["operationId"] == "updateEditingMaterialDescription"
    for operation in (
        collection["post"],
        collection["get"],
        detail["get"],
        description["put"],
    ):
        assert operation["security"] == [{"AppSession": []}]


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
            "describedAt": "2026-07-30T09:10:11.654321Z",
        },
    )
    assert ai_write.status_code == 409
    assert ai_write.json()["error"]["code"] == "material_description_protected"

    stored = next(iter(repository.materials.values()))[1]
    assert stored.ai_description == "用户自己写的露营记录"
    assert stored.description_source is DescriptionSource.USER
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
            "describedAt": "2026-07-30T09:10:11.654321Z",
        },
    )

    assert ai_write.status_code == 200
    assert ai_write.json()["aiDescription"] == "模型更新后的露营描述"
    assert ai_write.json()["aiTags"] == ["更新", "露营"]
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
            installation_id=foreign,  # type: ignore[arg-type]
            material=make_material(),
        )
    with pytest.raises(InvalidMaterialQuery):
        await service.register(
            installation_id=INSTALLATION_ID,
            material=foreign,  # type: ignore[arg-type]
        )
    with pytest.raises(InvalidMaterialQuery):
        await service.get(
            installation_id=foreign,  # type: ignore[arg-type]
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
        await service.update_description(
            installation_id=INSTALLATION_ID,
            material_id=MATERIAL_ID,
            source=foreign,  # type: ignore[arg-type]
            description="不会写入",
            tags=(),
            described_at=None,
        )
    with pytest.raises(InvalidMaterialQuery):
        await service.update_description(
            installation_id=INSTALLATION_ID,
            material_id=MATERIAL_ID,
            source=DescriptionSource.USER,
            description="不会写入",
            tags=("user 不接受标签",),
            described_at=None,
        )
    with pytest.raises(InvalidMaterialQuery):
        await service.update_description(
            installation_id=INSTALLATION_ID,
            material_id=MATERIAL_ID,
            source=DescriptionSource.AI,
            description="不会写入",
            tags=(),
            described_at=None,
        )


@pytest.mark.asyncio
async def test_service_detects_a_user_write_that_wins_before_the_return_read() -> None:
    class ConcurrentUserRepository(MemoryMaterialRepository):
        async def update_description_for_installation(
            self,
            material: Material,
            installation_id: InstallationId,
        ) -> None:
            self.materials[material.material_id] = (
                installation_id,
                material.with_user_description("并发写入的用户描述"),
            )

    repository = ConcurrentUserRepository()
    material = make_material()
    repository.materials[material.material_id] = (INSTALLATION_ID, material)
    service = MaterialService(repository=repository)

    with pytest.raises(MaterialDescriptionProtected):
        await service.update_description(
            installation_id=INSTALLATION_ID,
            material_id=MATERIAL_ID,
            source=DescriptionSource.AI,
            description="模型稍早写入的描述",
            tags=("模型",),
            described_at=NOW,
        )
    assert repository.materials[material.material_id][1].ai_description == "并发写入的用户描述"


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
