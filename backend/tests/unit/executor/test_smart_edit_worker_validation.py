"""COV-02: the untrusted-document boundary of the smart edit worker process.

Everything the worker reads off disk arrives as arbitrary JSON: the request
document, the material records inside it, the timestamps and the speech segment
lists. These helpers are the only place that decides what shape is admissible,
and every refusal collapses to one closed failure code so that nothing about the
rejected value leaks.

The refusals are the point, so that is what is enumerated here. A helper that
accepted a `bool` where an `int` is required, or a naive datetime where an aware
one is required, would not fail any test that only checked the happy path.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest import mock
from uuid import UUID

from automation_tool.executor.local_editing_worker import (
    LocalEditingScriptModelConfiguration,
    LocalEditingWorkerBootstrap,
    LocalSmartEditFailureCode,
)
from automation_tool.executor.material_probe import PackagedMediaTools
from automation_tool.executor.motion_authoring.authoring_workspace import (
    AuthoringWorkspace,
)
from automation_tool.executor.smart_edit_worker_process import (
    LocalSmartEditWorkerRejected,
    _cleanup_job,
    _load_object,
    _material,
    _private_directory,
    _request,
    _require_private_directory,
    _speech_segments,
    _timestamp,
    _tuple_of_ints,
    _uuid,
    create_local_smart_edit_pipeline,
)

_VALID_UUID = "0f8fad5b-d9cb-469f-a165-70867728950e"
_DIGEST = "a" * 64


def _material_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "aiDescription": "新品发布会产品特写",
        "aiTags": ["新品"],
        "audioLoudnessLufs": None,
        "contentDigest": _DIGEST,
        "describedAt": "2026-08-01T00:00:00Z",
        "descriptionSource": "ai",
        "durationMs": 5_000,
        "hasAudio": False,
        "hasSpeech": False,
        "height": 1_280,
        "kind": "video",
        "materialId": _VALID_UUID,
        "shotBoundariesMs": [0, 2_000],
        "speechSegmentsMs": [],
        "speechTranscript": None,
        "width": 720,
    }
    document.update(overrides)
    return document


class UuidTests(unittest.TestCase):
    def test_a_canonical_v4_string_parses(self) -> None:
        self.assertEqual(_uuid(_VALID_UUID), UUID(_VALID_UUID))

    def test_every_other_shape_is_refused(self) -> None:
        for label, value in [
            ("not a string", UUID(_VALID_UUID)),
            ("empty", ""),
            ("not a uuid at all", "not-a-uuid"),
            ("uppercase is not canonical", _VALID_UUID.upper()),
            ("braced form is not canonical", "{" + _VALID_UUID + "}"),
            ("urn form is not canonical", "urn:uuid:" + _VALID_UUID),
            ("unhyphenated is not canonical", _VALID_UUID.replace("-", "")),
            ("the nil uuid is not version 4", "00000000-0000-0000-0000-000000000000"),
            ("a version 1 uuid", "2c9b1d5e-6a3f-11ef-9c8a-0242ac120002"),
        ]:
            with self.subTest(label=label), self.assertRaises(LocalSmartEditWorkerRejected):
                _uuid(value)


class TimestampTests(unittest.TestCase):
    def test_none_stays_none(self) -> None:
        self.assertIsNone(_timestamp(None))

    def test_a_zulu_suffix_is_accepted_as_utc(self) -> None:
        self.assertEqual(_timestamp("2026-08-01T00:00:00Z"), datetime(2026, 8, 1, tzinfo=UTC))

    def test_an_explicit_offset_is_accepted(self) -> None:
        parsed = _timestamp("2026-08-01T08:00:00+08:00")

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.astimezone(UTC), datetime(2026, 8, 1, tzinfo=UTC))

    def test_a_naive_or_malformed_timestamp_is_refused(self) -> None:
        for label, value in [
            ("no timezone", "2026-08-01T00:00:00"),
            ("not a string", 1_785_000_000),
            ("not a timestamp", "yesterday"),
            ("empty", ""),
        ]:
            with self.subTest(label=label), self.assertRaises(LocalSmartEditWorkerRejected):
                _timestamp(value)


class IntegerListTests(unittest.TestCase):
    def test_a_list_of_integers_becomes_a_tuple(self) -> None:
        self.assertEqual(_tuple_of_ints([0, 2_000]), (0, 2_000))

    def test_booleans_do_not_count_as_integers(self) -> None:
        """`type(...) is int` on purpose: `True` would otherwise pass as 1."""
        with self.assertRaises(LocalSmartEditWorkerRejected):
            _tuple_of_ints([0, True])

    def test_anything_but_a_list_of_integers_is_refused(self) -> None:
        for value in ((0, 2_000), "0,2000", [0, "2000"], [0, 1.5], None):
            with self.subTest(value=value), self.assertRaises(LocalSmartEditWorkerRejected):
                _tuple_of_ints(value)


class SpeechSegmentTests(unittest.TestCase):
    def test_pairs_become_tuples(self) -> None:
        self.assertEqual(_speech_segments([[0, 500], [900, 1_400]]), ((0, 500), (900, 1_400)))

    def test_an_empty_list_is_allowed(self) -> None:
        self.assertEqual(_speech_segments([]), ())

    def test_malformed_segments_are_refused(self) -> None:
        for label, value in [
            ("not a list", "0-500"),
            ("segment is not a list", [(0, 500)]),
            ("segment too short", [[0]]),
            ("segment too long", [[0, 500, 900]]),
            ("segment bound is a bool", [[0, True]]),
            ("segment bound is a string", [[0, "500"]]),
        ]:
            with self.subTest(label=label), self.assertRaises(LocalSmartEditWorkerRejected):
                _speech_segments(value)


class MaterialDocumentTests(unittest.TestCase):
    def test_a_complete_document_registers(self) -> None:
        material = _material(_material_document())

        self.assertEqual(str(material.material_id), _VALID_UUID)
        self.assertEqual(material.duration_ms, 5_000)

    def test_a_document_with_the_wrong_key_set_is_refused(self) -> None:
        missing = _material_document()
        del missing["width"]
        extra = _material_document(unexpected="x")

        for label, document in [("missing key", missing), ("extra key", extra)]:
            with self.subTest(label=label), self.assertRaises(LocalSmartEditWorkerRejected):
                _material(document)

    def test_a_non_object_is_refused(self) -> None:
        values: tuple[object, ...] = ([], "material", None, 5)
        for value in values:
            with self.subTest(value=value), self.assertRaises(LocalSmartEditWorkerRejected):
                _material(value)

    def test_malformed_tags_are_refused(self) -> None:
        for tags in ("新品", [1], [None], [["新品"]]):
            with self.subTest(tags=tags), self.assertRaises(LocalSmartEditWorkerRejected):
                _material(_material_document(aiTags=tags))

    def test_a_field_the_domain_refuses_collapses_to_the_same_code(self) -> None:
        """Domain-level rejections must not escape as their own exception type."""
        cases: list[tuple[str, dict[str, Any]]] = [
            ("unknown kind", {"kind": "hologram"}),
            ("unknown description source", {"descriptionSource": "telepathy"}),
            ("negative duration", {"durationMs": -1}),
            ("digest of the wrong length", {"contentDigest": "a" * 63}),
            ("zero width", {"width": 0}),
        ]
        for label, overrides in cases:
            with self.subTest(label=label), self.assertRaises(LocalSmartEditWorkerRejected):
                _material(_material_document(**overrides))


class LoadObjectTests(unittest.TestCase):
    def _write(self, root: Path, payload: str, name: str = "request.json") -> Path:
        target = root / name
        target.write_text(payload, encoding="utf-8")
        return target

    def test_a_json_object_is_returned(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = self._write(Path(raw), '{"a": 1}')

            self.assertEqual(_load_object(path), {"a": 1})

    def test_a_missing_file_is_refused(self) -> None:
        with (
            tempfile.TemporaryDirectory() as raw,
            self.assertRaises(LocalSmartEditWorkerRejected),
        ):
            _load_object(Path(raw) / "absent.json")

    def test_a_symlink_is_refused_even_when_it_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            real = self._write(root, '{"a": 1}', name="real.json")
            link = root / "link.json"
            link.symlink_to(real)

            with self.assertRaises(LocalSmartEditWorkerRejected):
                _load_object(link)

    def test_a_directory_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "subdir"
            target.mkdir()

            with self.assertRaises(LocalSmartEditWorkerRejected):
                _load_object(target)

    def test_an_empty_or_oversized_document_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            empty = self._write(root, "", name="empty.json")
            oversized = root / "big.json"
            oversized.write_bytes(b"{" + b" " * (4 * 1024 * 1024) + b"}")

            for label, path in [("empty", empty), ("oversized", oversized)]:
                with self.subTest(label=label), self.assertRaises(LocalSmartEditWorkerRejected):
                    _load_object(path)

    def test_a_file_that_grew_between_stat_and_read_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = self._write(Path(raw), '{"a": 1}')

            with (
                mock.patch.object(Path, "read_bytes", return_value=b'{"a": 1, "b": 2}'),
                self.assertRaises(LocalSmartEditWorkerRejected),
            ):
                _load_object(path)

    def test_an_unreadable_file_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = self._write(Path(raw), '{"a": 1}')

            with (
                mock.patch.object(Path, "read_bytes", side_effect=OSError("denied")),
                self.assertRaises(LocalSmartEditWorkerRejected),
            ):
                _load_object(path)

    def test_duplicate_keys_are_refused(self) -> None:
        """`json.loads` would silently keep the last one."""
        with tempfile.TemporaryDirectory() as raw:
            path = self._write(Path(raw), '{"a": 1, "a": 2}')

            with self.assertRaises(LocalSmartEditWorkerRejected):
                _load_object(path)

    def test_invalid_encoding_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "bad.json"
            path.write_bytes(b'{"a": "\xff\xfe"}')

            with self.assertRaises(LocalSmartEditWorkerRejected):
                _load_object(path)

    def test_a_document_that_is_not_an_object_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for label, payload in [
                ("array", "[1, 2]"),
                ("string", '"request"'),
                ("number", "42"),
                ("null", "null"),
                ("not json", "{oops"),
            ]:
                path = self._write(root, payload, name=f"{label}.json")
                with self.subTest(label=label), self.assertRaises(LocalSmartEditWorkerRejected):
                    _load_object(path)

    def test_the_rejection_never_repeats_the_document(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = self._write(Path(raw), '{"secret": "hunter2"}')

            with (
                mock.patch.object(Path, "read_bytes", side_effect=OSError(os.strerror(13))),
                self.assertRaises(LocalSmartEditWorkerRejected) as caught,
            ):
                _load_object(path)

            self.assertNotIn("hunter2", repr(caught.exception))
            self.assertNotIn("hunter2", str(caught.exception))
            self.assertEqual(repr(caught.exception), "LocalSmartEditWorkerRejected(<redacted>)")


def _request_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "enableThinking": False,
        "jobId": _VALID_UUID,
        "materials": [_material_document()],
        "prompt": "把这些素材剪成一条产品短片",
        "schemaVersion": "smart-edit-generation-request.v1",
    }
    document.update(overrides)
    return document


class RequestDocumentTests(unittest.TestCase):
    def _load(self, root: Path, document: dict[str, Any]) -> Any:
        path = root / "request.json"
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        return _request(path, UUID(_VALID_UUID))

    def test_a_complete_request_parses(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            request = self._load(Path(raw), _request_document())

        self.assertEqual(request.prompt, "把这些素材剪成一条产品短片")
        self.assertEqual(len(request.materials), 1)
        self.assertFalse(request.enable_thinking)

    def test_a_schema_version_that_is_not_the_one_this_worker_speaks_is_refused(self) -> None:
        with (
            tempfile.TemporaryDirectory() as raw,
            self.assertRaises(LocalSmartEditWorkerRejected),
        ):
            self._load(Path(raw), _request_document(schemaVersion="smart-edit.v2"))

    def test_a_job_id_that_does_not_match_the_command_is_refused(self) -> None:
        """The document cannot redirect the worker at another job's directory."""
        with (
            tempfile.TemporaryDirectory() as raw,
            self.assertRaises(LocalSmartEditWorkerRejected),
        ):
            self._load(
                Path(raw),
                _request_document(jobId="1b4e28ba-2fa1-4d1f-b3f6-0c8f9e5a7b21"),
            )

    def test_an_unexpected_key_set_is_refused(self) -> None:
        missing = _request_document()
        del missing["prompt"]

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for label, document in [
                ("missing key", missing),
                ("extra key", _request_document(unexpected="x")),
            ]:
                with self.subTest(label=label), self.assertRaises(LocalSmartEditWorkerRejected):
                    self._load(root, document)

    def test_malformed_prompts_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for label, prompt in [
                ("not a string", 42),
                ("empty", ""),
                ("leading whitespace", " 剪一条片子"),
                ("trailing whitespace", "剪一条片子\n"),
                ("beyond four thousand characters", "剪" * 4_001),
            ]:
                with self.subTest(label=label), self.assertRaises(LocalSmartEditWorkerRejected):
                    self._load(root, _request_document(prompt=prompt))

    def test_malformed_material_lists_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for label, materials in [
                ("not a list", {"0": _material_document()}),
                ("empty", []),
                ("beyond thirty-two", [_material_document()] * 33),
            ]:
                with self.subTest(label=label), self.assertRaises(LocalSmartEditWorkerRejected):
                    self._load(root, _request_document(materials=materials))

    def test_a_thinking_flag_that_is_not_a_bool_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for value in (1, "true", None):
                with self.subTest(value=value), self.assertRaises(LocalSmartEditWorkerRejected):
                    self._load(root, _request_document(enableThinking=value))


