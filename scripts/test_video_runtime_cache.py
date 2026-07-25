#!/usr/bin/env python3
"""Tests for the reusable video runtime build cache.

Why this exists: ffmpeg, the motion Worker and the material Worker are all
built from pinned versions with pinned source digests, so their output is
deterministic — yet every acceptance run rebuilt them into a temporary
directory and deleted them afterwards. Rebuilding ffmpeg from source costs
minutes each time and produces a byte-identical result.

Treating them as disposable is correct for a database container and wrong for
a version-pinned compiled artifact. This cache keeps them on a stable
per-machine path, keyed by the digest of the contracts that pin them, so a
rebuild happens exactly when the pinned inputs change.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from video_runtime_cache import (  # noqa: E402
    VideoRuntimeCacheRejected,
    cache_root,
    contract_fingerprint,
    ensure_cached,
)


class ContractFingerprintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="runtime-cache-fingerprint-"))
        self.first = self.base / "first.json"
        self.second = self.base / "second.json"
        self.first.write_text('{"version": "1"}', encoding="utf-8")
        self.second.write_text('{"runtime": "node"}', encoding="utf-8")

    def test_the_same_contracts_produce_the_same_fingerprint(self) -> None:
        self.assertEqual(
            contract_fingerprint([self.first, self.second]),
            contract_fingerprint([self.first, self.second]),
        )

    def test_changing_a_contract_changes_the_fingerprint(self) -> None:
        before = contract_fingerprint([self.first, self.second])
        self.first.write_text('{"version": "2"}', encoding="utf-8")
        self.assertNotEqual(before, contract_fingerprint([self.first, self.second]))

    def test_order_does_not_matter(self) -> None:
        self.assertEqual(
            contract_fingerprint([self.first, self.second]),
            contract_fingerprint([self.second, self.first]),
        )

    def test_a_missing_contract_is_rejected(self) -> None:
        # Silently fingerprinting an absent contract would let the cache
        # believe a rebuild is unnecessary after the pin was deleted.
        with self.assertRaises(VideoRuntimeCacheRejected):
            contract_fingerprint([self.base / "absent.json"])


class EnsureCachedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="runtime-cache-"))
        self.root = self.base / "cache"
        self.contract = self.base / "contract.json"
        self.contract.write_text('{"pinned": "a"}', encoding="utf-8")
        self.builds: list[Path] = []

    def build(self, destination: Path) -> None:
        self.builds.append(destination)
        destination.mkdir(parents=True)
        (destination / "artifact").write_bytes(b"built")

    def ensure(self) -> Path:
        return ensure_cached(
            name="media-toolchain",
            contracts=[self.contract],
            build=self.build,
            root=self.root,
        )

    def test_the_first_call_builds(self) -> None:
        cached = self.ensure()
        self.assertEqual(len(self.builds), 1)
        self.assertEqual((cached / "artifact").read_bytes(), b"built")

    def test_a_second_call_reuses_the_artifact(self) -> None:
        first = self.ensure()
        second = self.ensure()
        self.assertEqual(first, second)
        self.assertEqual(len(self.builds), 1)

    def test_changing_the_pinned_contract_rebuilds(self) -> None:
        self.ensure()
        self.contract.write_text('{"pinned": "b"}', encoding="utf-8")
        self.ensure()
        self.assertEqual(len(self.builds), 2)

    def test_a_deleted_artifact_rebuilds_even_though_the_stamp_survives(self) -> None:
        cached = self.ensure()
        (cached / "artifact").unlink()
        cached.rmdir()
        self.ensure()
        self.assertEqual(len(self.builds), 2)

    def test_a_failed_build_leaves_no_stamp_to_trust(self) -> None:
        def failing(destination: Path) -> None:
            destination.mkdir(parents=True)
            (destination / "partial").write_bytes(b"half")
            raise RuntimeError("build blew up")

        with self.assertRaises(RuntimeError):
            ensure_cached(
                name="media-toolchain",
                contracts=[self.contract],
                build=failing,
                root=self.root,
            )
        # A half-built tree left behind with a valid stamp would be handed to
        # the release assembler as if it were complete.
        self.ensure()
        self.assertEqual(len(self.builds), 1)
        self.assertFalse((self.root / "media-toolchain" / "partial").exists())

    def test_a_corrupt_stamp_rebuilds_instead_of_raising(self) -> None:
        self.ensure()
        stamp = self.root / "media-toolchain.stamp.json"
        stamp.write_text("not json at all", encoding="utf-8")
        self.ensure()
        self.assertEqual(len(self.builds), 2)

    def test_the_stamp_records_the_fingerprint_it_was_built_from(self) -> None:
        self.ensure()
        stamp = json.loads(
            (self.root / "media-toolchain.stamp.json").read_text(encoding="utf-8")
        )
        self.assertEqual(stamp["fingerprint"], contract_fingerprint([self.contract]))


class CacheRootTests(unittest.TestCase):
    def test_the_cache_root_is_project_scoped_and_outside_the_repository(self) -> None:
        root = cache_root()
        self.assertIn("automation-tool", root.name)
        # The Worker build scripts refuse to write inside the checkout, and a
        # cache under the repository would also be swept by repo-wide cleanup.
        with self.assertRaises(ValueError):
            root.relative_to(ROOT)


if __name__ == "__main__":
    unittest.main()
