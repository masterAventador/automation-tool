"""PB-04: archive status query, viewlist paging, and webhook notification contract."""

import json
from pathlib import Path
from typing import Any

import pytest

from automation_tool.control_plane.domain.bilibili_open_api import (
    ArchiveListPage,
    ArchiveStatusNotification,
    ArchiveStatusSnapshot,
    BilibiliOpenApiContract,
    BilibiliPlatformRejection,
    InvalidBilibiliOpenApiContract,
    InvalidBilibiliOpenApiMessage,
    compute_webhook_signature,
    load_bilibili_open_api_contract,
    parse_archive_status_notification,
    parse_archive_view,
    parse_archive_viewlist,
    parse_webhook_verification,
    verify_webhook_signature,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts/publishing/bilibili-open-api.v1.json"
FIXTURE_ROOT = REPOSITORY_ROOT / "contracts/publishing/fixtures/bilibili-open-api-v1"


@pytest.fixture(scope="module")
def contract() -> BilibiliOpenApiContract:
    return load_bilibili_open_api_contract(CONTRACT_PATH)


def _fixture_payload(name: str) -> Any:
    document = json.loads((FIXTURE_ROOT / f"{name}.json").read_text(encoding="utf-8"))
    return json.loads(json.dumps(document["payload"]))


def _contract_document() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    return document


def _write(tmp_path: Path, document: dict[str, Any]) -> Path:
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


class TestArchiveStateVocabulary:
    def test_contract_locks_documented_archive_states(
        self, contract: BilibiliOpenApiContract
    ) -> None:
        assert contract.archive_state_open == 0
        assert contract.archive_state_rejected == -2

    def test_missing_documented_state_is_rejected(self, tmp_path: Path) -> None:
        document = _contract_document()
        del document["query"]["documented_states"]["-2"]
        with pytest.raises(InvalidBilibiliOpenApiContract):
            load_bilibili_open_api_contract(_write(tmp_path, document))


class TestArchiveViewPendingState:
    def test_pending_review_snapshot_allows_zero_publish_time(
        self, contract: BilibiliOpenApiContract
    ) -> None:
        payload = _fixture_payload("response-archive-view-pending-state")
        snapshot = parse_archive_view(contract, payload)
        assert isinstance(snapshot, ArchiveStatusSnapshot)
        assert snapshot.state not in {
            contract.archive_state_open,
            contract.archive_state_rejected,
        }
        assert snapshot.published_at_epoch_seconds == 0

    def test_negative_publish_time_is_still_rejected(
        self, contract: BilibiliOpenApiContract
    ) -> None:
        payload = _fixture_payload("response-archive-view-pending-state")
        payload["data"]["ptime"] = -1
        with pytest.raises(InvalidBilibiliOpenApiMessage):
            parse_archive_view(contract, payload)

    def test_zero_creation_time_is_still_rejected(self, contract: BilibiliOpenApiContract) -> None:
        payload = _fixture_payload("response-archive-view-pending-state")
        payload["data"]["ctime"] = 0
        with pytest.raises(InvalidBilibiliOpenApiMessage):
            parse_archive_view(contract, payload)


class TestArchiveViewlist:
    def test_valid_page_parses_every_archive_snapshot(
        self, contract: BilibiliOpenApiContract
    ) -> None:
        payload = _fixture_payload("response-archive-viewlist-valid")
        page = parse_archive_viewlist(contract, payload)
        assert isinstance(page, ArchiveListPage)
        assert page.page_number == 1
        assert page.page_size == 20
        assert page.total == 5
        assert len(page.items) == 2
        assert all(isinstance(item, ArchiveStatusSnapshot) for item in page.items)
        assert page.items[0].resource_id == "BV1MW421X7gM"

    def test_empty_page_is_valid(self, contract: BilibiliOpenApiContract) -> None:
        payload = _fixture_payload("response-archive-viewlist-empty")
        page = parse_archive_viewlist(contract, payload)
        assert isinstance(page, ArchiveListPage)
        assert page.items == ()
        assert page.total == 0

    def test_platform_rejection_is_classified(self, contract: BilibiliOpenApiContract) -> None:
        payload = _fixture_payload("response-archive-view-error-not-found")
        rejection = parse_archive_viewlist(contract, payload)
        assert isinstance(rejection, BilibiliPlatformRejection)
        assert rejection.code == 123004

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda data: data.pop("page"),
            lambda data: data.pop("list"),
            lambda data: data.update({"extra": 1}),
            lambda data: data["page"].pop("total"),
            lambda data: data["page"].update({"pn": 0}),
            lambda data: data["page"].update({"ps": 0}),
            lambda data: data["page"].update({"total": -1}),
            lambda data: data["page"].update({"total": "5"}),
            lambda data: data.update({"list": "archives"}),
            lambda data: data["list"].append("not-an-archive"),
            lambda data: data["list"][0].pop("addit_info"),
            lambda data: data["list"][0]["addit_info"].update({"state": "0"}),
        ],
    )
    def test_malformed_pages_fail_closed(
        self, contract: BilibiliOpenApiContract, mutate: Any
    ) -> None:
        payload = _fixture_payload("response-archive-viewlist-valid")
        mutate(payload["data"])
        with pytest.raises(InvalidBilibiliOpenApiMessage):
            parse_archive_viewlist(contract, payload)

    def test_missing_data_is_rejected(self, contract: BilibiliOpenApiContract) -> None:
        with pytest.raises(InvalidBilibiliOpenApiMessage):
            parse_archive_viewlist(contract, {"code": 0, "message": "0"})


