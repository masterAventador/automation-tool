"""COV-02: the checkpoint-document boundary of the local editing worker process.

The render request arrives as a JSON checkpoint written by another process, so
every field is untrusted: ids, timestamps, transitions, clips, tracks and the
material bindings. These helpers decide what is admissible and collapse each
refusal into a closed failure code.

`_render_failure` gets its own exhaustive treatment. It is a total mapping from
the visual renderer's rejection vocabulary to the worker's, and a mapping is
exactly the kind of thing where a spot-check passes while one entry is missing
or points at the wrong code -- so every member of the source enum is driven
through it, and the test fails if the enum grows a member nobody mapped.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, cast
from unittest import mock
from uuid import UUID

from automation_tool.executor.local_editing_worker import (
    LocalEditingWorkerBootstrap,
    LocalEditingWorkerFailureCode,
    LocalMaterialForgetCommand,
    LocalMaterialImportCommand,
    LocalMaterialStatusCommand,
    LocalMaterialWorkerFailureCode,
    LocalMaterialWorkerStatus,
)
from automation_tool.executor.local_editing_worker_process import (
    LocalEditingRenderCancelled,
    LocalEditingRenderDiagnosticCode,
    LocalEditingRenderRejected,
    LocalMaterialOperationRejected,
    _load_request,
    _material_registry,
    _materials,
    _object,
    _optional_uuid,
    _project,
    _render_failure,
    _timeline,
    _timestamp,
    _track,
    _transition,
    _uuid,
    execute_local_material_forget,
    execute_local_material_import,
    execute_local_material_status,
)
from automation_tool.executor.material_probe import MaterialPathRegistry, PackagedMediaTools
from automation_tool.executor.visual_render_execution import (
    VisualRenderExecutionRejected,
    VisualRenderExecutionRejection,
)

_UUID_A = "0f8fad5b-d9cb-469f-a165-70867728950e"
_UUID_B = "1b4e28ba-2fa1-4d1f-b3f6-0c8f9e5a7b21"


def _clip_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "clipId": "clip-1",
        "startMs": 0,
        "durationMs": 1_000,
        "sourceMaterialId": _UUID_A,
        "sourceInMs": 0,
        "sourceOutMs": 1_000,
        "text": None,
        "gainDb": None,
        "transitionIn": None,
        "originalAudioMode": None,
    }
    document.update(overrides)
    return document


class ObjectShapeTests(unittest.TestCase):
    def test_an_exact_key_set_is_returned(self) -> None:
        self.assertEqual(_object({"a": 1}, {"a"}), {"a": 1})

    def test_a_non_object_is_refused(self) -> None:
        values: tuple[object, ...] = ([], "a", None, 1)
        for value in values:
            with self.subTest(value=value), self.assertRaises(LocalEditingRenderRejected):
                _object(value, {"a"})

    def test_a_missing_or_extra_key_is_refused(self) -> None:
        for label, document in [("missing", {}), ("extra", {"a": 1, "b": 2})]:
            with self.subTest(label=label), self.assertRaises(LocalEditingRenderRejected):
                _object(document, {"a"})


class IdentifierTests(unittest.TestCase):
    def test_a_canonical_v4_uuid_parses(self) -> None:
        self.assertEqual(_uuid(_UUID_A), UUID(_UUID_A))

    def test_non_canonical_forms_are_refused(self) -> None:
        values: tuple[object, ...] = (
            UUID(_UUID_A),
            _UUID_A.upper(),
            _UUID_A.replace("-", ""),
            "{" + _UUID_A + "}",
            "00000000-0000-0000-0000-000000000000",
            "not-a-uuid",
            None,
        )
        for value in values:
            with self.subTest(value=value), self.assertRaises(LocalEditingRenderRejected):
                _uuid(value)

    def test_an_optional_id_may_be_absent(self) -> None:
        self.assertIsNone(_optional_uuid(None))
        material = _optional_uuid(_UUID_A)
        self.assertIsNotNone(material)
        assert material is not None
        self.assertEqual(str(material), _UUID_A)


class TimestampTests(unittest.TestCase):
    def test_a_zulu_timestamp_parses_as_utc(self) -> None:
        self.assertEqual(_timestamp("2026-08-01T00:00:00Z"), datetime(2026, 8, 1, tzinfo=UTC))

    def test_naive_or_malformed_timestamps_are_refused(self) -> None:
        values: tuple[object, ...] = ("2026-08-01T00:00:00", "yesterday", "", 0, None)
        for value in values:
            with self.subTest(value=value), self.assertRaises(LocalEditingRenderRejected):
                _timestamp(value)


class TransitionTests(unittest.TestCase):
    def test_no_transition_is_allowed(self) -> None:
        self.assertIsNone(_transition(None))

    def test_a_known_transition_parses(self) -> None:
        parsed = _transition({"kind": "fade", "durationMs": 500})

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.duration_ms, 500)

    def test_a_malformed_transition_is_refused(self) -> None:
        cases: list[tuple[str, object]] = [
            ("not an object", "fade"),
            ("wrong key set", {"kind": "fade"}),
            ("kind is not a string", {"kind": 1, "durationMs": 500}),
            ("unknown kind", {"kind": "teleport", "durationMs": 500}),
            ("duration is not an int", {"kind": "fade", "durationMs": "500"}),
            ("duration is negative", {"kind": "fade", "durationMs": -1}),
        ]
        for label, value in cases:
            with self.subTest(label=label), self.assertRaises(LocalEditingRenderRejected):
                _transition(value)


class TrackTests(unittest.TestCase):
    def test_a_track_of_clips_parses(self) -> None:
        track = _track({"trackId": "t1", "kind": "visual", "clips": [_clip_document()]})

        self.assertEqual(len(track.clips), 1)

    def test_a_malformed_track_is_refused(self) -> None:
        cases: list[tuple[str, object]] = [
            ("clips is not a list", {"trackId": "t1", "kind": "visual", "clips": {}}),
            ("unknown kind", {"trackId": "t1", "kind": "hologram", "clips": []}),
            (
                "clip with the wrong key set",
                {"trackId": "t1", "kind": "visual", "clips": [{"clipId": "c"}]},
            ),
            (
                "clip the domain refuses",
                {
                    "trackId": "t1",
                    "kind": "visual",
                    "clips": [_clip_document(durationMs=-1)],
                },
            ),
        ]
        for label, value in cases:
            with self.subTest(label=label), self.assertRaises(LocalEditingRenderRejected):
                _track(value)


def _project_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "projectId": _UUID_A,
        "title": "真实出片",
        "output": {"width": 1280, "height": 720, "fps": 25},
        "captionStyle": {
            "fontKey": "noto-sans-cjk-sc",
            "fontPx": 42,
            "strokePx": 2,
            "lineSpacing": 1.2,
        },
        "createdAt": "2026-08-01T00:00:00Z",
    }
    document.update(overrides)
    return document


def _timeline_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "timelineId": _UUID_B,
        "projectId": _UUID_A,
        "revision": 1,
        "durationMs": 1_000,
        "tracks": [{"trackId": "t1", "kind": "visual", "clips": [_clip_document()]}],
        "createdAt": "2026-08-01T00:00:01Z",
    }
    document.update(overrides)
    return document


class ProjectTests(unittest.TestCase):
    def test_a_project_document_parses(self) -> None:
        project = _project(_project_document())

        self.assertEqual(project.project_id.uuid, UUID(_UUID_A))
        self.assertEqual(project.output.width, 1280)

    def test_a_project_the_domain_refuses_is_an_invalid_timeline(self) -> None:
        """Field-shape checks happen here; value rules stay in the domain type.

        An odd frame width is well-formed JSON of the right type, so nothing in
        this module can see it. `OutputSpec` is what refuses it, and the caller
        must still receive one closed worker code rather than the domain error.
        """
        cases: list[tuple[str, dict[str, Any]]] = [
            (
                "odd frame width",
                _project_document(output={"width": 1281, "height": 720, "fps": 25}),
            ),
            ("title is not text", _project_document(title=42)),
            (
                "caption size the domain refuses",
                _project_document(
                    captionStyle={
                        "fontKey": "noto-sans-cjk-sc",
                        "fontPx": 0,
                        "strokePx": 2,
                        "lineSpacing": 1.2,
                    }
                ),
            ),
        ]
        for label, document in cases:
            with self.subTest(label=label), self.assertRaises(LocalEditingRenderRejected) as caught:
                _project(document)
            self.assertIs(caught.exception.code, LocalEditingWorkerFailureCode.INVALID_TIMELINE)


class TimelineTests(unittest.TestCase):
    def test_a_timeline_document_parses(self) -> None:
        timeline = _timeline(_timeline_document())

        self.assertEqual(timeline.timeline_id.uuid, UUID(_UUID_B))
        self.assertEqual(len(timeline.tracks), 1)

    def test_tracks_that_are_not_a_list_are_refused_before_construction(self) -> None:
        """Refused by shape, not by iterating: a string would otherwise iterate."""
        for label, tracks in [("an object", {}), ("text", "visual"), ("nothing", None)]:
            with (
                self.subTest(label=label),
                self.assertRaises(LocalEditingRenderRejected) as caught,
            ):
                _timeline(_timeline_document(tracks=tracks))
            self.assertIs(caught.exception.code, LocalEditingWorkerFailureCode.INVALID_TIMELINE)

    def test_a_timeline_the_domain_refuses_is_an_invalid_timeline(self) -> None:
        with self.assertRaises(LocalEditingRenderRejected) as caught:
            _timeline(_timeline_document(durationMs=-1))

        self.assertIs(caught.exception.code, LocalEditingWorkerFailureCode.INVALID_TIMELINE)


class MaterialBindingTests(unittest.TestCase):
    def test_bindings_parse(self) -> None:
        bindings = _materials(
            [
                {"materialId": _UUID_A, "hasAudio": True},
                {"materialId": _UUID_B, "hasAudio": False},
            ]
        )

        self.assertEqual([binding.has_audio for binding in bindings], [True, False])

    def test_malformed_bindings_are_refused(self) -> None:
        cases: list[tuple[str, object]] = [
            ("not a list", {"materialId": _UUID_A, "hasAudio": True}),
            ("empty", []),
            ("wrong key set", [{"materialId": _UUID_A}]),
            ("audio flag is not a bool", [{"materialId": _UUID_A, "hasAudio": 1}]),
            (
                "the same material twice",
                [
                    {"materialId": _UUID_A, "hasAudio": True},
                    {"materialId": _UUID_A, "hasAudio": False},
                ],
            ),
        ]
        for label, value in cases:
            with self.subTest(label=label), self.assertRaises(LocalEditingRenderRejected):
                _materials(value)


class LoadRequestTests(unittest.TestCase):
    _JOB = UUID(_UUID_A)

    def _checkpoint(self, app_data: Path) -> Path:
        directory = app_data / "video-workspaces-v1" / "jobs" / str(self._JOB) / "checkpoints"
        directory.mkdir(parents=True)
        return directory / "local-editing-render-request.checkpoint"

    def _document(self) -> dict[str, Any]:
        return {
            "schemaVersion": "local-editing-render-request.v1",
            "jobId": str(self._JOB),
            "project": {},
            "timeline": {},
            "materials": [],
        }

    def test_a_well_formed_checkpoint_loads(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            app_data = Path(raw)
            self._checkpoint(app_data).write_text(json.dumps(self._document()), encoding="utf-8")

            self.assertEqual(_load_request(app_data, self._JOB)["jobId"], str(self._JOB))

    def test_a_missing_checkpoint_is_refused(self) -> None:
        with (
            tempfile.TemporaryDirectory() as raw,
            self.assertRaises(LocalEditingRenderRejected),
        ):
            _load_request(Path(raw), self._JOB)

    def test_a_symlinked_checkpoint_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            app_data = Path(raw)
            checkpoint = self._checkpoint(app_data)
            real = checkpoint.parent / "real.json"
            real.write_text(json.dumps(self._document()), encoding="utf-8")
            checkpoint.symlink_to(real)

            with self.assertRaises(LocalEditingRenderRejected):
                _load_request(app_data, self._JOB)

    def test_a_directory_in_place_of_the_checkpoint_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            app_data = Path(raw)
            self._checkpoint(app_data).mkdir()

            with self.assertRaises(LocalEditingRenderRejected):
                _load_request(app_data, self._JOB)

    def test_an_empty_or_oversized_checkpoint_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            app_data = Path(raw)
            checkpoint = self._checkpoint(app_data)

            for label, payload in [
                ("empty", b""),
                ("oversized", b"{" + b" " * (1024 * 1024) + b"}"),
            ]:
                checkpoint.write_bytes(payload)
                with self.subTest(label=label), self.assertRaises(LocalEditingRenderRejected):
                    _load_request(app_data, self._JOB)

    def test_an_unreadable_checkpoint_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            app_data = Path(raw)
            self._checkpoint(app_data).write_text(json.dumps(self._document()), encoding="utf-8")

            with (
                mock.patch.object(Path, "read_bytes", side_effect=OSError("denied")),
                self.assertRaises(LocalEditingRenderRejected),
            ):
                _load_request(app_data, self._JOB)

    def test_duplicate_keys_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            app_data = Path(raw)
            self._checkpoint(app_data).write_text('{"jobId": "a", "jobId": "b"}', encoding="utf-8")

            with self.assertRaises(LocalEditingRenderRejected):
                _load_request(app_data, self._JOB)

    def test_malformed_payloads_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            app_data = Path(raw)
            checkpoint = self._checkpoint(app_data)

            for label, payload in [
                ("not json", b"{oops"),
                ("invalid utf-8", b'{"a": "\xff\xfe"}'),
                ("not an object", b"[1, 2]"),
                ("wrong key set", b'{"schemaVersion": "x"}'),
            ]:
                checkpoint.write_bytes(payload)
                with self.subTest(label=label), self.assertRaises(LocalEditingRenderRejected):
                    _load_request(app_data, self._JOB)


class RenderFailureMappingTests(unittest.TestCase):
    """Total mapping: every rejection the renderer can raise must be translated."""

    _EXPECTED: ClassVar[dict[VisualRenderExecutionRejection, LocalEditingWorkerFailureCode]] = {
        VisualRenderExecutionRejection.INVALID_REQUEST: (
            LocalEditingWorkerFailureCode.INVALID_TIMELINE
        ),
        VisualRenderExecutionRejection.TOOL_UNAVAILABLE: (
            LocalEditingWorkerFailureCode.RENDER_FAILED
        ),
        VisualRenderExecutionRejection.SOURCE_CHANGED: (
            LocalEditingWorkerFailureCode.MATERIAL_UNAVAILABLE
        ),
        VisualRenderExecutionRejection.WORKSPACE_UNUSABLE: (
            LocalEditingWorkerFailureCode.WORKSPACE_UNUSABLE
        ),
        VisualRenderExecutionRejection.OUTPUT_EXISTS: (
            LocalEditingWorkerFailureCode.WORKSPACE_UNUSABLE
        ),
        VisualRenderExecutionRejection.CAPTION_FAILED: (
            LocalEditingWorkerFailureCode.FONT_UNAVAILABLE
        ),
        VisualRenderExecutionRejection.PROCESS_FAILED: LocalEditingWorkerFailureCode.RENDER_FAILED,
        VisualRenderExecutionRejection.TIMED_OUT: LocalEditingWorkerFailureCode.RENDER_FAILED,
        VisualRenderExecutionRejection.OUTPUT_TOO_LARGE: (
            LocalEditingWorkerFailureCode.RESOURCE_EXHAUSTED
        ),
        VisualRenderExecutionRejection.OUTPUT_INVALID: LocalEditingWorkerFailureCode.RENDER_FAILED,
    }

    def test_cancellation_is_not_a_failure(self) -> None:
        with self.assertRaises(LocalEditingRenderCancelled):
            _render_failure(VisualRenderExecutionRejected(VisualRenderExecutionRejection.CANCELLED))

    def test_every_other_rejection_maps_to_a_worker_code(self) -> None:
        for rejection, expected in self._EXPECTED.items():
            with self.subTest(rejection=rejection):
                with self.assertRaises(LocalEditingRenderRejected) as caught:
                    _render_failure(VisualRenderExecutionRejected(rejection))
                self.assertIs(caught.exception.code, expected)

    def test_only_a_changed_source_gets_its_own_diagnostic(self) -> None:
        with self.assertRaises(LocalEditingRenderRejected) as changed:
            _render_failure(
                VisualRenderExecutionRejected(VisualRenderExecutionRejection.SOURCE_CHANGED)
            )
        self.assertIs(
            changed.exception.diagnostic,
            LocalEditingRenderDiagnosticCode.SOURCE_CHANGED,
        )

        with self.assertRaises(LocalEditingRenderRejected) as other:
            _render_failure(VisualRenderExecutionRejected(VisualRenderExecutionRejection.TIMED_OUT))
        self.assertIs(other.exception.diagnostic, LocalEditingRenderDiagnosticCode.REJECTED)

    def test_the_mapping_covers_the_whole_source_vocabulary(self) -> None:
        """Guards the enum growing a member that nobody translated."""
        covered = set(self._EXPECTED) | {VisualRenderExecutionRejection.CANCELLED}

        self.assertEqual(
            covered,
            set(VisualRenderExecutionRejection),
            "a rejection with no mapping would raise KeyError inside the worker",
        )


def _bootstrap(root: Path) -> LocalEditingWorkerBootstrap:
    asset_root = root / "app-data"
    asset_root.mkdir(mode=0o700)
    tools = root / "tools"
    tools.mkdir(mode=0o700)
    for name in ("ffmpeg", "ffprobe"):
        binary = tools / name
        binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        binary.chmod(0o700)
    return LocalEditingWorkerBootstrap(
        asset_root=asset_root,
        media_tools=PackagedMediaTools(
            ffmpeg_path=tools / "ffmpeg",
            ffprobe_path=tools / "ffprobe",
        ),
        _session_token=b"x" * 32,
    )


class MaterialRegistryHomeTests(unittest.TestCase):
    def test_the_state_directory_is_created_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bootstrap = _bootstrap(Path(raw).resolve())

            _material_registry(bootstrap)

            state = bootstrap.asset_root / "local-executor" / "state"
            self.assertTrue(state.is_dir())
            self.assertEqual(state.lstat().st_mode & 0o077, 0)

    def test_an_existing_state_directory_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bootstrap = _bootstrap(Path(raw).resolve())
            (bootstrap.asset_root / "local-executor" / "state").mkdir(parents=True, mode=0o700)

            _material_registry(bootstrap)

    def test_a_state_directory_that_cannot_be_created_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bootstrap = _bootstrap(Path(raw).resolve())

            with (
                mock.patch.object(Path, "mkdir", side_effect=OSError("read-only")),
                self.assertRaises(LocalMaterialOperationRejected) as caught,
            ):
                _material_registry(bootstrap)

            self.assertIs(
                caught.exception.code,
                LocalMaterialWorkerFailureCode.REGISTRY_UNWRITABLE,
            )

    def test_a_state_directory_whose_mode_cannot_be_set_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bootstrap = _bootstrap(Path(raw).resolve())

            with (
                mock.patch.object(Path, "chmod", side_effect=OSError("denied")),
                self.assertRaises(LocalMaterialOperationRejected) as caught,
            ):
                _material_registry(bootstrap)

            self.assertIs(
                caught.exception.code,
                LocalMaterialWorkerFailureCode.REGISTRY_UNWRITABLE,
            )


class MaterialCommandTests(unittest.TestCase):
    def test_forgetting_an_unregistered_material_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bootstrap = _bootstrap(Path(raw).resolve())

            execute_local_material_forget(
                bootstrap, LocalMaterialForgetCommand(material_id=UUID(_UUID_A))
            )

    def test_forget_refuses_a_bootstrap_or_command_of_the_wrong_type(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bootstrap = _bootstrap(Path(raw).resolve())
            command = LocalMaterialForgetCommand(material_id=UUID(_UUID_A))

            for label, args in [
                ("bad bootstrap", (cast(Any, object()), command)),
                ("bad command", (bootstrap, cast(Any, object()))),
            ]:
                with (
                    self.subTest(label=label),
                    self.assertRaises(LocalMaterialOperationRejected) as caught,
                ):
                    execute_local_material_forget(*args)
                self.assertIs(
                    caught.exception.code,
                    LocalMaterialWorkerFailureCode.UNUSABLE_IDENTIFIER,
                )

    def test_import_refuses_a_bootstrap_or_command_of_the_wrong_type(self) -> None:
        """Refused before the registry home is touched, so nothing is created."""
        with tempfile.TemporaryDirectory() as raw:
            bootstrap = _bootstrap(Path(raw).resolve())
            source = Path(raw) / "source.mp4"
            source.write_bytes(b"controlled source")
            command = LocalMaterialImportCommand(material_id=UUID(_UUID_A), source_path=source)

            for label, args in [
                ("bad bootstrap", (cast(Any, object()), command)),
                ("bad command", (bootstrap, cast(Any, object()))),
            ]:
                with (
                    self.subTest(label=label),
                    self.assertRaises(LocalMaterialOperationRejected) as caught,
                ):
                    execute_local_material_import(*args)
                self.assertIs(
                    caught.exception.code,
                    LocalMaterialWorkerFailureCode.UNUSABLE_IDENTIFIER,
                )

            self.assertFalse((bootstrap.asset_root / "local-executor").exists())

    def test_forget_translates_a_registry_reason_into_a_worker_code(self) -> None:
        """The registry's own refusal is not allowed to escape as a registry error.

        A well-typed command can still name an identifier the registry cannot
        store -- the registry's key is the canonical v4 text -- and the caller of
        this worker only understands worker codes.
        """
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            bootstrap = _bootstrap(root)
            state = bootstrap.asset_root / "local-executor" / "state"
            state.mkdir(parents=True, mode=0o700)
            source = root / "source.mp4"
            source.write_bytes(b"controlled source")
            MaterialPathRegistry(state_directory=state).register(UUID(_UUID_A), source)

            with self.assertRaises(LocalMaterialOperationRejected) as caught:
                execute_local_material_forget(
                    bootstrap, LocalMaterialForgetCommand(material_id=UUID(int=0))
                )

            self.assertIs(
                caught.exception.code,
                LocalMaterialWorkerFailureCode.UNUSABLE_IDENTIFIER,
            )
            self.assertNotIn(str(source), str(caught.exception))

    def test_status_reports_an_unregistered_material(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bootstrap = _bootstrap(Path(raw).resolve())

            status = execute_local_material_status(
                bootstrap, LocalMaterialStatusCommand(material_id=UUID(_UUID_A))
            )

            self.assertIs(status, LocalMaterialWorkerStatus.NOT_REGISTERED)

    def test_status_never_raises_for_a_wrong_type(self) -> None:
        """Status is a query: it answers with a closed value, it does not fail."""
        with tempfile.TemporaryDirectory() as raw:
            bootstrap = _bootstrap(Path(raw).resolve())
            command = LocalMaterialStatusCommand(material_id=UUID(_UUID_A))

            for label, args in [
                ("bad bootstrap", (cast(Any, object()), command)),
                ("bad command", (bootstrap, cast(Any, object()))),
            ]:
                with self.subTest(label=label):
                    self.assertIs(
                        execute_local_material_status(*args),
                        LocalMaterialWorkerStatus.UNUSABLE_IDENTIFIER,
                    )

    def test_status_translates_a_registry_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bootstrap = _bootstrap(Path(raw).resolve())

            with mock.patch.object(
                Path,
                "mkdir",
                side_effect=OSError("read-only"),
            ):
                status = execute_local_material_status(
                    bootstrap, LocalMaterialStatusCommand(material_id=UUID(_UUID_A))
                )

            self.assertIs(status, LocalMaterialWorkerStatus.REGISTRY_UNWRITABLE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
