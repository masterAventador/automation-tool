"""PB-02: Bilibili open-platform publishing contract lockdown tests."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from automation_tool.control_plane.domain.bilibili_open_api import (
    BILIBILI_OPEN_API_CONTRACT_VERSION,
    ArchiveListPage,
    ArchiveStatusNotification,
    ArchiveStatusSnapshot,
    ArchiveSubmissionReceipt,
    BilibiliErrorCategory,
    BilibiliOpenApiContract,
    BilibiliPlatformRejection,
    CoverUploadResult,
    InvalidBilibiliOpenApiContract,
    InvalidBilibiliOpenApiMessage,
    TokenGrant,
    TokenRefresh,
    UploadSession,
    classify_error_code,
    load_bilibili_open_api_contract,
    parse_archive_add,
    parse_archive_status_notification,
    parse_archive_view,
    parse_archive_viewlist,
    parse_cover_upload,
    parse_token_grant,
    parse_token_refresh,
    parse_transfer_ack,
    parse_upload_init,
    parse_webhook_verification,
    plan_upload_parts,
    validate_archive_submission,
    validate_part_number,
    validate_upload_init_request,
)
from automation_tool.control_plane.domain.video_publishing import PublishFailureCode

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts/publishing/bilibili-open-api.v1.json"
FIXTURE_ROOT = REPOSITORY_ROOT / "contracts/publishing/fixtures/bilibili-open-api-v1"

FIXTURE_PATHS = sorted(FIXTURE_ROOT.glob("*.json"))
FIXTURES = [json.loads(path.read_text(encoding="utf-8")) for path in FIXTURE_PATHS]


@pytest.fixture(scope="module")
def contract() -> BilibiliOpenApiContract:
    return load_bilibili_open_api_contract(CONTRACT_PATH)


def _contract_document() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    return document


def _write(tmp_path: Path, document: dict[str, Any]) -> Path:
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


class TestContractDocument:
    def test_contract_loads_and_freezes_official_endpoints(
        self, contract: BilibiliOpenApiContract
    ) -> None:
        assert contract.version == BILIBILI_OPEN_API_CONTRACT_VERSION == 1
        assert contract.verified_at == "2026-07-23"
        assert contract.token_url == "https://api.bilibili.com/x/account-oauth2/v1/token"
        assert (
            contract.refresh_token_url
            == "https://api.bilibili.com/x/account-oauth2/v1/refresh_token"
        )
        assert (
            contract.upload_init_url == "https://member.bilibili.com/arcopen/fn/archive/video/init"
        )
        assert contract.part_upload_url == "https://openupos.bilivideo.com/video/v2/part/upload"
        assert (
            contract.upload_complete_url
            == "https://member.bilibili.com/arcopen/fn/archive/video/complete"
        )
        assert contract.small_file_upload_url == "https://openupos.bilivideo.com/video/v2/upload"
        assert (
            contract.cover_upload_url
            == "https://member.bilibili.com/arcopen/fn/archive/cover/upload"
        )
        assert (
            contract.archive_add_url
            == "https://member.bilibili.com/arcopen/fn/archive/add-by-utoken"
        )
        assert contract.archive_view_url == "https://member.bilibili.com/arcopen/fn/archive/view"
        assert (
            contract.archive_viewlist_url
            == "https://member.bilibili.com/arcopen/fn/archive/viewlist"
        )
        assert contract.type_list_url == "https://member.bilibili.com/arcopen/fn/archive/type/list"

    def test_contract_freezes_auth_and_upload_boundaries(
        self, contract: BilibiliOpenApiContract
    ) -> None:
        assert contract.required_scope == "ARC_BASE"
        assert "USER_INFO" in contract.known_scopes
        assert contract.signature_version == "2.0"
        assert contract.signature_algorithm == "HMAC-SHA256"
        assert contract.signed_headers == (
            "x-bili-accesskeyid",
            "x-bili-content-md5",
            "x-bili-signature-method",
            "x-bili-signature-nonce",
            "x-bili-signature-version",
            "x-bili-timestamp",
        )
        assert contract.timestamp_max_skew_seconds == 600
        assert contract.part_size_bytes == 8 * 1024 * 1024
        assert contract.small_file_max_bytes == 100 * 1024 * 1024
        assert contract.video_max_bytes == 4 * 1024 * 1024 * 1024
        assert contract.max_part_count == 512
        assert contract.max_parallel_part_uploads == 1
        assert contract.title_max_chars == 80
        assert contract.description_max_chars == 250
        assert contract.tag_total_max_chars == 200
        assert contract.page_size_max == 50
        assert contract.archive_status_filters == frozenset(
            {"all", "is_pubing", "pubed", "not_pubed"}
        )

    def test_every_source_entry_has_url_and_verified_date(self) -> None:
        document = _contract_document()
        sources = document["sources"]
        assert len(sources) >= 15
        for source in sources:
            assert source["url"].startswith("https://")
            assert source["verified_at"] == "2026-07-23"

    def test_legacy_endpoints_are_explicitly_rejected(self) -> None:
        document = _contract_document()
        rejected = {entry["endpoint"] for entry in document["policy"]["rejected_legacy_endpoints"]}
        assert "https://passport.bilibili.com/register/pc_oauth2.html" in rejected
        assert "https://member.bilibili.com/x/vu/web/add" in rejected
        assert "https://passport.bilibili.com/api/v3/oauth2/*" in rejected

    def test_contract_registers_pending_credential_verification_items(self) -> None:
        document = _contract_document()
        pending = document["pending_credential_verification"]
        assert len(pending) >= 5
        joined = "\n".join(pending)
        assert "scope" in joined
        assert "限流" in joined
        assert "refresh_token" in joined


class TestContractLoaderFailClosed:
    def test_missing_top_level_section_is_rejected(self, tmp_path: Path) -> None:
        document = _contract_document()
        del document["signature"]
        with pytest.raises(InvalidBilibiliOpenApiContract):
            load_bilibili_open_api_contract(_write(tmp_path, document))

    def test_unknown_top_level_section_is_rejected(self, tmp_path: Path) -> None:
        document = _contract_document()
        document["extra_section"] = {}
        with pytest.raises(InvalidBilibiliOpenApiContract):
            load_bilibili_open_api_contract(_write(tmp_path, document))

    def test_wrong_version_is_rejected(self, tmp_path: Path) -> None:
        document = _contract_document()
        document["version"] = 2
        with pytest.raises(InvalidBilibiliOpenApiContract):
            load_bilibili_open_api_contract(_write(tmp_path, document))

    def test_http_endpoint_is_rejected(self, tmp_path: Path) -> None:
        document = _contract_document()
        document["upload"]["init_url"] = "http://member.bilibili.com/arcopen/fn/archive/video/init"
        with pytest.raises(InvalidBilibiliOpenApiContract):
            load_bilibili_open_api_contract(_write(tmp_path, document))

    def test_unknown_error_category_is_rejected(self, tmp_path: Path) -> None:
        document = _contract_document()
        document["error_codes"]["123013"]["category"] = "surprise"
        with pytest.raises(InvalidBilibiliOpenApiContract):
            load_bilibili_open_api_contract(_write(tmp_path, document))

    def test_non_numeric_error_code_key_is_rejected(self, tmp_path: Path) -> None:
        document = _contract_document()
        document["error_codes"]["abc"] = {"meaning": "x", "category": "platform_busy"}
        with pytest.raises(InvalidBilibiliOpenApiContract):
            load_bilibili_open_api_contract(_write(tmp_path, document))

    def test_success_code_zero_in_error_table_is_rejected(self, tmp_path: Path) -> None:
        document = _contract_document()
        document["error_codes"]["0"] = {"meaning": "x", "category": "platform_busy"}
        with pytest.raises(InvalidBilibiliOpenApiContract):
            load_bilibili_open_api_contract(_write(tmp_path, document))

    def test_missing_file_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(InvalidBilibiliOpenApiContract):
            load_bilibili_open_api_contract(tmp_path / "missing.json")

    def test_non_object_document_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "contract.json"
        path.write_text("[]", encoding="utf-8")
        with pytest.raises(InvalidBilibiliOpenApiContract):
            load_bilibili_open_api_contract(path)

    def test_unparseable_document_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "contract.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(InvalidBilibiliOpenApiContract):
            load_bilibili_open_api_contract(path)

    @pytest.mark.parametrize(
        "mutate",
        [
            pytest.param(lambda d: d["oauth"].update(authorize_pc_url=""), id="empty-url"),
            pytest.param(
                lambda d: d["signature"].update(timestamp_max_skew_seconds=0),
                id="non-positive-int",
            ),
            pytest.param(lambda d: d["oauth"].update(known_scopes=[]), id="empty-scope-list"),
            pytest.param(
                lambda d: d["oauth"].update(known_scopes=["ARC_BASE", "ARC_BASE"]),
                id="duplicate-scope",
            ),
            pytest.param(
                lambda d: d.update(error_categories=d["error_categories"][:-1]),
                id="missing-category-declaration",
            ),
            pytest.param(
                lambda d: d["error_codes"]["4000"].update(extra="x"),
                id="error-entry-extra-key",
            ),
            pytest.param(lambda d: d.update(error_codes={}), id="empty-error-table"),
            pytest.param(lambda d: d.update(sources=[]), id="empty-sources"),
            pytest.param(
                lambda d: d["oauth"].update(required_scope_for_video_publishing="NOPE"),
                id="required-scope-unknown",
            ),
            pytest.param(lambda d: d["upload"].update(max_part_count=1), id="part-count-too-low"),
            pytest.param(lambda d: d["error_codes"].update({"007": {}}), id="padded-code-key"),
            pytest.param(lambda d: d.update(oauth=[]), id="section-not-object"),
        ],
    )
    def test_contract_mutations_fail_closed(
        self, tmp_path: Path, mutate: Callable[[dict[str, Any]], object]
    ) -> None:
        document = _contract_document()
        mutate(document)
        with pytest.raises(InvalidBilibiliOpenApiContract):
            load_bilibili_open_api_contract(_write(tmp_path, document))


class TestErrorClassification:
    @pytest.mark.parametrize(
        ("code", "category", "failure"),
        [
            (4000, BilibiliErrorCategory.REQUEST_MALFORMED, PublishFailureCode.INVALID_INPUT),
            (122000, BilibiliErrorCategory.AUTH_REJECTED, PublishFailureCode.PLATFORM_ERROR),
            (122007, BilibiliErrorCategory.AUTH_REJECTED, PublishFailureCode.PLATFORM_ERROR),
            (
                122009,
                BilibiliErrorCategory.PLATFORM_BUSY,
                PublishFailureCode.DEPENDENCY_UNAVAILABLE,
            ),
            (
                127009,
                BilibiliErrorCategory.RATE_LIMITED,
                PublishFailureCode.DEPENDENCY_UNAVAILABLE,
            ),
            (
                127306,
                BilibiliErrorCategory.RATE_LIMITED,
                PublishFailureCode.DEPENDENCY_UNAVAILABLE,
            ),
            (127022, BilibiliErrorCategory.AUTH_REJECTED, PublishFailureCode.PLATFORM_ERROR),
            (123013, BilibiliErrorCategory.CONTENT_REJECTED, PublishFailureCode.INVALID_INPUT),
            (
                123026,
                BilibiliErrorCategory.RATE_LIMITED,
                PublishFailureCode.DEPENDENCY_UNAVAILABLE,
            ),
            (
                123050,
                BilibiliErrorCategory.HUMAN_VERIFICATION_REQUIRED,
                PublishFailureCode.PLATFORM_ERROR,
            ),
            (123004, BilibiliErrorCategory.ARCHIVE_CONFLICT, PublishFailureCode.PLATFORM_ERROR),
        ],
    )
    def test_documented_codes_map_to_frozen_categories(
        self,
        contract: BilibiliOpenApiContract,
        code: int,
        category: BilibiliErrorCategory,
        failure: PublishFailureCode,
    ) -> None:
        rejection = classify_error_code(contract, code)
        assert rejection == BilibiliPlatformRejection(
            code=code, category=category, failure_code=failure
        )

    def test_unknown_code_fails_closed_to_platform_error(
        self, contract: BilibiliOpenApiContract
    ) -> None:
        rejection = classify_error_code(contract, 999999)
        assert rejection.category is BilibiliErrorCategory.UNKNOWN
        assert rejection.failure_code is PublishFailureCode.PLATFORM_ERROR

    @pytest.mark.parametrize("code", [0, -1, "4000", None, 1.5])
    def test_non_error_codes_are_rejected(
        self, contract: BilibiliOpenApiContract, code: object
    ) -> None:
        with pytest.raises(InvalidBilibiliOpenApiMessage):
            classify_error_code(contract, code)

    def test_every_contract_code_maps_to_domain_failure_code(
        self, contract: BilibiliOpenApiContract
    ) -> None:
        for code in contract.error_categories:
            rejection = classify_error_code(contract, code)
            assert isinstance(rejection.failure_code, PublishFailureCode)
            assert rejection.category is not BilibiliErrorCategory.UNKNOWN


class TestResponseParsers:
    def test_token_grant_success_payload(self, contract: BilibiliOpenApiContract) -> None:
        payload = {
            "code": 0,
            "message": "0",
            "ttl": 1,
            "data": {
                "access_token": "fixture-access-token-000000000000",
                "refresh_token": "fixture-refresh-token-00000000000",
                "expires_in": 1785340800,
                "scopes": ["ARC_BASE"],
            },
        }
        grant = parse_token_grant(contract, payload)
        assert isinstance(grant, TokenGrant)
        assert grant.expires_at_epoch_seconds == 1785340800
        assert grant.scopes == ("ARC_BASE",)

    def test_token_grant_requires_publishing_scope_membership(
        self, contract: BilibiliOpenApiContract
    ) -> None:
        grant = parse_token_grant(
            contract,
            {
                "code": 0,
                "message": "0",
                "data": {
                    "access_token": "fixture-access-token-000000000000",
                    "refresh_token": "fixture-refresh-token-00000000000",
                    "expires_in": 1785340800,
                    "scopes": ["USER_INFO"],
                },
            },
        )
        assert isinstance(grant, TokenGrant)
        assert grant.grants_video_publishing is False

    def test_transfer_ack_success_is_none(self, contract: BilibiliOpenApiContract) -> None:
        assert parse_transfer_ack(contract, {"code": 0, "message": "0"}) is None

    def test_archive_add_receipt_resource_id_pattern(
        self, contract: BilibiliOpenApiContract
    ) -> None:
        receipt = parse_archive_add(
            contract,
            {"code": 0, "message": "0", "ttl": 1, "data": {"resource_id": "BV17B4y1s7R1"}},
        )
        assert isinstance(receipt, ArchiveSubmissionReceipt)
        assert receipt.resource_id == "BV17B4y1s7R1"

    @pytest.mark.parametrize("payload", [None, [], "x", 1])
    def test_non_object_response_is_rejected(
        self, contract: BilibiliOpenApiContract, payload: object
    ) -> None:
        with pytest.raises(InvalidBilibiliOpenApiMessage):
            parse_upload_init(contract, payload)

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({"code": 0, "message": "0", "ttl": -1}, id="negative-ttl"),
            pytest.param({"code": 0, "message": "0", "request_id": 5}, id="request-id-not-str"),
            pytest.param({"code": 0, "message": "0", "data": []}, id="data-not-object"),
        ],
    )
    def test_envelope_field_types_fail_closed(
        self, contract: BilibiliOpenApiContract, payload: object
    ) -> None:
        with pytest.raises(InvalidBilibiliOpenApiMessage):
            parse_transfer_ack(contract, payload)

    @pytest.mark.parametrize(
        "parser",
        [
            parse_token_grant,
            parse_token_refresh,
            parse_upload_init,
            parse_cover_upload,
            parse_archive_add,
            parse_archive_view,
        ],
    )
    def test_success_without_data_is_rejected_for_data_parsers(
        self,
        contract: BilibiliOpenApiContract,
        parser: Callable[[BilibiliOpenApiContract, object], object],
    ) -> None:
        with pytest.raises(InvalidBilibiliOpenApiMessage):
            parser(contract, {"code": 0, "message": "0"})

    def test_transfer_ack_with_data_is_rejected(self, contract: BilibiliOpenApiContract) -> None:
        with pytest.raises(InvalidBilibiliOpenApiMessage):
            parse_transfer_ack(contract, {"code": 0, "message": "0", "data": {}})

    def _view_payload(self) -> dict[str, Any]:
        fixture_path = FIXTURE_ROOT / "response-archive-view-valid.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        payload: dict[str, Any] = json.loads(json.dumps(fixture["payload"]))
        return payload

    def test_archive_view_bad_resource_id_is_rejected(
        self, contract: BilibiliOpenApiContract
    ) -> None:
        payload = self._view_payload()
        payload["data"]["resource_id"] = "av170001xxxx"
        with pytest.raises(InvalidBilibiliOpenApiMessage):
            parse_archive_view(contract, payload)

    def test_archive_view_addit_info_shape_is_rejected(
        self, contract: BilibiliOpenApiContract
    ) -> None:
        payload = self._view_payload()
        payload["data"]["addit_info"] = {"state": 0}
        with pytest.raises(InvalidBilibiliOpenApiMessage):
            parse_archive_view(contract, payload)
        payload["data"]["addit_info"] = "open"
        with pytest.raises(InvalidBilibiliOpenApiMessage):
            parse_archive_view(contract, payload)

    def test_archive_view_state_desc_type_is_rejected(
        self, contract: BilibiliOpenApiContract
    ) -> None:
        payload = self._view_payload()
        payload["data"]["addit_info"]["state_desc"] = 0
        with pytest.raises(InvalidBilibiliOpenApiMessage):
            parse_archive_view(contract, payload)


class TestRequestValidators:
    def test_plan_upload_parts_shapes_equal_chunks_except_last(
        self, contract: BilibiliOpenApiContract
    ) -> None:
        parts = plan_upload_parts(contract, 20 * 1024 * 1024)
        assert parts == (8 * 1024 * 1024, 8 * 1024 * 1024, 4 * 1024 * 1024)

    def test_plan_upload_parts_exact_multiple_has_no_tail(
        self, contract: BilibiliOpenApiContract
    ) -> None:
        parts = plan_upload_parts(contract, 16 * 1024 * 1024)
        assert parts == (8 * 1024 * 1024, 8 * 1024 * 1024)

    @pytest.mark.parametrize("size", [0, -1, 4 * 1024 * 1024 * 1024 + 1, "8", None])
    def test_plan_upload_parts_rejects_out_of_range_sizes(
        self, contract: BilibiliOpenApiContract, size: object
    ) -> None:
        with pytest.raises(InvalidBilibiliOpenApiMessage):
            plan_upload_parts(contract, size)

    def test_title_boundary_is_eighty_characters(self, contract: BilibiliOpenApiContract) -> None:
        validate_archive_submission(contract, title="标" * 80, tid=21, tag="科技", copyright_=1)
        with pytest.raises(InvalidBilibiliOpenApiMessage):
            validate_archive_submission(contract, title="标" * 81, tid=21, tag="科技", copyright_=1)

    def test_reprint_requires_source_and_original_forbids_it(
        self, contract: BilibiliOpenApiContract
    ) -> None:
        validate_archive_submission(
            contract,
            title="转载样例",
            tid=21,
            tag="科技",
            copyright_=2,
            source="https://example.com/origin",
        )
        with pytest.raises(InvalidBilibiliOpenApiMessage):
            validate_archive_submission(
                contract, title="转载样例", tid=21, tag="科技", copyright_=2
            )
        with pytest.raises(InvalidBilibiliOpenApiMessage):
            validate_archive_submission(
                contract,
                title="原创样例",
                tid=21,
                tag="科技",
                copyright_=1,
                source="https://example.com",
            )

    def test_upload_init_request_rejects_untrusted_file_names(
        self, contract: BilibiliOpenApiContract
    ) -> None:
        validate_upload_init_request(contract, file_name="demo.mp4", upload_type="0")
        for name in ("../evil.mp4", "a/b.mp4", "a\\b.mp4", ".mp4", "demo.", "demo", "demo.m p4"):
            with pytest.raises(InvalidBilibiliOpenApiMessage):
                validate_upload_init_request(contract, file_name=name, upload_type="0")

    def test_non_string_submission_fields_are_rejected(
        self, contract: BilibiliOpenApiContract
    ) -> None:
        with pytest.raises(InvalidBilibiliOpenApiMessage):
            validate_archive_submission(contract, title=123, tid=21, tag="科技", copyright_=1)
        with pytest.raises(InvalidBilibiliOpenApiMessage):
            validate_archive_submission(contract, title="标题", tid=21, tag=123, copyright_=1)
        with pytest.raises(InvalidBilibiliOpenApiMessage):
            validate_archive_submission(
                contract, title="标题", tid=21, tag="科技", copyright_=1, description=123
            )

    def test_control_characters_are_rejected_in_text_fields(
        self, contract: BilibiliOpenApiContract
    ) -> None:
        with pytest.raises(InvalidBilibiliOpenApiMessage):
            validate_archive_submission(
                contract, title="标题\x00隐患", tid=21, tag="科技", copyright_=1
            )

    def test_part_number_boundaries(self, contract: BilibiliOpenApiContract) -> None:
        validate_part_number(contract, 1)
        validate_part_number(contract, 512)
        for value in (0, 513, True, "1", None):
            with pytest.raises(InvalidBilibiliOpenApiMessage):
                validate_part_number(contract, value)


def _judge_response(contract: BilibiliOpenApiContract, operation: str, payload: object) -> object:
    parsers: dict[str, Callable[[BilibiliOpenApiContract, object], object]] = {
        "oauth_token_grant": parse_token_grant,
        "oauth_token_refresh": parse_token_refresh,
        "upload_init": parse_upload_init,
        "part_upload_ack": parse_transfer_ack,
        "upload_complete_ack": parse_transfer_ack,
        "cover_upload": parse_cover_upload,
        "archive_add": parse_archive_add,
        "archive_view": parse_archive_view,
        "archive_viewlist": parse_archive_viewlist,
        "archive_notification": parse_archive_status_notification,
        "webhook_verification": parse_webhook_verification,
    }
    return parsers[operation](contract, payload)


def _judge_request(
    contract: BilibiliOpenApiContract, operation: str, payload: dict[str, Any]
) -> None:
    if operation == "upload_init_request":
        validate_upload_init_request(
            contract,
            file_name=payload["file_name"],
            upload_type=payload["upload_type"],
        )
    elif operation == "part_number":
        validate_part_number(contract, payload["part_number"])
    elif operation == "chunk_plan":
        plan_upload_parts(contract, payload["total_size_bytes"])
    elif operation == "archive_submission":
        validate_archive_submission(
            contract,
            title=payload["title"],
            tid=payload["tid"],
            tag=payload["tag"],
            copyright_=payload["copyright"],
            description=payload["description"],
            source=payload["source"],
            no_reprint=payload["no_reprint"],
            cover_url=payload["cover_url"],
        )
    else:  # pragma: no cover - fixture inventory drift guard
        pytest.fail(f"unknown request operation: {operation}")


SUCCESS_TYPES = {
    "oauth_token_grant": TokenGrant,
    "oauth_token_refresh": TokenRefresh,
    "upload_init": UploadSession,
    "part_upload_ack": type(None),
    "upload_complete_ack": type(None),
    "cover_upload": CoverUploadResult,
    "archive_add": ArchiveSubmissionReceipt,
    "archive_view": ArchiveStatusSnapshot,
    "archive_viewlist": ArchiveListPage,
    "archive_notification": ArchiveStatusNotification,
    "webhook_verification": int,
}


class TestFixtureReplay:
    def test_fixture_inventory_is_meaningful(self) -> None:
        assert len(FIXTURES) >= 40
        expectations = {fixture["expected"] for fixture in FIXTURES}
        assert expectations == {
            "success",
            "malformed",
            "platform_rejection",
            "valid",
            "invalid",
        }
        response_operations = {
            fixture["operation"] for fixture in FIXTURES if fixture["direction"] == "response"
        }
        assert response_operations == set(SUCCESS_TYPES)

    @pytest.mark.parametrize(
        "fixture",
        FIXTURES,
        ids=[str(fixture["fixture"]) for fixture in FIXTURES],
    )
    def test_fixture_judgement_matches_expectation(
        self, contract: BilibiliOpenApiContract, fixture: dict[str, Any]
    ) -> None:
        expected = fixture["expected"]
        operation = fixture["operation"]
        payload = fixture["payload"]
        if fixture["direction"] == "response":
            if expected == "success":
                result = _judge_response(contract, operation, payload)
                assert isinstance(result, SUCCESS_TYPES[operation])
            elif expected == "platform_rejection":
                result = _judge_response(contract, operation, payload)
                assert isinstance(result, BilibiliPlatformRejection)
                assert result.category.value == fixture["expected_category"]
                assert result.failure_code.value == fixture["expected_failure_code"]
            else:
                assert expected == "malformed"
                with pytest.raises(InvalidBilibiliOpenApiMessage):
                    _judge_response(contract, operation, payload)
        else:
            if expected == "valid":
                _judge_request(contract, operation, payload)
            else:
                assert expected == "invalid"
                with pytest.raises(InvalidBilibiliOpenApiMessage):
                    _judge_request(contract, operation, payload)

    def test_fixtures_never_contain_real_credentials(self) -> None:
        def walk(value: object) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {"access_token", "refresh_token", "upload_token"} and isinstance(
                        item, str
                    ):
                        assert item.startswith("fixture-")
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        for fixture in FIXTURES:
            walk(fixture["payload"])
