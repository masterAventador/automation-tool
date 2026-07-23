#!/usr/bin/env python3
"""Deterministic rejection tests for the IM-08 formal-evidence verifier."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from verify_im_08_formal_evidence import EvidenceRejected, verify


class Im08EvidenceVerifierTest(unittest.TestCase):
    def test_rejects_non_real_evidence_before_reading_samples(self) -> None:
        with tempfile.TemporaryDirectory(prefix="im08-reject-") as directory:
            root = Path(directory).resolve()
            (root / "manifest.json").write_text(json.dumps({
                "schemaVersion": 1,
                "evidenceKind": "fixture",
                "samples": [],
                "failureScenarios": [],
                "platformPackages": [],
            }), encoding="utf-8")
            with self.assertRaisesRegex(EvidenceRejected, "real external-service"):
                verify(root, "ffprobe")

    def test_rejects_unknown_manifest_fields(self) -> None:
        with tempfile.TemporaryDirectory(prefix="im08-schema-") as directory:
            root = Path(directory).resolve()
            (root / "manifest.json").write_text(json.dumps({
                "schemaVersion": 1,
                "evidenceKind": "real_external_services",
                "samples": [],
                "failureScenarios": [],
                "platformPackages": [],
                "videoPath": "/tmp/not-allowed.mp4",
            }), encoding="utf-8")
            with self.assertRaisesRegex(EvidenceRejected, "schema mismatch"):
                verify(root, "ffprobe")

    def test_rejects_symlinked_manifest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="im08-link-") as directory:
            root = Path(directory).resolve()
            outside = root / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            try:
                (root / "manifest.json").symlink_to(outside)
            except OSError:
                self.skipTest("symlinks unavailable")
            with self.assertRaisesRegex(EvidenceRejected, "file missing"):
                verify(root, "ffprobe")


if __name__ == "__main__":
    unittest.main()
