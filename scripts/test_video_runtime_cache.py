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
import shutil
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


class SourcePackageInputTests(unittest.TestCase):
    """A pinned input can be a source package, not only a single pinning file.

    T32 removed the three background-music options from the material Worker's
    web UI because all three produced silence. The commit shipped. The package
    the user installed on 07-26 still had the control, because that Worker's
    cache key named only two contract files -- the source tree PyInstaller
    freezes was not part of it, so the release reused a binary built before the
    fix and the repair never reached anybody.

    A source package is a directory, so the digest has to cover its contents:
    an edit, a new file and a deleted file each change what the build produces.
    """

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="runtime-cache-package-"))
        self.package = self.base / "material_montage"
        (self.package / "webui").mkdir(parents=True)
        (self.package / "worker_main.py").write_text("main = 1\n", encoding="utf-8")
        (self.package / "webui" / "runtime.py").write_text("bgm = 3\n", encoding="utf-8")

    def test_editing_a_source_file_changes_the_fingerprint(self) -> None:
        before = contract_fingerprint([self.package])
        (self.package / "webui" / "runtime.py").write_text("bgm = 0\n", encoding="utf-8")
        self.assertNotEqual(
            before,
            contract_fingerprint([self.package]),
            "an edited Worker source file must invalidate the cached artifact",
        )

    def test_adding_a_source_file_changes_the_fingerprint(self) -> None:
        # Digesting only the file names that existed at the first build would
        # miss a new module entirely.
        before = contract_fingerprint([self.package])
        (self.package / "gateway.py").write_text("serve = 1\n", encoding="utf-8")
        self.assertNotEqual(before, contract_fingerprint([self.package]))

    def test_removing_a_source_file_changes_the_fingerprint(self) -> None:
        (self.package / "gateway.py").write_text("serve = 1\n", encoding="utf-8")
        before = contract_fingerprint([self.package])
        (self.package / "gateway.py").unlink()
        self.assertNotEqual(before, contract_fingerprint([self.package]))

    def test_the_same_package_in_another_checkout_fingerprints_the_same(self) -> None:
        """Worktrees must share the cache instead of each rebuilding everything.

        Keying by absolute path would give every worktree its own key, turning
        a shared per-machine cache into one PyInstaller run per checkout.
        """
        elsewhere = self.base / "another-worktree"
        elsewhere.mkdir()
        shutil.copytree(self.package, elsewhere / self.package.name)
        self.assertEqual(
            contract_fingerprint([self.package]),
            contract_fingerprint([elsewhere / self.package.name]),
        )

    def test_two_modules_with_one_name_in_different_directories_both_count(self) -> None:
        """Keying by base name alone would let one file mask the other."""
        (self.package / "webui" / "adapter.py").write_text("a = 1\n", encoding="utf-8")
        (self.package / "adapter.py").write_text("a = 1\n", encoding="utf-8")
        before = contract_fingerprint([self.package])
        (self.package / "webui" / "adapter.py").write_text("a = 2\n", encoding="utf-8")
        self.assertNotEqual(before, contract_fingerprint([self.package]))

    def test_compiled_python_caches_do_not_enter_the_fingerprint(self) -> None:
        """Otherwise merely importing the package would invalidate the cache."""
        before = contract_fingerprint([self.package])
        cache = self.package / "__pycache__"
        cache.mkdir()
        (cache / "worker_main.cpython-312.pyc").write_bytes(b"\x00compiled")
        (self.package / "stray.pyc").write_bytes(b"\x00compiled")
        self.assertEqual(before, contract_fingerprint([self.package]))

    def test_an_empty_source_package_is_rejected(self) -> None:
        # A vanished source tree must not fingerprint as "nothing changed". The
        # reason is asserted because refusing every directory would satisfy a
        # bare assertRaises while making directories unusable as inputs.
        empty = self.base / "empty_package"
        empty.mkdir()
        with self.assertRaisesRegex(VideoRuntimeCacheRejected, "holds no files"):
            contract_fingerprint([empty])

    def test_a_symlink_inside_a_source_package_is_rejected(self) -> None:
        """Its target is outside the digest, so its bytes could change unseen."""
        (self.package / "linked.py").symlink_to(self.base / "outside.py")
        with self.assertRaisesRegex(VideoRuntimeCacheRejected, "symbolic link"):
            contract_fingerprint([self.package])

    def test_inputs_that_collide_on_one_cache_key_name_are_rejected(self) -> None:
        # Two entries sharing a name make the key ambiguous about which file
        # each digest belongs to, so refuse rather than guess.
        twin = self.base / "elsewhere" / "material_montage"
        twin.mkdir(parents=True)
        (twin / "worker_main.py").write_text("main = 2\n", encoding="utf-8")
        with self.assertRaisesRegex(VideoRuntimeCacheRejected, "share the cache key"):
            contract_fingerprint([self.package, twin])


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

    def test_editing_a_worker_source_file_rebuilds_the_artifact(self) -> None:
        """The T32 defect, at the layer that decided the fix would not ship.

        The material Worker's web UI fix was committed and the release still
        installed the Worker built before it, because the cache answered "still
        current" for a source tree that had changed underneath it.
        """
        package = self.base / "material_montage"
        package.mkdir()
        (package / "webui_runtime.py").write_text("bgm_volume_select\n", encoding="utf-8")
        inputs = [self.contract, package]

        first = ensure_cached(
            name="material-video-worker", contracts=inputs, build=self.build, root=self.root
        )
        (package / "webui_runtime.py").write_text("# control removed\n", encoding="utf-8")
        second = ensure_cached(
            name="material-video-worker", contracts=inputs, build=self.build, root=self.root
        )

        self.assertEqual(first, second)
        self.assertEqual(
            2,
            len(self.builds),
            "an edited Worker source file must not be served from the old build",
        )

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
