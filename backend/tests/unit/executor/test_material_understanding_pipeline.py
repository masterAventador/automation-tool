"""LE-13 T2: consume LE-08 artifacts and parse one closed understanding result."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from automation_tool.executor.adaptive_frame_extraction import AdaptiveFrameArtifact
from automation_tool.executor.material_understanding import (
    MaterialUnderstandingOptions,
    MaterialUnderstandingRejected,
    MaterialUnderstandingReply,
    MaterialUnderstandingResult,
    MaterialUnderstandingShot,
    understand_material_artifacts,
)

JPEG_ONE = b"\xff\xd8\xff\xe0first-frame\xff\xd9"
JPEG_TWO = b"\xff\xd8\xff\xe0second-frame\xff\xd9"


class RecordingAdapter:
    def __init__(self, content: str, *, finish_reason: str = "stop") -> None:
        self.content = content
        self.finish_reason = finish_reason
        self.calls: list[object] = []

    def understand(
        self,
        frames: object,
        *,
        options: MaterialUnderstandingOptions,
    ) -> MaterialUnderstandingReply:
        self.calls.append((frames, options))
        return MaterialUnderstandingReply(
            request_id="req-understanding-t2",
            content=self.content,
            finish_reason=self.finish_reason,
        )


def _persist_artifacts(output: Path) -> tuple[AdaptiveFrameArtifact, ...]:
    artifacts: list[AdaptiveFrameArtifact] = []
    for index, (timestamp_ms, is_scene_cut, payload) in enumerate(
        ((0, True, JPEG_ONE), (4_000, False, JPEG_TWO)),
        start=1,
    ):
        filename = f"frame-{index:06d}.jpg"
        path = output / filename
        path.write_bytes(payload)
        path.chmod(0o600)
        artifacts.append(
            AdaptiveFrameArtifact(
                filename=filename,
                timestamp_ms=timestamp_ms,
                is_scene_cut=is_scene_cut,
                byte_size=len(payload),
            )
        )
    return tuple(artifacts)


def _content(**overrides: object) -> str:
    document: dict[str, object] = {
        "description": "一段从室内产品展示切换到户外使用的短片",
        "tags": ["产品", "户外"],
        "shots": [
            {"startMs": 0, "endMs": 4_000, "description": "室内展示"},
            {"startMs": 4_000, "endMs": 9_000, "description": "户外使用"},
        ],
    }
    document.update(overrides)
    return json.dumps(document, ensure_ascii=False)


def test_real_le08_artifacts_are_read_and_converted_to_a_closed_result(
    tmp_path: Path,
) -> None:
    artifacts = _persist_artifacts(tmp_path)
    adapter = RecordingAdapter(_content())

    result = understand_material_artifacts(
        adapter,
        output_directory=tmp_path,
        artifacts=artifacts,
        duration_ms=9_000,
        options=MaterialUnderstandingOptions(),
    )

    assert result == MaterialUnderstandingResult(
        request_id="req-understanding-t2",
        description="一段从室内产品展示切换到户外使用的短片",
        tags=("产品", "户外"),
        shots=(
            MaterialUnderstandingShot(
                start_ms=0,
                end_ms=4_000,
                description="室内展示",
            ),
            MaterialUnderstandingShot(
                start_ms=4_000,
                end_ms=9_000,
                description="户外使用",
            ),
        ),
    )
    assert result.shot_boundaries_ms == (0, 4_000)
    assert len(adapter.calls) == 1
    frames, _options = adapter.calls[0]
    assert isinstance(frames, tuple)
    assert [(frame.timestamp_ms, frame.is_scene_cut) for frame in frames] == [
        (0, True),
        (4_000, False),
    ]
    assert [frame.jpeg_bytes for frame in frames] == [JPEG_ONE, JPEG_TWO]


def test_static_image_accepts_the_models_zero_length_shot(
    tmp_path: Path,
) -> None:
    path = tmp_path / "frame-000001.jpg"
    path.write_bytes(JPEG_ONE)
    path.chmod(0o600)
    artifacts = (
        AdaptiveFrameArtifact(
            filename=path.name,
            timestamp_ms=0,
            is_scene_cut=True,
            byte_size=len(JPEG_ONE),
        ),
    )
    adapter = RecordingAdapter(
        _content(
            shots=[
                {
                    "startMs": 0,
                    "endMs": 0,
                    "description": "静态产品图",
                }
            ]
        )
    )

    result = understand_material_artifacts(
        adapter,
        output_directory=tmp_path,
        artifacts=artifacts,
        duration_ms=1,
        options=MaterialUnderstandingOptions(),
        static_image=True,
    )

    assert result.shots == (MaterialUnderstandingShot(0, 1, "静态产品图"),)


def test_closed_result_cannot_be_constructed_with_overlapping_shots() -> None:
    with pytest.raises(MaterialUnderstandingRejected):
        MaterialUnderstandingResult(
            request_id="req-overlap",
            description="有效描述",
            tags=(),
            shots=(
                MaterialUnderstandingShot(0, 5_000, "第一段"),
                MaterialUnderstandingShot(4_000, 9_000, "重叠段"),
            ),
        )


@pytest.mark.parametrize("request_id", ["req\nforged", "req\tforged"])
def test_closed_result_rejects_multiline_request_ids(request_id: str) -> None:
    with pytest.raises(MaterialUnderstandingRejected):
        MaterialUnderstandingResult(
            request_id=request_id,
            description="有效描述",
            tags=(),
            shots=(MaterialUnderstandingShot(0, 9_000, "全片"),),
        )


def test_structured_result_recursion_overflow_is_a_fixed_rejection(
    tmp_path: Path,
) -> None:
    artifacts = _persist_artifacts(tmp_path)
    deeply_nested_shots = "[" * 10_000 + "0" + "]" * 10_000
    content = '{"description":"有效","tags":[],"shots":' + deeply_nested_shots + "}"

    with pytest.raises(
        MaterialUnderstandingRejected,
        match="material understanding request rejected",
    ):
        understand_material_artifacts(
            RecordingAdapter(content),
            output_directory=tmp_path,
            artifacts=artifacts,
            duration_ms=9_000,
            options=MaterialUnderstandingOptions(),
        )


@pytest.mark.parametrize("description", ["", " \t\n "])
def test_empty_material_description_never_produces_a_partial_result(
    tmp_path: Path,
    description: str,
) -> None:
    artifacts = _persist_artifacts(tmp_path)
    adapter = RecordingAdapter(_content(description=description))

    with pytest.raises(
        MaterialUnderstandingRejected,
        match="material understanding request rejected",
    ):
        understand_material_artifacts(
            adapter,
            output_directory=tmp_path,
            artifacts=artifacts,
            duration_ms=9_000,
            options=MaterialUnderstandingOptions(),
        )

    assert len(adapter.calls) == 1


@pytest.mark.parametrize("finish_reason", ["length", "content_filter", "future_reason"])
def test_incomplete_or_unknown_finish_reasons_never_parse_partial_content(
    tmp_path: Path,
    finish_reason: str,
) -> None:
    artifacts = _persist_artifacts(tmp_path)
    adapter = RecordingAdapter(_content(), finish_reason=finish_reason)

    with pytest.raises(
        MaterialUnderstandingRejected,
        match="material understanding request rejected",
    ):
        understand_material_artifacts(
            adapter,
            output_directory=tmp_path,
            artifacts=artifacts,
            duration_ms=9_000,
            options=MaterialUnderstandingOptions(),
        )

    assert len(adapter.calls) == 1


@pytest.mark.parametrize(
    "content",
    [
        '{"description":"有效","description":"重复","tags":[],"shots":[]}',
        (
            '{"description":"有效","tags":[],"shots":['
            '{"startMs":0,"startMs":1,"endMs":9000,"description":"重复"}]}'
        ),
        json.dumps(
            {
                "description": "有效",
                "tags": [],
                "shots": [{"startMs": 0, "endMs": 9_000, "description": "全片"}],
                "future": True,
            }
        ),
        _content(
            shots=[
                {
                    "startMs": 0,
                    "endMs": 9_000,
                    "description": "全片",
                    "future": True,
                }
            ]
        ),
        _content(shots=[]),
        _content(
            shots=[
                {"startMs": 0, "endMs": 5_000, "description": "第一段"},
                {"startMs": 4_000, "endMs": 9_000, "description": "重叠"},
            ]
        ),
        _content(
            shots=[
                {"startMs": 0, "endMs": 9_001, "description": "越过素材时长"},
            ]
        ),
        _content(
            shots=[
                {"startMs": False, "endMs": 9_000, "description": "布尔不是毫秒"},
            ]
        ),
    ],
)
def test_structured_result_rejects_open_or_invalid_shot_shapes(
    tmp_path: Path,
    content: str,
) -> None:
    artifacts = _persist_artifacts(tmp_path)

    with pytest.raises(
        MaterialUnderstandingRejected,
        match="material understanding request rejected",
    ):
        understand_material_artifacts(
            RecordingAdapter(content),
            output_directory=tmp_path,
            artifacts=artifacts,
            duration_ms=9_000,
            options=MaterialUnderstandingOptions(),
        )


def test_artifact_drift_is_rejected_before_the_model_is_called(tmp_path: Path) -> None:
    artifacts = _persist_artifacts(tmp_path)
    (tmp_path / artifacts[0].filename).chmod(0o644)
    adapter = RecordingAdapter(_content())

    with pytest.raises(MaterialUnderstandingRejected):
        understand_material_artifacts(
            adapter,
            output_directory=tmp_path,
            artifacts=artifacts,
            duration_ms=9_000,
            options=MaterialUnderstandingOptions(),
        )

    assert adapter.calls == []


def test_artifact_metadata_cannot_name_an_uncontrolled_file(tmp_path: Path) -> None:
    _persist_artifacts(tmp_path)
    adapter = RecordingAdapter(_content())
    malicious = (
        AdaptiveFrameArtifact(
            filename="../private.jpg",
            timestamp_ms=0,
            is_scene_cut=True,
            byte_size=len(JPEG_ONE),
        ),
    )

    with pytest.raises(MaterialUnderstandingRejected):
        understand_material_artifacts(
            adapter,
            output_directory=tmp_path,
            artifacts=malicious,
            duration_ms=9_000,
            options=MaterialUnderstandingOptions(),
        )

    assert adapter.calls == []


@pytest.mark.parametrize("drift", ["hard-link", "symlink", "wrong-size", "not-jpeg"])
def test_artifact_identity_size_and_jpeg_envelope_are_rechecked_before_model_call(
    tmp_path: Path,
    drift: str,
) -> None:
    artifacts = _persist_artifacts(tmp_path)
    first = tmp_path / artifacts[0].filename
    if drift == "hard-link":
        os.link(first, tmp_path / "second-name.jpg")
    elif drift == "symlink":
        outside = tmp_path / "outside.jpg"
        outside.write_bytes(JPEG_ONE)
        outside.chmod(0o600)
        first.unlink()
        first.symlink_to(outside)
    elif drift == "wrong-size":
        artifacts = (replace(artifacts[0], byte_size=len(JPEG_ONE) + 1), *artifacts[1:])
    else:
        first.write_bytes(b"x" * len(JPEG_ONE))
        first.chmod(0o600)
    adapter = RecordingAdapter(_content())

    with pytest.raises(MaterialUnderstandingRejected):
        understand_material_artifacts(
            adapter,
            output_directory=tmp_path,
            artifacts=artifacts,
            duration_ms=9_000,
            options=MaterialUnderstandingOptions(),
        )

    assert adapter.calls == []


@pytest.mark.parametrize(
    ("duration_ms", "artifacts"),
    [
        (0, ()),
        (9_000, ()),
    ],
)
def test_empty_or_durationless_artifact_batches_are_rejected(
    tmp_path: Path,
    duration_ms: int,
    artifacts: tuple[AdaptiveFrameArtifact, ...],
) -> None:
    adapter = RecordingAdapter(_content())

    with pytest.raises(MaterialUnderstandingRejected):
        understand_material_artifacts(
            adapter,
            output_directory=tmp_path,
            artifacts=artifacts,
            duration_ms=duration_ms,
            options=MaterialUnderstandingOptions(),
        )

    assert adapter.calls == []
