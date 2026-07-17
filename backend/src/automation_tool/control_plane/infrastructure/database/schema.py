"""Versioned SQLAlchemy schema metadata for Control Plane persistence."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    LargeBinary,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from automation_tool.control_plane.domain import InstallationStatus

metadata = MetaData()

installations = Table(
    "installations",
    metadata,
    Column("id", UUID(as_uuid=True), nullable=False),
    Column("device_public_key", LargeBinary(length=32), nullable=False),
    Column(
        "status",
        String(length=16),
        nullable=False,
        server_default=text(f"'{InstallationStatus.ACTIVE.value}'"),
    ),
    Column("revision", BigInteger(), nullable=False, server_default=text("1")),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    CheckConstraint(
        "octet_length(device_public_key) = 32",
        name="ck_installations_device_public_key_length",
    ),
    CheckConstraint(
        "substring(id::text from 15 for 1) = '4' "
        "and substring(id::text from 20 for 1) in ('8', '9', 'a', 'b')",
        name="ck_installations_id_uuid_v4",
    ),
    CheckConstraint("revision > 0", name="ck_installations_revision_positive"),
    CheckConstraint(
        "(status = 'active' and revoked_at is null) "
        "or (status = 'revoked' and revoked_at is not null)",
        name="ck_installations_revocation_state",
    ),
    CheckConstraint(
        "status in ('active', 'revoked')",
        name="ck_installations_status",
    ),
    CheckConstraint(
        "updated_at >= created_at and (revoked_at is null or revoked_at >= created_at)",
        name="ck_installations_timestamp_order",
    ),
    PrimaryKeyConstraint("id", name="pk_installations"),
    UniqueConstraint(
        "device_public_key",
        name="uq_installations_device_public_key",
    ),
)

__all__ = ["installations", "metadata"]
