"""Fail-closed server-operator CLI for revoking one Installation."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence

from automation_tool.control_plane.bootstrap.database import database_from_environment
from automation_tool.control_plane.bootstrap.installation_revocations import (
    installation_revocation_service,
)


async def _revoke(installation_id: str, expected_revision: int) -> dict[str, object]:
    database = database_from_environment()
    try:
        revoked = await installation_revocation_service(database).revoke(
            installation_id=installation_id,
            expected_revision=expected_revision,
        )
        return {
            "revision": revoked.revision,
            "status": "revoked",
        }
    finally:
        await database.close()


def main(arguments: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Revoke one automation-tool installation")
    parser.add_argument("--installation-id", required=True)
    parser.add_argument("--expected-revision", required=True, type=int)
    parsed = parser.parse_args(arguments)
    try:
        result = asyncio.run(_revoke(parsed.installation_id, parsed.expected_revision))
    except Exception:
        raise SystemExit("Installation revocation failed") from None
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))


__all__ = ["main"]
