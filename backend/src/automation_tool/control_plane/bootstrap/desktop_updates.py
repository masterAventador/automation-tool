"""Deploy-time loader for the immutable desktop update catalog."""

import json
import os
from pathlib import Path
from typing import cast

from automation_tool.control_plane.application.desktop_updates import DesktopUpdateCatalog

_CATALOG_ENVIRONMENT_VARIABLE = "AUTOMATION_TOOL_DESKTOP_UPDATE_CATALOG_FILE"
_MAX_CATALOG_BYTES = 64 * 1024


def desktop_update_catalog_from_environment() -> DesktopUpdateCatalog:
    """Load an optional bounded catalog file without introducing a database dependency."""

    configured = os.environ.get(_CATALOG_ENVIRONMENT_VARIABLE)
    if configured is None:
        return DesktopUpdateCatalog.empty()
    path = Path(configured)
    try:
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            raise ValueError("invalid path")
        if path.stat().st_size > _MAX_CATALOG_BYTES:
            raise ValueError("oversized catalog")
        decoded = json.loads(path.read_bytes())
        if not isinstance(decoded, list) or not all(isinstance(item, dict) for item in decoded):
            raise ValueError("invalid catalog")
        return DesktopUpdateCatalog.from_documents(cast(list[dict[str, object]], decoded))
    except (OSError, TypeError, ValueError) as error:
        raise ValueError("desktop update catalog rejected") from error
