from dataclasses import FrozenInstanceError
from typing import cast
from uuid import UUID

import pytest

from automation_tool.control_plane.domain import (
    ActionId,
    ArtifactId,
    ExecutionAttemptId,
    ExecutorId,
    InstallationId,
    InvalidResourceId,
    ResourceId,
    TargetId,
    TaskId,
    UserId,
)

CANONICAL_UUID = "123e4567-e89b-42d3-a456-426614174000"
CANONICAL_VALUE = UUID(CANONICAL_UUID)
RESOURCE_ID_TYPES: tuple[type[ResourceId], ...] = (
    InstallationId,
    ExecutorId,
    TaskId,
    TargetId,
    ExecutionAttemptId,
    ActionId,
    ArtifactId,
    UserId,
)
RESOURCE_NAMES: dict[type[ResourceId], str] = {
    InstallationId: "installation",
    ExecutorId: "executor",
    TaskId: "task",
    TargetId: "target",
    ExecutionAttemptId: "execution attempt",
    ActionId: "action",
    ArtifactId: "artifact",
    UserId: "user",
}


@pytest.mark.parametrize("resource_id_type", RESOURCE_ID_TYPES)
def test_resource_ids_generate_canonical_random_values(
    resource_id_type: type[ResourceId],
) -> None:
    first = resource_id_type.new()
    second = resource_id_type.new()

    assert type(first) is resource_id_type
    assert first != second
    assert first.uuid.version == 4
    assert str(first) == str(first.uuid)


@pytest.mark.parametrize("resource_id_type", RESOURCE_ID_TYPES)
def test_resource_ids_round_trip_canonical_strings_and_uuid_values(
    resource_id_type: type[ResourceId],
) -> None:
    from_text = resource_id_type.parse(CANONICAL_UUID)
    from_uuid = resource_id_type.parse(CANONICAL_VALUE)

    assert from_text == from_uuid
    assert from_text.uuid == CANONICAL_VALUE
    assert resource_id_type.parse(from_text) is from_text


def test_equal_uuid_values_remain_distinct_across_resource_types() -> None:
    resource_ids = {
        resource_id_type.parse(CANONICAL_UUID) for resource_id_type in RESOURCE_ID_TYPES
    }

    assert len(resource_ids) == len(RESOURCE_ID_TYPES)
    installation_id = cast(ResourceId, InstallationId.parse(CANONICAL_UUID))
    executor_id = cast(ResourceId, ExecutorId.parse(CANONICAL_UUID))
    assert installation_id != executor_id


@pytest.mark.parametrize("resource_id_type", RESOURCE_ID_TYPES)
@pytest.mark.parametrize(
    "invalid_value",
    (
        "",
        " ",
        "not-a-uuid",
        CANONICAL_UUID.upper(),
        f" {CANONICAL_UUID}",
        f"{CANONICAL_UUID} ",
        CANONICAL_UUID.replace("-", ""),
        f"{{{CANONICAL_UUID}}}",
        f"urn:uuid:{CANONICAL_UUID}",
        "00000000-0000-0000-0000-000000000000",
        "123e4567-e89b-12d3-a456-426614174000",
    ),
)
def test_resource_ids_reject_malformed_noncanonical_and_non_v4_strings(
    resource_id_type: type[ResourceId], invalid_value: str
) -> None:
    with pytest.raises(InvalidResourceId) as captured:
        resource_id_type.parse(invalid_value)

    assert captured.value.resource == RESOURCE_NAMES[resource_id_type]


@pytest.mark.parametrize("invalid_value", (None, 1, 1.0, b"uuid", object()))
def test_resource_ids_reject_values_from_unsupported_types(invalid_value: object) -> None:
    with pytest.raises(InvalidResourceId):
        InstallationId.parse(invalid_value)


def test_resource_ids_reject_another_resource_type() -> None:
    executor_id = ExecutorId.parse(CANONICAL_UUID)

    with pytest.raises(InvalidResourceId):
        InstallationId.parse(executor_id)


@pytest.mark.parametrize(
    "invalid_uuid",
    (
        UUID("00000000-0000-0000-0000-000000000000"),
        UUID("123e4567-e89b-12d3-a456-426614174000"),
    ),
)
def test_direct_construction_cannot_bypass_uuid_version_validation(invalid_uuid: UUID) -> None:
    with pytest.raises(InvalidResourceId):
        InstallationId(invalid_uuid)


def test_resource_ids_are_immutable() -> None:
    installation_id = InstallationId.parse(CANONICAL_UUID)
    value_attribute = "_value"

    with pytest.raises(FrozenInstanceError):
        setattr(
            installation_id,
            value_attribute,
            UUID("223e4567-e89b-42d3-a456-426614174000"),
        )

    assert installation_id.uuid == CANONICAL_VALUE


def test_invalid_resource_id_errors_do_not_echo_external_values() -> None:
    sensitive_value = "secret-installation-id"

    with pytest.raises(InvalidResourceId) as captured:
        InstallationId.parse(cast(object, sensitive_value))

    assert str(captured.value) == "Invalid installation resource ID"
    assert sensitive_value not in repr(captured.value)
