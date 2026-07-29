"""Mutation self-check for the material probe's content digest (LE-07 T4).

Each entry rewrites one thing in the production module and reruns the digest
tests. A mutation that survives is a claim no test is making, so the survivors
are the output that matters — they are recorded, with a verdict for each, in
`docs/development/LE-07.md`.

Run it from anywhere:

    backend/.venv/bin/python scripts/mutate_material_probe.py

**The working tree is never edited.** `backend/src` is copied to a temporary
directory and every mutation is applied there, with `PYTHONPATH` pointing the
test run at the copy — measured, a `PYTHONPATH` entry precedes the editable
install's `.pth` in `sys.path`, so the copy wins without the virtualenv being
touched either. A pre-flight check asserts the module really did resolve to the
copy, because a silent fall-through to the real tree would make every result a
lie, and the tree's digest is compared again at the end.

That is worth the trouble: an earlier version mutated the tree in place, and a
hard kill skipped its restore and left the tree holding a mutant with the source
guard removed — while the digest it printed, taken before the run, still matched.

Not a pytest file and not imported by anything shipped: it edits source on disk,
so it must never run as part of an ordinary test session.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parent.parent
BACKEND = REPOSITORY / "backend"
RELATIVE_MODULE = Path("automation_tool/executor/material_probe.py")
TESTS = BACKEND / "tests/unit/executor/test_material_probe.py"
PYTHON = BACKEND / ".venv/bin/python"

SELECTION = (
    "TestContentDigest or TestContentDigestRejectsAnUnusableSource or "
    "TestContentDigestByteLimit or TestContentDigestNeedsTheFileToHoldStill or "
    "TestContentDigestNeedsMoreThanTheInode or "
    "TestContentDigestLeaksNoPath"
)

# (label, old, new). The entry labelled CANARY must die; if it survives, the
# harness is not running what it thinks it is running.
MUTATIONS: list[tuple[str, str, str]] = [
    # --- the byte limit, both endpoints and both directions ---
    (
        "limit `>` -> `>=`",
        "if opened.st_size > MAX_SOURCE_FILE_BYTES:",
        "if opened.st_size >= MAX_SOURCE_FILE_BYTES:",
    ),
    (
        "limit off by one up",
        "if opened.st_size > MAX_SOURCE_FILE_BYTES:",
        "if opened.st_size > MAX_SOURCE_FILE_BYTES + 1:",
    ),
    (
        "limit off by one down",
        "if opened.st_size > MAX_SOURCE_FILE_BYTES:",
        "if opened.st_size > MAX_SOURCE_FILE_BYTES - 1:",
    ),
    (
        "limit guard deleted",
        "        if opened.st_size > MAX_SOURCE_FILE_BYTES:\n"
        "            _reject(MaterialProbeRejection.FILE_TOO_LARGE)\n",
        "",
    ),
    (
        "limit value halved",
        "MAX_SOURCE_FILE_BYTES: Final = 16 * 1024 * 1024 * 1024",
        "MAX_SOURCE_FILE_BYTES: Final = 8 * 1024 * 1024 * 1024",
    ),
    (
        "limit value quadrupled",
        "MAX_SOURCE_FILE_BYTES: Final = 16 * 1024 * 1024 * 1024",
        "MAX_SOURCE_FILE_BYTES: Final = 64 * 1024 * 1024 * 1024",
    ),
    (
        "limit read from path not descriptor",
        "opened = os.fstat(descriptor)",
        "opened = path.stat()",
    ),
    # --- the growth bound ---
    (
        "growth bound `>` -> `>=`",
        "if read_bytes > opened.st_size:",
        "if read_bytes >= opened.st_size:",
    ),
    (
        "growth bound deleted",
        "            if read_bytes > opened.st_size:\n                return None\n",
        "",
    ),
    # --- the short-read check ---
    (
        "short-read `<` -> `<=`",
        "if read_bytes < opened.st_size:",
        "if read_bytes <= opened.st_size:",
    ),
    (
        "short-read check deleted",
        "    if read_bytes < opened.st_size:\n        return None\n",
        "",
    ),
    # --- the closing identity check ---
    (
        "identity check deleted",
        "    if not _names_the_same_file(opened, path.stat()):\n        return None\n",
        "",
    ),
    (
        "identity compares dev only",
        "return left.st_dev == right.st_dev and left.st_ino == right.st_ino",
        "return left.st_dev == right.st_dev",
    ),
    (
        "identity compares ino only",
        "return left.st_dev == right.st_dev and left.st_ino == right.st_ino",
        "return left.st_ino == right.st_ino",
    ),
    # The path is what may have been swapped; a descriptor cannot change identity
    # behind itself, so comparing against it can never fire. An earlier entry
    # here re-opened the already-closed descriptor instead, which raised `EBADF`
    # on every call and so was killed by any happy-path test without saying
    # anything at all about this check.
    (
        "closing check looks at the descriptor",
        "_names_the_same_file(opened, path.stat())",
        "_names_the_same_file(opened, after)",
    ),
    # --- the digest itself ---
    ("sha256 -> sha1", "digest = hashlib.sha256()", "digest = hashlib.sha1()"),
    (
        "hexdigest -> uppercase",
        "return digest.hexdigest()",
        "return digest.hexdigest().upper()",
    ),
    (
        "chunk size 1 MiB -> 4 KiB",
        "_DIGEST_CHUNK_BYTES: Final = 1024 * 1024",
        "_DIGEST_CHUNK_BYTES: Final = 4096",
    ),
    (
        "skip the first chunk",
        "        while chunk := os.read(descriptor, _DIGEST_CHUNK_BYTES):",
        "        os.read(descriptor, _DIGEST_CHUNK_BYTES)\n"
        "        while chunk := os.read(descriptor, _DIGEST_CHUNK_BYTES):",
    ),
    ("hash the chunk reversed", "digest.update(chunk)", "digest.update(chunk[::-1])"),
    # --- the entry point ---
    (
        "source guard deleted",
        "    path, expected = _require_source_file(source)",
        "    path, expected = source, os.stat(source)",
    ),
    (
        "reason UNREADABLE -> UNSAFE_PATH",
        "        _reject(MaterialProbeRejection.UNREADABLE)\n    return content_digest",
        "        _reject(MaterialProbeRejection.UNSAFE_PATH)\n    return content_digest",
    ),
    (
        "reason FILE_TOO_LARGE -> PROBE_FAILED",
        "_reject(MaterialProbeRejection.FILE_TOO_LARGE)",
        "_reject(MaterialProbeRejection.PROBE_FAILED)",
    ),
    (
        "`is None` -> `is not None`",
        "    if content_digest is None:\n        _reject(",
        "    if content_digest is not None:\n        _reject(",
    ),
    (
        "except OSError -> except Exception",
        "    except OSError:\n        content_digest = None",
        "    except Exception:\n        content_digest = None",
    ),
    (
        "reject inside the handler",
        "    except OSError:\n        content_digest = None\n    if content_digest is None:",
        "    except OSError:\n        _reject(MaterialProbeRejection.UNREADABLE)\n"
        "    if content_digest is None:",
    ),
    # --- the file has to hold still ---
    (
        "mtime check deleted",
        "    if after.st_mtime_ns != opened.st_mtime_ns:\n        return None\n",
        "",
    ),
    (
        "mtime compared at second resolution",
        "if after.st_mtime_ns != opened.st_mtime_ns:",
        "if int(after.st_mtime) != int(opened.st_mtime):",
    ),
    (
        "`after` taken before the read",
        "        after = os.fstat(descriptor)",
        "        after = opened",
    ),
    # --- the open itself ---
    (
        "O_NONBLOCK dropped",
        "os.open(os.fspath(path), os.O_RDONLY | os.O_NONBLOCK)",
        "os.open(os.fspath(path), os.O_RDONLY)",
    ),
    (
        "S_ISREG check deleted",
        "        if not stat.S_ISREG(opened.st_mode):\n            return None\n",
        "",
    ),
    (
        "pre-open stat check deleted",
        "        if not _names_the_same_file(expected, opened):\n            return None\n",
        "",
    ),
    # --- canary: must die ---
    (
        "CANARY message changed",
        'super().__init__("material probe rejected")',
        'super().__init__("material probe rejected!")',
    ),
]


def _environment(clone_source: Path) -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": str(Path.home()),
        "PYTHONPATH": str(clone_source),
    }


def run_tests(clone_source: Path) -> bool:
    """True when the suite passed. A hang counts as a detection, not a survival."""
    for cache in clone_source.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    completed = subprocess.run(
        [str(PYTHON), "-B", "-m", "pytest", str(TESTS), "-x", "-q", "-k", SELECTION],
        cwd=BACKEND,
        capture_output=True,
        env=_environment(clone_source),
        timeout=300,
    )
    return completed.returncode == 0


def run_tests_guarded(clone_source: Path) -> bool:
    try:
        return run_tests(clone_source)
    except subprocess.TimeoutExpired:
        print("    (suite hung — counted as killed)", flush=True)
        return False


def digest_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    tree_module = BACKEND / "src" / RELATIVE_MODULE
    before = digest_of(tree_module)
    print("module sha256:", before)

    survivors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="automation-tool-mutate-") as workspace:
        clone_source = Path(workspace) / "src"
        shutil.copytree(BACKEND / "src", clone_source)
        clone_module = clone_source / RELATIVE_MODULE
        pristine = clone_module.read_text(encoding="utf-8")

        resolved = subprocess.run(
            [
                str(PYTHON),
                "-c",
                "import automation_tool.executor.material_probe as m; print(m.__file__)",
            ],
            cwd=BACKEND,
            capture_output=True,
            text=True,
            env=_environment(clone_source),
        ).stdout.strip()
        if resolved != str(clone_module):
            print(
                f"tests would import {resolved}, not the copy — refusing to report anything"
            )
            return 1
        print("mutating a copy at", clone_module, flush=True)

        if not run_tests_guarded(clone_source):
            print("BASELINE IS RED — stopping")
            return 1
        print("baseline green\n", flush=True)

        for label, old, new in MUTATIONS:
            if pristine.count(old) != 1:
                print(
                    f"{label}: SKIPPED (anchor appears {pristine.count(old)} times)",
                    flush=True,
                )
                survivors.append(f"{label} (anchor)")
                continue
            clone_module.write_text(pristine.replace(old, new), encoding="utf-8")
            alive = run_tests_guarded(clone_source)
            print(f"{label}: {'SURVIVED' if alive else 'killed'}", flush=True)
            if alive:
                survivors.append(label)
            clone_module.write_text(pristine, encoding="utf-8")

    print(f"\n{len(MUTATIONS) - len(survivors)}/{len(MUTATIONS)} killed")
    if survivors:
        print("survivors:", *survivors, sep="\n  ")

    unchanged = digest_of(tree_module) == before
    print("working tree module unchanged:", unchanged)
    return 0 if unchanged else 1


if __name__ == "__main__":
    sys.exit(main())
