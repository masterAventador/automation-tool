import pytest
from conftest import AlembicRunner
from sqlalchemy import text

from automation_tool.control_plane.application.action_risk_authorizations import (
    ActionRiskLimitReason,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    action_failure_circuits,
    action_risk_results,
)


def test_a7_14_failure_circuit_contract_exists() -> None:
    assert ActionRiskLimitReason.CONSECUTIVE_FAILURE_CIRCUIT.value == (
        "consecutive_failure_circuit"
    )
    assert action_failure_circuits.name == "action_failure_circuits"
    assert action_risk_results.name == "action_risk_results"


@pytest.mark.asyncio
async def test_failure_circuit_migration_has_exact_tables_and_downgrades_cleanly(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    alembic_runner(postgresql_url, "check")
    database = Database.from_url(postgresql_url)
    try:
        async with database.session() as session:
            revision = await session.scalar(text("select version_num from alembic_version"))
            result_columns = set(
                await session.scalars(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_schema = 'public' and table_name = 'action_risk_results'"
                    )
                )
            )
            circuit_columns = set(
                await session.scalars(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_schema = 'public' and table_name = 'action_failure_circuits'"
                    )
                )
            )
            result_constraints = set(
                await session.scalars(
                    text(
                        "select conname from pg_constraint where conrelid = "
                        "'public.action_risk_results'::regclass"
                    )
                )
            )
            circuit_constraints = set(
                await session.scalars(
                    text(
                        "select conname from pg_constraint where conrelid = "
                        "'public.action_failure_circuits'::regclass"
                    )
                )
            )
        assert revision == "20260721_0027"
        assert result_columns == {
            "action_id",
            "installation_id",
            "platform",
            "action",
            "outcome",
            "consecutive_failures_after",
            "consecutive_failure_threshold",
            "circuit_open_after",
            "triggered_handoff",
            "observed_at",
            "created_at",
        }
        assert circuit_columns == {
            "installation_id",
            "platform",
            "action",
            "consecutive_failures",
            "circuit_open",
            "revision",
            "last_action_id",
            "opened_by_action_id",
            "opened_at",
            "created_at",
            "updated_at",
        }
        assert {
            name for name in result_constraints if name.startswith(("pk_", "fk_", "ck_", "uq_"))
        } == {
            "pk_action_risk_results",
            "fk_action_risk_results_authorization",
            "ck_action_risk_results_outcome",
            "ck_action_risk_results_platform",
            "ck_action_risk_results_action",
            "ck_action_risk_results_limits",
            "ck_action_risk_results_failure_count",
            "ck_action_risk_results_handoff",
            "ck_action_risk_results_time_order",
            "uq_action_risk_results_scope_binding",
        }
        assert {
            name for name in circuit_constraints if name.startswith(("pk_", "fk_", "ck_", "uq_"))
        } == {
            "pk_action_failure_circuits",
            "fk_action_failure_circuits_installation",
            "fk_action_failure_circuits_last_result",
            "fk_action_failure_circuits_open_result",
            "ck_action_failure_circuits_platform",
            "ck_action_failure_circuits_action",
            "ck_action_failure_circuits_counters",
            "ck_action_failure_circuits_open_state",
            "ck_action_failure_circuits_time_order",
        }

        alembic_runner(postgresql_url, "downgrade", "20260720_0022")
        async with database.session() as session:
            assert await session.scalar(text("select version_num from alembic_version")) == (
                "20260720_0022"
            )
            assert (
                await session.scalar(text("select to_regclass('public.action_risk_results')"))
                is None
            )
            assert (
                await session.scalar(text("select to_regclass('public.action_failure_circuits')"))
                is None
            )
    finally:
        await database.close()
        alembic_runner(postgresql_url, "upgrade", "head")
