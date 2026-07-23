"""Aliyun IMS timeline compilation and single-dispatch editing job submission.

VE-05 turns one provider-neutral `EditingTimeline` into the officially
documented `SubmitMediaProducingJob` timeline document (VideoTracks /
AudioTracks / SubtitleTracks with transition effects), rejects capability gaps
loudly before submission, and dispatches exactly once per editing job: the
prepared intent (request hash) is persisted before dispatch, the acknowledged
JobId is persisted after dispatch, and a lost response becomes
`outcome_uncertain` without any automatic replay. Every Aliyun DTO in this
module is the adapter's private vocabulary and never enters the
provider-neutral editing domain (`video_editing*`). Error messages are fixed
and never carry buckets, object keys, credentials or user content.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum, unique
from types import MappingProxyType
from typing import Final, Never, Protocol, final

from automation_tool.control_plane.domain.aliyun_ims_editing_staging import (
    AliyunImsEditingStagingContract,
    AliyunImsRegion,
    EditingServicePreflight,
    InvalidAliyunImsEditingStagingModel,
    MediaStagingPlan,
)
from automation_tool.control_plane.domain.resource_ids import ArtifactId
from automation_tool.control_plane.domain.video_creation import (
    MAX_ARTIFACT_REFERENCES,
    MAX_TRACKS,
    MAX_VIDEO_DURATION_MS,
    TimelineClip,
    TimelineTrackKind,
    TransitionKind,
)
from automation_tool.control_plane.domain.video_editing import (
    EditingFailureCode,
    EditingJobId,
    EditingJobStateMachine,
    EditingJobStatus,
    EditingTimeline,
)
from automation_tool.control_plane.domain.video_editing_provider import (
    EditingIdempotencyKey,
    EditingProviderCapabilities,
    EditingProviderErrorCode,
    EditingProviderFailure,
    EditingProviderId,
    EditingProviderJobSnapshot,
    EditingSubmission,
)

ALIYUN_IMS_EDITING_PROVIDER_ID: Final = EditingProviderId("aliyun_ims")
OUTPUT_OBJECT_KEY_PREFIX: Final = "editing-output/v1/"
SUBMIT_ACTION: Final = "SubmitMediaProducingJob"
OUTPUT_MEDIA_TARGET_OSS_OBJECT: Final = "oss-object"
ACS3_ALGORITHM: Final = "ACS3-HMAC-SHA256"

_BUCKET_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
_VENDOR_JOB_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9-]{8,128}$")
_REQUEST_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_SHA256_HEX_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")

# Official SubmitMediaProducingJob output bounds: width/height 128..4096 px,
# short side at most 2160 px.
_MIN_OUTPUT_PIXELS: Final = 128
_MAX_OUTPUT_PIXELS: Final = 4096
_MAX_OUTPUT_SHORT_SIDE: Final = 2160

_VIDEO_EXTENSIONS: Final = frozenset({".mp4", ".mov"})
_IMAGE_EXTENSIONS: Final = frozenset({".jpeg", ".jpg", ".png"})
_AUDIO_EXTENSIONS: Final = frozenset({".aac", ".m4a", ".mp3", ".wav"})

# Official normal-transition SubTypes; the internal vocabulary maps onto the
# documented values only. DISSOLVE has no documented plain equivalent and is
# rejected as an unsupported capability instead of silently degrading.
_TRANSITION_SUBTYPES: Final[Mapping[TransitionKind, str]] = MappingProxyType(
    {
        TransitionKind.FADE: "fade",
        TransitionKind.WIPE: "wiperight",
    }
)

_UNRESERVED: Final = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~")


class InvalidAliyunImsEditingProviderModel(ValueError):
    """An Aliyun IMS editing provider value is invalid."""

    def __init__(self) -> None:
        super().__init__("Aliyun IMS editing provider value is invalid")


def _reject() -> Never:
    raise InvalidAliyunImsEditingProviderModel


def _fail(code: EditingProviderErrorCode) -> Never:
    raise EditingProviderFailure(code)


@final
class AliyunOssBucketName(str):
    """One validated OSS bucket name; never rendered into error messages."""

    __slots__ = ()

    def __new__(cls, value: str) -> AliyunOssBucketName:
        if type(value) is not str or _BUCKET_PATTERN.fullmatch(value) is None:
            _reject()
        return str.__new__(cls, value)


@final
@dataclass(frozen=True, slots=True)
class AliyunEditingOutputConfig:
    """Output resolution locked to the official media-producing bounds."""

    width: int
    height: int

    def __post_init__(self) -> None:
        if (
            type(self.width) is not int
            or type(self.height) is not int
            or not _MIN_OUTPUT_PIXELS <= self.width <= _MAX_OUTPUT_PIXELS
            or not _MIN_OUTPUT_PIXELS <= self.height <= _MAX_OUTPUT_PIXELS
            or min(self.width, self.height) > _MAX_OUTPUT_SHORT_SIDE
        ):
            _reject()


@final
@dataclass(frozen=True, slots=True)
class AliyunSubmitMediaProducingRequest:
    """One fully assembled, deterministic SubmitMediaProducingJob request."""

    endpoint: str
    api_version: str
    action: str
    region: AliyunImsRegion
    timeline_json: str
    output_media_target: str
    output_media_config_json: str
    client_token: EditingIdempotencyKey

    def __post_init__(self) -> None:
        if (
            type(self.endpoint) is not str
            or not isinstance(self.region, AliyunImsRegion)
            or self.endpoint != f"ice.{self.region.value}.aliyuncs.com"
            or self.api_version != "2020-11-09"
            or self.action != SUBMIT_ACTION
            or type(self.timeline_json) is not str
            or not self.timeline_json
            or self.output_media_target != OUTPUT_MEDIA_TARGET_OSS_OBJECT
            or type(self.output_media_config_json) is not str
            or not self.output_media_config_json
            or type(self.client_token) is not EditingIdempotencyKey
        ):
            _reject()

    def query_parameters(self) -> Mapping[str, str]:
        """Return the documented RPC query parameters for this request."""
        return MappingProxyType(
            {
                "ClientToken": str(self.client_token),
                "OutputMediaConfig": self.output_media_config_json,
                "OutputMediaTarget": self.output_media_target,
                "Timeline": self.timeline_json,
            }
        )


@final
@dataclass(frozen=True, slots=True)
class AliyunSubmitAcknowledgement:
    """The acknowledged identifiers of one accepted submission."""

    vendor_job_id: str
    request_id: str

    def __post_init__(self) -> None:
        if (
            type(self.vendor_job_id) is not str
            or _VENDOR_JOB_ID_PATTERN.fullmatch(self.vendor_job_id) is None
            or type(self.request_id) is not str
            or _REQUEST_ID_PATTERN.fullmatch(self.request_id) is None
        ):
            _reject()


@unique
class AliyunImsTransportFailureKind(StrEnum):
    """Closed transport outcomes; 5xx and timeouts are `response_lost`.

    Transports may only use a `rejected_*` kind when the gateway definitively
    did not create the job; any ambiguous outcome must be `response_lost`.
    """

    NOT_DISPATCHED = "not_dispatched"
    RESPONSE_LOST = "response_lost"
    REJECTED_INVALID = "rejected_invalid"
    REJECTED_PERMISSION = "rejected_permission"
    REJECTED_THROTTLED = "rejected_throttled"


_TRANSPORT_MESSAGES: Final[Mapping[AliyunImsTransportFailureKind, str]] = MappingProxyType(
    {
        AliyunImsTransportFailureKind.NOT_DISPATCHED: ("Aliyun IMS request was not dispatched"),
        AliyunImsTransportFailureKind.RESPONSE_LOST: (
            "Aliyun IMS response was lost after dispatch"
        ),
        AliyunImsTransportFailureKind.REJECTED_INVALID: (
            "Aliyun IMS rejected the request as invalid"
        ),
        AliyunImsTransportFailureKind.REJECTED_PERMISSION: (
            "Aliyun IMS rejected the request for missing permission"
        ),
        AliyunImsTransportFailureKind.REJECTED_THROTTLED: ("Aliyun IMS throttled the request"),
    }
)


@final
class AliyunImsTransportFailure(Exception):
    """A closed transport failure carrying only a fixed, pre-sanitized message."""

    def __init__(self, kind: AliyunImsTransportFailureKind) -> None:
        if not isinstance(kind, AliyunImsTransportFailureKind):
            _reject()
        super().__init__(_TRANSPORT_MESSAGES[kind])
        self.kind: Final[AliyunImsTransportFailureKind] = kind


@unique
class AliyunEditingIntentState(StrEnum):
    """Durable single-dispatch states for one editing submission intent."""

    PREPARED = "prepared"
    DISPATCHED = "dispatched"
    UNCERTAIN = "uncertain"


@final
@dataclass(frozen=True, slots=True)
class AliyunEditingIntent:
    """One persisted submission intent: request hash first, JobId after dispatch."""

    editing_job_id: EditingJobId
    request_hash: str
    state: AliyunEditingIntentState
    vendor_job_id: str | None
    status: EditingJobStatus
    failure_code: EditingFailureCode | None
    output_artifact_ids: tuple[ArtifactId, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.editing_job_id, EditingJobId)
            or type(self.request_hash) is not str
            or _SHA256_HEX_PATTERN.fullmatch(self.request_hash) is None
            or not isinstance(self.state, AliyunEditingIntentState)
            or not isinstance(self.status, EditingJobStatus)
            or (
                self.failure_code is not None
                and not isinstance(self.failure_code, EditingFailureCode)
            )
            or not isinstance(self.output_artifact_ids, tuple)
            or len(self.output_artifact_ids) > MAX_ARTIFACT_REFERENCES
            or any(
                not isinstance(artifact_id, ArtifactId) for artifact_id in self.output_artifact_ids
            )
            or len(set(self.output_artifact_ids)) != len(self.output_artifact_ids)
        ):
            _reject()

        if self.state is AliyunEditingIntentState.PREPARED:
            valid = (
                self.vendor_job_id is None
                and self.status is EditingJobStatus.QUEUED
                and self.failure_code is None
                and not self.output_artifact_ids
            )
        elif self.state is AliyunEditingIntentState.UNCERTAIN:
            valid = (
                self.vendor_job_id is None
                and self.status is EditingJobStatus.OUTCOME_UNCERTAIN
                and self.failure_code is None
                and not self.output_artifact_ids
            )
        else:
            has_valid_job_id = (
                type(self.vendor_job_id) is str
                and _VENDOR_JOB_ID_PATTERN.fullmatch(self.vendor_job_id) is not None
            )
            if self.status is EditingJobStatus.SUCCEEDED:
                facts = bool(self.output_artifact_ids) and self.failure_code is None
            elif self.status is EditingJobStatus.FAILED:
                facts = not self.output_artifact_ids and self.failure_code is not None
            else:
                facts = not self.output_artifact_ids and self.failure_code is None
            valid = has_valid_job_id and facts
        if not valid:
            _reject()


class AliyunEditingIntentStore(Protocol):
    """Durable storage port for submission intents keyed by editing job."""

    async def load(self, editing_job_id: EditingJobId) -> AliyunEditingIntent | None:
        """Return the persisted intent for one editing job, if any."""
        ...

    async def save(self, intent: AliyunEditingIntent) -> None:
        """Persist or replace the intent for its editing job."""
        ...


@final
class InMemoryAliyunEditingIntentStore:
    """Process-local reference implementation of the intent store port."""

    __slots__ = ("_intents",)

    def __init__(self) -> None:
        self._intents: dict[EditingJobId, AliyunEditingIntent] = {}

    async def load(self, editing_job_id: EditingJobId) -> AliyunEditingIntent | None:
        if not isinstance(editing_job_id, EditingJobId):
            _reject()
        return self._intents.get(editing_job_id)

    async def save(self, intent: AliyunEditingIntent) -> None:
        if not isinstance(intent, AliyunEditingIntent):
            _reject()
        self._intents[intent.editing_job_id] = intent


class AliyunImsSubmitTransport(Protocol):
    """Network port that performs exactly one SubmitMediaProducingJob call."""

    async def submit_media_producing_job(
        self, request: AliyunSubmitMediaProducingRequest
    ) -> AliyunSubmitAcknowledgement:
        """Dispatch once; raise `AliyunImsTransportFailure` on any failure."""
        ...


class AliyunMediaStagingPlanner(Protocol):
    """Port resolving one timeline's artifacts into a same-region staging plan."""

    async def plan(self, timeline: EditingTimeline) -> MediaStagingPlan:
        """Return the staging plan covering every referenced artifact."""
        ...


