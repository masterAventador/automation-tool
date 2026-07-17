"""Establish the initial Control Plane migration baseline."""

from collections.abc import Sequence

revision: str = "20260718_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Reserve the baseline revision before product tables are introduced."""


def downgrade() -> None:
    """Return the empty schema to its pre-baseline state."""
