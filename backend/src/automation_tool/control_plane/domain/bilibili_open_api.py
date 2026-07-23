"""Bilibili open-platform publishing API contract loader and fail-closed validators.

PB-02 locks the officially documented OAuth, upload, submission, query and
error-code surface of the Bilibili open platform into closed types.  No HTTP
client lives here; PB-03 consumes this contract for the real upload flow.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Final, Never, final

from automation_tool.control_plane.domain.video_publishing import PublishFailureCode

BILIBILI_OPEN_API_CONTRACT_VERSION: Final = 1

_RESOURCE_ID_PATTERN: Final = re.compile(r"^BV[0-9A-Za-z]{10}$")
_FILE_NAME_PATTERN: Final = re.compile(r"^[^/\\]+\.[A-Za-z0-9]+$")
_ENVELOPE_KEYS: Final = frozenset({"code", "message", "ttl", "data", "request_id"})

_TOP_LEVEL_KEYS: Final = frozenset(
    {
        "contract",
        "version",
        "verified_at",
        "policy",
        "sources",
        "oauth",
        "signature",
        "upload",
        "cover",
        "submission",
        "query",
        "rate_limits",
        "sandbox",
        "error_categories",
        "error_codes",
        "pending_credential_verification",
    }
)


class InvalidBilibiliOpenApiContract(ValueError):
    """The committed Bilibili open-api contract document is invalid."""

    def __init__(self) -> None:
        super().__init__("Bilibili open-api contract document is invalid")


class InvalidBilibiliOpenApiMessage(ValueError):
    """A request or response payload violates the locked Bilibili contract."""

    def __init__(self) -> None:
        super().__init__("Bilibili open-api message violates the locked contract")


def _reject_contract() -> Never:
    raise InvalidBilibiliOpenApiContract


def _reject_message() -> Never:
    raise InvalidBilibiliOpenApiMessage


class BilibiliErrorCategory(StrEnum):
    """Closed classification for documented Bilibili open-platform error codes."""

    REQUEST_MALFORMED = "request_malformed"
    AUTH_REJECTED = "auth_rejected"
    RATE_LIMITED = "rate_limited"
    PLATFORM_BUSY = "platform_busy"
    CONTENT_REJECTED = "content_rejected"
    ARCHIVE_CONFLICT = "archive_conflict"
    HUMAN_VERIFICATION_REQUIRED = "human_verification_required"
    UNKNOWN = "unknown"


_CATEGORY_TO_FAILURE_CODE: Final[Mapping[BilibiliErrorCategory, PublishFailureCode]] = (
    MappingProxyType(
        {
            BilibiliErrorCategory.REQUEST_MALFORMED: PublishFailureCode.INVALID_INPUT,
            BilibiliErrorCategory.CONTENT_REJECTED: PublishFailureCode.INVALID_INPUT,
            BilibiliErrorCategory.AUTH_REJECTED: PublishFailureCode.PLATFORM_ERROR,
            BilibiliErrorCategory.ARCHIVE_CONFLICT: PublishFailureCode.PLATFORM_ERROR,
            BilibiliErrorCategory.HUMAN_VERIFICATION_REQUIRED: PublishFailureCode.PLATFORM_ERROR,
            BilibiliErrorCategory.UNKNOWN: PublishFailureCode.PLATFORM_ERROR,
            BilibiliErrorCategory.RATE_LIMITED: PublishFailureCode.DEPENDENCY_UNAVAILABLE,
            BilibiliErrorCategory.PLATFORM_BUSY: PublishFailureCode.DEPENDENCY_UNAVAILABLE,
        }
    )
)

_CONTRACT_CATEGORIES: Final = frozenset(
    category for category in BilibiliErrorCategory if category is not BilibiliErrorCategory.UNKNOWN
)


@final
@dataclass(frozen=True, slots=True)
class BilibiliOpenApiContract:
    """Frozen, machine-consumed view of the committed contract document."""

    version: int
    verified_at: str
    authorize_pc_url: str
    authorize_h5_url: str
    token_url: str
    refresh_token_url: str
    required_scope: str
    known_scopes: frozenset[str]
    signature_version: str
    signature_algorithm: str
    signed_headers: tuple[str, ...]
    timestamp_max_skew_seconds: int
    upload_init_url: str
    part_upload_url: str
    upload_complete_url: str
    small_file_upload_url: str
    cover_upload_url: str
    archive_add_url: str
    archive_view_url: str
    archive_viewlist_url: str
    type_list_url: str
    part_size_bytes: int
    max_part_count: int
    max_parallel_part_uploads: int
    small_file_max_bytes: int
    video_max_bytes: int
    video_max_duration_seconds: int
    cover_max_bytes: int
    cover_formats: frozenset[str]
    title_max_chars: int
    description_max_chars: int
    tag_total_max_chars: int
    page_size_max: int
    archive_status_filters: frozenset[str]
    error_categories: Mapping[int, BilibiliErrorCategory]


@final
@dataclass(frozen=True, slots=True)
class BilibiliPlatformRejection:
    """A well-formed platform error mapped onto the PB-01 failure semantics."""

    code: int
    category: BilibiliErrorCategory
    failure_code: PublishFailureCode


@final
@dataclass(frozen=True, slots=True)
class TokenGrant:
    """Successful authorization-code token grant."""

    access_token: str
    refresh_token: str
    expires_at_epoch_seconds: int
    scopes: tuple[str, ...]
    grants_video_publishing: bool


@final
@dataclass(frozen=True, slots=True)
class TokenRefresh:
    """Successful refresh-token renewal; the used refresh token is now consumed."""

    access_token: str
    refresh_token: str
    expires_at_epoch_seconds: int


@final
@dataclass(frozen=True, slots=True)
class UploadSession:
    """Successful upload pre-processing; the token drives part upload and submit."""

    upload_token: str


@final
@dataclass(frozen=True, slots=True)
class CoverUploadResult:
    """Successful cover upload; the URL is the only cover accepted by submit."""

    url: str


@final
@dataclass(frozen=True, slots=True)
class ArchiveSubmissionReceipt:
    """Successful archive submission; resource_id is the platform archive key."""

    resource_id: str


@final
@dataclass(frozen=True, slots=True)
class ArchiveStatusSnapshot:
    """Successful single-archive query used by PB-04 reconciliation."""

    resource_id: str
    title: str
    state: int
    state_desc: str
    reject_reason: str
    created_at_epoch_seconds: int
    published_at_epoch_seconds: int


def _require_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        _reject_contract()
    return value


def _section(document: Mapping[str, object], key: str) -> Mapping[str, object]:
    # The exact top-level key check in the loader guarantees presence.
    return _require_mapping(document[key])


def _contract_str(section: Mapping[str, object], key: str) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value:
        _reject_contract()
    return value


def _contract_https_url(section: Mapping[str, object], key: str) -> str:
    value = _contract_str(section, key)
    if not value.startswith("https://"):
        _reject_contract()
    return value


def _contract_int(section: Mapping[str, object], key: str) -> int:
    value = section.get(key)
    if type(value) is not int or value < 1:
        _reject_contract()
    return value


def _contract_str_items(section: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = section.get(key)
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
        or len(set(value)) != len(value)
    ):
        _reject_contract()
    return tuple(value)


def _parse_error_categories(
    document: Mapping[str, object],
) -> Mapping[int, BilibiliErrorCategory]:
    declared = frozenset(_contract_str_items(document, "error_categories"))
    if declared != frozenset(category.value for category in _CONTRACT_CATEGORIES):
        _reject_contract()
    table = _section(document, "error_codes")
    categories: dict[int, BilibiliErrorCategory] = {}
    for raw_code, entry_value in table.items():
        if not raw_code.isdigit() or str(int(raw_code)) != raw_code:
            _reject_contract()
        code = int(raw_code)
        if code < 1:
            _reject_contract()
        entry = _require_mapping(entry_value)
        if set(entry) != {"meaning", "category"}:
            _reject_contract()
        _contract_str(entry, "meaning")
        raw_category = _contract_str(entry, "category")
        if raw_category not in declared:
            _reject_contract()
        categories[code] = BilibiliErrorCategory(raw_category)
    if not categories:
        _reject_contract()
    return MappingProxyType(categories)


def load_bilibili_open_api_contract(path: Path) -> BilibiliOpenApiContract:
    """Load the committed contract document; any drift or ambiguity fails closed."""
    if not path.is_file():
        _reject_contract()
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise InvalidBilibiliOpenApiContract from None
    document = _require_mapping(raw)
    if set(document) != set(_TOP_LEVEL_KEYS):
        _reject_contract()
    if (
        document.get("contract") != "bilibili-open-api"
        or document.get("version") != BILIBILI_OPEN_API_CONTRACT_VERSION
    ):
        _reject_contract()
    verified_at = _contract_str(document, "verified_at")
    sources = document.get("sources")
    if not isinstance(sources, list) or not sources:
        _reject_contract()
    for source_value in sources:
        source = _require_mapping(source_value)
        _contract_https_url(source, "url")
        _contract_str(source, "name")
        _contract_str(source, "verified_at")
    oauth = _section(document, "oauth")
    signature = _section(document, "signature")
    upload = _section(document, "upload")
    cover = _section(document, "cover")
    submission = _section(document, "submission")
    query = _section(document, "query")
    known_scopes = frozenset(_contract_str_items(oauth, "known_scopes"))
    required_scope = _contract_str(oauth, "required_scope_for_video_publishing")
    if required_scope not in known_scopes:
        _reject_contract()
    part_size = _contract_int(upload, "part_size_bytes")
    video_max = _contract_int(upload, "video_max_bytes")
    max_parts = _contract_int(upload, "max_part_count")
    if max_parts * part_size < video_max:
        _reject_contract()
    status_filters = frozenset(_contract_str_items(query, "status_filters"))
    return BilibiliOpenApiContract(
        version=BILIBILI_OPEN_API_CONTRACT_VERSION,
        verified_at=verified_at,
        authorize_pc_url=_contract_https_url(oauth, "authorize_pc_url"),
        authorize_h5_url=_contract_https_url(oauth, "authorize_h5_url"),
        token_url=_contract_https_url(oauth, "token_url"),
        refresh_token_url=_contract_https_url(oauth, "refresh_token_url"),
        required_scope=required_scope,
        known_scopes=known_scopes,
        signature_version=_contract_str(signature, "signature_version"),
        signature_algorithm=_contract_str(signature, "algorithm"),
        signed_headers=_contract_str_items(signature, "signed_headers"),
        timestamp_max_skew_seconds=_contract_int(signature, "timestamp_max_skew_seconds"),
        upload_init_url=_contract_https_url(upload, "init_url"),
        part_upload_url=_contract_https_url(upload, "part_upload_url"),
        upload_complete_url=_contract_https_url(upload, "complete_url"),
        small_file_upload_url=_contract_https_url(upload, "small_file_upload_url"),
        cover_upload_url=_contract_https_url(cover, "upload_url"),
        archive_add_url=_contract_https_url(submission, "add_url"),
        archive_view_url=_contract_https_url(query, "view_url"),
        archive_viewlist_url=_contract_https_url(query, "viewlist_url"),
        type_list_url=_contract_https_url(submission, "type_list_url"),
        part_size_bytes=part_size,
        max_part_count=max_parts,
        max_parallel_part_uploads=_contract_int(upload, "max_parallel_part_uploads"),
        small_file_max_bytes=_contract_int(upload, "small_file_max_bytes"),
        video_max_bytes=video_max,
        video_max_duration_seconds=_contract_int(upload, "video_max_duration_seconds"),
        cover_max_bytes=_contract_int(cover, "max_bytes"),
        cover_formats=frozenset(_contract_str_items(cover, "formats")),
        title_max_chars=_contract_int(submission, "title_max_chars"),
        description_max_chars=_contract_int(submission, "description_max_chars"),
        tag_total_max_chars=_contract_int(submission, "tag_total_max_chars"),
        page_size_max=_contract_int(query, "page_size_max"),
        archive_status_filters=status_filters,
        error_categories=_parse_error_categories(document),
    )


def classify_error_code(
    contract: BilibiliOpenApiContract, code: object
) -> BilibiliPlatformRejection:
    """Map one non-zero platform error code onto the PB-01 failure semantics."""
    if type(code) is not int or code < 1:
        _reject_message()
    category = contract.error_categories.get(code, BilibiliErrorCategory.UNKNOWN)
    return BilibiliPlatformRejection(
        code=code,
        category=category,
        failure_code=_CATEGORY_TO_FAILURE_CODE[category],
    )


def _parse_envelope(
    contract: BilibiliOpenApiContract, payload: object
) -> BilibiliPlatformRejection | Mapping[str, object] | None:
    """Return a rejection, the success ``data`` object, or ``None`` when absent."""
    if not isinstance(payload, Mapping) or not set(payload) <= _ENVELOPE_KEYS:
        _reject_message()
    code = payload.get("code")
    if type(code) is not int:
        _reject_message()
    if code != 0:
        return classify_error_code(contract, code)
    message = payload.get("message")
    if not isinstance(message, str):
        _reject_message()
    ttl = payload.get("ttl")
    if ttl is not None and (type(ttl) is not int or ttl < 0):
        _reject_message()
    request_id = payload.get("request_id")
    if request_id is not None and not isinstance(request_id, str):
        _reject_message()
    data = payload.get("data")
    if data is None:
        return None
    if not isinstance(data, Mapping):
        _reject_message()
    return _require_message_mapping(data)


def _require_message_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        _reject_message()
    return value


def _data_exact_keys(data: Mapping[str, object], keys: frozenset[str]) -> None:
    if set(data) != keys:
        _reject_message()


def _message_secret(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value or value != value.strip():
        _reject_message()
    return value


def _message_epoch(data: Mapping[str, object], key: str) -> int:
    value = data.get(key)
    if type(value) is not int or value < 1:
        _reject_message()
    return value


def parse_token_grant(
    contract: BilibiliOpenApiContract, payload: object
) -> TokenGrant | BilibiliPlatformRejection:
    """Parse the authorization-code token grant response."""
    data = _parse_envelope(contract, payload)
    if isinstance(data, BilibiliPlatformRejection):
        return data
    if data is None:
        _reject_message()
    _data_exact_keys(data, frozenset({"access_token", "refresh_token", "expires_in", "scopes"}))
    raw_scopes = data.get("scopes")
    if (
        not isinstance(raw_scopes, list)
        or not raw_scopes
        or any(scope not in contract.known_scopes for scope in raw_scopes)
        or len(set(raw_scopes)) != len(raw_scopes)
    ):
        _reject_message()
    scopes = tuple(str(scope) for scope in raw_scopes)
    return TokenGrant(
        access_token=_message_secret(data, "access_token"),
        refresh_token=_message_secret(data, "refresh_token"),
        expires_at_epoch_seconds=_message_epoch(data, "expires_in"),
        scopes=scopes,
        grants_video_publishing=contract.required_scope in scopes,
    )


def parse_token_refresh(
    contract: BilibiliOpenApiContract, payload: object
) -> TokenRefresh | BilibiliPlatformRejection:
    """Parse the refresh-token renewal response; each refresh token is single-use."""
    data = _parse_envelope(contract, payload)
    if isinstance(data, BilibiliPlatformRejection):
        return data
    if data is None:
        _reject_message()
    _data_exact_keys(data, frozenset({"access_token", "refresh_token", "expires_in"}))
    return TokenRefresh(
        access_token=_message_secret(data, "access_token"),
        refresh_token=_message_secret(data, "refresh_token"),
        expires_at_epoch_seconds=_message_epoch(data, "expires_in"),
    )


def parse_upload_init(
    contract: BilibiliOpenApiContract, payload: object
) -> UploadSession | BilibiliPlatformRejection:
    """Parse the upload pre-processing response."""
    data = _parse_envelope(contract, payload)
    if isinstance(data, BilibiliPlatformRejection):
        return data
    if data is None:
        _reject_message()
    _data_exact_keys(data, frozenset({"upload_token"}))
    return UploadSession(upload_token=_message_secret(data, "upload_token"))


def parse_transfer_ack(
    contract: BilibiliOpenApiContract, payload: object
) -> None | BilibiliPlatformRejection:
    """Parse the bodyless part-upload and merge acknowledgements."""
    data = _parse_envelope(contract, payload)
    if isinstance(data, BilibiliPlatformRejection):
        return data
    if data is not None:
        _reject_message()
    return None


def parse_cover_upload(
    contract: BilibiliOpenApiContract, payload: object
) -> CoverUploadResult | BilibiliPlatformRejection:
    """Parse the cover upload response; only https cover URLs are accepted."""
    data = _parse_envelope(contract, payload)
    if isinstance(data, BilibiliPlatformRejection):
        return data
    if data is None:
        _reject_message()
    _data_exact_keys(data, frozenset({"url"}))
    url = _message_secret(data, "url")
    if not url.startswith("https://"):
        _reject_message()
    return CoverUploadResult(url=url)


def parse_archive_add(
    contract: BilibiliOpenApiContract, payload: object
) -> ArchiveSubmissionReceipt | BilibiliPlatformRejection:
    """Parse the archive submission response into the platform archive key."""
    data = _parse_envelope(contract, payload)
    if isinstance(data, BilibiliPlatformRejection):
        return data
    if data is None:
        _reject_message()
    _data_exact_keys(data, frozenset({"resource_id"}))
    resource_id = _message_secret(data, "resource_id")
    if _RESOURCE_ID_PATTERN.fullmatch(resource_id) is None:
        _reject_message()
    return ArchiveSubmissionReceipt(resource_id=resource_id)


_ARCHIVE_VIEW_KEYS: Final = frozenset(
    {
        "resource_id",
        "title",
        "cover",
        "tid",
        "no_reprint",
        "desc",
        "tag",
        "copyright",
        "ctime",
        "ptime",
        "addit_info",
        "video_info",
    }
)


def parse_archive_view(
    contract: BilibiliOpenApiContract, payload: object
) -> ArchiveStatusSnapshot | BilibiliPlatformRejection:
    """Parse the single-archive detail response used for status reconciliation."""
    data = _parse_envelope(contract, payload)
    if isinstance(data, BilibiliPlatformRejection):
        return data
    if data is None:
        _reject_message()
    _data_exact_keys(data, _ARCHIVE_VIEW_KEYS)
    resource_id = _message_secret(data, "resource_id")
    if _RESOURCE_ID_PATTERN.fullmatch(resource_id) is None:
        _reject_message()
    addit_info = _require_message_mapping(data.get("addit_info"))
    if set(addit_info) != {"state", "state_desc", "reject_reason"}:
        _reject_message()
    state = addit_info.get("state")
    if type(state) is not int:
        _reject_message()
    state_desc = addit_info.get("state_desc")
    reject_reason = addit_info.get("reject_reason")
    title = data.get("title")
    if (
        not isinstance(state_desc, str)
        or not isinstance(reject_reason, str)
        or not isinstance(title, str)
    ):
        _reject_message()
    return ArchiveStatusSnapshot(
        resource_id=resource_id,
        title=title,
        state=state,
        state_desc=state_desc,
        reject_reason=reject_reason,
        created_at_epoch_seconds=_message_epoch(data, "ctime"),
        published_at_epoch_seconds=_message_epoch(data, "ptime"),
    )


def validate_upload_init_request(
    contract: BilibiliOpenApiContract, *, file_name: object, upload_type: object
) -> None:
    """Validate the upload pre-processing request; file names are untrusted input."""
    if (
        not isinstance(file_name, str)
        or not 1 <= len(file_name) <= 255
        or _FILE_NAME_PATTERN.fullmatch(file_name) is None
        or file_name.startswith(".")
        or any(unicodedata.category(character).startswith("C") for character in file_name)
    ):
        _reject_message()
    if upload_type not in {"0", "1"}:
        _reject_message()


def validate_part_number(contract: BilibiliOpenApiContract, part_number: object) -> None:
    """Validate one 1-based part number against the frozen part-count ceiling."""
    if type(part_number) is not int or not 1 <= part_number <= contract.max_part_count:
        _reject_message()


def plan_upload_parts(
    contract: BilibiliOpenApiContract, total_size_bytes: object
) -> tuple[int, ...]:
    """Split one file into fixed-size parts; only the final part may be smaller."""
    if (
        type(total_size_bytes) is not int
        or total_size_bytes < 1
        or total_size_bytes > contract.video_max_bytes
    ):
        _reject_message()
    full_parts, tail = divmod(total_size_bytes, contract.part_size_bytes)
    sizes = [contract.part_size_bytes] * full_parts
    if tail:
        sizes.append(tail)
    return tuple(sizes)


def _validate_bounded_text(value: str, *, maximum: int) -> None:
    if not value or value != value.strip() or len(value) > maximum:
        _reject_message()
    if any(unicodedata.category(character).startswith("C") for character in value):
        _reject_message()


def validate_archive_submission(
    contract: BilibiliOpenApiContract,
    *,
    title: object,
    tid: object,
    tag: object,
    copyright_: object,
    description: object = None,
    source: object = None,
    no_reprint: object = 0,
    cover_url: object = None,
) -> None:
    """Validate an archive submission body against the frozen field boundaries."""
    if not isinstance(title, str):
        _reject_message()
    _validate_bounded_text(title, maximum=contract.title_max_chars)
    if type(tid) is not int or tid < 1:
        _reject_message()
    if not isinstance(tag, str):
        _reject_message()
    _validate_bounded_text(tag, maximum=contract.tag_total_max_chars)
    for segment in tag.split(","):
        if not segment or segment != segment.strip():
            _reject_message()
    if type(copyright_) is not int or copyright_ not in {1, 2}:
        _reject_message()
    if copyright_ == 2:
        if not isinstance(source, str):
            _reject_message()
        _validate_bounded_text(source, maximum=contract.tag_total_max_chars)
    elif source is not None:
        _reject_message()
    if description is not None:
        if not isinstance(description, str):
            _reject_message()
        _validate_bounded_text(description, maximum=contract.description_max_chars)
    if type(no_reprint) is not int or no_reprint not in {0, 1}:
        _reject_message()
    if cover_url is not None and (
        not isinstance(cover_url, str) or not cover_url.startswith("https://")
    ):
        _reject_message()


__all__ = [
    "BILIBILI_OPEN_API_CONTRACT_VERSION",
    "ArchiveStatusSnapshot",
    "ArchiveSubmissionReceipt",
    "BilibiliErrorCategory",
    "BilibiliOpenApiContract",
    "BilibiliPlatformRejection",
    "CoverUploadResult",
    "InvalidBilibiliOpenApiContract",
    "InvalidBilibiliOpenApiMessage",
    "TokenGrant",
    "TokenRefresh",
    "UploadSession",
    "classify_error_code",
    "load_bilibili_open_api_contract",
    "parse_archive_add",
    "parse_archive_view",
    "parse_cover_upload",
    "parse_token_grant",
    "parse_token_refresh",
    "parse_transfer_ack",
    "parse_upload_init",
    "plan_upload_parts",
    "validate_archive_submission",
    "validate_part_number",
    "validate_upload_init_request",
]
