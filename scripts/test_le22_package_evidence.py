from __future__ import annotations

import math
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from le22_package_evidence import (
    compare_pcm_envelopes,
    validate_le22_database_evidence,
    validate_le22_ffprobe,
)

MATERIAL_ID = "11111111-1111-4111-8111-111111111111"
ARTIFACT_ID = "22222222-2222-4222-8222-222222222222"
TRANSCRIPT = "Mister Quilter is the apostle of the middle classes."


def clip(
    clip_id: str,
    *,
    source_material_id: str | None,
    text: str | None,
    gain_db: float | None,
    original_audio_mode: str | None,
) -> dict[str, object]:
    return {
        "clip_id": clip_id,
        "start_ms": 0,
        "duration_ms": 5_088,
        "source_material_id": source_material_id,
        "source_in_ms": 480 if source_material_id is not None else None,
        "source_out_ms": 5_568 if source_material_id is not None else None,
        "text": text,
        "gain_db": gain_db,
        "transition_in": None,
        "original_audio_mode": original_audio_mode,
    }


def valid_database_document() -> dict[str, object]:
    return {
        "material": {
            "materialId": MATERIAL_ID,
            "kind": "video",
            "durationMs": 6_173,
            "hasSpeech": True,
            "speechSegmentsMs": [[480, 5_568]],
            "speechTranscript": TRANSCRIPT,
        },
        "timeline": {
            "revision": 1,
            "durationMs": 5_088,
            "tracks": [
                {
                    "track_id": "visual",
                    "kind": "visual",
                    "clips": [
                        clip(
                            "visual-0001",
                            source_material_id=MATERIAL_ID,
                            text=None,
                            gain_db=None,
                            original_audio_mode=None,
                        )
                    ],
                },
                {
                    "track_id": "ambient",
                    "kind": "ambient",
                    "clips": [
                        clip(
                            "ambient-0001",
                            source_material_id=MATERIAL_ID,
                            text=None,
                            gain_db=0.0,
                            original_audio_mode="auto_duck",
                        )
                    ],
                },
                {
                    "track_id": "caption",
                    "kind": "caption",
                    "clips": [
                        clip(
                            "caption-0001",
                            source_material_id=None,
                            text=TRANSCRIPT,
                            gain_db=None,
                            original_audio_mode=None,
                        )
                    ],
                },
            ],
        },
        "job": {
            "status": "succeeded",
            "timelineRevision": 1,
            "failureCode": None,
            "outputArtifactId": ARTIFACT_ID,
        },
    }


class Le22DatabaseEvidenceTests(unittest.TestCase):
    def test_accepts_one_original_speech_timeline_without_tts(self) -> None:
        summary = validate_le22_database_evidence(
            valid_database_document(),
            required_transcript_words=frozenset({"QUILTER", "APOSTLE"}),
        )

        self.assertEqual(summary.material_id, MATERIAL_ID)
        self.assertEqual(summary.artifact_id, ARTIFACT_ID)
        self.assertEqual(summary.timeline_duration_ms, 5_088)
        self.assertEqual(summary.source_window_ms, (480, 5_568))
        self.assertEqual(summary.speech_segment_count, 1)

    def test_rejects_missing_or_fake_original_audio_evidence(self) -> None:
        cases: list[dict[str, object]] = []

        missing_ambient = valid_database_document()
        missing_ambient["timeline"]["tracks"].pop(1)  # type: ignore[index]
        cases.append(missing_ambient)

        with_narration = valid_database_document()
        with_narration["timeline"]["tracks"].insert(  # type: ignore[index]
            1,
            {
                "track_id": "narration",
                "kind": "narration",
                "clips": [
                    clip(
                        "narration-0001",
                        source_material_id=ARTIFACT_ID,
                        text=None,
                        gain_db=0.0,
                        original_audio_mode=None,
                    )
                ],
            },
        )
        cases.append(with_narration)

        wrong_source = valid_database_document()
        wrong_source["timeline"]["tracks"][1]["clips"][0][  # type: ignore[index]
            "source_material_id"
        ] = ARTIFACT_ID
        cases.append(wrong_source)

        wrong_caption = valid_database_document()
        wrong_caption["timeline"]["tracks"][2]["clips"][0]["text"] = (  # type: ignore[index]
            "synthetic narration"
        )
        cases.append(wrong_caption)

        for document in cases:
            with (
                self.subTest(document=document),
                self.assertRaisesRegex(
                    RuntimeError, "LE-22 database evidence is invalid"
                ),
            ):
                validate_le22_database_evidence(
                    document,
                    required_transcript_words=frozenset({"QUILTER", "APOSTLE"}),
                )


class Le22MediaEvidenceTests(unittest.TestCase):
    def test_accepts_the_expected_rendered_streams_and_duration(self) -> None:
        summary = validate_le22_ffprobe(
            {
                "streams": [
                    {
                        "codec_type": "video",
                        "codec_name": "h264",
                        "width": 720,
                        "height": 1280,
                        "avg_frame_rate": "20/1",
                        "nb_read_frames": "102",
                    },
                    {"codec_type": "audio", "codec_name": "aac"},
                ],
                "format": {"duration": "5.100000"},
            },
            artifact_bytes=240_000,
            timeline_duration_ms=5_088,
        )

        self.assertEqual(summary.video_frames, 102)
        self.assertEqual(summary.duration_ms, 5_100)
        self.assertEqual(summary.artifact_bytes, 240_000)

    def test_malformed_ffprobe_streams_fail_with_the_fixed_boundary_error(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "LE-22 ffprobe evidence is invalid"):
            validate_le22_ffprobe(
                {"streams": ["not-a-stream"], "format": {"duration": "5.1"}},
                artifact_bytes=240_000,
                timeline_duration_ms=5_088,
            )

    def test_pcm_envelope_matches_scaled_shifted_original_but_not_tts_shape(
        self,
    ) -> None:
        sample_rate = 8_000
        frame_samples = sample_rate // 50

        def pcm(envelope: list[float]) -> bytes:
            values: list[int] = []
            for frame_index, gain in enumerate(envelope):
                for offset in range(frame_samples):
                    phase = (
                        2
                        * math.pi
                        * 220
                        * (frame_index * frame_samples + offset)
                        / sample_rate
                    )
                    values.append(round(20_000 * gain * math.sin(phase)))
            return struct.pack(f"<{len(values)}h", *values)

        original_shape = [0.1 + 0.8 * abs(math.sin(index / 13)) for index in range(140)]
        shifted_scaled = [0.0, 0.0, *[value * 0.65 for value in original_shape]]
        unrelated = [0.1 + 0.8 * abs(math.sin(index / 3.7)) for index in range(140)]

        self.assertGreater(
            compare_pcm_envelopes(pcm(original_shape), pcm(shifted_scaled)), 0.95
        )
        self.assertLess(compare_pcm_envelopes(pcm(original_shape), pcm(unrelated)), 0.8)


if __name__ == "__main__":
    unittest.main()
