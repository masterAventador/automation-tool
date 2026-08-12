"""Single fixed local installation for the demo deployment.

The device-identity mechanism was removed: the App no longer registers a
device or holds credentials. Every business API resolves the current
installation to this one fixed row, created on startup when missing.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import insert, select

from automation_tool.control_plane.domain import InstallationId
from automation_tool.control_plane.domain.local_installation import (
    LOCAL_INSTALLATION_UUID,
    local_installation_id,
)
from automation_tool.control_plane.infrastructure.database import Database, installations


async def ensure_local_installation(database: Database) -> InstallationId:
    marker_key = hashlib.sha256(b"automation-tool.local-installation.v1").digest()
    async with database.session() as session:
        existing = await session.execute(
            select(installations.c.id).where(installations.c.id == LOCAL_INSTALLATION_UUID)
        )
        if existing.first() is None:
            await session.execute(
                insert(installations).values(
                    id=LOCAL_INSTALLATION_UUID,
                    device_public_key=marker_key,
                )
            )
    return local_installation_id()


__all__ = ["ensure_local_installation", "local_installation_id"]
