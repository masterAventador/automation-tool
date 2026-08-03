from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.browser_surface_lease import BrowserSurfaceLeaseManager
from automation_tool.executor.rpa.douyin.page_anchors import VISIBLE_MATCH_ENGINE
from automation_tool.executor.rpa.douyin.publish_artifact import open_publish_artifact
from automation_tool.executor.rpa.douyin.publish_page import (
    DOUYIN_PUBLISH_ENTRY_URL,
    DOUYIN_PUBLISH_PAGE_SELECTOR_VERSION,
)
from automation_tool.executor.rpa.douyin.publish_preflight import (
    DOUYIN_PUBLISH_PREFLIGHT_FLOW_VERSION,
    MAX_DOUYIN_PUBLISH_DESCRIPTION_CHARACTERS,
    MAX_DOUYIN_PUBLISH_TITLE_CHARACTERS,
    DouyinPublishPreflight,
    DouyinPublishPreflightEvidence,
    DouyinPublishPreflightIntent,
    DouyinPublishPreflightRejected,
    DouyinPublishPreflightState,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts/publishing/douyin-browser-use-preflight.v1.json"
FORM_URL = "https://creator.douyin.com/creator-micro/content/post/video"
ARTIFACT_INPUT = '[data-e2e="publish-artifact-input"]'
TITLE_INPUT = '[data-e2e="publish-title-input"]'
DESCRIPTION_INPUT = '[data-e2e="publish-description-input"]'
SUBMIT_CONTROL = '[data-e2e="publish-submit"]'
ACCOUNT_NAME = '[data-e2e="publish-account-name"]'
LOGIN_PANEL = '[data-e2e="login-panel"]'
BLOCKING_DIALOG = '[role="dialog"]'
RISK_CHALLENGE = 'iframe[src^="https://rmc.bytedance.com/verifycenter/captcha/"]'
FORM_SELECTORS = {TITLE_INPUT, DESCRIPTION_INPUT, SUBMIT_CONTROL}
TITLE = "自动化运营工具测试标题"
DESCRIPTION = "自动化运营工具测试简介"
PAYLOAD = b"\x00\x00\x00\x18ftypmp42automation-tool-pb-05-preflight"


class FakeLocator:
    """Models grouped matches plus Playwright's visible-only filtering."""

    def __init__(self, selector: str, page: FakePage, *, visible_only: bool = False) -> None:
        self.selector = selector
        self._page = page
        self._visible_only = visible_only

    @property
    def first(self) -> FakeLocator:
        return self

    def locator(self, selector: str) -> FakeLocator:
        assert selector == VISIBLE_MATCH_ENGINE
        return FakeLocator(self.selector, self._page, visible_only=True)

    def _matches(self) -> list[bool]:
        matches: list[bool] = []
        for candidate in self.selector.split(", "):
            matches.extend(False for _ in range(candidate in self._page.hidden_selectors))
            matches.extend(True for _ in range(candidate in self._page.visible_selectors))
        return [match for match in matches if match or not self._visible_only]

    def count(self) -> int:
        if self.selector in self._page.failed_selectors:
            raise RuntimeError("private count failure")
        return len(self._matches())

    def is_visible(self) -> bool:
        return any(self._matches())

    def is_enabled(self) -> bool:
        return self.selector not in self._page.disabled_selectors

    def inner_text(self) -> str:
        for selector in self.selector.split(", "):
            if selector in self._page.texts:
                return self._page.texts[selector]
        return ""

    def fill(self, value: str, *, timeout: float) -> None:
        assert timeout > 0
        if self.selector in self._page.failed_fill_selectors:
            raise RuntimeError("private fill failure")
        for selector in self.selector.split(", "):
            self._page.filled[selector] = value

    def set_input_files(self, files: object, *, timeout: float) -> None:
        assert timeout > 0
        if self.selector in self._page.failed_upload_selectors:
            raise PlaywrightTimeoutError("private upload timeout")
        self._page.uploaded.append(files)
        self._page.on_upload()

    def click(self, **_arguments: object) -> None:  # pragma: no cover - must never run
        self._page.clicks.append(self.selector)

    def wait_for(self, *, state: str, timeout: float) -> None:
        assert state == "visible"
        assert timeout > 0
        callback = self._page.wait_callbacks.pop(self.selector, None)
        if callback is not None:
            callback()
        if not self.is_visible():
            raise PlaywrightTimeoutError("private wait timeout")


class FakePage:
    def __init__(self, *, url: str = "https://creator.douyin.com/creator-micro/home") -> None:
        self.url = url
        self.visible_selectors: set[str] = set()
        self.hidden_selectors: set[str] = set()
        self.failed_selectors: set[str] = set()
        self.disabled_selectors: set[str] = set()
        self.failed_fill_selectors: set[str] = set()
        self.failed_upload_selectors: set[str] = set()
        self.wait_callbacks: dict[str, Callable[[], None]] = {}
        self.texts: dict[str, str] = {}
        self.filled: dict[str, str] = {}
        self.uploaded: list[object] = []
        self.clicks: list[str] = []
        self.navigations: list[str] = []
        self.locator_requests: list[str] = []
        self.persistent_selectors: set[str] = set()
        self.entry_selectors: set[str] = {ARTIFACT_INPUT}
        self.form_selectors: set[str] = set(FORM_SELECTORS)
        self.goto_failure: Exception | None = None

    def locator(self, selector: str) -> FakeLocator:
        self.locator_requests.append(selector)
        return FakeLocator(selector, self)

    def goto(self, url: str, *, wait_until: str, timeout: float) -> None:
        assert wait_until == "domcontentloaded"
        assert timeout > 0
        if self.goto_failure is not None:
            raise self.goto_failure
        self.navigations.append(url)
        self.url = url
        self.visible_selectors = set(self.entry_selectors) | self.persistent_selectors

    def on_upload(self) -> None:
        self.url = FORM_URL
        self.visible_selectors = set(self.form_selectors) | self.persistent_selectors


def window(page: FakePage) -> BrowserWindow:
    return BrowserWindow._for_runtime(object(), cast(Any, page))


def artifact(tmp_path: Path) -> Any:
    path = tmp_path / "clip.mp4"
    path.write_bytes(PAYLOAD)
    path.chmod(0o600)
    return open_publish_artifact(path)


def intent(tmp_path: Path) -> DouyinPublishPreflightIntent:
    return DouyinPublishPreflightIntent(
        artifact=artifact(tmp_path),
        title=TITLE,
        description=DESCRIPTION,
    )


def owned_lease() -> BrowserSurfaceLeaseManager:
    return BrowserSurfaceLeaseManager()


def run(page: FakePage, tmp_path: Path, lease: BrowserSurfaceLeaseManager | None = None) -> Any:
    preflight = DouyinPublishPreflight(window=window(page), lease=lease or owned_lease())
    return preflight.run(intent(tmp_path))


def test_contract_pins_the_content_bounds() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))["content"]
    assert contract["titleMaximumCharacters"] == MAX_DOUYIN_PUBLISH_TITLE_CHARACTERS
    assert contract["descriptionMaximumCharacters"] == MAX_DOUYIN_PUBLISH_DESCRIPTION_CHARACTERS