class TestWebhookNotificationContract:
    def test_contract_locks_the_webhook_transport(self, contract: BilibiliOpenApiContract) -> None:
        assert contract.notification_signature_header == "x-bilibili-signature"
        assert contract.notification_verification_event == "verify_webhooks"
        assert dict(contract.notification_archive_events) == {
            "video_open": 0,
            "video_fail": -2,
        }
        assert contract.notification_connection_timeout_seconds == 5
        assert contract.notification_max_delivery_attempts == 3

    def test_missing_notifications_section_is_rejected(self, tmp_path: Path) -> None:
        document = _contract_document()
        del document["notifications"]
        with pytest.raises(InvalidBilibiliOpenApiContract):
            load_bilibili_open_api_contract(_write(tmp_path, document))

    def test_video_open_notification_parses(self, contract: BilibiliOpenApiContract) -> None:
        payload = _fixture_payload("notification-video-open-valid")
        notification = parse_archive_status_notification(contract, payload)
        assert isinstance(notification, ArchiveStatusNotification)
        assert notification.event == "video_open"
        assert notification.resource_id == "BV1S9L90H082"
        assert notification.state == 0
        assert notification.state_desc == "开放浏览"

    def test_video_fail_notification_parses(self, contract: BilibiliOpenApiContract) -> None:
        payload = _fixture_payload("notification-video-fail-valid")
        notification = parse_archive_status_notification(contract, payload)
        assert isinstance(notification, ArchiveStatusNotification)
        assert notification.event == "video_fail"
        assert notification.state == -2

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda payload: payload.update({"event": "video_deleted"}),
            lambda payload: payload.update({"event": "verify_webhooks"}),
            lambda payload: payload.pop("timestamp"),
            lambda payload: payload.update({"timestamp": "2021/01/01"}),
            lambda payload: payload.update({"extra": 1}),
            lambda payload: payload["content"].pop("resource_id"),
            lambda payload: payload["content"].update({"resource_id": "av17000"}),
            lambda payload: payload["content"].update({"state": "0"}),
            lambda payload: payload["content"].update({"state": 1}),
            lambda payload: payload["content"].update({"openid": ""}),
            lambda payload: payload["content"].update({"extra": 1}),
            lambda payload: payload.update({"content": "gone"}),
        ],
    )
    def test_malformed_notifications_fail_closed(
        self, contract: BilibiliOpenApiContract, mutate: Any
    ) -> None:
        payload = _fixture_payload("notification-video-open-valid")
        mutate(payload)
        with pytest.raises(InvalidBilibiliOpenApiMessage):
            parse_archive_status_notification(contract, payload)

    def test_state_must_match_the_documented_event_state(
        self, contract: BilibiliOpenApiContract
    ) -> None:
        payload = _fixture_payload("notification-video-fail-valid")
        payload["content"]["state"] = 0
        with pytest.raises(InvalidBilibiliOpenApiMessage):
            parse_archive_status_notification(contract, payload)

    def test_verification_challenge_parses(self, contract: BilibiliOpenApiContract) -> None:
        payload = _fixture_payload("notification-verify-webhooks-valid")
        assert parse_webhook_verification(contract, payload) == 1371848576

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda payload: payload.update({"event": "video_open"}),
            lambda payload: payload["content"].update({"data": "1371848576"}),
            lambda payload: payload["content"].pop("data"),
            lambda payload: payload["content"].update({"extra": 1}),
            lambda payload: payload.pop("timestamp"),
        ],
    )
    def test_malformed_verification_fails_closed(
        self, contract: BilibiliOpenApiContract, mutate: Any
    ) -> None:
        payload = _fixture_payload("notification-verify-webhooks-valid")
        mutate(payload)
        with pytest.raises(InvalidBilibiliOpenApiMessage):
            parse_webhook_verification(contract, payload)


