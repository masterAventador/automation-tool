"""Material domain invariants for the local editing library."""

from __future__ import annotations

import pytest

from automation_tool.control_plane.domain.material import (
    DescriptionSource,
    InvalidMaterialModel,
    MaterialId,
    MaterialKind,
)
from automation_tool.control_plane.domain.resource_ids import InvalidResourceId


def test_material_id_is_a_uuid4_resource_id() -> None:
    identifier = MaterialId.new()
    assert MaterialId.parse(str(identifier)) == identifier


def test_material_id_rejects_a_foreign_identifier_type() -> None:
    from automation_tool.control_plane.domain.resource_ids import ArtifactId

    with pytest.raises(InvalidResourceId):
        MaterialId.parse(ArtifactId.new())


def test_material_kinds_are_exactly_the_three_supported() -> None:
    assert {kind.value for kind in MaterialKind} == {"video", "image", "audio"}


def test_description_sources_distinguish_ai_from_user() -> None:
    assert {source.value for source in DescriptionSource} == {"ai", "user"}


def test_invalid_material_model_is_a_value_error() -> None:
    assert issubclass(InvalidMaterialModel, ValueError)
