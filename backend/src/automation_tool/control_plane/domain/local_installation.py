"""The single fixed local installation identity for the demo deployment."""

from __future__ import annotations

from uuid import UUID

from automation_tool.control_plane.domain.resource_ids import InstallationId

LOCAL_INSTALLATION_UUID = UUID("aa11aa11-aa11-4a11-8a11-aa11aa11aa11")


def local_installation_id() -> InstallationId:
    return InstallationId(LOCAL_INSTALLATION_UUID)


__all__ = ["LOCAL_INSTALLATION_UUID", "local_installation_id"]
