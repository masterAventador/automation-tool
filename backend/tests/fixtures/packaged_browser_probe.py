"""Frozen B5-07 probe; never included in the production Executor package."""

from __future__ import annotations

import sys
from pathlib import Path

from automation_tool.executor.browser_runtime import (
    BrowserLaunchRequest,
    BrowserRuntimeRejected,
    PackagedBrowserRuntime,
)

_READY = "browser.runtime.ready"
_ERROR = "Packaged browser runtime is unavailable"


def main(arguments: list[str] | None = None) -> int:
    resolved = sys.argv[1:] if arguments is None else arguments
    try:
        if len(resolved) != 2:
            raise BrowserRuntimeRejected
        request = BrowserLaunchRequest(
            executable_path=Path(resolved[0]),
            profile_directory=Path(resolved[1]),
        )
        with PackagedBrowserRuntime().open(request):
            print(_READY, flush=True)
    except BrowserRuntimeRejected:
        print(_ERROR, file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - frozen process entry
    raise SystemExit(main())
