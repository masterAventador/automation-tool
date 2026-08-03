"""COV-02: filename parsing, candidate sampling and frame re-reading.

These are the pure decisions inside adaptive frame extraction -- which FFmpeg
output filename is a legitimate timestamp, which candidates survive the frame
budget, and whether a frame read back off disk is the same bytes that were
written. Every refusal here is a fail-closed one, so the tests are mostly about
the rejected shapes rather than the happy path.
"""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automation_tool.executor import adaptive_frame_extraction as module
from automation_tool.executor.adaptive_frame_extraction import (
    AdaptiveFrameArtifact,
    AdaptiveFrameRejection,
    BoundedFfmpegOutput,
    ExtractedFrame,
    _globally_uniform_indices,
    _open_output_workspace,
    _OutputWorkspace,
    _parse_scene_frames,
    _parse_supplement_frame,
    _read_final_frame,
    _timestamp_from_name,
    _uniformly_sample,
    _write_final_frames,
    read_adaptive_frame_artifacts,
)

_JPEG = b"\xff\xd8ok\xff\xd9"


class TimestampFromNameTests(unittest.TestCase):
    def test_a_canonical_name_yields_its_milliseconds(self) -> None:
        self.assertEqual(
            _timestamp_from_name("scene-000000001500.jpg", prefix="scene-", suffix=".jpg"),
            1500,
        )

    def test_every_malformed_shape_is_refused(self) -> None:
        for name in (
            "supplement-000000001500.jpg",  # wrong prefix
            "scene-000000001500.png",  # wrong suffix
            "scene-1500.jpg",  # too few digits
            "scene-0000000000001500.jpg",  # too many digits
            "scene-0000000015o0.jpg",  # not all digits
            "scene-００００００００１５００.jpg",  # full-width digits are not ASCII
        ):
            with self.subTest(name=name):
                self.assertIsNone(_timestamp_from_name(name, prefix="scene-", suffix=".jpg"))


class ParseSceneFramesTests(unittest.TestCase):
    def test_named_outputs_become_scene_cuts_in_order(self) -> None:
        parsed = _parse_scene_frames(
            BoundedFfmpegOutput(
                files=(
                    ("scene-000000000000.jpg", _JPEG),
                    ("scene-000000002000.jpg", _JPEG),
                )
            )
        )

        self.assertNotIsInstance(parsed, AdaptiveFrameRejection)
        assert not isinstance(parsed, AdaptiveFrameRejection)
        self.assertEqual([frame.timestamp_ms for frame in parsed], [0, 2000])
        self.assertTrue(all(frame.is_scene_cut for frame in parsed))

    def test_an_unparseable_name_fails_the_whole_pass(self) -> None:
        parsed = _parse_scene_frames(BoundedFfmpegOutput(files=(("junk.jpg", _JPEG),)))

        self.assertIs(parsed, AdaptiveFrameRejection.TOOL_FAILED)

    def test_producing_no_frames_at_all_is_a_tool_failure(self) -> None:
        self.assertIs(
            _parse_scene_frames(BoundedFfmpegOutput(files=())),
            AdaptiveFrameRejection.TOOL_FAILED,
        )


class ParseSupplementFrameTests(unittest.TestCase):
    def test_a_single_named_output_becomes_a_non_scene_frame(self) -> None:
        frame = _parse_supplement_frame(
            BoundedFfmpegOutput(files=(("supplement-000000004000.jpg", _JPEG),))
        )

        self.assertIsInstance(frame, ExtractedFrame)
        assert isinstance(frame, ExtractedFrame)
        self.assertEqual(frame.timestamp_ms, 4000)
        self.assertFalse(frame.is_scene_cut)

    def test_no_output_means_the_seek_landed_past_the_end(self) -> None:
        """`None`, not a rejection: the caller decides whether that is fatal."""
        self.assertIsNone(_parse_supplement_frame(BoundedFfmpegOutput(files=())))

    def test_more_than_one_output_is_undecodable(self) -> None:
        self.assertIs(
            _parse_supplement_frame(
                BoundedFfmpegOutput(
                    files=(
                        ("supplement-000000004000.jpg", _JPEG),
                        ("supplement-000000005000.jpg", _JPEG),
                    )
                )
            ),
            AdaptiveFrameRejection.UNDECODABLE,
        )

    def test_an_unparseable_name_is_a_tool_failure(self) -> None:
        self.assertIs(
            _parse_supplement_frame(BoundedFfmpegOutput(files=(("junk.jpg", _JPEG),))),
            AdaptiveFrameRejection.TOOL_FAILED,
        )


