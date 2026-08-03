"""The current Timeline repository API and database-enforced lineage identity."""

from __future__ import annotations

import inspect

from sqlalchemy import ForeignKeyConstraint, PrimaryKeyConstraint, UniqueConstraint

from automation_tool.control_plane.infrastructure.database import (
    editing_project_timelines,
    timelines,
)
from automation_tool.control_plane.infrastructure.database.timeline_repository import (
    SqlAlchemyTimelineRepository,
)


def _columns(constraint: object) -> tuple[str, ...]:
    assert hasattr(constraint, "columns")
    return tuple(column.name for column in constraint.columns)


def test_repository_has_only_the_three_installation_scoped_operations() -> None:
    public = {
        name: member
        for name, member in inspect.getmembers(
            SqlAlchemyTimelineRepository,
            inspect.isfunction,
        )
        if not name.startswith("_")
    }

    assert set(public) == {"save", "get", "latest_revision"}
    assert tuple(inspect.signature(public["save"]).parameters) == (
        "self",
        "timeline",
        "installation_id",
    )
    assert tuple(inspect.signature(public["get"]).parameters) == (
        "self",
        "timeline_id",
        "revision",
        "installation_id",
    )
    assert tuple(inspect.signature(public["latest_revision"]).parameters) == (
        "self",
        "project_id",
        "installation_id",
    )


def test_schema_enforces_one_timeline_identity_per_project() -> None:
    identity_constraints = editing_project_timelines.constraints
    assert {
        constraint.name: (type(constraint), _columns(constraint))
        for constraint in identity_constraints
    } == {
        "pk_editing_project_timelines": (
            PrimaryKeyConstraint,
            ("project_id",),
        ),
        "fk_editing_project_timelines_project": (
            ForeignKeyConstraint,
            ("project_id",),
        ),
        "uq_editing_project_timelines_timeline": (
            UniqueConstraint,
            ("timeline_id",),
        ),
        "uq_editing_project_timelines_project_timeline": (
            UniqueConstraint,
            ("project_id", "timeline_id"),
        ),
    }

    timeline_foreign_keys = {
        constraint.name: (
            _columns(constraint),
            tuple(element.target_fullname for element in constraint.elements),
        )
        for constraint in timelines.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert timeline_foreign_keys == {
        "fk_timelines_project_timeline": (
            ("project_id", "timeline_id"),
            (
                "editing_project_timelines.project_id",
                "editing_project_timelines.timeline_id",
            ),
        )
    }