def test_preflight_fills_the_form_and_stops_before_submission(tmp_path: Path) -> None:
    page = FakePage()
    page.texts[ACCOUNT_NAME] = "运营测试账号"
    page.persistent_selectors.add(ACCOUNT_NAME)
    receipt = run(page, tmp_path)

    assert receipt.state is DouyinPublishPreflightState.PRE_SUBMIT_READY
    assert receipt.evidence is DouyinPublishPreflightEvidence.PRE_SUBMIT_CONFIRMED
    assert receipt.flow_version == DOUYIN_PUBLISH_PREFLIGHT_FLOW_VERSION
    assert receipt.selector_version == DOUYIN_PUBLISH_PAGE_SELECTOR_VERSION
    assert page.navigations == [DOUYIN_PUBLISH_ENTRY_URL]
    assert page.filled[TITLE_INPUT] == TITLE
    assert page.filled[DESCRIPTION_INPUT] == DESCRIPTION
    assert len(page.uploaded) == 1
    # PB-05 hard boundary: no publish control is ever pressed.
    assert page.clicks == []
    assert len(receipt.content_hash or "") == 64


def test_content_hash_binds_the_artifact_and_the_text(tmp_path: Path) -> None:
    first = run(FakePage(), tmp_path).content_hash
    same = run(FakePage(), tmp_path).content_hash
    assert first == same

    other = tmp_path / "other"
    other.mkdir()
    path = other / "clip.mp4"
    path.write_bytes(PAYLOAD + b"different")
    path.chmod(0o600)
    changed = DouyinPublishPreflight(window=window(FakePage()), lease=owned_lease()).run(
        DouyinPublishPreflightIntent(
            artifact=open_publish_artifact(path),
            title=TITLE,
            description=DESCRIPTION,
        )
    )
    assert changed.content_hash != first

    retitled = DouyinPublishPreflight(window=window(FakePage()), lease=owned_lease()).run(
        DouyinPublishPreflightIntent(
            artifact=artifact(tmp_path),
            title=f"{TITLE}2",
            description=DESCRIPTION,
        )
    )
    assert retitled.content_hash != first