class PrivateDirectoryTests(unittest.TestCase):
    def test_a_fresh_directory_is_created_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            # Resolved: on macOS the temp root lives under /var, itself a symlink
            # to /private/var, and the production check compares `resolve()` with
            # `abspath` -- an unresolved root fails it for the wrong reason.
            target = Path(raw).resolve() / "jobs" / _VALID_UUID
            _private_directory(target)

            self.assertTrue(target.is_dir())
            self.assertEqual(target.lstat().st_mode & 0o077, 0, "group/other bits must be clear")

    def test_an_existing_directory_is_refused(self) -> None:
        """`exist_ok=False`: reusing a staging directory is never correct."""
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "jobs"
            target.mkdir()

            with self.assertRaises(LocalSmartEditWorkerRejected):
                _private_directory(target)

    def test_a_directory_that_cannot_be_created_is_refused(self) -> None:
        with (
            tempfile.TemporaryDirectory() as raw,
            mock.patch.object(Path, "mkdir", side_effect=OSError("read-only")),
            self.assertRaises(LocalSmartEditWorkerRejected),
        ):
            _private_directory(Path(raw) / "jobs")


class RequirePrivateDirectoryTests(unittest.TestCase):
    _CODE = LocalSmartEditFailureCode.WORKSPACE_UNUSABLE

    def test_an_owner_only_directory_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw).resolve() / "private"
            target.mkdir(mode=0o700)

            _require_private_directory(target, self._CODE)

    def test_a_missing_directory_is_refused(self) -> None:
        with (
            tempfile.TemporaryDirectory() as raw,
            self.assertRaises(LocalSmartEditWorkerRejected),
        ):
            _require_private_directory(Path(raw) / "absent", self._CODE)

    def test_a_plain_file_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "file"
            target.write_bytes(b"x")

            with self.assertRaises(LocalSmartEditWorkerRejected):
                _require_private_directory(target, self._CODE)

    def test_a_symlink_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            real = root / "real"
            real.mkdir(mode=0o700)
            link = root / "link"
            link.symlink_to(real, target_is_directory=True)

            with self.assertRaises(LocalSmartEditWorkerRejected):
                _require_private_directory(link, self._CODE)

    def test_a_group_or_world_readable_directory_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "loose"
            target.mkdir(mode=0o755)

            with self.assertRaises(LocalSmartEditWorkerRejected):
                _require_private_directory(target, self._CODE)


