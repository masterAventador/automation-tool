"""Contract-level parsing and signature checks for Aliyun IMS callbacks.

The local Control Plane exposes no public inbound endpoint, so VE-06 keeps
callbacks as a pure contract layer verified against fixtures: polling via
`GetMediaProducingJob` is the primary reconciliation path. This module covers
the two officially documented pieces so a future deployment with a reachable
callback URL can verify and consume events without new research:

- HTTP callback authentication: `X-ICE-SIGNATURE = md5(callbackURL |
  X-ICE-TIMESTAMP | secret)` with support for the documented old/new secret
  rotation and a bounded timestamp freshness window;
- the `ProduceMediaComplete` event body (`MessageBody.JobId` plus
  `MessageBody.Status` of `Success`/`Fail`), parsed into an adapter event that
  feeds the same idempotent reconciliation path as polling.

Secrets only flow through the verification functions; they are never stored,
logged or echoed in error messages, which are fixed strings.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Final, Never, final

ICE_CALLBACK_TIMESTAMP_HEADER: Final = "X-ICE-TIMESTAMP"
ICE_CALLBACK_SIGNATURE_HEADER: Final = "X-ICE-SIGNATURE"
PRODUCE_MEDIA_COMPLETE_EVENT_TYPE: Final = "ProduceMediaComplete"
CALLBACK_TIMESTAMP_TOLERANCE_SECONDS: Final = 300

_VENDOR_JOB_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9-]{8,128}$")
_STATUS_TOKEN_PATTERN: Final = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,63}$")
_TIMESTAMP_PATTERN: Final = re.compile(r"^[0-9]{1,20}$")
_MAX_CALLBACK_DOCUMENT_BYTES: Final = 64 * 1024


class InvalidAliyunImsEditingCallback(ValueError):
    """An Aliyun IMS editing callback payload or parameter is invalid."""

    def __init__(self) -> None:
        super().__init__("Aliyun IMS editing callback payload is invalid")


def _reject() -> Never:
    raise InvalidAliyunImsEditingCallback


@final
@dataclass(frozen=True, slots=True)
class AliyunProduceMediaCompleteEvent:
    """One parsed ProduceMediaComplete event; adapter-private vocabulary."""

    vendor_job_id: str
    status_token: str
    event_time: str

    def __post_init__(self) -> None:
        if (
            type(self.vendor_job_id) is not str
            or _VENDOR_JOB_ID_PATTERN.fullmatch(self.vendor_job_id) is None
            or type(self.status_token) is not str
            or _STATUS_TOKEN_PATTERN.fullmatch(self.status_token) is None
            or type(self.event_time) is not str
            or len(self.event_time) > 64
        ):
            _reject()


def compute_ice_callback_signature(*, callback_url: str, timestamp: str, secret: str) -> str:
    """Compute the documented callback signature for one request.

    Official formula: `md5sum(callbackURL | X-ICE-TIMESTAMP | secret)` joined
    with the pipe character. MD5 is mandated by the vendor contract; it is not
    a local security choice.
    """
    if (
        type(callback_url) is not str
        or not callback_url
        or type(timestamp) is not str
        or not timestamp
        or type(secret) is not str
        or not secret
    ):
        _reject()
    content = f"{callback_url}|{timestamp}|{secret}"
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def verify_ice_callback(
    *,
    callback_url: str,
    timestamp: str,
    signature: str,
    secrets: tuple[str, ...],
    now_unix_seconds: int,
    tolerance_seconds: int = CALLBACK_TIMESTAMP_TOLERANCE_SECONDS,
) -> bool:
    """Verify one callback signature with rotation and freshness checks.

    Any secret in `secrets` may authenticate the request (the documented
    old/new rotation window). Comparison is constant-time; a stale or future
    timestamp beyond the tolerance rejects the request regardless of the
    signature.
    """
    if (
        type(callback_url) is not str
        or not callback_url
        or type(timestamp) is not str
        or type(signature) is not str
        or not isinstance(secrets, tuple)
        or any(type(secret) is not str or not secret for secret in secrets)
        or type(now_unix_seconds) is not int
        or type(tolerance_seconds) is not int
        or tolerance_seconds < 0
    ):
        _reject()
    if _TIMESTAMP_PATTERN.fullmatch(timestamp) is None:
        return False
    if abs(now_unix_seconds - int(timestamp)) > tolerance_seconds:
        return False
    return any(
        hmac.compare_digest(
            compute_ice_callback_signature(
                callback_url=callback_url, timestamp=timestamp, secret=secret
            ),
            signature,
        )
        for secret in secrets
    )


def parse_produce_media_complete_event(payload: str) -> AliyunProduceMediaCompleteEvent:
    """Parse one official ProduceMediaComplete callback document.

    Only the documented event type is accepted; other event types and any
    malformed document raise the fixed callback error. The status token is
    validated as a bounded token but deliberately not interpreted here: the
    reconciler maps unknown tokens to `outcome_uncertain` without guessing.
    """
    if type(payload) is not str or len(payload.encode("utf-8")) > _MAX_CALLBACK_DOCUMENT_BYTES:
        _reject()
    try:
        document = json.loads(payload)
    except json.JSONDecodeError:
        _reject()
    if not isinstance(document, dict):
        _reject()
    if document.get("EventType") != PRODUCE_MEDIA_COMPLETE_EVENT_TYPE:
        _reject()
    message_body = document.get("MessageBody")
    if not isinstance(message_body, dict):
        _reject()
    vendor_job_id = message_body.get("JobId")
    status_token = message_body.get("Status")
    event_time = document.get("EventTime", "")
    if type(vendor_job_id) is not str or type(status_token) is not str:
        _reject()
    if type(event_time) is not str:
        _reject()
    return AliyunProduceMediaCompleteEvent(
        vendor_job_id=vendor_job_id,
        status_token=status_token,
        event_time=event_time,
    )


__all__ = [
    "CALLBACK_TIMESTAMP_TOLERANCE_SECONDS",
    "ICE_CALLBACK_SIGNATURE_HEADER",
    "ICE_CALLBACK_TIMESTAMP_HEADER",
    "PRODUCE_MEDIA_COMPLETE_EVENT_TYPE",
    "AliyunProduceMediaCompleteEvent",
    "InvalidAliyunImsEditingCallback",
    "compute_ice_callback_signature",
    "parse_produce_media_complete_event",
    "verify_ice_callback",
]