class AliyunEditingPreflightSource(Protocol):
    """Port exposing the current VE-04 editing-service preflight result."""

    async def current(self) -> EditingServicePreflight:
        """Return the latest preflight; never invent a passed check."""
        ...


def aliyun_editing_capabilities() -> EditingProviderCapabilities:
    """Declare the provider-neutral capabilities of the Aliyun IMS adapter."""
    return EditingProviderCapabilities(
        provider_id=ALIYUN_IMS_EDITING_PROVIDER_ID,
        supported_track_kinds=frozenset(
            {TimelineTrackKind.VISUAL, TimelineTrackKind.AUDIO, TimelineTrackKind.CAPTION}
        ),
        supported_transition_kinds=frozenset(
            {TransitionKind.CUT, TransitionKind.FADE, TransitionKind.WIPE}
        ),
        max_timeline_duration_ms=MAX_VIDEO_DURATION_MS,
        max_tracks=MAX_TRACKS,
    )


def _seconds(milliseconds: int) -> float:
    return milliseconds / 1000


def _canonical_json(document: object) -> str:
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _assert_transitions_supported(timeline: EditingTimeline) -> None:
    for track in timeline.tracks:
        for index, clip in enumerate(track.clips):
            transition = clip.transition_in
            if transition is None or transition.kind is TransitionKind.CUT:
                continue
            if (
                transition.kind not in _TRANSITION_SUBTYPES
                or index == 0
                or track.kind is not TimelineTrackKind.VISUAL
            ):
                _fail(EditingProviderErrorCode.UNSUPPORTED_CAPABILITY)


