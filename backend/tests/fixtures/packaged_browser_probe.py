"""Frozen B5-07 probe; never included in the production Executor package."""

from __future__ import annotations

import sys
from pathlib import Path

from automation_tool.executor.browser_runtime import (
    BrowserLaunchRequest,
    BrowserRuntime,
    BrowserRuntimeRejected,
)

_READY = "browser.runtime.ready"
_ERROR = "Packaged browser runtime is unavailable"
_HOLD_ARGUMENT = "--hold-for-process-tree-test"


def main(arguments: list[str] | None = None) -> int:
    resolved = sys.argv[1:] if arguments is None else arguments
    try:
        if len(resolved) not in {2, 3} or (len(resolved) == 3 and resolved[2] != _HOLD_ARGUMENT):
            raise BrowserRuntimeRejected
        request = BrowserLaunchRequest(
            executable_path=Path(resolved[0]),
            profile_directory=Path(resolved[1]),
        )
        runtime = BrowserRuntime()
        with runtime.running(request):
            runtime.primary_window()
            extra_window = runtime.open_window()
            if len(runtime.windows()) != 2:
                raise BrowserRuntimeRejected
            runtime.close_window(extra_window)
            if len(runtime.windows()) != 1:
                raise BrowserRuntimeRejected
            print(_READY, flush=True)
            if len(resolved) == 3:
                sys.stdin.buffer.read(1)
    except BrowserRuntimeRejected:
        print(_ERROR, file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - frozen process entry
    raise SystemExit(main())
