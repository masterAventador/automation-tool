"""Focused contract tests for the LE-18 T3 frozen acceptance driver."""

from __future__ import annotations

import os
import queue
import unittest
from pathlib import Path
from unittest import mock
from uuid import uuid4

from automation_tool.executor.material_probe import (
    MaterialProbeRejected,
    MaterialProbeRejection,
)

import run_le_18_t3_acceptance as acceptance


class _RunningProcess:
    def poll(self) -> None:
        return None


class FrozenWorkerEventTest(unittest.TestCase):
    @staticmethod
    def _next(lines: queue.Queue[str], source: Path) -> None:
        with mock.patch.object(acceptance, "TIMEOUT_SECONDS", 0.01):
            acceptance._next_event(
                lines,
                queue.Queue(),
                _RunningProcess(),  # type: ignore[arg-type]
                "worker.material.imported",
                [],
                source,
            )

    def test_material_failure_event_is_reported_immediately(self) -> None:
        lines: queue.Queue[str] = queue.Queue()
        lines.put(
            '{"event":"worker.material.import_failed",'
            '"failureCode":"registry_unreadable"}\n'
        )

        with self.assertRaisesRegex(
            AssertionError,
            r"worker\.material\.import_failed: registry_unreadable",
        ):
            self._next(lines, Path("/operator-private-source.mp4"))

    def test_unknown_failure_code_is_not_echoed(self) -> None:
        lines: queue.Queue[str] = queue.Queue()
        lines.put(
            '{"event":"worker.material.import_failed",'
            '"failureCode":"operator-private-name"}\n'
        )

        with self.assertRaisesRegex(
            AssertionError,
            r"invalid worker\.material\.import_failed$",
        ) as rejected:
            self._next(lines, Path("/operator-private-source.mp4"))

        self.assertNotIn("operator-private-name", str(rejected.exception))

    def test_event_source_path_leak_is_rejected_before_failure_reporting(self) -> None:
        source = Path("/operator-private-source.mp4")
        lines: queue.Queue[str] = queue.Queue()
        lines.put(
            '{"event":"worker.material.import_failed",'
            f'"failureCode":"registry_unreadable","detail":"{source}"}}\n'
        )

        with self.assertRaisesRegex(
            AssertionError,
            "event leaked the selected source path",
        ):
            self._next(lines, source)


class GeneratedSourceSettlementTest(unittest.TestCase):
    def test_retries_until_the_generated_source_is_unchanged(self) -> None:
        source = Path("/generated-source.mp4")
        still_writing = MaterialProbeRejected(MaterialProbeRejection.SOURCE_NOT_AT_REST)
        with (
            mock.patch.object(
                acceptance,
                "approve_source",
                return_value=(source, mock.sentinel.approved),
            ),
            mock.patch.object(
                acceptance,
                "require_source_unchanged",
                side_effect=[
                    still_writing,
                    (source, mock.sentinel.settled),
                ],
            ) as require,
            mock.patch("run_le_18_t3_acceptance.time.sleep") as sleep,
        ):
            acceptance._wait_for_generated_source(source)

        self.assertEqual(require.call_count, 2)
        self.assertEqual(sleep.call_count, 2)


class FrozenWorkerStatusEventTest(unittest.TestCase):
    def test_accepts_authenticated_closed_status(self) -> None:
        material_id = uuid4()
        token = os.urandom(32)
        event = {
            "event": "worker.material.status",
            "materialId": str(material_id),
            "workerKind": "python",
            "protocolVersion": "1.0",
            "workerVersion": "1.2.3",
            "status": "available",
        }
        event["authenticationProof"] = acceptance._proof(
            token,
            acceptance.EVENT_DOMAIN,
            "atvwp1.",
            (
                "worker.material.status",
                "python",
                "1.0",
                "1.2.3",
                f"{material_id}\0available",
            ),
        )

        acceptance._verify_status_event(event, token, material_id, "available")

    def test_rejects_unknown_status_without_echoing_it(self) -> None:
        material_id = uuid4()
        event = {
            "event": "worker.material.status",
            "workerVersion": "1.2.3",
            "status": "operator-private-status",
        }

        with self.assertRaisesRegex(
            AssertionError, "invalid material status$"
        ) as rejected:
            acceptance._verify_status_event(
                event,
                os.urandom(32),
                material_id,
                "available",
            )

        self.assertNotIn("operator-private-status", str(rejected.exception))


if __name__ == "__main__":
    unittest.main()