def _validate_timeline_capabilities(timeline: EditingTimeline) -> None:
    if not isinstance(timeline, EditingTimeline):
        _reject()
    if not aliyun_editing_capabilities().supports(timeline):
        _fail(EditingProviderErrorCode.UNSUPPORTED_CAPABILITY)
    _assert_transitions_supported(timeline)


def _staged_media_url(
    staging_plan: MediaStagingPlan, bucket: AliyunOssBucketName, artifact_id: ArtifactId
) -> tuple[str, str]:
    try:
        object_key = staging_plan.object_key_for(str(artifact_id))
    except InvalidAliyunImsEditingStagingModel:
        _fail(EditingProviderErrorCode.INVALID_INPUT)
    extension = object_key[object_key.rfind(".") :]
    url = f"https://{bucket}.oss-{staging_plan.region.value}.aliyuncs.com/{object_key}"
    return url, extension


def _transition_effect(clip: TimelineClip) -> dict[str, object] | None:
    transition = clip.transition_in
    if transition is None or transition.kind is TransitionKind.CUT:
        return None
    return {
        "Type": "Transition",
        "SubType": _TRANSITION_SUBTYPES[transition.kind],
        "Duration": _seconds(transition.duration_ms),
    }


def compile_aliyun_timeline(
    *,
    timeline: EditingTimeline,
    staging_plan: MediaStagingPlan,
    bucket: AliyunOssBucketName,
) -> str:
    """Compile one internal timeline into the official Timeline document.

    The compilation is a deterministic pure function: identical inputs always
    produce the identical canonical JSON string. Capability gaps raise
    `unsupported_capability`; broken material references raise `invalid_input`.
    """
    if not isinstance(staging_plan, MediaStagingPlan) or type(bucket) is not AliyunOssBucketName:
        _reject()
    _validate_timeline_capabilities(timeline)

    video_tracks: list[dict[str, object]] = []
    audio_tracks: list[dict[str, object]] = []
    subtitle_tracks: list[dict[str, object]] = []
    for track in timeline.tracks:
        if track.kind is TimelineTrackKind.VISUAL:
            clips: list[dict[str, object]] = []
            for clip in track.clips:
                if clip.source_artifact_id is None:
                    _fail(EditingProviderErrorCode.INVALID_INPUT)
                url, extension = _staged_media_url(staging_plan, bucket, clip.source_artifact_id)
                compiled: dict[str, object] = {
                    "MediaURL": url,
                    "TimelineIn": _seconds(clip.start_ms),
                    "TimelineOut": _seconds(clip.end_ms),
                }
                if extension in _IMAGE_EXTENSIONS:
                    compiled["Type"] = "Image"
                    compiled["Duration"] = _seconds(clip.duration_ms)
                elif extension not in _VIDEO_EXTENSIONS:
                    _fail(EditingProviderErrorCode.INVALID_INPUT)
                effect = _transition_effect(clip)
                if effect is not None:
                    previous_effects = clips[-1].setdefault("Effects", [])
                    assert isinstance(previous_effects, list)
                    previous_effects.append(effect)
                clips.append(compiled)
            video_tracks.append({"VideoTrackClips": clips})
        elif track.kind is TimelineTrackKind.AUDIO:
            audio_clips: list[dict[str, object]] = []
            for clip in track.clips:
                if clip.source_artifact_id is None:
                    _fail(EditingProviderErrorCode.INVALID_INPUT)
                url, extension = _staged_media_url(staging_plan, bucket, clip.source_artifact_id)
                if extension not in _AUDIO_EXTENSIONS:
                    _fail(EditingProviderErrorCode.INVALID_INPUT)
                audio_clips.append(
                    {
                        "MediaURL": url,
                        "TimelineIn": _seconds(clip.start_ms),
                        "TimelineOut": _seconds(clip.end_ms),
                    }
                )
            audio_tracks.append({"AudioTrackClips": audio_clips})
        else:
            subtitle_clips: list[dict[str, object]] = []
            for clip in track.clips:
                if clip.text is None:
                    _fail(EditingProviderErrorCode.INVALID_INPUT)
                subtitle_clips.append(
                    {
                        "Type": "Text",
                        "Content": clip.text,
                        "TimelineIn": _seconds(clip.start_ms),
                        "TimelineOut": _seconds(clip.end_ms),
                    }
                )
            subtitle_tracks.append({"SubtitleTrackClips": subtitle_clips})

    document: dict[str, object] = {}
    if video_tracks:
        document["VideoTracks"] = video_tracks
    if audio_tracks:
        document["AudioTracks"] = audio_tracks
    if subtitle_tracks:
        document["SubtitleTracks"] = subtitle_tracks
    return _canonical_json(document)


