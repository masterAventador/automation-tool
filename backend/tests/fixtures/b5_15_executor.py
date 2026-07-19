"""Acceptance-only Executor entrypoint for deterministic official-origin page facts."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

from automation_tool.executor import cli as executor_cli
from automation_tool.executor.browser_runtime import (
    BrowserLaunchRequest,
    BrowserRuntime,
    BrowserRuntimeRejected,
)
from automation_tool.executor.platform_commands import DouyinLoginCommandOperation

_PROBE_ROUTE = "https://www.douyin.com/user/self*"
_STATE_ENVIRONMENT = "AUTOMATION_TOOL_B515_PAGE_STATE"
_PAGES = {
    "healthy": '<main><div data-e2e="user-avatar">ready</div></main>',
    "expired": (
        '<main><div data-e2e="session-expired">登录状态已失效</div>'
        "<p>扫码登录</p><p>如何扫码</p>"
        '<img aria-label="二维码" style="display:block;width:320px;min-height:48px"></main>'
    ),
    "risk": '<main><div data-e2e="captcha-container">manual</div></main>',
}


def _state_path() -> Path:
    source = os.environ.get(_STATE_ENVIRONMENT)
    if source is None:
        raise BrowserRuntimeRejected
    path = Path(source)
    if not path.is_absolute() or not path.is_file():
        raise BrowserRuntimeRejected
    return path


class AcceptanceBrowserRuntime(BrowserRuntime):
    """Route only the fixed Douyin probe in the separately signed test package."""

    def __init__(self, state_path: Path) -> None:
        super().__init__()
        self._state_path = state_path

    def start(self, request: BrowserLaunchRequest) -> None:
        super().start(request)
        try:
            page = cast(Any, self.primary_window().playwright_page)

            def fulfill(route: Any) -> None:
                state = self._state_path.read_text(encoding="ascii").strip()
                body = _PAGES.get(state)
                if body is None:
                    route.abort()
                    return
                route.fulfill(
                    status=200,
                    content_type="text/html; charset=utf-8",
                    body=body,
                )

            page.context.route(_PROBE_ROUTE, fulfill)
        except Exception:
            self.close()
            raise BrowserRuntimeRejected from None


class AcceptanceDouyinLoginCommandOperation(DouyinLoginCommandOperation):
    def __init__(self, *, health_reporter: Any, outbound: Any) -> None:
        super().__init__(
            health_reporter=health_reporter,
            outbound=outbound,
            runtime_factory=lambda: AcceptanceBrowserRuntime(_state_path()),
        )


def main() -> None:
    vars(executor_cli)["DouyinLoginCommandOperation"] = AcceptanceDouyinLoginCommandOperation
    executor_cli.main()


if __name__ == "__main__":
    main()
