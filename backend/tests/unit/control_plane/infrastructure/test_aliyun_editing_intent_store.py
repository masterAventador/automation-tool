from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from automation_tool.control_plane.domain.aliyun_ims_editing_provider import (
    AliyunEditingIntent,
    AliyunEditingIntentState,
)
from automation_tool.control_plane.domain.video_editing import (
    EditingJobId,
    EditingJobStatus,
)
from automation_tool.control_plane.infrastructure.aliyun.editing_intent_store import (
    FileAliyunEditingIntentStore,
    InvalidAliyunEditingIntentStore,
)

JOB_ID = "00000000-0000-4000-8000-000000000203"


def _state_directory(tmp_path: Path) -> Path:
    directory = tmp_path / "state"
    directory.mkdir()
    directory.chmod(0o700)
    return directory


def _intent(
    state: AliyunEditingIntentState,
    *,
    vendor_job_id: str | None,
    status: EditingJobStatus,
) -> AliyunEditingIntent:
    return AliyunEditingIntent(
        editing_job_id=EditingJobId.parse(JOB_ID),
        request_hash="a" * 64,
        state=state,
        vendor_job_id=vendor_job_id,
        status=status,
        failure_code=None,
        output_artifact_ids=(),
    )


@pytest.mark.asyncio
async def test_intent_is_atomically_replaced_and_survives_reopen(tmp_path: Path) -> None:
    directory = _state_directory(tmp_path)
    store = FileAliyunEditingIntentStore(directory)
    prepared = _intent(
        AliyunEditingIntentState.PREPARED,
        vendor_job_id=None,
        status=EditingJobStatus.QUEUED,
    )
    await store.save(prepared)
    assert await store.load(EditingJobId.parse(JOB_ID)) == prepared

    dispatched = _intent(
        AliyunEditingIntentState.DISPATCHED,
        vendor_job_id="vendor-job-persisted",
        status=EditingJobStatus.RUNNING,
    )
    await store.save(dispatched)

    reopened = FileAliyunEditingIntentStore(directory)
    assert await reopened.load(EditingJobId.parse(JOB_ID)) == dispatched
    assert await reopened.load_all() == (dispatched,)
    assert await reopened.load_by_vendor_job_id("vendor-job-persisted") == dispatched
    assert {path.name for path in directory.iterdir()} == {"aliyun-editing-intent.checkpoint"}
    if os.name != "nt":
        mode = stat.S_IMODE((directory / "aliyun-editing-intent.checkpoint").stat().st_mode)
        assert mode == 0o600


@pytest.mark.asyncio
async def test_tampered_or_supplier_extended_intent_is_rejected(tmp_path: Path) -> None:
    directory = _state_directory(tmp_path)
    store = FileAliyunEditingIntentStore(directory)
    checkpoint = directory / "aliyun-editing-intent.checkpoint"
    checkpoint.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "editingJobId": JOB_ID,
                "requestHash": "a" * 64,
                "state": "dispatched",
                "vendorJobId": "vendor-job-persisted",
                "status": "running",
                "failureCode": None,
                "outputArtifactIds": [],
                "supplierExtension": "must-not-cross",
            }
        ),
        encoding="utf-8",
    )
    checkpoint.chmod(0o600)

    with pytest.raises(InvalidAliyunEditingIntentStore):
        await store.load(EditingJobId.parse(JOB_ID))
