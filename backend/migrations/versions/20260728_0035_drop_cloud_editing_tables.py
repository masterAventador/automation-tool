"""Drop the Aliyun cloud-editing tables.

LE-01 removed the cloud-editing route. The two migrations that created these
tables (20260723_0032, 20260723_0034) stay on disk: 0032 sits mid-chain with
0033 pointing at it, so deleting the file would break the chain, and databases
that already ran them would keep the tables regardless.

All three tables dropped here -- `aliyun_editing_intents`, `editing_output_lineages`,
and `editing_output_artifacts` -- were created by the two cloud-editing migrations
(20260723_0032 and 20260723_0034) and had exactly one repository consumer between
them (`aliyun_editing_intent_repository.py`, `editing_output_ledger_repository.py`),
both deleted in Task 2. LE-05's rewritten ledger targets a different domain model
(Material / Timeline / EditingJob with source in/out points) and does not reuse this
shape, so nothing is left reading these tables. `editing_output_artifacts` must be
dropped before `editing_output_lineages`: it carries a foreign key to it, and
dropping the referenced table first fails with `DependentObjectsStillExistError`.

Revision ID: 20260728_0035
Revises: 20260723_0034
"""

from __future__ import annotations

from alembic import op

revision: str = "20260728_0035"
down_revision: str | None = "20260723_0034"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.drop_table("editing_output_artifacts")
    op.drop_table("editing_output_lineages")
    op.drop_table("aliyun_editing_intents")


def downgrade() -> None:
    raise NotImplementedError(
        "LE-01 dropped the cloud-editing route; recreating these tables would "
        "resurrect a vendor layer no product path reaches."
    )