def build_output_media_config(
    *,
    bucket: AliyunOssBucketName,
    region: AliyunImsRegion,
    editing_job_id: EditingJobId,
    output: AliyunEditingOutputConfig,
) -> str:
    """Build the OutputMediaConfig JSON targeting the versioned output prefix."""
    if (
        type(bucket) is not AliyunOssBucketName
        or not isinstance(region, AliyunImsRegion)
        or not isinstance(editing_job_id, EditingJobId)
        or not isinstance(output, AliyunEditingOutputConfig)
    ):
        _reject()
    media_url = (
        f"https://{bucket}.oss-{region.value}.aliyuncs.com/"
        f"{OUTPUT_OBJECT_KEY_PREFIX}{editing_job_id}.mp4"
    )
    return _canonical_json({"MediaURL": media_url, "Width": output.width, "Height": output.height})


def build_submit_request(
    *,
    contract: AliyunImsEditingStagingContract,
    region: AliyunImsRegion,
    bucket: AliyunOssBucketName,
    output: AliyunEditingOutputConfig,
    submission: EditingSubmission,
    staging_plan: MediaStagingPlan,
) -> AliyunSubmitMediaProducingRequest:
    """Assemble one deterministic SubmitMediaProducingJob request."""
    if (
        not isinstance(contract, AliyunImsEditingStagingContract)
        or not isinstance(region, AliyunImsRegion)
        or not isinstance(submission, EditingSubmission)
    ):
        _reject()
    if staging_plan.region is not region:
        _fail(EditingProviderErrorCode.INVALID_INPUT)
    return AliyunSubmitMediaProducingRequest(
        endpoint=contract.endpoints[region],
        api_version=contract.api_version,
        action=SUBMIT_ACTION,
        region=region,
        timeline_json=compile_aliyun_timeline(
            timeline=submission.timeline, staging_plan=staging_plan, bucket=bucket
        ),
        output_media_target=OUTPUT_MEDIA_TARGET_OSS_OBJECT,
        output_media_config_json=build_output_media_config(
            bucket=bucket,
            region=region,
            editing_job_id=submission.editing_job_id,
            output=output,
        ),
        client_token=submission.idempotency_key,
    )