class UniformSampleTests(unittest.TestCase):
    def test_a_negative_limit_is_a_programming_error(self) -> None:
        with self.assertRaises(ValueError):
            _uniformly_sample((1, 2, 3), -1)

    def test_a_zero_limit_selects_nothing(self) -> None:
        self.assertEqual(_uniformly_sample((1, 2, 3), 0), ())

    def test_a_limit_at_or_above_the_supply_keeps_everything(self) -> None:
        self.assertEqual(_uniformly_sample((1, 2, 3), 3), (1, 2, 3))
        self.assertEqual(_uniformly_sample((1, 2, 3), 9), (1, 2, 3))

    def test_a_single_pick_lands_nearest_the_midpoint(self) -> None:
        self.assertEqual(_uniformly_sample((0, 10, 90, 100), 1), (10,))

    def test_two_picks_are_the_endpoints(self) -> None:
        self.assertEqual(_globally_uniform_indices((0, 10, 50, 90, 100), 2), (0, 4))

    def test_three_picks_spread_across_the_span(self) -> None:
        selected = _uniformly_sample((0, 10, 50, 90, 100), 3)

        self.assertEqual(len(selected), 3)
        self.assertEqual(selected[0], 0)
        self.assertEqual(selected[-1], 100)


class ReadFinalFrameTests(unittest.TestCase):
    def _prepared(
        self, root: Path, payload: bytes = _JPEG
    ) -> tuple[_OutputWorkspace, AdaptiveFrameArtifact]:
        workspace = _open_output_workspace(root)
        assert workspace is not None
        written = _write_final_frames(
            workspace,
            (ExtractedFrame(timestamp_ms=10, jpeg_bytes=payload, is_scene_cut=True),),
        )
        assert not isinstance(written, AdaptiveFrameRejection)
        return workspace, written[0]

    def test_a_frame_written_then_read_back_matches(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace, artifact = self._prepared(Path(raw))
            try:
                self.assertEqual(_read_final_frame(workspace, artifact), _JPEG)
            finally:
                workspace.close()

    def test_a_truncated_read_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace, artifact = self._prepared(Path(raw))
            try:
                with mock.patch.object(os, "read", return_value=b""):
                    self.assertIsNone(_read_final_frame(workspace, artifact))
            finally:
                workspace.close()

    def test_a_size_that_disagrees_with_the_file_raises(self) -> None:
        """Not `None`: the stat mismatch is an OSError the batch reader catches."""
        with tempfile.TemporaryDirectory() as raw:
            workspace, artifact = self._prepared(Path(raw))
            shrunk = AdaptiveFrameArtifact(
                filename=artifact.filename,
                timestamp_ms=artifact.timestamp_ms,
                is_scene_cut=artifact.is_scene_cut,
                byte_size=artifact.byte_size - 1,
            )
            try:
                with self.assertRaises(OSError):
                    _read_final_frame(workspace, shrunk)
            finally:
                workspace.close()

    def test_bytes_appearing_after_the_declared_size_are_refused(self) -> None:
        """The file grew between `fstat` and the final probe read."""
        with tempfile.TemporaryDirectory() as raw:
            workspace, artifact = self._prepared(Path(raw))
            try:
                with mock.patch.object(os, "read", side_effect=[_JPEG, b"extra"]):
                    self.assertIsNone(_read_final_frame(workspace, artifact))
            finally:
                workspace.close()

    def test_a_file_swapped_while_it_is_being_read_is_refused(self) -> None:
        """`fstat` before and after the read must describe the same file."""
        with tempfile.TemporaryDirectory() as raw:
            workspace, artifact = self._prepared(Path(raw))
            real_fstat = os.fstat
            calls = {"n": 0}

            def drifting(descriptor: int) -> os.stat_result:
                calls["n"] += 1
                metadata = real_fstat(descriptor)
                if calls["n"] == 1:
                    return metadata
                fields = list(metadata)
                fields[stat.ST_MTIME] = metadata.st_mtime + 5
                return os.stat_result(fields)

            try:
                with mock.patch.object(os, "fstat", drifting):
                    self.assertIsNone(_read_final_frame(workspace, artifact))
            finally:
                workspace.close()

    def test_a_directory_entry_pointing_elsewhere_than_the_open_file_is_refused(self) -> None:
        """The name must still resolve to the very file the descriptor holds."""
        with tempfile.TemporaryDirectory() as raw:
            workspace, artifact = self._prepared(Path(raw))
            real_stat_frame = type(workspace).stat_frame

            def drifting(self: _OutputWorkspace, filename: str) -> os.stat_result:
                metadata = real_stat_frame(self, filename)
                fields = list(metadata)
                fields[stat.ST_INO] = metadata.st_ino + 1
                return os.stat_result(fields)

            try:
                with mock.patch.object(type(workspace), "stat_frame", drifting):
                    self.assertIsNone(_read_final_frame(workspace, artifact))
            finally:
                workspace.close()

    def test_a_payload_without_jpeg_markers_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            workspace, artifact = self._prepared(root, payload=b"not-a-jpeg!")
            try:
                self.assertIsNone(_read_final_frame(workspace, artifact))
            finally:
                workspace.close()


class ReadArtifactBatchTests(unittest.TestCase):
    def _write_batch(self, root: Path, count: int = 2) -> tuple[AdaptiveFrameArtifact, ...]:
        workspace = _open_output_workspace(root)
        assert workspace is not None
        try:
            written = _write_final_frames(
                workspace,
                tuple(
                    ExtractedFrame(
                        timestamp_ms=(index + 1) * 100,
                        jpeg_bytes=_JPEG,
                        is_scene_cut=index == 0,
                    )
                    for index in range(count)
                ),
            )
        finally:
            workspace.close()
        assert not isinstance(written, AdaptiveFrameRejection)
        return written

    def test_a_written_batch_reads_back_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            artifacts = self._write_batch(root)

            frames = read_adaptive_frame_artifacts(root, artifacts, duration_ms=10_000)

            self.assertNotIsInstance(frames, AdaptiveFrameRejection)
            assert not isinstance(frames, AdaptiveFrameRejection)
            self.assertEqual([frame.timestamp_ms for frame in frames], [100, 200])

    def test_malformed_inputs_are_refused_before_any_file_is_touched(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            artifacts = self._write_batch(root, count=1)
            good = artifacts[0]

            cases: list[tuple[str, object, object, object]] = [
                ("directory is not a Path", os.fspath(root), artifacts, 10_000),
                ("artifacts is not a tuple", root, list(artifacts), 10_000),
                ("no artifacts", root, (), 10_000),
                ("too many artifacts", root, (good,) * 61, 10_000),
                ("duration is a bool", root, artifacts, True),
                ("duration is zero", root, artifacts, 0),
                ("duration beyond four hours", root, artifacts, 14_400_001),
            ]
            for label, directory, batch, duration in cases:
                with self.subTest(label=label):
                    self.assertIs(
                        read_adaptive_frame_artifacts(directory, batch, duration_ms=duration),  # type: ignore[arg-type]
                        AdaptiveFrameRejection.WORKSPACE_UNUSABLE,
                    )

    def test_a_missing_workspace_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            artifacts = self._write_batch(root, count=1)

            self.assertIs(
                read_adaptive_frame_artifacts(root / "gone", artifacts, duration_ms=10_000),
                AdaptiveFrameRejection.WORKSPACE_UNUSABLE,
            )

    def test_metadata_that_disagrees_with_the_batch_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            artifacts = self._write_batch(root, count=1)
            original = artifacts[0]

            def replaced(**changes: object) -> tuple[AdaptiveFrameArtifact, ...]:
                fields = {
                    "filename": original.filename,
                    "timestamp_ms": original.timestamp_ms,
                    "is_scene_cut": original.is_scene_cut,
                    "byte_size": original.byte_size,
                }
                fields.update(changes)
                return (AdaptiveFrameArtifact(**fields),)  # type: ignore[arg-type]

            for label, batch in [
                ("filename off the expected sequence", replaced(filename="frame-000009.jpg")),
                ("timestamp at or past the duration", replaced(timestamp_ms=10_000)),
                ("timestamp is a bool", replaced(timestamp_ms=True)),
                ("scene flag is not a bool", replaced(is_scene_cut=1)),
                ("size below a JPEG minimum", replaced(byte_size=3)),
            ]:
                with self.subTest(label=label):
                    self.assertIs(
                        read_adaptive_frame_artifacts(root, batch, duration_ms=10_000),
                        AdaptiveFrameRejection.WORKSPACE_UNUSABLE,
                    )

    def test_a_batch_over_the_byte_ceiling_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            artifacts = self._write_batch(root, count=1)
            huge = (
                AdaptiveFrameArtifact(
                    filename=artifacts[0].filename,
                    timestamp_ms=artifacts[0].timestamp_ms,
                    is_scene_cut=artifacts[0].is_scene_cut,
                    byte_size=64 * 1024 * 1024 + 1,
                ),
            )

            self.assertIs(
                read_adaptive_frame_artifacts(root, huge, duration_ms=10_000),
                AdaptiveFrameRejection.OUTPUT_LIMIT_EXCEEDED,
            )

    def test_a_frame_that_vanished_from_disk_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            artifacts = self._write_batch(root, count=1)
            (root / artifacts[0].filename).unlink()

            self.assertIs(
                read_adaptive_frame_artifacts(root, artifacts, duration_ms=10_000),
                AdaptiveFrameRejection.WORKSPACE_UNUSABLE,
            )

    def test_a_frame_that_reads_back_as_unusable_stops_the_batch(self) -> None:
        """`_read_final_frame` returning None must not become an empty frame."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            artifacts = self._write_batch(root, count=1)

            with mock.patch.object(module, "_read_final_frame", return_value=None):
                self.assertIs(
                    read_adaptive_frame_artifacts(root, artifacts, duration_ms=10_000),
                    AdaptiveFrameRejection.WORKSPACE_UNUSABLE,
                )

    def test_a_frame_whose_mode_was_widened_is_refused(self) -> None:
        """0600 is part of what was written; a looser mode means interference."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            artifacts = self._write_batch(root, count=1)
            target = root / artifacts[0].filename
            target.chmod(0o644)

            self.assertIs(
                read_adaptive_frame_artifacts(root, artifacts, duration_ms=10_000),
                AdaptiveFrameRejection.WORKSPACE_UNUSABLE,
            )
            self.assertEqual(stat.S_IMODE(target.lstat().st_mode), 0o644)


if __name__ == "__main__":
    unittest.main(verbosity=2)
