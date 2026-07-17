"""Dependency interfaces consumed by the Control Plane application layer."""

from typing import Protocol


class DatabaseLifecycle(Protocol):
    """Minimal database lifecycle required by application and health checks."""

    async def check_connection(self) -> None:
        """Raise when the database cannot serve requests."""

    async def close(self) -> None:
        """Release all database resources owned by this instance."""
