"""Drop the Aliyun cloud-editing tables.

LE-01 removed the cloud-editing route. The two migrations that created these
tables (20260723_0032, 20260723_0034) stay on disk: 0032 sits mid-chain with
0033 pointing at it, so deleting the file would break the chain, and databases
that already ran them would keep the tables regardless.

`editing_output_artifacts` (also created by 20260723_0034) is not one of the
dropped tables: it holds generic finished-output artifact rows that LE-05's
rewritten local-editing ledger is expected to reuse. It carries a foreign key
to `editing_output_lineages`, so that constraint must be dropped first --
`op.drop_table("editing_output_lineages")` alone fails against a real database
with `DependentObjectsStillExistError` because the child row's constraint
still points at the table being dropped.

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
    op.drop_constraint(
        "fk_editing_output_artifacts_lineage",
        "editing_output_artifacts",
        type_="foreignkey",
    )
    op.drop_table("editing_output_lineages")
    op.drop_table("aliyun_editing_intents")


def downgrade() -> None:
    raise NotImplementedError(
        "LE-01 dropped the cloud-editing route; recreating these tables would "
        "resurrect a vendor layer no product path reaches."
    )
