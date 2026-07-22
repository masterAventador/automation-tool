"""Acceptance-only Executor for one controlled original-caller MVP journey."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

from automation_tool.executor import cli as executor_cli
from automation_tool.executor.action_operation import (
    ProductionDouyinActionOperation as RealProductionDouyinActionOperation,
)
from automation_tool.executor.browser_runtime import (
    BrowserLaunchRequest,
    BrowserRuntime,
    BrowserRuntimeRejected,
)
from automation_tool.executor.discovery_operation import (
    DouyinDiscoveryExecutionResult,
    DouyinDiscoveryOperationState,
)
from automation_tool.executor.ledger import ExecutorLedger
from automation_tool.executor.platform_commands import DouyinLoginCommandOperation
from automation_tool.executor.rpa.douyin.browse import DouyinBrowseExecution
from automation_tool.protocol import (
    DouyinCandidate,
    DouyinCandidateSource,
    DouyinCandidateSummary,
    PlatformSessionState,
)

_OBSERVATION_ENVIRONMENT = "AUTOMATION_TOOL_H816F_OBSERVATIONS"
_LOGIN_ROUTE = "https://www.douyin.com/user/self*"
_PROFILE_ROUTE = "https://www.douyin.com/user/**"
_LOGIN_PAGE = '<main><div data-e2e="user-avatar">ready</div></main>'
_PROFILE_PAGE = """<!doctype html>
<html lang="zh-CN">
  <head><meta charset="utf-8"></head>
  <body>
    <main aria-label="用户主页"><h1>隔离验收目标</h1></main>
    <script>window.__browseSideEffects = 0;</script>
  </body>
</html>"""


def _observation_path() -> Path:
    source = os.environ.get(_OBSERVATION_ENVIRONMENT)
    if source is None:
        raise BrowserRuntimeRejected
    path = Path(source)
    if not path.is_absolute() or path.parent.is_symlink() or not path.parent.is_dir():
        raise BrowserRuntimeRejected
    return path


def _record(event: str, **facts: object) -> None:
    document = (
        json.dumps(
            {"event": event, **facts},
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        + b"\n"
    )
    descriptor = os.open(
        _observation_path(),
        os.O_APPEND | os.O_CREAT | os.O_WRONLY,
        0o600,
    )
    try:
        os.write(descriptor, document)
    finally:
        os.close(descriptor)


class AcceptanceLoginBrowserRuntime(BrowserRuntime):
    def start(self, request: BrowserLaunchRequest) -> None:
        if request.headless is not True:
            raise BrowserRuntimeRejected
        super().start(request)
        try:
            page = cast(Any, self.primary_window().playwright_page)
            page.context.route(
                _LOGIN_ROUTE,
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html; charset=utf-8",
                    body=_LOGIN_PAGE,
                ),
            )
            _record("login_browser_started", headless=True)
        except Exception:
            self.close()
            raise BrowserRuntimeRejected from None


class AcceptanceActionBrowserRuntime(BrowserRuntime):
    def start(self, request: BrowserLaunchRequest) -> None:
        if request.headless is not True:
            raise BrowserRuntimeRejected
        super().start(request)
        try:
            page = cast(Any, self.primary_window().playwright_page)
            page.context.route(
                _PROFILE_ROUTE,
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html; charset=utf-8",
                    body=_PROFILE_PAGE,
                ),
            )
            _record("action_browser_started", headless=True)
        except Exception:
            self.close()
            raise BrowserRuntimeRejected from None

    def close(self) -> None:
        try:
            page = cast(Any, self.primary_window().playwright_page)
            side_effects = page.evaluate("window.__browseSideEffects")
            _record("browse_page_closed", sideEffects=side_effects)
        except Exception:
            pass
        super().close()


class AcceptanceDouyinLoginCommandOperation(DouyinLoginCommandOperation):
    def __init__(
        self,
        *,
        health_reporter: Any,
        outbound: Any,
        browser_authority: Any,
        **_: Any,
    ) -> None:
        super().__init__(
            health_reporter=health_reporter,
            outbound=outbound,
            runtime_factory=AcceptanceLoginBrowserRuntime,
            browser_authority=browser_authority,
        )


class AcceptanceDouyinDiscoveryOperation:
    def __init__(self, *, ledger: ExecutorLedger, **_: Any) -> None:
        self._ledger = ledger

    def run(self, payload: Any, *, cancellation_requested: Any) -> DouyinDiscoveryExecutionResult:
        if cancellation_requested():
            raise RuntimeError("acceptance discovery was cancelled")
        session = self._ledger.get_platform_session("douyin")
        if session is None or session.state is not PlatformSessionState.HEALTHY:
            _record("discovery_login_required")
            return DouyinDiscoveryExecutionResult(
                state=DouyinDiscoveryOperationState.LOGIN_REQUIRED,
                evidence="login_required",
                page_revision=payload.page_revision,
                candidates=(),
            )
        candidates = tuple(
            DouyinCandidate(
                platform_target_id=f"h816f-target-{index}",
                summary=DouyinCandidateSummary(
                    display_name=f"验收目标 {index}",
                    public_handle=f"h816f_{index}",
                ),
                source=DouyinCandidateSource.GENERAL_SEARCH_AUTHOR,
                page_revision=payload.page_revision,
            )
            for index in (1, 2)
        )
        _record("discovery_completed", candidateCount=len(candidates))
        return DouyinDiscoveryExecutionResult(
            state=DouyinDiscoveryOperationState.COMPLETED,
            evidence="candidates_extracted",
            page_revision=payload.page_revision,
            candidates=candidates,
        )


class AcceptanceProductionDouyinActionOperation:
    def __init__(self, **arguments: Any) -> None:
        arguments["runtime_factory"] = AcceptanceActionBrowserRuntime
        arguments["browse_factory"] = DouyinBrowseExecution
        self._delegate = RealProductionDouyinActionOperation(**arguments)

    def run(self, command: Any) -> Any:
        return self._delegate.run(command)


def main() -> None:
    vars(executor_cli)["DouyinLoginCommandOperation"] = AcceptanceDouyinLoginCommandOperation
    vars(executor_cli)["ProductionDouyinDiscoveryOperation"] = AcceptanceDouyinDiscoveryOperation
    vars(executor_cli)["ProductionDouyinActionOperation"] = (
        AcceptanceProductionDouyinActionOperation
    )
    executor_cli.main()


if __name__ == "__main__":
    main()