def test_borrowed_surface_denies_the_preflight_without_touching_the_page(tmp_path: Path) -> None:
    lease = owned_lease()
    lease.begin_takeover(cdp_url="http://127.0.0.1:45123", timeout_seconds=60, pause_confirmed=True)
    page = FakePage()
    receipt = run(page, tmp_path, lease)
    assert receipt.state is DouyinPublishPreflightState.BLOCKED
    assert receipt.evidence is DouyinPublishPreflightEvidence.SURFACE_NOT_OWNED
    assert page.navigations == []
    assert page.locator_requests == []
    assert page.uploaded == []


def test_reclaim_required_surface_denies_the_preflight(tmp_path: Path) -> None:
    lease = owned_lease()
    grant = lease.begin_takeover(
        cdp_url="http://127.0.0.1:45123", timeout_seconds=60, pause_confirmed=True
    )
    lease.report_borrower_failure(grant.token)
    receipt = run(FakePage(), tmp_path, lease)
    assert receipt.evidence is DouyinPublishPreflightEvidence.SURFACE_NOT_OWNED


def test_surface_lost_mid_flow_stops_before_the_upload(tmp_path: Path) -> None:
    lease = owned_lease()
    page = FakePage()

    class LosingLease(BrowserSurfaceLeaseManager):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def authorize_playwright_action(self) -> None:
            self.calls += 1
            if self.calls > 1:
                self.begin_takeover(
                    cdp_url="http://127.0.0.1:45123",
                    timeout_seconds=60,
                    pause_confirmed=True,
                )
            super().authorize_playwright_action()

    lease = LosingLease()
    receipt = run(page, tmp_path, lease)
    assert receipt.state is DouyinPublishPreflightState.BLOCKED
    assert receipt.evidence is DouyinPublishPreflightEvidence.SURFACE_LOST
    assert page.uploaded == []
    assert page.clicks == []


def test_expired_login_hands_off_before_any_upload(tmp_path: Path) -> None:
    page = FakePage()
    page.entry_selectors = {ARTIFACT_INPUT, LOGIN_PANEL}
    receipt = run(page, tmp_path)
    assert receipt.state is DouyinPublishPreflightState.HANDOFF_REQUIRED
    assert receipt.evidence is DouyinPublishPreflightEvidence.LOGIN_REQUIRED
    assert page.uploaded == []


def test_risk_challenge_hands_off_and_is_never_bypassed(tmp_path: Path) -> None:
    page = FakePage()
    page.entry_selectors = {ARTIFACT_INPUT, RISK_CHALLENGE}
    receipt = run(page, tmp_path)
    assert receipt.state is DouyinPublishPreflightState.HANDOFF_REQUIRED
    assert receipt.evidence is DouyinPublishPreflightEvidence.RISK_CHALLENGE
    assert page.uploaded == []
    assert page.clicks == []


def test_risk_challenge_after_upload_hands_off_before_submission(tmp_path: Path) -> None:
    page = FakePage()
    page.form_selectors = FORM_SELECTORS | {RISK_CHALLENGE}
    receipt = run(page, tmp_path)
    assert receipt.state is DouyinPublishPreflightState.HANDOFF_REQUIRED
    assert receipt.evidence is DouyinPublishPreflightEvidence.RISK_CHALLENGE
    assert page.filled == {}
    assert page.clicks == []


def test_blocking_overlay_stops_the_preflight(tmp_path: Path) -> None:
    page = FakePage()
    page.entry_selectors = {ARTIFACT_INPUT, BLOCKING_DIALOG}
    receipt = run(page, tmp_path)
    assert receipt.state is DouyinPublishPreflightState.BLOCKED
    assert receipt.evidence is DouyinPublishPreflightEvidence.DIALOG_BLOCKED


def test_page_revision_that_removes_the_upload_anchor_is_page_drift(tmp_path: Path) -> None:
    page = FakePage()
    page.entry_selectors = set()
    receipt = run(page, tmp_path)
    assert receipt.state is DouyinPublishPreflightState.BLOCKED
    assert receipt.evidence is DouyinPublishPreflightEvidence.PAGE_DRIFT
    assert page.uploaded == []