def editing_submit_request_hash(
    *, submission: EditingSubmission, request: AliyunSubmitMediaProducingRequest
) -> str:
    """Derive the persisted request hash binding submission identity and payload."""
    if not isinstance(submission, EditingSubmission) or not isinstance(
        request, AliyunSubmitMediaProducingRequest
    ):
        _reject()
    envelope = _canonical_json(
        {
            "action": request.action,
            "apiVersion": request.api_version,
            "clientToken": str(request.client_token),
            "editingJobId": str(submission.editing_job_id),
            "endpoint": request.endpoint,
            "outputMediaConfig": request.output_media_config_json,
            "outputMediaTarget": request.output_media_target,
            "projectId": str(submission.project_id),
            "timeline": request.timeline_json,
            "timelineId": str(submission.timeline.timeline_id),
            "timelineRevision": submission.timeline.revision,
        }
    )
    return hashlib.sha256(envelope.encode("utf-8")).hexdigest()


def _percent_encode(value: str) -> str:
    encoded: list[str] = []
    for byte in value.encode("utf-8"):
        character = chr(byte)
        if character in _UNRESERVED:
            encoded.append(character)
        else:
            encoded.append(f"%{byte:02X}")
    return "".join(encoded)


def canonical_query_string(query: Mapping[str, str]) -> str:
    """Build the RFC 3986 canonical query string required by the V3 signature."""
    if not isinstance(query, Mapping):
        _reject()
    pairs: list[tuple[str, str]] = []
    for key, value in query.items():
        if type(key) is not str or type(value) is not str:
            _reject()
        pairs.append((_percent_encode(key), _percent_encode(value)))
    pairs.sort()
    return "&".join(f"{key}={value}" for key, value in pairs)