class CleanupJobTests(unittest.TestCase):
    def test_an_existing_job_directory_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            job = Path(raw) / "job"
            (job / "staging").mkdir(parents=True)
            (job / "request.json").write_bytes(b"{}")

            self.assertTrue(_cleanup_job(job))
            self.assertFalse(job.exists())

    def test_an_absent_job_directory_counts_as_clean(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            self.assertTrue(_cleanup_job(Path(raw) / "never-existed"))

    def test_a_directory_that_survives_removal_reports_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            job = Path(raw) / "job"
            job.mkdir()

            with mock.patch(
                "automation_tool.executor.smart_edit_worker_process.shutil.rmtree",
                side_effect=OSError("busy"),
            ):
                self.assertFalse(_cleanup_job(job))

    def test_a_directory_whose_state_cannot_be_read_reports_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            job = Path(raw) / "job"
            job.mkdir()

            with (
                mock.patch(
                    "automation_tool.executor.smart_edit_worker_process.shutil.rmtree",
                    side_effect=OSError("busy"),
                ),
                mock.patch.object(Path, "lstat", side_effect=OSError("denied")),
            ):
                self.assertFalse(_cleanup_job(job))


class PipelineConstructionTests(unittest.TestCase):
    """`create_local_smart_edit_pipeline` must refuse rather than half-build."""

    def _bootstrap(self, root: Path, *, with_model: bool) -> LocalEditingWorkerBootstrap:
        asset_root = root / "app-data"
        asset_root.mkdir(mode=0o700)
        tools = root / "tools"
        tools.mkdir(mode=0o700)
        for name in ("ffmpeg", "ffprobe"):
            binary = tools / name
            binary.write_bytes(b"#!/bin/sh\nexit 0\n")
            binary.chmod(0o700)
        model = (
            LocalEditingScriptModelConfiguration(
                base_url="https://dashscope.example.com/compatible-mode/v1",
                model_id="qwen-plus",
                api_key="sk-" + "x" * 32,
            )
            if with_model
            else None
        )
        return LocalEditingWorkerBootstrap(
            asset_root=asset_root,
            media_tools=PackagedMediaTools(
                ffmpeg_path=tools / "ffmpeg",
                ffprobe_path=tools / "ffprobe",
            ),
            script_model=model,
            _session_token=b"x" * 32,
        )

    def _workspace(self, root: Path) -> AuthoringWorkspace:
        workspace_root = root / "workspace"
        workspace_root.mkdir(mode=0o700)
        return AuthoringWorkspace(workspace_root)

    def test_a_bootstrap_of_the_wrong_type_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()

            with self.assertRaises(LocalSmartEditWorkerRejected):
                create_local_smart_edit_pipeline(cast(Any, object()), self._workspace(root))

    def test_a_bootstrap_without_a_script_model_is_refused(self) -> None:
        """No key configured is a configuration problem, not a runtime failure."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()

            with self.assertRaises(LocalSmartEditWorkerRejected):
                create_local_smart_edit_pipeline(
                    self._bootstrap(root, with_model=False), self._workspace(root)
                )

    def test_a_workspace_of_the_wrong_type_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()

            with self.assertRaises(LocalSmartEditWorkerRejected):
                create_local_smart_edit_pipeline(
                    self._bootstrap(root, with_model=True), cast(Any, object())
                )

    def test_an_adapter_that_will_not_load_is_refused(self) -> None:
        """A half-built pipeline must never be returned."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()

            with (
                mock.patch(
                    "automation_tool.executor.smart_edit_worker_process"
                    ".load_bailian_material_understanding_config",
                    side_effect=RuntimeError("catalog unreadable"),
                ),
                self.assertRaises(LocalSmartEditWorkerRejected),
            ):
                create_local_smart_edit_pipeline(
                    self._bootstrap(root, with_model=True), self._workspace(root)
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
