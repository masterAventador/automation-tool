"""Bind Installations to immutable customer account owners.

Revision ID: 20260723_0030
Revises: 20260722_0029
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260723_0030"
down_revision: str | None = "20260722_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "installations",
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_installations_owner_user",
        "installations",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_installations_owner_user", "installations", ["owner_user_id"], unique=False)
    op.execute(
        """
        CREATE FUNCTION prevent_installation_owner_reassignment()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.owner_user_id IS NOT NULL
               AND NEW.owner_user_id IS DISTINCT FROM OLD.owner_user_id THEN
                RAISE EXCEPTION 'installation owner is immutable' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_installations_owner_immutable
        BEFORE UPDATE OF owner_user_id ON installations
        FOR EACH ROW EXECUTE FUNCTION prevent_installation_owner_reassignment()
        """
    )
    op.create_table(
        "account_installation_binding_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_public_key", sa.LargeBinary(length=32), nullable=False),
        sa.Column("proof_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "substring(id::text from 15 for 1) = '4' "
            "and substring(id::text from 20 for 1) in ('8', '9', 'a', 'b')",
            name="ck_account_binding_challenges_id_uuid_v4",
        ),
        sa.CheckConstraint(
            "octet_length(device_public_key) = 32",
            name="ck_account_binding_challenges_device_key_length",
        ),
        sa.CheckConstraint(
            "octet_length(proof_hash) = 32",
            name="ck_account_binding_challenges_proof_hash_length",
        ),
        sa.CheckConstraint(
            "expires_at > created_at and expires_at <= created_at + interval '5 minutes'",
            name="ck_account_binding_challenges_expiry",
        ),
        sa.CheckConstraint(
            "(consumed_at is null and installation_id is null) or "
            "(consumed_at is not null and installation_id is not null "
            "and consumed_at >= created_at and consumed_at < expires_at)",
            name="ck_account_binding_challenges_consumption_state",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_account_binding_challenges_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["installations.id"],
            name="fk_account_binding_challenges_installation",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_account_binding_challenges"),
    )
    op.create_index(
        "ix_account_binding_challenges_user_expiry",
        "account_installation_binding_challenges",
        ["user_id", "expires_at"],
        unique=False,
    )
    op.create_index(
        "uq_account_binding_challenges_pending_device",
        "account_installation_binding_challenges",
        ["user_id", "device_public_key"],
        unique=True,
        postgresql_where=sa.text("consumed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_account_binding_challenges_pending_device",
        table_name="account_installation_binding_challenges",
    )
    op.drop_index(
        "ix_account_binding_challenges_user_expiry",
        table_name="account_installation_binding_challenges",
    )
    op.drop_table("account_installation_binding_challenges")
    op.execute("DROP TRIGGER trg_installations_owner_immutable ON installations")
    op.execute("DROP FUNCTION prevent_installation_owner_reassignment()")
    op.drop_index("ix_installations_owner_user", table_name="installations")
    op.drop_constraint("fk_installations_owner_user", "installations", type_="foreignkey")
    op.drop_column("installations", "owner_user_id")
