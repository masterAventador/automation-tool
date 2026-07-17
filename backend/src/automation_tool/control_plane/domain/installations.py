"""Installation lifecycle values owned by the Control Plane domain."""

from enum import StrEnum


class InstallationStatus(StrEnum):
    """Persisted lifecycle states for one App installation."""

    ACTIVE = "active"
    REVOKED = "revoked"


__all__ = ["InstallationStatus"]
