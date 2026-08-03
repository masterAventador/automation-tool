"""Strict, path-free evidence validators for the LE-22 package journey."""

from __future__ import annotations

import math
import re
import struct
from collections.abc import Mapping
from dataclasses import dataclass

UUID_V4 = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
WORD = re.compile(r"[A-Z0-9]+")
CLIP_KEYS = {
    "clip_id",
    "start_ms",
    "duration_ms",
    "source_material_id",
    "source_in_ms",
    "source_out_ms",
    "text",
    "gain_db",
    "transition_in",
    "original_audio_mode",
}


@dataclass(frozen=True, slots=True)
class Le22DatabaseSummary:
    material_id: str
    artifact_id: str
    timeline_duration_ms: int
    source_window_ms: tuple[int, int]
    speech_segment_count: int


@dataclass(frozen=True, slots=True)
class Le22MediaSummary:
    video_frames: int
    duration_ms: int
    artifact_bytes: int


def _reject_database() -> None:
    raise RuntimeError("LE-22 database evidence is invalid") from None


def _object(value: object, keys: set[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != keys:
        _reject_database()
    return value


def _integer(value: object, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        _reject_database()
    return value


def _uuid(value: object) -> str:
    if type(value) is not str or UUID_V4.fullmatch(value) is None:
        _reject_database()
    return value


def _clip(value: object) -> dict[str, object]:
    return _object(value, CLIP_KEYS)


def _single_track(value: object, *, expected_kind: str) -> dict[str, object]:
    track = _object(value, {"track_id", "kind", "clips"})
    clips = track["clips"]
    if (
        track["track_id"] != expected_kind
        or track["kind"] != expected_kind
        or type(clips) is not list
        or len(clips) != 1
    ):
        _reject_database()
    return _clip(clips[0])


def validate_le22_database_evidence(
    source: Mapping[str, object],
    *,
    required_transcript_words: frozenset[str],
) -> Le22DatabaseSummary:
    document = _object(source, {"material", "timeline", "job"})
    material = _object(
        document["material"],
        {
            "materialId",
            "kind",
            "durationMs",
            "hasSpeech",
            "speechSegmentsMs",
            "speechTranscript",
        },
    )
    material_id = _uuid(material["materialId"])
    material_duration_ms = _integer(material["durationMs"], minimum=1)
    transcript = material["speechTranscript"]
    segments = material["speechSegmentsMs"]
    if (
        material["kind"] != "video"
        or material["hasSpeech"] is not True
        or type(transcript) is not str
        or not transcript.strip()
        or type(segments) is not list
        or not segments
        or not required_transcript_words
        or not required_transcript_words <= set(WORD.findall(transcript.upper()))
    ):
        _reject_database()

    validated_segments: list[tuple[int, int]] = []
    previous_end = 0
    for raw_segment in segments:
        if type(raw_segment) is not list or len(raw_segment) != 2:
            _reject_database()
        start_ms = _integer(raw_segment[0])
        end_ms = _integer(raw_segment[1], minimum=1)
        if (
            start_ms < previous_end
            or start_ms >= end_ms
            or end_ms > material_duration_ms
        ):
            _reject_database()
        validated_segments.append((start_ms, end_ms))
        previous_end = end_ms
    source_window = (validated_segments[0][0], validated_segments[-1][1])

    timeline = _object(document["timeline"], {"revision", "durationMs", "tracks"})
    timeline_duration_ms = _integer(timeline["durationMs"], minimum=1)
    tracks = timeline["tracks"]
    if (
        timeline["revision"] != 1
        or timeline_duration_ms != source_window[1] - source_window[0]
        or type(tracks) is not list
        or len(tracks) != 3
    ):
        _reject_database()
    visual = _single_track(tracks[0], expected_kind="visual")
    ambient = _single_track(tracks[1], expected_kind="ambient")
    caption = _single_track(tracks[2], expected_kind="caption")

    common = {
        "start_ms": 0,
        "duration_ms": timeline_duration_ms,
        "source_material_id": material_id,
        "source_in_ms": source_window[0],
        "source_out_ms": source_window[1],
        "text": None,
        "transition_in": None,
    }
    if (
        visual
        != {
            **common,
            "clip_id": "visual-0001",
            "gain_db": None,
            "original_audio_mode": None,
        }
        or ambient
        != {
            **common,
            "clip_id": "ambient-0001",
            "gain_db": 0.0,
            "original_audio_mode": "auto_duck",
        }
        or caption
        != {
            "clip_id": "caption-0001",
            "start_ms": 0,
            "duration_ms": timeline_duration_ms,
            "source_material_id": None,
            "source_in_ms": None,
            "source_out_ms": None,
            "text": transcript,
            "gain_db": None,
            "transition_in": None,
            "original_audio_mode": None,
        }
    ):
        _reject_database()

    job = _object(
        document["job"],
        {"status", "timelineRevision", "failureCode", "outputArtifactId"},
    )
    artifact_id = _uuid(job["outputArtifactId"])
    if (
        job["status"] != "succeeded"
        or job["timelineRevision"] != 1
        or job["failureCode"] is not None
    ):
        _reject_database()
    return Le22DatabaseSummary(
        material_id=material_id,
        artifact_id=artifact_id,
        timeline_duration_ms=timeline_duration_ms,
        source_window_ms=source_window,
        speech_segment_count=len(validated_segments),
    )


def validate_le22_ffprobe(
    source: Mapping[str, object],
    *,
    artifact_bytes: int,
    timeline_duration_ms: int,
) -> Le22MediaSummary:
    try:
        streams = source["streams"]
        format_document = source["format"]
        if (
            type(streams) is not list
            or any(type(stream) is not dict for stream in streams)
            or type(format_document) is not dict
        ):
            raise ValueError
        videos = [stream for stream in streams if stream.get("codec_type") == "video"]
        audios = [stream for stream in streams if stream.get("codec_type") == "audio"]
        if len(videos) != 1 or len(audios) != 1:
            raise ValueError
        video = videos[0]
        audio = audios[0]
        frames = int(video["nb_read_frames"])
        duration_ms = round(float(format_document["duration"]) * 1_000)
    except (KeyError, TypeError, ValueError, OverflowError):
        raise RuntimeError("LE-22 ffprobe evidence is invalid") from None
    if (
        type(artifact_bytes) is not int
        or artifact_bytes < 10_000
        or type(timeline_duration_ms) is not int
        or timeline_duration_ms < 1
        or video.get("codec_name") != "h264"
        or video.get("width") != 720
        or video.get("height") != 1280
        or video.get("avg_frame_rate") != "20/1"
        or frames < 1
        or audio.get("codec_name") != "aac"
        or abs(duration_ms - timeline_duration_ms) > 100
    ):
        raise RuntimeError("LE-22 ffprobe evidence is invalid") from None
    return Le22MediaSummary(
        video_frames=frames,
        duration_ms=duration_ms,
        artifact_bytes=artifact_bytes,
    )


def _pcm_envelope(payload: bytes) -> tuple[float, ...]:
    if type(payload) is not bytes or len(payload) % 2 != 0:
        raise RuntimeError("LE-22 PCM evidence is invalid") from None
    sample_count = len(payload) // 2
    if sample_count < 8_000 * 2:
        raise RuntimeError("LE-22 PCM evidence is invalid") from None
    samples = struct.unpack(f"<{sample_count}h", payload)
    frame_samples = 8_000 // 50
    envelope = tuple(
        math.sqrt(
            sum(value * value for value in samples[start : start + frame_samples])
            / frame_samples
        )
        for start in range(0, sample_count - frame_samples + 1, frame_samples)
    )
    if len(envelope) < 100 or max(envelope) == 0:
        raise RuntimeError("LE-22 PCM evidence is invalid") from None
    return envelope


def _correlation(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    left_centered = tuple(value - left_mean for value in left)
    right_centered = tuple(value - right_mean for value in right)
    denominator = math.sqrt(
        sum(value * value for value in left_centered)
        * sum(value * value for value in right_centered)
    )
    if denominator == 0:
        return -1.0
    return (
        sum(
            left_value * right_value
            for left_value, right_value in zip(
                left_centered, right_centered, strict=True
            )
        )
        / denominator
    )


def compare_pcm_envelopes(original: bytes, rendered: bytes) -> float:
    """Return the best 20 ms RMS-envelope correlation within ±200 ms."""
    left = _pcm_envelope(original)
    right = _pcm_envelope(rendered)
    best = -1.0
    for offset in range(-10, 11):
        if offset < 0:
            aligned_left = left[-offset:]
            aligned_right = right[: len(aligned_left)]
        else:
            aligned_left = left[: len(right) - offset]
            aligned_right = right[offset : offset + len(aligned_left)]
        length = min(len(aligned_left), len(aligned_right))
        if length < 100:
            continue
        best = max(best, _correlation(aligned_left[:length], aligned_right[:length]))
    if best < -0.5:
        raise RuntimeError("LE-22 PCM evidence is invalid") from None
    return best
