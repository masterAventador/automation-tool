"""VE-06: contract-level parsing and signature checks for Aliyun IMS callbacks.

The local Control Plane has no public inbound endpoint, so callbacks are a
contract + fixture layer only: the official `ProduceMediaComplete` message is
parsed into an adapter event and the documented `X-ICE-SIGNATURE` scheme is
verified as pure functions. Polling stays the primary reconciliation path.
"""

import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest
from test_aliyun_ims_editing_reconciliation import (
    CONTRACT_PATH,
    _dispatched_intent,
    _reconciler,
)

from automation_tool.control_plane.domain.aliyun_ims_editing_callback import (
    CALLBACK_TIMESTAMP_TOLERANCE_SECONDS,
    ICE_CALLBACK_SIGNATURE_HEADER,
    ICE_CALLBACK_TIMESTAMP_HEADER,
    PRODUCE_MEDIA_COMPLETE_EVENT_TYPE,
    AliyunProduceMediaCompleteEvent,
    InvalidAliyunImsEditingCallback,
    compute_ice_callback_signature,
    parse_produce_media_complete_event,
    verify_ice_callback,
)
from automation_tool.control_plane.domain.aliyun_ims_editing_staging import (
    AliyunImsEditingStagingContract,
    load_aliyun_ims_editing_staging_contract,
)
from automation_tool.control_plane.domain.video_editing import (
    EditingFailureCode,
    EditingJobId,
    EditingJobStatus,
)
from automation_tool.control_plane.domain.video_editing_provider import (
    EditingProviderErrorCode,
    EditingProviderFailure,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SUCCESS_FIXTURE = FIXTURES / "aliyun-ims-produce-media-complete-success.v1.json"
FAIL_FIXTURE = FIXTURES / "aliyun-ims-produce-media-complete-fail.v1.json"

CALLBACK_URL = "https://callback.example.invalid/automation-tool/editing"
TIMESTAMP = "1786000000"
SECRET = "RotateMe123"
VENDOR_JOB_ID = "46c446e2420348e0950e4d7876acc6fb"
JOB_ID = EditingJobId(UUID("00000000-0000-4000-8000-0000000000cc"))


@pytest.fixture(scope="module")
def contract() -> AliyunImsEditingStagingContract:
    return load_aliyun_ims_editing_staging_contract(CONTRACT_PATH)


def _expected_signature(secret: str) -> str:
    content = f"{CALLBACK_URL}|{TIMESTAMP}|{secret}"
    return hashlib.md5(content.encode("utf-8")).hexdigest()


class TestSignatureContract:
    def test_header_names_match_official_documentation(self) -> None:
        assert ICE_CALLBACK_TIMESTAMP_HEADER == "X-ICE-TIMESTAMP"
        assert ICE_CALLBACK_SIGNATURE_HEADER == "X-ICE-SIGNATURE"
        assert CALLBACK_TIMESTAMP_TOLERANCE_SECONDS == 300

    def test_signature_is_md5_of_url_timestamp_and_secret(self) -> None:
        signature = compute_ice_callback_signature(
            callback_url=CALLBACK_URL, timestamp=TIMESTAMP, secret=SECRET
        )
        assert signature == _expected_signature(SECRET)

    def test_valid_signature_within_tolerance_is_accepted(self) -> None:
        assert verify_ice_callback(
            callback_url=CALLBACK_URL,
            timestamp=TIMESTAMP,
            signature=_expected_signature(SECRET),
            secrets=(SECRET,),
            now_unix_seconds=int(TIMESTAMP) + 200,
        )

    def test_tampered_signature_is_rejected(self) -> None:
        tampered = _expected_signature(SECRET)[:-1] + ("0" if SECRET[-1] != "0" else "1")
        assert not verify_ice_callback(
            callback_url=CALLBACK_URL,
            timestamp=TIMESTAMP,
            signature=tampered,
            secrets=(SECRET,),
            now_unix_seconds=int(TIMESTAMP),
        )

    def test_wrong_secret_is_rejected(self) -> None:
        assert not verify_ice_callback(
            callback_url=CALLBACK_URL,
            timestamp=TIMESTAMP,
            signature=_expected_signature("OtherSecret9"),
            secrets=(SECRET,),
            now_unix_seconds=int(TIMESTAMP),
        )

    def test_rotation_accepts_old_or_new_secret(self) -> None:
        old, new = SECRET, "NextSecret456"
        for accepted in (old, new):
            assert verify_ice_callback(
                callback_url=CALLBACK_URL,
                timestamp=TIMESTAMP,
                signature=_expected_signature(accepted),
                secrets=(old, new),
                now_unix_seconds=int(TIMESTAMP),
            )

    @pytest.mark.parametrize("skew", [301, -301, 100_000])
    def test_stale_or_future_timestamp_is_rejected(self, skew: int) -> None:
        assert not verify_ice_callback(
            callback_url=CALLBACK_URL,
            timestamp=TIMESTAMP,
            signature=_expected_signature(SECRET),
            secrets=(SECRET,),
            now_unix_seconds=int(TIMESTAMP) + skew,
        )

    @pytest.mark.parametrize("timestamp", ["", "not-a-number", "12.5", "-5"])
    def test_malformed_timestamp_is_rejected(self, timestamp: str) -> None:
        assert not verify_ice_callback(
            callback_url=CALLBACK_URL,
            timestamp=timestamp,
            signature=_expected_signature(SECRET),
            secrets=(SECRET,),
            now_unix_seconds=int(TIMESTAMP),
        )

    def test_empty_secret_set_never_verifies(self) -> None:
        assert not verify_ice_callback(
            callback_url=CALLBACK_URL,
            timestamp=TIMESTAMP,
            signature=_expected_signature(SECRET),
            secrets=(),
            now_unix_seconds=int(TIMESTAMP),
        )


class TestProduceMediaCompleteParsing:
    def test_success_fixture_parses_into_adapter_event(self) -> None:
        event = parse_produce_media_complete_event(SUCCESS_FIXTURE.read_text(encoding="utf-8"))
        assert isinstance(event, AliyunProduceMediaCompleteEvent)
        assert event.vendor_job_id == VENDOR_JOB_ID
        assert event.status_token == "Success"

    def test_fail_fixture_parses_into_adapter_event(self) -> None:
        event = parse_produce_media_complete_event(FAIL_FIXTURE.read_text(encoding="utf-8"))
        assert event.vendor_job_id == VENDOR_JOB_ID
        assert event.status_token == "Fail"

    def test_event_type_is_the_documented_constant(self) -> None:
        assert PRODUCE_MEDIA_COMPLETE_EVENT_TYPE == "ProduceMediaComplete"

    def test_other_event_type_is_rejected(self) -> None:
        document = json.loads(SUCCESS_FIXTURE.read_text(encoding="utf-8"))
        document["EventType"] = "SmartJobComplete"
        with pytest.raises(InvalidAliyunImsEditingCallback):
            parse_produce_media_complete_event(json.dumps(document))

    def test_missing_job_id_is_rejected(self) -> None:
        document = json.loads(SUCCESS_FIXTURE.read_text(encoding="utf-8"))
        del document["MessageBody"]["JobId"]
        with pytest.raises(InvalidAliyunImsEditingCallback):
            parse_produce_media_complete_event(json.dumps(document))

    @pytest.mark.parametrize(
        "payload",
        ["", "not json", "[]", '{"EventType": "ProduceMediaComplete"}'],
    )
    def test_malformed_payload_is_rejected(self, payload: str) -> None:
        with pytest.raises(InvalidAliyunImsEditingCallback):
            parse_produce_media_complete_event(payload)

    def test_unknown_status_token_is_carried_not_guessed(self) -> None:
        document = json.loads(SUCCESS_FIXTURE.read_text(encoding="utf-8"))
        document["MessageBody"]["Status"] = "Archived"
        event = parse_produce_media_complete_event(json.dumps(document))
        assert event.status_token == "Archived"


@pytest.mark.asyncio
class TestCallbackApplication:
    async def test_success_event_confirms_job_success(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        reconciler, _, store, registrar, _ = await _reconciler(
            contract,
            intents=[_dispatched_intent(status=EditingJobStatus.RUNNING)],
            outcomes=[],
        )
        event = parse_produce_media_complete_event(SUCCESS_FIXTURE.read_text(encoding="utf-8"))
        snapshot = await reconciler.apply_callback_event(event)
        assert snapshot.status is EditingJobStatus.SUCCEEDED
        assert registrar.calls == [JOB_ID]
        stored = await store.load(JOB_ID)
        assert stored is not None and stored.status is EditingJobStatus.SUCCEEDED

    async def test_fail_event_confirms_job_failure(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        reconciler, _, _, _, _ = await _reconciler(
            contract,
            intents=[_dispatched_intent()],
            outcomes=[],
        )
        event = parse_produce_media_complete_event(FAIL_FIXTURE.read_text(encoding="utf-8"))
        snapshot = await reconciler.apply_callback_event(event)
        assert snapshot.status is EditingJobStatus.FAILED
        assert snapshot.failure_code is EditingFailureCode.EDITING_FAILED

    async def test_duplicate_and_out_of_order_events_never_regress_terminal(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        reconciler, _, _, registrar, _ = await _reconciler(
            contract,
            intents=[_dispatched_intent(status=EditingJobStatus.RUNNING)],
            outcomes=[],
        )
        success = parse_produce_media_complete_event(SUCCESS_FIXTURE.read_text(encoding="utf-8"))
        fail = parse_produce_media_complete_event(FAIL_FIXTURE.read_text(encoding="utf-8"))
        first = await reconciler.apply_callback_event(success)
        replay = await reconciler.apply_callback_event(success)
        conflicting = await reconciler.apply_callback_event(fail)
        assert first.status is EditingJobStatus.SUCCEEDED
        assert replay == first
        assert conflicting == first
        assert registrar.calls == [JOB_ID]

    async def test_event_for_unknown_vendor_job_is_not_found(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        reconciler, _, _, _, _ = await _reconciler(
            contract, intents=[], outcomes=[]
        )
        event = parse_produce_media_complete_event(SUCCESS_FIXTURE.read_text(encoding="utf-8"))
        with pytest.raises(EditingProviderFailure) as error:
            await reconciler.apply_callback_event(event)
        assert error.value.code is EditingProviderErrorCode.NOT_FOUND
