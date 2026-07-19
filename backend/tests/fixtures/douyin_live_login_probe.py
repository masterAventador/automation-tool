"""Interactive B5-09 acceptance probe using only production browser/session adapters."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, cast

from automation_tool.executor.browser_runtime import (
    BrowserLaunchRequest,
    BrowserRuntime,
)
from automation_tool.executor.rpa.douyin.session import (
    DOUYIN_SESSION_PROBE_URL,
    DouyinSessionDetector,
    DouyinSessionState,
)

_ACCEPTANCE_TIMEOUT_SECONDS = 15 * 60
_POLL_INTERVAL_SECONDS = 1.5
_READY = "douyin.session.ready"
_ERROR = "Douyin session acceptance is unavailable"


def main(arguments: list[str] | None = None) -> int:
    resolved = sys.argv[1:] if arguments is None else arguments
    if len(resolved) != 2:
        print(_ERROR, file=sys.stderr, flush=True)
        return 1
    runtime = BrowserRuntime()
    try:
        request = BrowserLaunchRequest(
            executable_path=Path(resolved[0]),
            profile_directory=Path(resolved[1]),
        )
        with runtime.running(request):
            window = runtime.primary_window()
            page = cast(Any, window.playwright_page)
            page.goto(
                DOUYIN_SESSION_PROBE_URL,
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            detector = DouyinSessionDetector()
            deadline = time.monotonic() + _ACCEPTANCE_TIMEOUT_SECONDS
            previous_state: DouyinSessionState | None = None
            while time.monotonic() < deadline:
                observation = detector.check(window)
                if observation.state is not previous_state:
                    print(f"douyin.session.{observation.state.value}", flush=True)
                    previous_state = observation.state
                if observation.state is DouyinSessionState.HEALTHY:
                    print(_READY, flush=True)
                    return 0
                time.sleep(_POLL_INTERVAL_SECONDS)
    except Exception:
        pass
    print(_ERROR, file=sys.stderr, flush=True)
    return 1


if __name__ == "__main__":  # pragma: no cover - interactive acceptance entry
    raise SystemExit(main())
