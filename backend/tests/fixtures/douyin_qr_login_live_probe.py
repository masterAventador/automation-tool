"""Interactive B5-10 state-transition probe using only the production QR flow."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from automation_tool.executor.browser_runtime import BrowserLaunchRequest, BrowserRuntime
from automation_tool.executor.rpa.douyin.login import (
    DouyinQrLoginFlow,
    DouyinQrLoginState,
)

_ACCEPTANCE_TIMEOUT_SECONDS = 15 * 60
_POLL_INTERVAL_SECONDS = 1.5
_TERMINAL_STATES = {
    DouyinQrLoginState.QR_EXPIRED,
    DouyinQrLoginState.HEALTHY,
    DouyinQrLoginState.RISK,
}
_READY = "douyin.qr-login.ready"
_ERROR = "Douyin QR login acceptance is unavailable"


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
            flow = DouyinQrLoginFlow(runtime)
            observation = flow.begin()
            previous_state: DouyinQrLoginState | None = None
            deadline = time.monotonic() + _ACCEPTANCE_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                if observation.state is not previous_state:
                    print(f"douyin.qr-login.{observation.state.value}", flush=True)
                    previous_state = observation.state
                if observation.state in _TERMINAL_STATES:
                    print(_READY, flush=True)
                    return 0
                time.sleep(_POLL_INTERVAL_SECONDS)
                observation = flow.recheck()
    except Exception:
        pass
    print(_ERROR, file=sys.stderr, flush=True)
    return 1


if __name__ == "__main__":  # pragma: no cover - interactive acceptance entry
    raise SystemExit(main())
