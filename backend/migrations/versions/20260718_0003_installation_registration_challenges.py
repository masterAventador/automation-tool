"""Create persistent one-time installation registration challenges.

Revision ID: 20260718_0003
Revises: 20260718_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260718_0003"
down_revision: str | None = "20260718_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the challenge table with atomic-consumption constraints."""
    op.create_table(
        "installation_registration_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("environment_id", sa.String(length=64), nullable=False),
        sa.Column("bootstrap_fingerprint", sa.LargeBinary(length=32), nullable=False),
        sa.Column("device_public_key", sa.LargeBinary(length=32), nullable=False),
        sa.Column("proof_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "octet_length(bootstrap_fingerprint) = 32",
            name="ck_registration_challenges_bootstrap_fingerprint_length",
        ),
        sa.CheckConstraint(
            "octet_length(device_public_key) = 32",
            name="ck_registration_challenges_device_public_key_length",
        ),
        sa.CheckConstraint(
            "environment_id ~ '^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$'",
            name="ck_registration_challenges_environment_id",
        ),
        sa.CheckConstraint(
            "substring(id::text from 15 for 1) = '4' "
            "and substring(id::text from 20 for 1) in ('8', '9', 'a', 'b')",
            name="ck_registration_challenges_id_uuid_v4",
        ),
        sa.CheckConstraint(
            "octet_length(proof_hash) = 32",
            name="ck_registration_challenges_proof_hash_length",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_registration_challenges_expiry",
        ),
        sa.CheckConstraint(
            "(consumed_at is null and installation_id is null) or "
            "(consumed_at is not null and installation_id is not null "
            "and consumed_at >= created_at and consumed_at < expires_at)",
            name="ck_registration_challenges_consumption_state",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["installations.id"],
            name="fk_registration_challenges_installation_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_registration_challenges"),
    )


def downgrade() -> None:
    """Remove registration challenges while preserving installations."""
    op.drop_table("installation_registration_challenges")