class TestWebhookSignature:
    def test_signature_is_sha1_of_secret_plus_body(self) -> None:
        # SHA1("secret" + "body") computed independently.
        assert (
            compute_webhook_signature("secret", b"body")
            == "16d335e9e89439586140f1ab58ccd5d60b998b92"
        )

    def test_verification_accepts_only_the_matching_signature(self) -> None:
        signature = compute_webhook_signature("app-secret", b'{"event":"video_open"}')
        assert verify_webhook_signature("app-secret", b'{"event":"video_open"}', signature)
        assert not verify_webhook_signature("app-secret", b'{"event":"video_fail"}', signature)
        assert not verify_webhook_signature("other-secret", b'{"event":"video_open"}', signature)
        assert not verify_webhook_signature("app-secret", b'{"event":"video_open"}', "")

    @pytest.mark.parametrize("secret", ["", 1, None])
    def test_invalid_secret_is_rejected(self, secret: Any) -> None:
        with pytest.raises(InvalidBilibiliOpenApiMessage):
            compute_webhook_signature(secret, b"body")

    def test_invalid_body_or_signature_types_are_rejected(self) -> None:
        with pytest.raises(InvalidBilibiliOpenApiMessage):
            compute_webhook_signature("secret", "body")
        assert verify_webhook_signature("secret", b"body", None) is False


class TestNotificationContractFailClosed:
    @pytest.mark.parametrize(
        "mutate",
        [
            lambda document: document["query"]["documented_states"].update({"01": "补零键"}),
            lambda document: document["query"]["documented_states"].update({"abc": "非整数键"}),
            lambda document: document["query"]["documented_states"].update({"0": 1}),
            lambda document: document["query"]["documented_states"].update({"-2": ""}),
            lambda document: document["notifications"].update({"archive_status_events": {"": 0}}),
            lambda document: document["notifications"].update(
                {"archive_status_events": {"video_open": "0"}}
            ),
            lambda document: document["notifications"].update({"archive_status_events": {}}),
            lambda document: document["notifications"].update({"verification_event": "video_open"}),
            lambda document: document["notifications"].update({"signature_header": ""}),
            lambda document: document["notifications"].update({"connection_timeout_seconds": 0}),
        ],
    )
    def test_invalid_notification_or_state_sections_are_rejected(
        self, tmp_path: Path, mutate: Any
    ) -> None:
        document = _contract_document()
        mutate(document)
        with pytest.raises(InvalidBilibiliOpenApiContract):
            load_bilibili_open_api_contract(_write(tmp_path, document))

    def test_non_string_notification_content_fields_are_rejected(
        self, contract: BilibiliOpenApiContract
    ) -> None:
        payload = _fixture_payload("notification-video-open-valid")
        payload["content"]["state_desc"] = 1
        with pytest.raises(InvalidBilibiliOpenApiMessage):
            parse_archive_status_notification(contract, payload)
