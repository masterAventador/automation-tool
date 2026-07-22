"""Business-agnostic immutable desktop update catalog."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from functools import total_ordering
from typing import Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

_CHANNEL_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SIGNATURE_PATTERN = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")
_IDENTIFIER_PATTERN = re.compile(r"^[0-9A-Za-z-]+$")
_MAX_ARTIFACT_BYTES = 1024 * 1024 * 1024


def _safe_text(value: str, *, maximum_bytes: int) -> bool:
    if not value or len(value.encode("utf-8")) > maximum_bytes:
        return False
    return not any(
        (ord(character) <= 0x1F and character not in "\n\t")
        or ord(character) == 0x7F
        or 0x202A <= ord(character) <= 0x202E
        or 0x2066 <= ord(character) <= 0x2069
        for character in value
    )


@total_ordering
@dataclass(eq=False, frozen=True, slots=True)
class _SemVersion:
    raw: str
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] | None

    @classmethod
    def parse(cls, value: str) -> Self:
        if not isinstance(value, str) or not value:
            raise ValueError("invalid semantic version")
        core_and_prerelease, separator, build = value.partition("+")
        if separator and not _valid_identifiers(build, allow_numeric_leading_zero=True):
            raise ValueError("invalid semantic version")
        core, prerelease_separator, prerelease_source = core_and_prerelease.partition("-")
        components = core.split(".")
        if len(components) != 3 or any(not _valid_core_number(item) for item in components):
            raise ValueError("invalid semantic version")
        prerelease = None
        if prerelease_separator:
            if not _valid_identifiers(prerelease_source, allow_numeric_leading_zero=False):
                raise ValueError("invalid semantic version")
            prerelease = tuple(prerelease_source.split("."))
        return cls(
            raw=value,
            major=int(components[0]),
            minor=int(components[1]),
            patch=int(components[2]),
            prerelease=prerelease,
        )

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, _SemVersion):
            return NotImplemented
        core = (self.major, self.minor, self.patch)
        other_core = (other.major, other.minor, other.patch)
        if core != other_core:
            return core < other_core
        if self.prerelease is None:
            return False
        if other.prerelease is None:
            return True
        for left, right in zip(self.prerelease, other.prerelease, strict=False):
            if left == right:
                continue
            left_numeric = left.isdigit()
            right_numeric = right.isdigit()
            if left_numeric and right_numeric:
                return int(left) < int(right)
            if left_numeric != right_numeric:
                return left_numeric
            return left < right
        return len(self.prerelease) < len(other.prerelease)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _SemVersion) and (
            self.major,
            self.minor,
            self.patch,
            self.prerelease,
        ) == (
            other.major,
            other.minor,
            other.patch,
            other.prerelease,
        )


def _valid_core_number(value: str) -> bool:
    return bool(value) and value.isascii() and value.isdigit() and (value == "0" or value[0] != "0")


def _valid_identifiers(value: str, *, allow_numeric_leading_zero: bool) -> bool:
    identifiers = value.split(".")
    return bool(value) and all(
        identifier
        and _IDENTIFIER_PATTERN.fullmatch(identifier)
        and (
            allow_numeric_leading_zero
            or not identifier.isdigit()
            or identifier == "0"
            or identifier[0] != "0"
        )
        for identifier in identifiers
    )


def validate_canonical_semver(value: str) -> str:
    """Validate one exact SemVer 2.0.0 string while preserving its spelling."""

    _SemVersion.parse(value)
    return value


class DesktopUpdateRelease(BaseModel):
    """Strict deploy-time release document, never returned directly."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, populate_by_name=True)

    version: str
    channel: str
    policy: Literal["optional", "forced"]
    target: Literal["darwin", "windows"]
    arch: Literal["aarch64", "x86_64"]
    url: str
    signature: str
    sha256: str
    size_bytes: int = Field(alias="sizeBytes", ge=1, le=_MAX_ARTIFACT_BYTES)
    notes: str | None = None
    published_at: str | None = Field(default=None, alias="publishedAt")

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        return validate_canonical_semver(value)

    @field_validator("channel")
    @classmethod
    def validate_channel(cls, value: str) -> str:
        if _CHANNEL_PATTERN.fullmatch(value) is None:
            raise ValueError("invalid channel")
        return value

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError("invalid update URL")
        return value

    @field_validator("signature")
    @classmethod
    def validate_signature(cls, value: str) -> str:
        if len(value) > 4096 or _SIGNATURE_PATTERN.fullmatch(value) is None:
            raise ValueError("invalid signature")
        return value

    @field_validator("sha256")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        if _DIGEST_PATTERN.fullmatch(value) is None:
            raise ValueError("invalid digest")
        return value

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, value: str | None) -> str | None:
        if value is not None and not _safe_text(value, maximum_bytes=8192):
            raise ValueError("invalid notes")
        return value

    @field_validator("published_at")
    @classmethod
    def validate_published_at(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("invalid published time") from error
        if parsed.tzinfo is None:
            raise ValueError("invalid published time")
        return value

    def feed_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "version": self.version,
            "url": self.url,
            "signature": self.signature,
            "update_contract": {
                "version": 1,
                "channel": self.channel,
                "policy": self.policy,
                "artifact": {
                    "target": self.target,
                    "arch": self.arch,
                    "sha256": self.sha256,
                    "size_bytes": self.size_bytes,
                },
            },
        }
        if self.notes is not None:
            document["notes"] = self.notes
        if self.published_at is not None:
            document["pub_date"] = self.published_at
        return document


class DesktopUpdateCatalog:
    """Immutable exact-match catalog with deterministic highest-version selection."""

    def __init__(self, releases: tuple[DesktopUpdateRelease, ...]) -> None:
        identities: list[tuple[str, str, str, _SemVersion]] = []
        for release in releases:
            identity = (
                release.channel,
                release.target,
                release.arch,
                _SemVersion.parse(release.version),
            )
            if identity in identities:
                raise ValueError("desktop update catalog rejected")
            identities.append(identity)
        self._releases = releases

    @classmethod
    def empty(cls) -> Self:
        return cls(())

    @classmethod
    def from_documents(cls, documents: list[dict[str, object]]) -> Self:
        try:
            releases = tuple(
                DesktopUpdateRelease.model_validate(document) for document in documents
            )
            return cls(releases)
        except (TypeError, ValidationError, ValueError) as error:
            raise ValueError("desktop update catalog rejected") from error

    def find_update(
        self,
        *,
        channel: str,
        target: str,
        arch: str,
        current_version: str,
    ) -> DesktopUpdateRelease | None:
        current = _SemVersion.parse(current_version)
        candidates = [
            release
            for release in self._releases
            if release.channel == channel
            and release.target == target
            and release.arch == arch
            and current < _SemVersion.parse(release.version)
        ]
        return max(candidates, key=lambda release: _SemVersion.parse(release.version), default=None)