def test_page_revision_that_moves_the_entry_route_is_reported(tmp_path: Path) -> None:
    page = FakePage()
    page.goto_failure = RuntimeError("private navigation failure")
    receipt = run(page, tmp_path)
    assert receipt.state is DouyinPublishPreflightState.BLOCKED
    assert receipt.evidence is DouyinPublishPreflightEvidence.ENTRY_UNAVAILABLE


def test_form_that_never_appears_after_upload_is_reported(tmp_path: Path) -> None:
    page = FakePage()
    page.form_selectors = set()
    receipt = run(page, tmp_path)
    assert receipt.state is DouyinPublishPreflightState.BLOCKED
    assert receipt.evidence is DouyinPublishPreflightEvidence.FORM_UNAVAILABLE
    assert page.filled == {}


def test_upload_failure_is_reported_without_filling(tmp_path: Path) -> None:
    page = FakePage()
    page.failed_upload_selectors.add(", ".join(_artifact_selectors()))
    receipt = run(page, tmp_path)
    assert receipt.state is DouyinPublishPreflightState.BLOCKED
    assert receipt.evidence is DouyinPublishPreflightEvidence.UPLOAD_UNAVAILABLE
    assert page.filled == {}


def _artifact_selectors() -> tuple[str, ...]:
    from automation_tool.executor.rpa.douyin.publish_page import DOUYIN_PUBLISH_ARTIFACT_SELECTORS

    return DOUYIN_PUBLISH_ARTIFACT_SELECTORS


def test_fill_failure_is_reported(tmp_path: Path) -> None:
    from automation_tool.executor.rpa.douyin.publish_page import DOUYIN_PUBLISH_TITLE_SELECTORS

    page = FakePage()
    page.failed_fill_selectors.add(", ".join(DOUYIN_PUBLISH_TITLE_SELECTORS))
    receipt = run(page, tmp_path)
    assert receipt.state is DouyinPublishPreflightState.BLOCKED
    assert receipt.evidence is DouyinPublishPreflightEvidence.FILL_UNAVAILABLE
    assert page.clicks == []


def test_disabled_publish_control_is_never_forced(tmp_path: Path) -> None:
    from automation_tool.executor.rpa.douyin.publish_page import DOUYIN_PUBLISH_SUBMIT_SELECTORS

    page = FakePage()
    page.disabled_selectors.add(", ".join(DOUYIN_PUBLISH_SUBMIT_SELECTORS))
    receipt = run(page, tmp_path)
    assert receipt.state is DouyinPublishPreflightState.BLOCKED
    assert receipt.evidence is DouyinPublishPreflightEvidence.SUBMIT_CONTROL_DISABLED
    assert page.clicks == []


def test_replaced_artifact_path_is_refused_before_the_upload(tmp_path: Path) -> None:
    page = FakePage()
    selected = artifact(tmp_path)
    (tmp_path / "clip.mp4").unlink()
    replaced = tmp_path / "clip.mp4"
    replaced.write_bytes(PAYLOAD + b"substituted")
    replaced.chmod(0o600)
    receipt = DouyinPublishPreflight(window=window(page), lease=owned_lease()).run(
        DouyinPublishPreflightIntent(artifact=selected, title=TITLE, description=DESCRIPTION)
    )
    assert receipt.state is DouyinPublishPreflightState.BLOCKED
    assert receipt.evidence is DouyinPublishPreflightEvidence.ARTIFACT_REJECTED
    assert page.uploaded == []


def test_untrusted_account_name_is_redacted_and_bounded(tmp_path: Path) -> None:
    page = FakePage()
    page.persistent_selectors.add(ACCOUNT_NAME)
    page.texts[ACCOUNT_NAME] = "运营账号 session=abc123 /Users/private/path"
    receipt = run(page, tmp_path)
    assert receipt.state is DouyinPublishPreflightState.PRE_SUBMIT_READY
    account = receipt.target_account or ""
    assert "abc123" not in account
    assert "/Users/private" not in account
    assert "[redacted" in account


def test_hostile_account_text_is_dropped_instead_of_propagated(tmp_path: Path) -> None:
    page = FakePage()
    page.persistent_selectors.add(ACCOUNT_NAME)
    page.texts[ACCOUNT_NAME] = "a" * 4096
    receipt = run(page, tmp_path)
    assert receipt.state is DouyinPublishPreflightState.PRE_SUBMIT_READY
    assert receipt.target_account is None


