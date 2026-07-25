"""PB-05: versioned, non-submitting Douyin creator publish Page Object.

The publish surface lives on the creator host and is recognized only through
the frozen v1 routes and anchors. Every other page fact - a revised layout, a
blocking overlay, an expired login panel or a captcha/slider/risk challenge -
is reported as an explicit state instead of being guessed around. This object
deliberately exposes no way to press the publish control: PB-05 stops before
submission and PB-06 owns the single, confirmed dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from typing import Protocol, cast
from urllib.parse import SplitResult, urlsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.rpa.douyin.page_anchors import (
    AnchorConflict,
    AnchorLocator,
    any_visible,
    unique_visible,
    visible_matches,
)
from automation_tool.executor.rpa.douyin.session import DOUYIN_RISK_CHALLENGE_SELECTORS
from automation_tool.protocol.safe_text import contains_control_or_bidi

DOUYIN_PUBLISH_PAGE_SELECTOR_VERSION = "douyin.publish-page.v1"
DOUYIN_PUBLISH_HOST = "creator.douyin.com"
DOUYIN_PUBLISH_UPLOAD_ROUTE = "/creator-micro/content/upload"
DOUYIN_PUBLISH_FORM_ROUTE = "/creator-micro/content/post/video"
DOUYIN_PUBLISH_ENTRY_URL = f"https://{DOUYIN_PUBLISH_HOST}{DOUYIN_PUBLISH_UPLOAD_ROUTE}"

DOUYIN_PUBLISH_ARTIFACT_SELECTORS = (
    '[data-e2e="publish-artifact-input"]',
    'input[type="file"][accept*="video"]',
)
DOUYIN_PUBLISH_TITLE_SELECTORS = (
    '[data-e2e="publish-title-input"]',
    'input[placeholder*="作品标题"]',
)
DOUYIN_PUBLISH_DESCRIPTION_SELECTORS = (
    '[data-e2e="publish-description-input"]',
    'textarea[placeholder*="作品简介"]',
)
DOUYIN_PUBLISH_SUBMIT_SELECTORS = (
    '[data-e2e="publish-submit"]',
    'button:text-is("发布")',
)
DOUYIN_PUBLISH_ACCOUNT_SELECTORS = (
    '[data-e2e="publish-account-name"]',
    '[data-e2e="creator-account-name"]',
)
_LOGIN_PANEL_SELECTORS = (
    '[data-e2e="login-panel"]',
    '[role="dialog"]:has-text("扫码登录")',
)
_BLOCKING_DIALOG_SELECTORS = (
    '[data-e2e="publish-guide-mask"]',
    '[data-e2e="modal"]',
    '[role="dialog"]',
)

_MAX_PAGE_URL_CHARACTERS = 2048
_MAX_WAIT_MILLISECONDS = 120_000


class DouyinPublishPageRejected(RuntimeError):
    """The publish page cannot be used through the frozen v1 contract."""

    def __init__(self) -> None:
        super().__init__("douyin publish page is unavailable")


class DouyinPublishRoute(StrEnum):
    UPLOAD_ENTRY = "upload_entry"
    POST_FORM = "post_form"
    UNKNOWN = "unknown"


class DouyinPublishPageState(StrEnum):
    AWAITING_ARTIFACT = "awaiting_artifact"
    FORM_READY = "form_ready"
    LOGIN_REQUIRED = "login_required"
    RISK_CHALLENGE = "risk_challenge"
    DIALOG_BLOCKED = "dialog_blocked"
    UNKNOWN = "unknown"


class DouyinPublishPageEvidence(StrEnum):
    ARTIFACT_INPUT_VISIBLE = "artifact_input_visible"
    FORM_ANCHORS_VISIBLE = "form_anchors_visible"
    LOGIN_PANEL = "login_panel"
    RISK_CHALLENGE = "risk_challenge"
    BLOCKING_DIALOG = "blocking_dialog"
    REQUIRED_ANCHOR_MISSING = "required_anchor_missing"
    CONFLICTING_ANCHORS = "conflicting_anchors"
    ROUTE_UNKNOWN = "route_unknown"
    PAGE_UNAVAILABLE = "page_unavailable"


_KNOWN_ROUTES = (DouyinPublishRoute.UPLOAD_ENTRY, DouyinPublishRoute.POST_FORM)
_HANDOFF_STATES = frozenset(
    {DouyinPublishPageState.LOGIN_REQUIRED, DouyinPublishPageState.RISK_CHALLENGE}
)
_USABLE_STATES = frozenset(
    {DouyinPublishPageState.AWAITING_ARTIFACT, DouyinPublishPageState.FORM_READY}
)
_SHARED_FAILURE_OBSERVATIONS = (
    (DouyinPublishPageState.LOGIN_REQUIRED, DouyinPublishPageEvidence.LOGIN_PANEL),
    (DouyinPublishPageState.RISK_CHALLENGE, DouyinPublishPageEvidence.RISK_CHALLENGE),
    (DouyinPublishPageState.DIALOG_BLOCKED, DouyinPublishPageEvidence.BLOCKING_DIALOG),
    (DouyinPublishPageState.UNKNOWN, DouyinPublishPageEvidence.REQUIRED_ANCHOR_MISSING),
    (DouyinPublishPageState.UNKNOWN, DouyinPublishPageEvidence.CONFLICTING_ANCHORS),
    (DouyinPublishPageState.UNKNOWN, DouyinPublishPageEvidence.PAGE_UNAVAILABLE),
)
_ALLOWED_OBSERVATIONS = frozenset(
    {
        (
            DouyinPublishRoute.UPLOAD_ENTRY,
            DouyinPublishPageState.AWAITING_ARTIFACT,
            DouyinPublishPageEvidence.ARTIFACT_INPUT_VISIBLE,
        ),
        (
            DouyinPublishRoute.POST_FORM,
            DouyinPublishPageState.FORM_READY,
            DouyinPublishPageEvidence.FORM_ANCHORS_VISIBLE,
        ),
        *(
            (route, state, evidence)
            for route in _KNOWN_ROUTES
            for state, evidence in _SHARED_FAILURE_OBSERVATIONS
        ),
        *(
            (DouyinPublishRoute.UNKNOWN, DouyinPublishPageState.UNKNOWN, evidence)
            for evidence in (
                DouyinPublishPageEvidence.ROUTE_UNKNOWN,
                DouyinPublishPageEvidence.PAGE_UNAVAILABLE,
            )
        ),
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class DouyinPublishPageObservation:
    route: DouyinPublishRoute
    state: DouyinPublishPageState
    evidence: DouyinPublishPageEvidence
    selector_version: str = DOUYIN_PUBLISH_PAGE_SELECTOR_VERSION

    def __post_init__(self) -> None:
        if (
            not isinstance(self.route, DouyinPublishRoute)
            or not isinstance(self.state, DouyinPublishPageState)
            or not isinstance(self.evidence, DouyinPublishPageEvidence)
            or self.selector_version != DOUYIN_PUBLISH_PAGE_SELECTOR_VERSION
            or (self.route, self.state, self.evidence) not in _ALLOWED_OBSERVATIONS
        ):
            raise DouyinPublishPageRejected

    @property
    def circuit_open(self) -> bool:
        return self.state not in _USABLE_STATES

    @property
    def handoff_required(self) -> bool:
        return self.state in _HANDOFF_STATES

    def __repr__(self) -> str:
        return (
            "DouyinPublishPageObservation("
            f"route={self.route.value!r}, state={self.state.value!r}, "
            f"evidence={self.evidence.value!r}, "
            f"selector_version={self.selector_version!r}, "
            f"circuit_open={self.circuit_open!r})"
        )


class DouyinPublishRouteModel:
    """Recognize only the frozen creator publish routes owned by the v1 adapter."""

    def check(self, source: object) -> DouyinPublishRoute:
        parsed = _creator_url(source)
        if parsed is None:
            return DouyinPublishRoute.UNKNOWN
        if parsed.path == DOUYIN_PUBLISH_UPLOAD_ROUTE:
            return DouyinPublishRoute.UPLOAD_ENTRY
        if parsed.path == DOUYIN_PUBLISH_FORM_ROUTE:
            return DouyinPublishRoute.POST_FORM
        return DouyinPublishRoute.UNKNOWN

    def __repr__(self) -> str:
        return f"DouyinPublishRouteModel(version={DOUYIN_PUBLISH_PAGE_SELECTOR_VERSION!r})"


class _WaitLocator(Protocol):
    @property
    def first(self) -> _WaitLocator: ...

    def wait_for(self, *, state: str, timeout: float) -> None: ...


class _EnabledLocator(Protocol):
    def is_enabled(self) -> bool: ...


class _AccountLocator(Protocol):
    def inner_text(self) -> str: ...


class _FillLocator(Protocol):
    def fill(self, value: str, *, timeout: float) -> None: ...


class _UploadLocator(Protocol):
    def set_input_files(self, files: object, *, timeout: float) -> None: ...


class _FillCallable(Protocol):
    def __call__(self, value: str, *, timeout: float) -> None: ...


class _UploadCallable(Protocol):
    def __call__(self, files: object, *, timeout: float) -> None: ...


class PublishTextField:
    """Write-only access to one publish text field.

    The Playwright locator is captured in a closure instead of being stored as
    an attribute, so ordinary attribute access, `vars()` and `dataclasses.fields`
    cannot walk from this field back to `click`, `press` or `evaluate`. This is
    a misuse barrier, not a security boundary: Python reflection over
    `__closure__` can still reach the locator.
    """

    __slots__ = ("fill",)

    # Declared for static checking; the value is bound per instance below so the
    # locator lives in a closure instead of a reachable attribute.
    fill: _FillCallable

    def __init__(self, locator: object) -> None:
        typed = cast(_FillLocator, locator)

        def fill(value: str, *, timeout: float) -> None:
            typed.fill(value, timeout=timeout)

        object.__setattr__(self, "fill", fill)

    def __repr__(self) -> str:
        return "PublishTextField(<redacted>)"


class PublishUploadField:
    """Upload-only access to the artifact input; see `PublishTextField`."""

    __slots__ = ("set_input_files",)

    set_input_files: _UploadCallable

    def __init__(self, locator: object) -> None:
        typed = cast(_UploadLocator, locator)

        def set_input_files(files: object, *, timeout: float) -> None:
            typed.set_input_files(files, timeout=timeout)

        object.__setattr__(self, "set_input_files", set_input_files)

    def __repr__(self) -> str:
        return "PublishUploadField(<redacted>)"


class _Page(Protocol):
    @property
    def url(self) -> str: ...

    def locator(self, selector: str) -> AnchorLocator: ...

    def goto(self, url: str, *, wait_until: str, timeout: float) -> None: ...


class DouyinPublishPage:
    """Own the publish anchors without ever pressing the publish control."""

    def __init__(self, window: BrowserWindow) -> None:
        if not isinstance(window, BrowserWindow):
            raise DouyinPublishPageRejected
        self._page = cast(_Page, window.playwright_page)
        self._routes = DouyinPublishRouteModel()

    def __repr__(self) -> str:
        return "DouyinPublishPage(<redacted>)"

    def observe(self) -> DouyinPublishPageObservation:
        try:
            route = self._routes.check(self._page.url)
        except Exception:
            return _observation(
                DouyinPublishRoute.UNKNOWN,
                DouyinPublishPageState.UNKNOWN,
                DouyinPublishPageEvidence.PAGE_UNAVAILABLE,
            )
        if route is DouyinPublishRoute.UNKNOWN:
            return _observation(
                route,
                DouyinPublishPageState.UNKNOWN,
                DouyinPublishPageEvidence.ROUTE_UNKNOWN,
            )
        try:
            if any_visible(self._page, DOUYIN_RISK_CHALLENGE_SELECTORS):
                return _observation(
                    route,
                    DouyinPublishPageState.RISK_CHALLENGE,
                    DouyinPublishPageEvidence.RISK_CHALLENGE,
                )
            if any_visible(self._page, _LOGIN_PANEL_SELECTORS):
                return _observation(
                    route,
                    DouyinPublishPageState.LOGIN_REQUIRED,
                    DouyinPublishPageEvidence.LOGIN_PANEL,
                )
            if any_visible(self._page, _BLOCKING_DIALOG_SELECTORS):
                return _observation(
                    route,
                    DouyinPublishPageState.DIALOG_BLOCKED,
                    DouyinPublishPageEvidence.BLOCKING_DIALOG,
                )
            anchors = _REQUIRED_ANCHORS[route]
            present = all(
                unique_visible(self._page, selectors) is not None for selectors in anchors
            )
        except AnchorConflict:
            return _observation(
                route,
                DouyinPublishPageState.UNKNOWN,
                DouyinPublishPageEvidence.CONFLICTING_ANCHORS,
            )
        except Exception:
            return _observation(
                route,
                DouyinPublishPageState.UNKNOWN,
                DouyinPublishPageEvidence.PAGE_UNAVAILABLE,
            )
        if not present:
            return _observation(
                route,
                DouyinPublishPageState.UNKNOWN,
                DouyinPublishPageEvidence.REQUIRED_ANCHOR_MISSING,
            )
        return _observation(route, *_READY_FACTS[route])

    def open_entry(self, *, timeout_milliseconds: int) -> DouyinPublishPageObservation:
        """Navigate the operations window to the frozen publish entry route."""
        _require_timeout(timeout_milliseconds)
        try:
            self._page.goto(
                DOUYIN_PUBLISH_ENTRY_URL,
                wait_until="domcontentloaded",
                timeout=float(timeout_milliseconds),
            )
        except Exception:
            return _observation(
                DouyinPublishRoute.UNKNOWN,
                DouyinPublishPageState.UNKNOWN,
                DouyinPublishPageEvidence.PAGE_UNAVAILABLE,
            )
        return self._wait_for(
            expected=DouyinPublishPageState.AWAITING_ARTIFACT,
            anchor_groups=DOUYIN_PUBLISH_ARTIFACT_ANCHORS,
            timeout_milliseconds=timeout_milliseconds,
        )

    def wait_for_form(self, *, timeout_milliseconds: int) -> DouyinPublishPageObservation:
        """Wait for the post form that follows a successful artifact upload."""
        return self._wait_for(
            expected=DouyinPublishPageState.FORM_READY,
            anchor_groups=DOUYIN_PUBLISH_FORM_ANCHORS,
            timeout_milliseconds=timeout_milliseconds,
        )

    def artifact_input(self) -> PublishUploadField:
        self._require_state(DouyinPublishPageState.AWAITING_ARTIFACT)
        return PublishUploadField(self._require_locator(DOUYIN_PUBLISH_ARTIFACT_SELECTORS))

    def title_input(self) -> PublishTextField:
        self._require_state(DouyinPublishPageState.FORM_READY)
        return PublishTextField(self._require_locator(DOUYIN_PUBLISH_TITLE_SELECTORS))

    def description_input(self) -> PublishTextField:
        self._require_state(DouyinPublishPageState.FORM_READY)
        return PublishTextField(self._require_locator(DOUYIN_PUBLISH_DESCRIPTION_SELECTORS))

    def target_account(self) -> str | None:
        """Return the page-provided account name as raw, still untrusted text."""
        try:
            locator = unique_visible(self._page, DOUYIN_PUBLISH_ACCOUNT_SELECTORS)
        except Exception:
            return None
        if locator is None:
            return None
        try:
            text = cast(_AccountLocator, locator).inner_text()
        except Exception:
            return None
        return text if type(text) is str else None

    def submit_enabled(self) -> bool:
        """Report whether the publish control is armed; never press it in PB-05."""
        self._require_state(DouyinPublishPageState.FORM_READY)
        locator = cast(_EnabledLocator, self._require_locator(DOUYIN_PUBLISH_SUBMIT_SELECTORS))
        try:
            enabled = locator.is_enabled()
        except Exception:
            raise DouyinPublishPageRejected from None
        if type(enabled) is not bool:
            raise DouyinPublishPageRejected
        return enabled

    def _require_state(self, expected: DouyinPublishPageState) -> None:
        if self.observe().state is not expected:
            raise DouyinPublishPageRejected

    def _require_locator(self, selectors: tuple[str, ...]) -> AnchorLocator:
        try:
            locator = unique_visible(self._page, selectors)
        except Exception:
            raise DouyinPublishPageRejected from None
        if locator is None:
            raise DouyinPublishPageRejected
        return locator

    def _wait_for(
        self,
        *,
        expected: DouyinPublishPageState,
        anchor_groups: tuple[tuple[str, ...], ...],
        timeout_milliseconds: int,
    ) -> DouyinPublishPageObservation:
        _require_timeout(timeout_milliseconds)
        observation = self.observe()
        if observation.state is expected or not _can_wait(observation, expected):
            return observation
        deadline = monotonic() + timeout_milliseconds / 1_000
        for selectors in anchor_groups:
            remaining = (deadline - monotonic()) * 1_000
            if remaining <= 0:
                return self.observe()
            try:
                cast(
                    _WaitLocator,
                    visible_matches(self._page, ", ".join(selectors)),
                ).first.wait_for(state="visible", timeout=remaining)
            except PlaywrightTimeoutError:
                return self.observe()
            except Exception:
                return _observation(
                    self._routes.check(_safe_url(self._page)),
                    DouyinPublishPageState.UNKNOWN,
                    DouyinPublishPageEvidence.PAGE_UNAVAILABLE,
                )
            observation = self.observe()
            if observation.state is expected or not _can_wait(observation, expected):
                return observation
        return observation


DOUYIN_PUBLISH_ARTIFACT_ANCHORS: tuple[tuple[str, ...], ...] = (DOUYIN_PUBLISH_ARTIFACT_SELECTORS,)
DOUYIN_PUBLISH_FORM_ANCHORS: tuple[tuple[str, ...], ...] = (
    DOUYIN_PUBLISH_TITLE_SELECTORS,
    DOUYIN_PUBLISH_DESCRIPTION_SELECTORS,
    DOUYIN_PUBLISH_SUBMIT_SELECTORS,
)
_REQUIRED_ANCHORS = {
    DouyinPublishRoute.UPLOAD_ENTRY: DOUYIN_PUBLISH_ARTIFACT_ANCHORS,
    DouyinPublishRoute.POST_FORM: DOUYIN_PUBLISH_FORM_ANCHORS,
}
_READY_FACTS = {
    DouyinPublishRoute.UPLOAD_ENTRY: (
        DouyinPublishPageState.AWAITING_ARTIFACT,
        DouyinPublishPageEvidence.ARTIFACT_INPUT_VISIBLE,
    ),
    DouyinPublishRoute.POST_FORM: (
        DouyinPublishPageState.FORM_READY,
        DouyinPublishPageEvidence.FORM_ANCHORS_VISIBLE,
    ),
}


def _creator_url(source: object) -> SplitResult | None:
    if (
        type(source) is not str
        or not source
        or len(source) > _MAX_PAGE_URL_CHARACTERS
        or contains_control_or_bidi(source)
    ):
        return None
    try:
        parsed = urlsplit(source)
        if (
            parsed.scheme != "https"
            or parsed.hostname != DOUYIN_PUBLISH_HOST
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            return None
    except ValueError:
        return None
    return parsed


def _require_timeout(timeout_milliseconds: object) -> None:
    if (
        type(timeout_milliseconds) is not int
        or not 1 <= timeout_milliseconds <= _MAX_WAIT_MILLISECONDS
    ):
        raise DouyinPublishPageRejected


def _safe_url(page: _Page) -> object:
    try:
        return page.url
    except Exception:
        return ""


def _can_wait(
    observation: DouyinPublishPageObservation,
    expected: DouyinPublishPageState,
) -> bool:
    return (
        observation.route in _KNOWN_ROUTES
        and observation.state is DouyinPublishPageState.UNKNOWN
        and observation.evidence is DouyinPublishPageEvidence.REQUIRED_ANCHOR_MISSING
        and expected in _USABLE_STATES
    )


def _observation(
    route: DouyinPublishRoute,
    state: DouyinPublishPageState,
    evidence: DouyinPublishPageEvidence,
) -> DouyinPublishPageObservation:
    return DouyinPublishPageObservation(route=route, state=state, evidence=evidence)


__all__ = [
    "DOUYIN_PUBLISH_ACCOUNT_SELECTORS",
    "DOUYIN_PUBLISH_ARTIFACT_SELECTORS",
    "DOUYIN_PUBLISH_DESCRIPTION_SELECTORS",
    "DOUYIN_PUBLISH_ENTRY_URL",
    "DOUYIN_PUBLISH_FORM_ROUTE",
    "DOUYIN_PUBLISH_HOST",
    "DOUYIN_PUBLISH_PAGE_SELECTOR_VERSION",
    "DOUYIN_PUBLISH_SUBMIT_SELECTORS",
    "DOUYIN_PUBLISH_TITLE_SELECTORS",
    "DOUYIN_PUBLISH_UPLOAD_ROUTE",
    "DouyinPublishPage",
    "DouyinPublishPageEvidence",
    "DouyinPublishPageObservation",
    "DouyinPublishPageRejected",
    "DouyinPublishPageState",
    "DouyinPublishRoute",
    "DouyinPublishRouteModel",
    "PublishTextField",
    "PublishUploadField",
]
