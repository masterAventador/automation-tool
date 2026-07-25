"""PB-05: the Douyin publish preflight that always stops before submission.

One run opens the frozen creator publish entry inside the persistent
operations profile, proves the exclusive surface lease is still held by this
executor, uploads exactly one revalidated local artifact, fills the frozen
form anchors and then stops. The flow owns no way to press the publish
control: PB-06 adds the confirmed single dispatch on top of the content hash
this receipt binds. Page revisions, blocking overlays, expired logins and
captcha/slider/risk challenges each surface as an explicit, non-guessing
outcome, and any challenge is handed to the user instead of being bypassed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.browser_surface_lease import (
    BrowserSurfaceLeaseManager,
    SurfaceLeaseRejected,
)
from automation_tool.executor.browser_use_safety import redact_untrusted_text
from automation_tool.executor.rpa.douyin.publish_artifact import (
    DouyinPublishArtifact,
    DouyinPublishArtifactRejected,
)
from automation_tool.executor.rpa.douyin.publish_page import (
    DOUYIN_PUBLISH_PAGE_SELECTOR_VERSION,
    DouyinPublishPage,
    DouyinPublishPageEvidence,
    DouyinPublishPageObservation,
    DouyinPublishPageState,
)
from automation_tool.protocol.safe_text import (
    contains_control_or_bidi,
    is_sha256_hex,
    is_unsafe_text,
)

DOUYIN_PUBLISH_PREFLIGHT_FLOW_VERSION: Final = "douyin.publish-preflight.v1"
MAX_DOUYIN_PUBLISH_TITLE_CHARACTERS: Final = 30
MAX_DOUYIN_PUBLISH_DESCRIPTION_CHARACTERS: Final = 1000
MAX_DOUYIN_PUBLISH_ACCOUNT_CHARACTERS: Final = 64

_ENTRY_TIMEOUT_MILLISECONDS: Final = 15_000
_UPLOAD_TIMEOUT_MILLISECONDS: Final = 60_000
_FORM_TIMEOUT_MILLISECONDS: Final = 30_000
_FILL_TIMEOUT_MILLISECONDS: Final = 15_000


class DouyinPublishPreflightRejected(RuntimeError):
    """The preflight cannot run through the frozen v1 contract."""

    def __init__(self) -> None:
        super().__init__("douyin publish preflight is unavailable")


class DouyinPublishPreflightState(StrEnum):
    PRE_SUBMIT_READY = "pre_submit_ready"
    HANDOFF_REQUIRED = "handoff_required"
    BLOCKED = "blocked"


class DouyinPublishPreflightEvidence(StrEnum):
    PRE_SUBMIT_CONFIRMED = "pre_submit_confirmed"
    LOGIN_REQUIRED = "login_required"
    RISK_CHALLENGE = "risk_challenge"
    SURFACE_NOT_OWNED = "surface_not_owned"
    BROWSER_BUSY = "browser_busy"
    BROWSER_UNAVAILABLE = "browser_unavailable"
    CONTENT_REJECTED = "content_rejected"
    SURFACE_LOST = "surface_lost"
    ENTRY_UNAVAILABLE = "entry_unavailable"
    PAGE_DRIFT = "page_drift"
    DIALOG_BLOCKED = "dialog_blocked"
    ARTIFACT_REJECTED = "artifact_rejected"
    UPLOAD_UNAVAILABLE = "upload_unavailable"
    FORM_UNAVAILABLE = "form_unavailable"
    FILL_UNAVAILABLE = "fill_unavailable"
    SUBMIT_CONTROL_DISABLED = "submit_control_disabled"


_HANDOFF_EVIDENCE: Final = frozenset(
    {
        DouyinPublishPreflightEvidence.LOGIN_REQUIRED,
        DouyinPublishPreflightEvidence.RISK_CHALLENGE,
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class DouyinPublishPreflightIntent:
    """One publish intent bound to a validated artifact and bounded user text."""

    artifact: DouyinPublishArtifact
    title: str
    description: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.artifact, DouyinPublishArtifact)
            or _unsafe_field(self.title, MAX_DOUYIN_PUBLISH_TITLE_CHARACTERS)
            or _unsafe_field(self.description, MAX_DOUYIN_PUBLISH_DESCRIPTION_CHARACTERS)
        ):
            raise DouyinPublishPreflightRejected

    @property
    def content_hash(self) -> str:
        """Bind artifact identity and user text into one confirmable digest."""
        encoded = json.dumps(
            {
                "artifactSha256": self.artifact.sha256,
                "description": self.description,
                "domain": DOUYIN_PUBLISH_PREFLIGHT_FLOW_VERSION,
                "mediaType": self.artifact.media_type,
                "sizeBytes": self.artifact.size_bytes,
                "title": self.title,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        return hashlib.sha256(encoded).hexdigest()

    def __repr__(self) -> str:
        return f"DouyinPublishPreflightIntent(content_hash={self.content_hash[:12]!r}…, <redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class DouyinPublishPreflightReceipt:
    """The stop-before-submit outcome of one preflight run."""

    state: DouyinPublishPreflightState
    evidence: DouyinPublishPreflightEvidence
    content_hash: str | None = None
    target_account: str | None = field(default=None)
    page_evidence: DouyinPublishPageEvidence | None = None
    flow_version: str = DOUYIN_PUBLISH_PREFLIGHT_FLOW_VERSION
    selector_version: str = DOUYIN_PUBLISH_PAGE_SELECTOR_VERSION

    def __post_init__(self) -> None:
        ready = (
            self.state is DouyinPublishPreflightState.PRE_SUBMIT_READY
            and self.evidence is DouyinPublishPreflightEvidence.PRE_SUBMIT_CONFIRMED
            and is_sha256_hex(self.content_hash)
        )
        handoff = (
            self.state is DouyinPublishPreflightState.HANDOFF_REQUIRED
            and self.evidence in _HANDOFF_EVIDENCE
            and self.content_hash is None
        )
        blocked = (
            self.state is DouyinPublishPreflightState.BLOCKED
            and self.evidence not in _HANDOFF_EVIDENCE
            and self.evidence is not DouyinPublishPreflightEvidence.PRE_SUBMIT_CONFIRMED
            and self.content_hash is None
        )
        if (
            not isinstance(self.state, DouyinPublishPreflightState)
            or not isinstance(self.evidence, DouyinPublishPreflightEvidence)
            or self.flow_version != DOUYIN_PUBLISH_PREFLIGHT_FLOW_VERSION
            or self.selector_version != DOUYIN_PUBLISH_PAGE_SELECTOR_VERSION
            or (
                self.page_evidence is not None
                and not isinstance(self.page_evidence, DouyinPublishPageEvidence)
            )
            or (
                self.target_account is not None
                and _unsafe_field(self.target_account, MAX_DOUYIN_PUBLISH_ACCOUNT_CHARACTERS)
            )
            or not (ready or handoff or blocked)
        ):
            raise DouyinPublishPreflightRejected

    @property
    def ready(self) -> bool:
        return self.state is DouyinPublishPreflightState.PRE_SUBMIT_READY

    @property
    def handoff_required(self) -> bool:
        return self.state is DouyinPublishPreflightState.HANDOFF_REQUIRED

    @property
    def circuit_open(self) -> bool:
        return not self.ready

    def __repr__(self) -> str:
        page_evidence = None if self.page_evidence is None else self.page_evidence.value
        return (
            "DouyinPublishPreflightReceipt("
            f"state={self.state.value!r}, evidence={self.evidence.value!r}, "
            f"page_evidence={page_evidence!r}, flow_version={self.flow_version!r}, "
            f"selector_version={self.selector_version!r}, <redacted>)"
        )


class DouyinPublishPreflight:
    """Run one lease-guarded publish preflight that never submits."""

    def __init__(self, *, window: BrowserWindow, lease: BrowserSurfaceLeaseManager) -> None:
        if not isinstance(window, BrowserWindow) or not isinstance(
            lease, BrowserSurfaceLeaseManager
        ):
            raise DouyinPublishPreflightRejected
        self._page = DouyinPublishPage(window)
        self._lease = lease
        self._executed = False

    def __repr__(self) -> str:
        return "DouyinPublishPreflight(<redacted>)"

    def run(self, intent: DouyinPublishPreflightIntent) -> DouyinPublishPreflightReceipt:
        if self._executed or not isinstance(intent, DouyinPublishPreflightIntent):
            raise DouyinPublishPreflightRejected
        self._executed = True
        if not self._surface_owned():
            return _receipt(DouyinPublishPreflightEvidence.SURFACE_NOT_OWNED)

        entry = self._page.open_entry(timeout_milliseconds=_ENTRY_TIMEOUT_MILLISECONDS)
        if entry.state is not DouyinPublishPageState.AWAITING_ARTIFACT:
            return _observed_receipt(
                entry,
                DouyinPublishPreflightEvidence.ENTRY_UNAVAILABLE,
                missing_evidence=DouyinPublishPreflightEvidence.PAGE_DRIFT,
            )

        if not self._surface_owned():
            return _receipt(DouyinPublishPreflightEvidence.SURFACE_LOST)
        try:
            intent.artifact.revalidate()
        except DouyinPublishArtifactRejected:
            return _receipt(DouyinPublishPreflightEvidence.ARTIFACT_REJECTED)
        try:
            self._page.artifact_input().set_input_files(
                intent.artifact.path,
                timeout=_UPLOAD_TIMEOUT_MILLISECONDS,
            )
        except Exception:
            return _receipt(DouyinPublishPreflightEvidence.UPLOAD_UNAVAILABLE)
        try:
            # Close the selection-to-upload window: the bytes Playwright just
            # opened by path must still be exactly the confirmed artifact.
            intent.artifact.revalidate()
        except DouyinPublishArtifactRejected:
            return _receipt(DouyinPublishPreflightEvidence.ARTIFACT_REJECTED)

        form = self._page.wait_for_form(timeout_milliseconds=_FORM_TIMEOUT_MILLISECONDS)
        if form.state is not DouyinPublishPageState.FORM_READY:
            return _observed_receipt(form, DouyinPublishPreflightEvidence.FORM_UNAVAILABLE)

        if not self._surface_owned():
            return _receipt(DouyinPublishPreflightEvidence.SURFACE_LOST)
        try:
            self._page.title_input().fill(intent.title, timeout=_FILL_TIMEOUT_MILLISECONDS)
            self._page.description_input().fill(
                intent.description, timeout=_FILL_TIMEOUT_MILLISECONDS
            )
        except Exception:
            return _receipt(DouyinPublishPreflightEvidence.FILL_UNAVAILABLE)

        filled = self._page.observe()
        if filled.state is not DouyinPublishPageState.FORM_READY:
            return _observed_receipt(filled, DouyinPublishPreflightEvidence.FORM_UNAVAILABLE)
        try:
            armed = self._page.submit_enabled()
        except Exception:
            return _observed_receipt(filled, DouyinPublishPreflightEvidence.FORM_UNAVAILABLE)
        if not armed:
            return _observed_receipt(filled, DouyinPublishPreflightEvidence.SUBMIT_CONTROL_DISABLED)
        # The publish control stays untouched here; PB-06 owns the single dispatch.
        return DouyinPublishPreflightReceipt(
            state=DouyinPublishPreflightState.PRE_SUBMIT_READY,
            evidence=DouyinPublishPreflightEvidence.PRE_SUBMIT_CONFIRMED,
            content_hash=intent.content_hash,
            target_account=self._target_account(),
            page_evidence=filled.evidence,
        )

    def _surface_owned(self) -> bool:
        try:
            self._lease.authorize_playwright_action()
        except SurfaceLeaseRejected:
            return False
        return True

    def _target_account(self) -> str | None:
        """Redact the page-provided account name; the page object owns the anchors."""
        raw = self._page.target_account()
        if type(raw) is not str:
            return None
        redacted = redact_untrusted_text(raw).strip()
        if _unsafe_field(redacted, MAX_DOUYIN_PUBLISH_ACCOUNT_CHARACTERS):
            return None
        return redacted


def _unsafe_field(value: object, maximum_characters: int) -> bool:
    return (
        type(value) is not str
        or not value.strip()
        or contains_control_or_bidi(value)
        or is_unsafe_text(value, maximum_characters=maximum_characters)
    )


def _receipt(
    evidence: DouyinPublishPreflightEvidence,
    *,
    page_evidence: DouyinPublishPageEvidence | None = None,
) -> DouyinPublishPreflightReceipt:
    state = (
        DouyinPublishPreflightState.HANDOFF_REQUIRED
        if evidence in _HANDOFF_EVIDENCE
        else DouyinPublishPreflightState.BLOCKED
    )
    return DouyinPublishPreflightReceipt(
        state=state,
        evidence=evidence,
        page_evidence=page_evidence,
    )


def _observed_receipt(
    observation: DouyinPublishPageObservation,
    fallback: DouyinPublishPreflightEvidence,
    *,
    missing_evidence: DouyinPublishPreflightEvidence | None = None,
) -> DouyinPublishPreflightReceipt:
    evidence = {
        DouyinPublishPageState.LOGIN_REQUIRED: DouyinPublishPreflightEvidence.LOGIN_REQUIRED,
        DouyinPublishPageState.RISK_CHALLENGE: DouyinPublishPreflightEvidence.RISK_CHALLENGE,
        DouyinPublishPageState.DIALOG_BLOCKED: DouyinPublishPreflightEvidence.DIALOG_BLOCKED,
    }.get(observation.state)
    if evidence is None:
        if observation.evidence is DouyinPublishPageEvidence.CONFLICTING_ANCHORS:
            evidence = DouyinPublishPreflightEvidence.PAGE_DRIFT
        elif observation.evidence is DouyinPublishPageEvidence.REQUIRED_ANCHOR_MISSING:
            evidence = missing_evidence or fallback
    return _receipt(evidence or fallback, page_evidence=observation.evidence)


__all__ = [
    "DOUYIN_PUBLISH_PREFLIGHT_FLOW_VERSION",
    "MAX_DOUYIN_PUBLISH_ACCOUNT_CHARACTERS",
    "MAX_DOUYIN_PUBLISH_DESCRIPTION_CHARACTERS",
    "MAX_DOUYIN_PUBLISH_TITLE_CHARACTERS",
    "DouyinPublishPreflight",
    "DouyinPublishPreflightEvidence",
    "DouyinPublishPreflightIntent",
    "DouyinPublishPreflightReceipt",
    "DouyinPublishPreflightRejected",
    "DouyinPublishPreflightState",
]