def test_intent_rejects_unsafe_or_oversized_content(tmp_path: Path) -> None:
    valid = artifact(tmp_path)
    for title, description in (
        ("", DESCRIPTION),
        ("   ", DESCRIPTION),
        ("a" * (MAX_DOUYIN_PUBLISH_TITLE_CHARACTERS + 1), DESCRIPTION),
        ("标题‮", DESCRIPTION),
        ("标题\n", DESCRIPTION),
        (TITLE, ""),
        (TITLE, "a" * (MAX_DOUYIN_PUBLISH_DESCRIPTION_CHARACTERS + 1)),
        (TITLE, "cookie: sessionid=1"),
        (TITLE, "/Users/aventador/secret.mp4"),
        (cast(Any, None), DESCRIPTION),
        (TITLE, cast(Any, 7)),
    ):
        with pytest.raises(DouyinPublishPreflightRejected):
            DouyinPublishPreflightIntent(artifact=valid, title=title, description=description)
    with pytest.raises(DouyinPublishPreflightRejected):
        DouyinPublishPreflightIntent(
            artifact=cast(Any, "clip.mp4"), title=TITLE, description=DESCRIPTION
        )


def test_intent_repr_exposes_no_content(tmp_path: Path) -> None:
    text = repr(intent(tmp_path))
    assert TITLE not in text
    assert DESCRIPTION not in text


def test_receipt_repr_exposes_no_content(tmp_path: Path) -> None:
    receipt = run(FakePage(), tmp_path)
    text = repr(receipt)
    assert TITLE not in text
    assert "pre_submit_ready" in text


def test_preflight_requires_a_runtime_window_and_a_real_lease(tmp_path: Path) -> None:
    with pytest.raises(DouyinPublishPreflightRejected):
        DouyinPublishPreflight(window=cast(Any, object()), lease=owned_lease())
    with pytest.raises(DouyinPublishPreflightRejected):
        DouyinPublishPreflight(window=window(FakePage()), lease=cast(Any, object()))
    preflight = DouyinPublishPreflight(window=window(FakePage()), lease=owned_lease())
    with pytest.raises(DouyinPublishPreflightRejected):
        preflight.run(cast(Any, object()))


def test_preflight_runs_at_most_once(tmp_path: Path) -> None:
    preflight = DouyinPublishPreflight(window=window(FakePage()), lease=owned_lease())
    preflight.run(intent(tmp_path))
    with pytest.raises(DouyinPublishPreflightRejected):
        preflight.run(intent(tmp_path))


def test_content_hash_is_absent_unless_pre_submit_ready(tmp_path: Path) -> None:
    page = FakePage()
    page.entry_selectors = {ARTIFACT_INPUT, LOGIN_PANEL}
    receipt = run(page, tmp_path)
    assert receipt.content_hash is None
    assert receipt.circuit_open
    assert receipt.handoff_required


def test_content_hash_uses_the_frozen_canonical_binding(tmp_path: Path) -> None:
    selected = artifact(tmp_path)
    receipt = DouyinPublishPreflight(window=window(FakePage()), lease=owned_lease()).run(
        DouyinPublishPreflightIntent(artifact=selected, title=TITLE, description=DESCRIPTION)
    )
    expected = hashlib.sha256(
        json.dumps(
            {
                "artifactSha256": selected.sha256,
                "description": DESCRIPTION,
                "domain": DOUYIN_PUBLISH_PREFLIGHT_FLOW_VERSION,
                "mediaType": selected.media_type,
                "sizeBytes": selected.size_bytes,
                "title": TITLE,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    ).hexdigest()
    assert receipt.content_hash == expected


def test_artifact_replaced_during_the_upload_is_refused_before_pre_submit(
    tmp_path: Path,
) -> None:
    """The confirmed digest must still match the bytes after the upload runs."""
    page = FakePage()
    selected = artifact(tmp_path)
    target = tmp_path / "clip.mp4"

    original_on_upload = page.on_upload

    def swap_then_continue() -> None:
        target.unlink()
        target.write_bytes(PAYLOAD + b"swapped-during-upload")
        target.chmod(0o600)
        original_on_upload()

    page.on_upload = swap_then_continue  # type: ignore[method-assign]
    receipt = DouyinPublishPreflight(window=window(page), lease=owned_lease()).run(
        DouyinPublishPreflightIntent(artifact=selected, title=TITLE, description=DESCRIPTION)
    )
    assert receipt.state is DouyinPublishPreflightState.BLOCKED
    assert receipt.evidence is DouyinPublishPreflightEvidence.ARTIFACT_REJECTED
    assert page.filled == {}
    assert page.clicks == []
