"""Focused contract tests for the LE-18 T3 frozen acceptance driver."""

from __future__ import annotations

import queue
import unittest
from pathlib import Path
from unittest import mock

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


if __name__ == "__main__":
    unittest.main()
