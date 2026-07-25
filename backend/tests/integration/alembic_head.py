"""The single source of truth for the expected Alembic head revision.

Hard-coding the head in every schema and lifecycle test made each new
migration silently break an unrelated batch of tests. Resolving it from the
committed migration scripts keeps one definition and fails loudly only when
the migration graph itself is broken (zero or multiple heads).
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def resolve_head_revision() -> str:
    """Return the one head revision of the committed migration graph."""
    script = ScriptDirectory.from_config(Config(str(BACKEND_ROOT / "alembic.ini")))
    heads = script.get_heads()
    if len(heads) != 1:
        raise AssertionError(f"the migration graph must have exactly one head: {heads}")
    return heads[0]


HEAD_REVISION = resolve_head_revision()
