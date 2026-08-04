#!/usr/bin/env python3
"""One unpacked Chromium per machine, shared by dev builds and releases.

Both sides already unpacked the same locked archive with the same
`build_staging()` and the same contract — but into two different places: the
desktop-E2E cache under `.local/desktop-e2e/`, and a fresh directory on every
release run. Same inputs, same code, two copies, and only the digest manifest
standing between them and drifting apart unnoticed.

Unpacking is not cheap (a 171 MB archive into 328 files, each digested), and
"two copies that should be identical" is exactly the shape of the problems this
repository keeps paying for. So it becomes one cache, keyed on the staging
contract, which pins `archive_sha256` per target — swap the archive and the key
changes with it.

The release still signs every Mach-O and re-takes the inventory afterwards, so
what it ships genuinely differs from the cache. That work happens on its own
copy: the cache holds the pre-signature tree only, and nothing signs in place.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from desktop_e2e_prerequisites import (  # noqa: E402
    DesktopPrerequisiteRejected,
    release_target_id,
)
from embedded_browser_staging_cache import (  # noqa: E402
    STAGING_CONTRACT_PATH,
    cache_name,
    ensure_staged_browser,
    locked_archive,
)
from video_runtime_cache import cache_root  # noqa: E402

# The target this host can actually stage. Naming one outright pinned the whole
# suite to whichever machine the author happened to be on; see `HostTargetTests`.
try:
    TARGET_ID: str | None = release_target_id()
except DesktopPrerequisiteRejected:
    TARGET_ID = None


def stage_for_this_host(test: unittest.TestCase) -> tuple[str, Path]:
    """Return this host's target and staged tree, or skip saying why not.

    The distinction matters more than it looks: an absent archive is a fact
    about this machine, while a failure inside `ensure_staged_browser` is a
    defect in the cache. Letting the first one raise made them the same red.
    """
    if TARGET_ID is None:
        test.skipTest(f"no release target is declared for {sys.platform}")
        raise AssertionError("unreachable")
    archive = locked_archive(TARGET_ID)
    if not archive.is_file():
        test.skipTest(f"the locked Chromium archive is not downloaded yet: {archive}")
    return TARGET_ID, ensure_staged_browser(target_id=TARGET_ID)


class CacheLocationTests(unittest.TestCase):
    def test_cache_lives_beside_the_other_pinned_artifacts(self) -> None:
        _, staged = stage_for_this_host(self)

        self.assertTrue(
            staged.is_relative_to(cache_root()),
            f"{staged} must sit in the shared machine artifact cache",
        )

    def test_each_target_gets_its_own_cache_entry(self) -> None:
        self.assertNotEqual(
            cache_name("macos-arm64"),
            cache_name("windows-x86_64"),
            "two targets sharing one cache entry would overwrite each other",
        )

    def test_the_staging_contract_is_the_cache_key(self) -> None:
        # It pins `archive_sha256` per target, so a swapped archive changes the
        # key. A cache keyed on anything less would serve the previous browser.
        self.assertTrue(STAGING_CONTRACT_PATH.is_file())
        text = STAGING_CONTRACT_PATH.read_text(encoding="utf-8")
        self.assertIn("archive_sha256", text)


class CacheContentTests(unittest.TestCase):
    def test_the_cached_tree_is_a_verified_distribution(self) -> None:
        _, staged = stage_for_this_host(self)

        manifest = staged / "distribution-manifest.v1.json"
        self.assertTrue(manifest.is_file(), "the cache has no distribution manifest")
        files = sum(1 for path in staged.rglob("*") if path.is_file())
        self.assertGreater(files, 300, f"implausibly small Chromium tree: {files} files")

    def test_second_call_reuses_the_tree_rather_than_unpacking_again(self) -> None:
        target, first = stage_for_this_host(self)
        marker = first / "distribution-manifest.v1.json"
        before = marker.stat().st_mtime_ns

        second = ensure_staged_browser(target_id=target)

        self.assertEqual(first, second)
        self.assertEqual(
            before,
            marker.stat().st_mtime_ns,
            "the cache was rebuilt even though its key had not changed",
        )


class CopyTests(unittest.TestCase):
    def test_a_copy_preserves_symlinks_and_still_verifies(self) -> None:
        """Chrome's framework is a symlink tree; dereferencing it breaks it.

        `shutil.copytree` follows symlinks by default, which turns the 330-file
        distribution into 848 real files and makes `verify_distribution` report
        "symlink entry drifted". The manifest declares those links, so a copy
        that flattens them is not the distribution any more — and the release
        signs and ships whatever it copied.

        Stated plainly because the suite now runs on whichever target the host
        can stage: `windows-x86_64` carries **no** symlinks (measured — 310
        files, 0 links), so on Windows the symlink half of this holds trivially
        and what is left being checked is file-count preservation and manifest
        verification. Both are real; neither is the symlink property. The link
        count is asserted below so the record says which one this run proved.
        """
        import tempfile

        from build_embedded_browser_distribution import verify_distribution
        from embedded_browser_staging_cache import copy_staged_browser

        target, cached = stage_for_this_host(self)
        cached_files = sum(1 for path in cached.rglob("*") if path.is_file())

        with tempfile.TemporaryDirectory(prefix="staged-copy-") as temporary:
            output = Path(temporary) / "browser-staging"
            copy_staged_browser(target_id=target, output=output)

            copied = sum(1 for path in output.rglob("*") if path.is_file())
            self.assertEqual(
                cached_files, copied, "the copy did not preserve the tree shape"
            )
            cached_links = sum(1 for path in cached.rglob("*") if path.is_symlink())
            copied_links = sum(1 for path in output.rglob("*") if path.is_symlink())
            self.assertEqual(
                cached_links,
                copied_links,
                f"{target}: the copy did not preserve the {cached_links} declared links",
            )
            report = verify_distribution(staging=output, target_id=target)
            self.assertGreater(report.verified_files, 300)


class HostTargetTests(unittest.TestCase):
    def test_the_suite_exercises_the_target_this_host_can_actually_stage(self) -> None:
        """A suite pinned to a foreign target cannot pass anywhere else.

        Four of the tests above named `macos-arm64` outright. On a Windows host
        — the one that builds the Windows package — that archive is never going
        to be present, so they did not skip, they *errored*:

            EmbeddedBrowserStagingUnavailable: the locked Chromium archive is
            not downloaded yet: …\\embedded-browser-archives\\chrome-mac-arm64.zip

        Which makes "this suite does not apply on this host" and "the shared
        staging cache is broken" produce the same red. The cache itself is
        cross-platform by construction (one `ensure_staged_browser`, one
        contract, a target id), so the suite should exercise whichever target
        the host is actually able to stage.
        """
        self.assertEqual(
            TARGET_ID,
            release_target_id(),
            "the staging-cache suite is pinned to a target this host cannot stage",
        )

    def test_no_staging_call_names_a_target_literally(self) -> None:
        """The constant above is not enough on its own to keep this fixed.

        It only says what `TARGET_ID` holds. Someone writing
        `ensure_staged_browser(target_id="macos-arm64")` at a call site puts the
        suite straight back where it was and this guard would still be green,
        which is the shape of a gate that stops catching the thing it was added
        for. Read from the AST rather than by regex so a rename or a reformat
        cannot slip past it.

        `cache_name("macos-arm64")` is deliberately still allowed: comparing two
        target names is what that test is *for*, and it stages nothing.
        """
        import ast

        source = Path(__file__).read_text(encoding="utf-8")
        staging_calls = {
            "ensure_staged_browser",
            "copy_staged_browser",
            "verify_distribution",
            "locked_archive",
        }
        offenders = []
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            called = node.func
            name = called.attr if isinstance(called, ast.Attribute) else getattr(called, "id", "")
            if name not in staging_calls:
                continue
            for keyword in node.keywords:
                if keyword.arg == "target_id" and isinstance(keyword.value, ast.Constant):
                    literal = keyword.value.value
                    offenders.append(
                        f"line {node.lineno}: {name}(target_id={literal!r})"
                    )
            for argument in node.args:
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    offenders.append(f"line {node.lineno}: {name}({argument.value!r})")

        self.assertEqual(
            [],
            offenders,
            "a staging call names a target literally, so this suite errors on "
            f"every other host: {offenders}",
        )


if __name__ == "__main__":
    unittest.main()
