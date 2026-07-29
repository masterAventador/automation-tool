"""Mutation self-check for the material probe's content digest (LE-07 T4).

Each entry rewrites one thing in the production module and reruns the digest
tests. A mutation that survives is a claim no test is making, so the survivors
are the output that matters — they are recorded, with a verdict for each, in
`docs/development/LE-07.md`.

Run it from the repository root:

    backend/.venv/bin/python scripts/mutate_material_probe.py

The module is restored in a `finally`, but a hard kill skips that: if the run is
interrupted, check `git diff` on the module rather than the printed digest,
which is taken before the run starts. That has actually happened once.

Not a pytest file and not imported by anything shipped: it edits source on disk,
so it must never run as part of an ordinary test session.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
MODULE = BACKEND / "src/automation_tool/executor/material_probe.py"
TESTS = BACKEND / "tests/unit/executor/test_material_probe.py"
PYTHON = BACKEND / ".venv/bin/python"

# (label, old, new). `None` for `old` marks the canary: it must die.
MUTATIONS: list[tuple[str, str, str]] = [
    # --- the byte limit, both endpoints and both directions ---
    ("limit `>` -> `>=`", "if opened.st_size > MAX_SOURCE_FILE_BYTES:", "if opened.st_size >= MAX_SOURCE_FILE_BYTES:"),
    ("limit off by one up", "if opened.st_size > MAX_SOURCE_FILE_BYTES:", "if opened.st_size > MAX_SOURCE_FILE_BYTES + 1:"),
    ("limit off by one down", "if opened.st_size > MAX_SOURCE_FILE_BYTES:", "if opened.st_size > MAX_SOURCE_FILE_BYTES - 1:"),
    ("limit guard deleted", "        if opened.st_size > MAX_SOURCE_FILE_BYTES:\n            _reject(MaterialProbeRejection.FILE_TOO_LARGE)\n", ""),
    ("limit value halved", "MAX_SOURCE_FILE_BYTES: Final = 16 * 1024 * 1024 * 1024", "MAX_SOURCE_FILE_BYTES: Final = 8 * 1024 * 1024 * 1024"),
    ("limit value quadrupled", "MAX_SOURCE_FILE_BYTES: Final = 16 * 1024 * 1024 * 1024", "MAX_SOURCE_FILE_BYTES: Final = 64 * 1024 * 1024 * 1024"),
    ("limit read from path not descriptor", "opened = os.fstat(descriptor)", "opened = path.stat()"),
    # --- the growth bound ---
    ("growth bound `>` -> `>=`", "if read_bytes > opened.st_size:", "if read_bytes >= opened.st_size:"),
    ("growth bound deleted", "            if read_bytes > opened.st_size:\n                return None\n", ""),
    # --- the short-read check ---
    ("short-read `<` -> `<=`", "if read_bytes < opened.st_size:", "if read_bytes <= opened.st_size:"),
    ("short-read check deleted", "    if read_bytes < opened.st_size:\n        return None\n", ""),
    # --- the identity check ---
    ("identity check deleted", "    if not _names_the_same_file(opened, path.stat()):\n        return None\n", ""),
    ("identity compares dev only", "return left.st_dev == right.st_dev and left.st_ino == right.st_ino", "return left.st_dev == right.st_dev"),
    ("identity compares ino only", "return left.st_dev == right.st_dev and left.st_ino == right.st_ino", "return left.st_ino == right.st_ino"),
    ("re-stat the descriptor not the path", "_names_the_same_file(opened, path.stat())", "_names_the_same_file(opened, os.fstat(descriptor))"),
    # --- the digest itself ---
    ("sha256 -> sha1", "digest = hashlib.sha256()", "digest = hashlib.sha1()"),
    ("hexdigest -> uppercase", "return digest.hexdigest()", "return digest.hexdigest().upper()"),
    ("chunk size 1 MiB -> 4 KiB", "_DIGEST_CHUNK_BYTES: Final = 1024 * 1024", "_DIGEST_CHUNK_BYTES: Final = 4096"),
    ("skip the first chunk", "        while chunk := os.read(descriptor, _DIGEST_CHUNK_BYTES):", "        os.read(descriptor, _DIGEST_CHUNK_BYTES)\n        while chunk := os.read(descriptor, _DIGEST_CHUNK_BYTES):"),
    ("hash the chunk reversed", "digest.update(chunk)", "digest.update(chunk[::-1])"),
    # --- the entry point ---
    ("source guard deleted", "    path, expected = _require_source_file(source)", "    path, expected = source, os.stat(source)"),
    ("reason UNREADABLE -> UNSAFE_PATH", "        _reject(MaterialProbeRejection.UNREADABLE)\n    return content_digest", "        _reject(MaterialProbeRejection.UNSAFE_PATH)\n    return content_digest"),
    ("reason FILE_TOO_LARGE -> PROBE_FAILED", "_reject(MaterialProbeRejection.FILE_TOO_LARGE)", "_reject(MaterialProbeRejection.PROBE_FAILED)"),
    ("`is None` -> `is not None`", "    if content_digest is None:\n        _reject(", "    if content_digest is not None:\n        _reject("),
    ("except OSError -> except Exception", "    except OSError:\n        content_digest = None", "    except Exception:\n        content_digest = None"),
    ("reject inside the handler", "    except OSError:\n        content_digest = None\n    if content_digest is None:", "    except OSError:\n        _reject(MaterialProbeRejection.UNREADABLE)\n    if content_digest is None:"),
    # --- review finding [1]: in-place rewrite ---
    ("mtime check deleted", "    if after.st_mtime_ns != opened.st_mtime_ns:\n        return None\n", ""),
    ("mtime compared at second resolution", "if after.st_mtime_ns != opened.st_mtime_ns:", "if int(after.st_mtime) != int(opened.st_mtime):"),
    ("`after` taken before the read", "        after = os.fstat(descriptor)", "        after = opened"),
    # --- review finding [2]: FIFO / non-regular ---
    ("O_NONBLOCK dropped", "os.open(os.fspath(path), os.O_RDONLY | os.O_NONBLOCK)", "os.open(os.fspath(path), os.O_RDONLY)"),
    ("S_ISREG check deleted", "        if not stat.S_ISREG(opened.st_mode):\n            return None\n", ""),
    ("pre-open stat check deleted", "        if not _names_the_same_file(expected, opened):\n            return None\n", ""),
    # --- canary: must die ---
    ("CANARY message changed", 'super().__init__("material probe rejected")', 'super().__init__("material probe rejected!")'),
]

SELECTION = (
    "TestContentDigest or TestContentDigestRejectsAnUnusableSource or "
    "TestContentDigestByteLimit or TestContentDigestNeedsTheFileToHoldStill or "
    "TestContentDigestNeedsMoreThanTheInode or "
    "TestContentDigestLeaksNoPath"
)


def run_tests() -> bool:
    """True when the suite passed. A hang counts as a detection, not a survival."""
    for cache in BACKEND.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    completed = subprocess.run(
        [str(PYTHON), "-B", "-m", "pytest", str(TESTS), "-x", "-q", "-k", SELECTION],
        cwd=BACKEND,
        capture_output=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1", "HOME": str(Path.home())},
        timeout=180,
    )
    return completed.returncode == 0


def run_tests_guarded() -> bool:
    try:
        return run_tests()
    except subprocess.TimeoutExpired:
        print("    (suite hung — counted as killed)", flush=True)
        return False


original = MODULE.read_text(encoding="utf-8")
print("module sha256:", hashlib.sha256(original.encode("utf-8")).hexdigest())

if not run_tests():
    print("BASELINE IS RED — stopping")
    sys.exit(1)
print("baseline green\n", flush=True)

survivors: list[str] = []
try:
    for label, old, new in MUTATIONS:
        if original.count(old) != 1:
            print(f"{label}: SKIPPED (anchor appears {original.count(old)} times)")
            survivors.append(f"{label} (anchor)")
            continue
        MODULE.write_text(original.replace(old, new), encoding="utf-8")
        alive = run_tests_guarded()
        print(f"{label}: {'SURVIVED' if alive else 'killed'}", flush=True)
        if alive:
            survivors.append(label)
finally:
    MODULE.write_text(original, encoding="utf-8")
    for cache in BACKEND.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)

print(f"\n{len(MUTATIONS) - len(survivors)}/{len(MUTATIONS)} killed")
if survivors:
    print("survivors:", *survivors, sep="\n  ")
print("restored sha256:", hashlib.sha256(MODULE.read_text(encoding='utf-8').encode('utf-8')).hexdigest())
