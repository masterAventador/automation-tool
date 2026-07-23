"""First-release publishing capability freeze and PublishJob domain contracts."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Never, final

from automation_tool.control_plane.domain.resource_ids import ArtifactId, ResourceId

PUBLISHING_CAPABILITIES_CONTRACT_VERSION: Final = 1
MAX_PUBLISH_TITLE_CHARACTERS: Final = 200
MAX_PUBLISH_DESCRIPTION_CHARACTERS: Final = 4_000

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class InvalidVideoPublishingModel(ValueError):
    """A video publishing domain value is invalid."""

    def __init__(self) -> None:
        super().__init__("Video publishing domain model is invalid")


class InvalidPublishJobTransition(ValueError):
    """A publish job transition is not part of the closed lifecycle graph."""

    def __init__(self) -> None:
        super().__init__("Publish job state transition is invalid")


@final
class PublishJobId(ResourceId):
    """Stable identifier for one video publishing job."""

    __slots__ = ()
    _resource = "publish job"


class PublishPlatform(StrEnum):
    """Closed set of social platforms known to the publishing domain."""

    BILIBILI = "bilibili"
    DOUYIN = "douyin"
    KUAISHOU = "kuaishou"
    XIAOHONGSHU = "xiaohongshu"
    WECHAT_CHANNELS = "wechat_channels"


class PublishMechanism(StrEnum):
    """Closed set of publishing mechanisms; deferred means no entry point exists."""

    OFFICIAL_API = "official_api"
    BROWSER_USE = "browser_use"
    DEFERRED = "deferred"


FIRST_RELEASE_PUBLISHING_CAPABILITIES: Final[Mapping[PublishPlatform, PublishMechanism]] = (
    MappingProxyType(
        {
            PublishPlatform.BILIBILI: PublishMechanism.OFFICIAL_API,
            PublishPlatform.DOUYIN: PublishMechanism.BROWSER_USE,
            PublishPlatform.KUAISHOU: PublishMechanism.DEFERRED,
            PublishPlatform.XIAOHONGSHU: PublishMechanism.DEFERRED,
            PublishPlatform.WECHAT_CHANNELS: PublishMechanism.DEFERRED,
        }
    )
)


def _reject() -> Never:
    raise InvalidVideoPublishingModel


@dataclass(frozen=True, slots=True)
class PublishCapability:
    """One platform capability that must exactly match the first-release freeze."""

    platform: PublishPlatform
    mechanism: PublishMechanism
    enabled: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.platform, PublishPlatform)
            or not isinstance(self.mechanism, PublishMechanism)
            or type(self.enabled) is not bool
            or self.mechanism is not FIRST_RELEASE_PUBLISHING_CAPABILITIES[self.platform]
            or self.enabled is not (self.mechanism is not PublishMechanism.DEFERRED)
        ):
            _reject()


def publish_capability_for(platform: object) -> PublishCapability:
    """Return the frozen first-release capability for one known platform."""
    if not isinstance(platform, PublishPlatform):
        _reject()
    mechanism = FIRST_RELEASE_PUBLISHING_CAPABILITIES[platform]
    return PublishCapability(
        platform=platform,
        mechanism=mechanism,
        enabled=mechanism is not PublishMechanism.DEFERRED,
    )


def enabled_publish_platforms() -> frozenset[PublishPlatform]:
    """Return the platforms a PublishJob may target in the first release."""
    return frozenset(
        platform
        for platform, mechanism in FIRST_RELEASE_PUBLISHING_CAPABILITIES.items()
        if mechanism is not PublishMechanism.DEFERRED
    )


class PublishJobStatus(StrEnum):
    """Closed lifecycle states for one publish job."""

    DRAFT = "draft"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    DISPATCHING = "dispatching"
    PUBLISHED = "published"
    REJECTED = "rejected"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    OUTCOME_UNCERTAIN = "outcome_uncertain"


class PublishFailureCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    PLATFORM_ERROR = "platform_error"


_TERMINAL_STATUSES: Final[frozenset[PublishJobStatus]] = frozenset(
    {
        PublishJobStatus.PUBLISHED,
        PublishJobStatus.REJECTED,
        PublishJobStatus.FAILED,
        PublishJobStatus.CANCELLED,
        PublishJobStatus.OUTCOME_UNCERTAIN,
    }
)

_PRE_DISPATCH_STATUSES: Final[frozenset[PublishJobStatus]] = frozenset(
    {
        PublishJobStatus.DRAFT,
        PublishJobStatus.AWAITING_APPROVAL,
        PublishJobStatus.APPROVED,
    }
)

_TRANSITIONS: Final[Mapping[PublishJobStatus, frozenset[PublishJobStatus]]] = MappingProxyType(
    {
        PublishJobStatus.DRAFT: frozenset(
            {
                PublishJobStatus.AWAITING_APPROVAL,
                PublishJobStatus.CANCELLING,
            }
        ),
        PublishJobStatus.AWAITING_APPROVAL: frozenset(
            {
                PublishJobStatus.DRAFT,
                PublishJobStatus.APPROVED,
                PublishJobStatus.CANCELLING,
            }
        ),
        PublishJobStatus.APPROVED: frozenset(
            {
                PublishJobStatus.DISPATCHING,
                PublishJobStatus.CANCELLING,
            }
        ),
        PublishJobStatus.DISPATCHING: frozenset(
            {
                PublishJobStatus.PUBLISHED,
                PublishJobStatus.REJECTED,
                PublishJobStatus.FAILED,
                PublishJobStatus.OUTCOME_UNCERTAIN,
                PublishJobStatus.CANCELLING,
            }
        ),
        PublishJobStatus.CANCELLING: frozenset(
            {
                PublishJobStatus.CANCELLED,
                PublishJobStatus.PUBLISHED,
                PublishJobStatus.REJECTED,
                PublishJobStatus.FAILED,
                PublishJobStatus.OUTCOME_UNCERTAIN,
            }
        ),
        PublishJobStatus.PUBLISHED: frozenset(),
        PublishJobStatus.REJECTED: frozenset(),
        PublishJobStatus.FAILED: frozenset(),
        PublishJobStatus.CANCELLED: frozenset(),
        PublishJobStatus.OUTCOME_UNCERTAIN: frozenset(),
    }
)


class PublishJobStateMachine:
    """Stateless transition policy for publish jobs; dispatch requires approval."""

    @staticmethod
    def terminal_statuses() -> frozenset[PublishJobStatus]:
        return _TERMINAL_STATUSES

    @staticmethod
    def is_terminal(status: object) -> bool:
        return isinstance(status, PublishJobStatus) and status in _TERMINAL_STATUSES

    @staticmethod
    def pre_dispatch_statuses() -> frozenset[PublishJobStatus]:
        """Statuses in which no external platform side effect can have happened."""
        return _PRE_DISPATCH_STATUSES

    @staticmethod
    def may_dispatch(status: object) -> bool:
        """Only an approved job may start the single external dispatch."""
        return isinstance(status, PublishJobStatus) and status is PublishJobStatus.APPROVED

    @staticmethod
    def allowed_targets(status: object) -> frozenset[PublishJobStatus]:
        if not isinstance(status, PublishJobStatus):
            raise InvalidPublishJobTransition
        return _TRANSITIONS[status]

    @staticmethod
    def can_transition(current: object, target: object) -> bool:
        return (
            isinstance(current, PublishJobStatus)
            and isinstance(target, PublishJobStatus)
            and target in _TRANSITIONS[current]
        )

    @staticmethod
    def transition(current: object, target: object) -> PublishJobStatus:
        if (
            not isinstance(current, PublishJobStatus)
            or not isinstance(target, PublishJobStatus)
            or target not in _TRANSITIONS[current]
        ):
            raise InvalidPublishJobTransition
        return target


def _validate_text(value: object, *, maximum: int, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum:
        _reject()
    for character in value:
        if character in {"\n", "\t"}:
            continue
        if unicodedata.category(character).startswith("C"):
            _reject()


def _validate_timestamp(value: object) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        _reject()


@dataclass(frozen=True, slots=True)
class PublishJob:
    """One publish intent bound to an enabled first-release platform capability."""

    publish_job_id: PublishJobId
    platform: PublishPlatform
    mechanism: PublishMechanism
    status: PublishJobStatus
    revision: int
    video_artifact_id: ArtifactId
    cover_artifact_id: ArtifactId | None
    title: str
    description: str | None
    content_sha256: str
    failure_code: PublishFailureCode | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.publish_job_id, PublishJobId)
            or not isinstance(self.platform, PublishPlatform)
            or not isinstance(self.mechanism, PublishMechanism)
            or self.mechanism is not FIRST_RELEASE_PUBLISHING_CAPABILITIES[self.platform]
            or self.mechanism is PublishMechanism.DEFERRED
            or not isinstance(self.status, PublishJobStatus)
            or type(self.revision) is not int
            or self.revision < 1
            or not isinstance(self.video_artifact_id, ArtifactId)
            or (
                self.cover_artifact_id is not None
                and not isinstance(self.cover_artifact_id, ArtifactId)
            )
            or self.cover_artifact_id == self.video_artifact_id
            or not isinstance(self.content_sha256, str)
            or _SHA256_PATTERN.fullmatch(self.content_sha256) is None
            or (
                self.failure_code is not None
                and not isinstance(self.failure_code, PublishFailureCode)
            )
            or (self.failure_code is not None) is not (self.status is PublishJobStatus.FAILED)
        ):
            _reject()
        _validate_text(self.title, maximum=MAX_PUBLISH_TITLE_CHARACTERS)
        _validate_text(self.description, maximum=MAX_PUBLISH_DESCRIPTION_CHARACTERS, optional=True)
        _validate_timestamp(self.created_at)
        _validate_timestamp(self.updated_at)
        if self.updated_at < self.created_at:
            _reject()


__all__ = [
    "FIRST_RELEASE_PUBLISHING_CAPABILITIES",
    "MAX_PUBLISH_DESCRIPTION_CHARACTERS",
    "MAX_PUBLISH_TITLE_CHARACTERS",
    "PUBLISHING_CAPABILITIES_CONTRACT_VERSION",
    "InvalidPublishJobTransition",
    "InvalidVideoPublishingModel",
    "PublishCapability",
    "PublishFailureCode",
    "PublishJob",
    "PublishJobId",
    "PublishJobStateMachine",
    "PublishJobStatus",
    "PublishMechanism",
    "PublishPlatform",
    "enabled_publish_platforms",
    "publish_capability_for",
]