def acs3_signed_headers(
    *,
    access_key_id: str,
    access_key_secret: str,
    method: str,
    host: str,
    path: str,
    query: Mapping[str, str],
    action: str,
    api_version: str,
    body: bytes,
    timestamp: str,
    nonce: str,
) -> Mapping[str, str]:
    """Build the official ACS3-HMAC-SHA256 header set for one request.

    Deterministic for fixed inputs; the secret only influences the signature
    and never appears in any returned header value.
    """
    if (
        type(access_key_id) is not str
        or not access_key_id
        or type(access_key_secret) is not str
        or not access_key_secret
        or type(method) is not str
        or method not in {"GET", "POST"}
        or type(host) is not str
        or not host
        or type(path) is not str
        or not path.startswith("/")
        or type(action) is not str
        or not action
        or type(api_version) is not str
        or not api_version
        or type(body) is not bytes
        or type(timestamp) is not str
        or not timestamp
        or type(nonce) is not str
        or not nonce
    ):
        _reject()
    body_sha256 = hashlib.sha256(body).hexdigest()
    headers: dict[str, str] = {
        "host": host,
        "x-acs-action": action,
        "x-acs-content-sha256": body_sha256,
        "x-acs-date": timestamp,
        "x-acs-signature-nonce": nonce,
        "x-acs-version": api_version,
    }
    sorted_names = sorted(headers)
    canonical_headers = "".join(f"{name}:{headers[name]}\n" for name in sorted_names)
    signed_header_names = ";".join(sorted_names)
    canonical_request = (
        f"{method}\n{path}\n{canonical_query_string(query)}\n"
        f"{canonical_headers}\n{signed_header_names}\n{body_sha256}"
    )
    hashed_request = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    string_to_sign = f"{ACS3_ALGORITHM}\n{hashed_request}"
    signature = hmac.new(
        access_key_secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    headers["Authorization"] = (
        f"{ACS3_ALGORITHM} Credential={access_key_id},"
        f"SignedHeaders={signed_header_names},Signature={signature}"
    )
    return MappingProxyType(headers)


@final
class AliyunImsEditingProvider:
    """Aliyun IMS implementation of the provider-neutral editing port.

    `submit` consumes the VE-04 preflight and staging plan, persists the
    prepared intent (request hash) before the single dispatch, persists the
    acknowledged JobId afterwards, and never replays automatically once a
    response is lost. Callback/polling reconciliation belongs to VE-06; its
    entry points are `record_running` / `record_succeeded` / `record_failed`.
    """

    __slots__ = (
        "_capabilities",
        "_contract",
        "_intent_store",
        "_output",
        "_preflight_source",
        "_region",
        "_staging_bucket",
        "_staging_planner",
        "_transport",
    )

    def __init__(
        self,
        *,
        contract: AliyunImsEditingStagingContract,
        region: AliyunImsRegion,
        staging_bucket: AliyunOssBucketName,
        output: AliyunEditingOutputConfig,
        preflight_source: AliyunEditingPreflightSource,
        staging_planner: AliyunMediaStagingPlanner,
        transport: AliyunImsSubmitTransport,
        intent_store: AliyunEditingIntentStore,
    ) -> None:
        if (
            not isinstance(contract, AliyunImsEditingStagingContract)
            or not isinstance(region, AliyunImsRegion)
            or type(staging_bucket) is not AliyunOssBucketName
            or not isinstance(output, AliyunEditingOutputConfig)
        ):
            _reject()
        self._contract = contract
        self._region = region
        self._staging_bucket = staging_bucket
        self._output = output
        self._preflight_source = preflight_source
        self._staging_planner = staging_planner
        self._transport = transport
        self._intent_store = intent_store
        self._capabilities = aliyun_editing_capabilities()

    async def capabilities(self) -> EditingProviderCapabilities:
        return self._capabilities

    async def validate(self, timeline: EditingTimeline) -> None:
        _validate_timeline_capabilities(timeline)

    async def submit(self, submission: EditingSubmission) -> EditingProviderJobSnapshot:
        if not isinstance(submission, EditingSubmission):
            _reject()
        await self.validate(submission.timeline)
        preflight = await self._preflight_source.current()
        if not isinstance(preflight, EditingServicePreflight) or not preflight.ready:
            _fail(EditingProviderErrorCode.DEPENDENCY_UNAVAILABLE)

        try:
            staging_plan = await self._staging_planner.plan(submission.timeline)
        except InvalidAliyunImsEditingStagingModel:
            _fail(EditingProviderErrorCode.INVALID_INPUT)
        request = build_submit_request(
            contract=self._contract,
            region=self._region,
            bucket=self._staging_bucket,
            output=self._output,
            submission=submission,
            staging_plan=staging_plan,
        )
        request_hash = editing_submit_request_hash(submission=submission, request=request)

        existing = await self._intent_store.load(submission.editing_job_id)
        if existing is not None:
            if existing.request_hash != request_hash:
                _fail(EditingProviderErrorCode.CONFLICT)
            if existing.state is not AliyunEditingIntentState.PREPARED:
                return self._snapshot_of(existing)
        else:
            await self._intent_store.save(
                AliyunEditingIntent(
                    editing_job_id=submission.editing_job_id,
                    request_hash=request_hash,
                    state=AliyunEditingIntentState.PREPARED,
                    vendor_job_id=None,
                    status=EditingJobStatus.QUEUED,
                    failure_code=None,
                    output_artifact_ids=(),
                )
            )

        try:
            acknowledgement = await self._transport.submit_media_producing_job(request)
        except AliyunImsTransportFailure as failure:
            if failure.kind is AliyunImsTransportFailureKind.RESPONSE_LOST:
                uncertain = AliyunEditingIntent(
                    editing_job_id=submission.editing_job_id,
                    request_hash=request_hash,
                    state=AliyunEditingIntentState.UNCERTAIN,
                    vendor_job_id=None,
                    status=EditingJobStatus.OUTCOME_UNCERTAIN,
                    failure_code=None,
                    output_artifact_ids=(),
                )
                await self._intent_store.save(uncertain)
                return self._snapshot_of(uncertain)
            if failure.kind is AliyunImsTransportFailureKind.REJECTED_INVALID:
                _fail(EditingProviderErrorCode.INVALID_INPUT)
            _fail(EditingProviderErrorCode.DEPENDENCY_UNAVAILABLE)

        dispatched = AliyunEditingIntent(
            editing_job_id=submission.editing_job_id,
            request_hash=request_hash,
            state=AliyunEditingIntentState.DISPATCHED,
            vendor_job_id=acknowledgement.vendor_job_id,
            status=EditingJobStatus.QUEUED,
            failure_code=None,
            output_artifact_ids=(),
        )
        await self._intent_store.save(dispatched)
        return self._snapshot_of(dispatched)

    async def get(self, editing_job_id: EditingJobId) -> EditingProviderJobSnapshot:
        return self._snapshot_of(await self._require_settled_intent(editing_job_id))

    async def cancel(self, editing_job_id: EditingJobId) -> EditingProviderJobSnapshot:
        intent = await self._require_settled_intent(editing_job_id)
        if EditingJobStateMachine.is_terminal(intent.status):
            return self._snapshot_of(intent)
        cancelling = AliyunEditingIntent(
            editing_job_id=intent.editing_job_id,
            request_hash=intent.request_hash,
            state=intent.state,
            vendor_job_id=intent.vendor_job_id,
            status=EditingJobStateMachine.transition(intent.status, EditingJobStatus.CANCELLING),
            failure_code=None,
            output_artifact_ids=(),
        )
        await self._intent_store.save(cancelling)
        return self._snapshot_of(cancelling)

    async def fetch_artifacts(self, editing_job_id: EditingJobId) -> tuple[ArtifactId, ...]:
        intent = await self._require_settled_intent(editing_job_id)
        if intent.status is not EditingJobStatus.SUCCEEDED:
            _fail(EditingProviderErrorCode.CONFLICT)
        return intent.output_artifact_ids

    async def record_running(self, editing_job_id: EditingJobId) -> None:
        """VE-06 reconciliation entry point: the vendor reported the job running."""
        await self._transition_intent(editing_job_id, EditingJobStatus.RUNNING, (), None)

    async def record_succeeded(
        self, editing_job_id: EditingJobId, output_artifact_ids: tuple[ArtifactId, ...]
    ) -> None:
        """VE-06 reconciliation entry point: confirmed success with outputs."""
        await self._transition_intent(
            editing_job_id, EditingJobStatus.SUCCEEDED, output_artifact_ids, None
        )

    async def record_failed(
        self, editing_job_id: EditingJobId, failure_code: EditingFailureCode
    ) -> None:
        """VE-06 reconciliation entry point: confirmed terminal failure."""
        await self._transition_intent(editing_job_id, EditingJobStatus.FAILED, (), failure_code)

    async def _transition_intent(
        self,
        editing_job_id: EditingJobId,
        target: EditingJobStatus,
        output_artifact_ids: tuple[ArtifactId, ...],
        failure_code: EditingFailureCode | None,
    ) -> None:
        intent = await self._require_settled_intent(editing_job_id)
        if not EditingJobStateMachine.can_transition(intent.status, target):
            _fail(EditingProviderErrorCode.CONFLICT)
        await self._intent_store.save(
            AliyunEditingIntent(
                editing_job_id=intent.editing_job_id,
                request_hash=intent.request_hash,
                state=intent.state,
                vendor_job_id=intent.vendor_job_id,
                status=target,
                failure_code=failure_code,
                output_artifact_ids=output_artifact_ids,
            )
        )

    async def _require_settled_intent(self, editing_job_id: EditingJobId) -> AliyunEditingIntent:
        if not isinstance(editing_job_id, EditingJobId):
            _fail(EditingProviderErrorCode.INVALID_INPUT)
        intent = await self._intent_store.load(editing_job_id)
        if intent is None or intent.state is AliyunEditingIntentState.PREPARED:
            _fail(EditingProviderErrorCode.NOT_FOUND)
        return intent

    def _snapshot_of(self, intent: AliyunEditingIntent) -> EditingProviderJobSnapshot:
        return EditingProviderJobSnapshot(
            provider_id=ALIYUN_IMS_EDITING_PROVIDER_ID,
            editing_job_id=intent.editing_job_id,
            status=intent.status,
            failure_code=intent.failure_code,
            output_artifact_ids=intent.output_artifact_ids,
        )


__all__ = [
    "ACS3_ALGORITHM",
    "ALIYUN_IMS_EDITING_PROVIDER_ID",
    "OUTPUT_MEDIA_TARGET_OSS_OBJECT",
    "OUTPUT_OBJECT_KEY_PREFIX",
    "SUBMIT_ACTION",
    "AliyunEditingIntent",
    "AliyunEditingIntentState",
    "AliyunEditingIntentStore",
    "AliyunEditingOutputConfig",
    "AliyunEditingPreflightSource",
    "AliyunImsEditingProvider",
    "AliyunImsSubmitTransport",
    "AliyunImsTransportFailure",
    "AliyunImsTransportFailureKind",
    "AliyunMediaStagingPlanner",
    "AliyunOssBucketName",
    "AliyunSubmitAcknowledgement",
    "AliyunSubmitMediaProducingRequest",
    "InMemoryAliyunEditingIntentStore",
    "InvalidAliyunImsEditingProviderModel",
    "acs3_signed_headers",
    "aliyun_editing_capabilities",
    "build_output_media_config",
    "build_submit_request",
    "canonical_query_string",
    "compile_aliyun_timeline",
    "editing_submit_request_hash",
]
