"""Production Bilibili publishing composition."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from automation_tool.control_plane.application.bilibili_archive_publishing import (
    BilibiliArchivePublishRejected,
)
from automation_tool.control_plane.application.bilibili_publishing_runtime import (
    BilibiliPublishingRuntime,
)
from automation_tool.control_plane.domain.bilibili_open_api import (
    load_bilibili_open_api_contract,
)
from automation_tool.control_plane.infrastructure.database import Database

_CONTRACT_ENVIRONMENT: Final = "AUTOMATION_TOOL_BILIBILI_CONTRACT_FILE"
_CONTRACT_RELATIVE: Final = Path("contracts/publishing/bilibili-open-api.v1.json")


class BilibiliPublishingConfigurationError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Bilibili publishing configuration is invalid")


def _contract_path() -> Path:
    configured = os.environ.get(_CONTRACT_ENVIRONMENT)
    candidates = ((Path(configured),) if configured is not None else ()) + (
        Path("/app") / _CONTRACT_RELATIVE,
        Path(__file__).resolve().parents[5] / _CONTRACT_RELATIVE,
    )
    for candidate in candidates:
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    raise BilibiliPublishingConfigurationError


def bilibili_publishing_runtime(database: Database) -> BilibiliPublishingRuntime:
    if not isinstance(database, Database):
        raise BilibiliPublishingConfigurationError
    try:
        contract = load_bilibili_open_api_contract(_contract_path())
        return BilibiliPublishingRuntime(database=database, contract=contract)
    except (BilibiliArchivePublishRejected, OSError, ValueError):
        raise BilibiliPublishingConfigurationError from None


__all__ = [
    "BilibiliPublishingConfigurationError",
    "bilibili_publishing_runtime",
]
