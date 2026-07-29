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
        "            if read_bytes > opened.st_size:\n"
        "                return MaterialProbeRejection.SOURCE_NOT_AT_REST\n",
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
        "    if read_bytes < opened.st_size:\n        return MaterialProbeRejection.SOURCE_NOT_AT_REST\n",
        "",
    ),
    # --- the closing identity check ---
    (
        "identity check deleted",
        "    if not _names_the_same_file(opened, path.stat()):\n"
        "        return MaterialProbeRejection.SOURCE_NOT_AT_REST\n",
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
        "        outcome = MaterialProbeRejection.UNREADABLE\n    if isinstance(",
        "        outcome = MaterialProbeRejection.UNSAFE_PATH\n    if isinstance(",
    ),
    (
        # Anchored on the descriptor-level check specifically: the orchestration
        # gained a second `FILE_TOO_LARGE` rejection, and a bare anchor now
        # matches both.
        "reason FILE_TOO_LARGE -> PROBE_FAILED",
        "        if opened.st_size > MAX_SOURCE_FILE_BYTES:\n"
        "            _reject(MaterialProbeRejection.FILE_TOO_LARGE)",
        "        if opened.st_size > MAX_SOURCE_FILE_BYTES:\n"
        "            _reject(MaterialProbeRejection.PROBE_FAILED)",
    ),
    (
        "outcome discriminated on the wrong type",
        "    if isinstance(outcome, MaterialProbeRejection):",
        "    if not isinstance(outcome, MaterialProbeRejection):",
    ),
    (
        "except OSError -> except Exception",
        "    except OSError:\n        # Whatever",
        "    except BaseException:\n        # Whatever",
    ),
    (
        "reject inside the handler",
        "        outcome = MaterialProbeRejection.UNREADABLE\n    if isinstance(",
        "        _reject(MaterialProbeRejection.UNREADABLE)\n    if isinstance(",
    ),
    # --- the file has to hold still ---
    (
        "mtime check deleted",
        "    if after.st_mtime_ns != opened.st_mtime_ns:\n"
        "        return MaterialProbeRejection.SOURCE_NOT_AT_REST\n",
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
        "        if not stat.S_ISREG(opened.st_mode):\n"
        "            return MaterialProbeRejection.UNREADABLE\n",
        "",
    ),
    (
        "pre-open stat check deleted",
        "        if not _names_the_same_file(expected, opened):\n"
        "            return MaterialProbeRejection.SOURCE_NOT_AT_REST\n",
        "",
    ),
    # --- canary: must die ---
    (
        "CANARY message changed",
        'super().__init__("material probe rejected")',
        'super().__init__("material probe rejected!")',
    ),
]

# The orchestration (T5) runs against the whole file rather than a slice of it.
# Its own tests are one half of what should catch these; the other half is the
# three steps' existing tests still passing, since the orchestration is allowed
# to change none of their behaviour.
T5_SELECTION = ""

T5_MUTATIONS: list[tuple[str, str, str]] = [
    # --- the order of the three steps ---
    (
        "digest taken before the reading pass",
        "    streams = read_stream_facts(tools, path)\n"
        "    audio = read_audio_facts(tools, path, streams)\n"
        "    content_digest = read_content_digest(path)\n",
        "    content_digest = read_content_digest(path)\n"
        "    streams = read_stream_facts(tools, path)\n"
        "    audio = read_audio_facts(tools, path, streams)\n",
    ),
    (
        "digest taken before the measuring pass",
        "    audio = read_audio_facts(tools, path, streams)\n"
        "    content_digest = read_content_digest(path)\n",
        "    content_digest = read_content_digest(path)\n"
        "    audio = read_audio_facts(tools, path, streams)\n",
    ),
    (
        "measuring pass skipped altogether",
        "    audio = read_audio_facts(tools, path, streams)",
        "    audio = AudioFacts(has_audio=False, loudness_lufs=None)",
    ),
    # --- the rejection travels up untouched ---
    (
        "steps wrapped in a handler that rebuilds the rejection",
        "    streams = read_stream_facts(tools, path)",
        "    try:\n"
        "        streams = read_stream_facts(tools, path)\n"
        "    except MaterialProbeRejected as error:\n"
        "        raise MaterialProbeRejected(error.rejection) from None",
    ),
    # --- the source is checked at both ends ---
    (
        "opening source guard deleted",
        "    path, before = _require_source_file(source)",
        "    path, before = source, os.stat(source)",
    ),
    (
        "closing check deleted",
        "    _, after = _require_source_file(path)\n"
        "    if not _held_still(before, after):\n"
        "        _reject(MaterialProbeRejection.SOURCE_NOT_AT_REST)\n",
        "",
    ),
    (
        "closing check inverted",
        "    if not _held_still(before, after):",
        "    if _held_still(before, after):",
    ),
    (
        "closing check compares the file with itself",
        "    if not _held_still(before, after):",
        "    if not _held_still(after, after):",
    ),
    (
        "closing reason SOURCE_NOT_AT_REST -> UNREADABLE",
        "    if not _held_still(before, after):\n"
        "        _reject(MaterialProbeRejection.SOURCE_NOT_AT_REST)",
        "    if not _held_still(before, after):\n        _reject(MaterialProbeRejection.UNREADABLE)",
    ),
    # --- the three terms of "it held still", one at a time ---
    (
        "held-still identity term deleted",
        "        _names_the_same_file(before, after)\n        and before.st_mtime_ns",
        "        before.st_mtime_ns",
    ),
    (
        "held-still timestamp term deleted",
        "        and before.st_mtime_ns == after.st_mtime_ns\n",
        "",
    ),
    (
        "held-still length term deleted",
        "        and before.st_size == after.st_size\n",
        "",
    ),
    (
        "held-still `and` -> `or` (timestamp)",
        "and before.st_mtime_ns ==",
        "or before.st_mtime_ns ==",
    ),
    (
        "held-still `and` -> `or` (length)",
        "and before.st_size ==",
        "or before.st_size ==",
    ),
    (
        "held-still timestamp at second resolution",
        "before.st_mtime_ns == after.st_mtime_ns",
        "int(before.st_mtime) == int(after.st_mtime)",
    ),
    # Symmetric in both remaining terms and in `_names_the_same_file`, so this
    # one is expected to survive. It is kept because an implementation that
    # stopped being symmetric — comparing sizes with `<=`, say — would make it
    # start dying, and a survivor list that never changes says nothing.
    (
        "held-still arguments swapped",
        "_held_still(before, after)",
        "_held_still(after, before)",
    ),
    # The whole stat result compared instead of the three fields. It dies, but
    # in the loosening direction only: `stat_result.__eq__` compares the
    # 10-tuple, whose time fields are whole seconds, so the millisecond rewrite
    # slips past it. What it also does — refuse a material for having been read,
    # once the access time crosses a second boundary — is what
    # `test_a_source_whose_access_time_moved_is_still_probed` exists to catch.
    (
        "whole stat compared instead of the three fields",
        "    return (\n"
        "        _names_the_same_file(before, after)\n"
        "        and before.st_mtime_ns == after.st_mtime_ns\n"
        "        and before.st_size == after.st_size\n"
        "    )",
        "    return before == after",
    ),
    # --- the size is refused before anything is spent on it ---
    (
        "up-front size check deleted",
        "    if before.st_size > MAX_SOURCE_FILE_BYTES:\n"
        "        _reject(MaterialProbeRejection.FILE_TOO_LARGE)\n",
        "",
    ),
    (
        "up-front size check `>` -> `>=`",
        "    if before.st_size > MAX_SOURCE_FILE_BYTES:",
        "    if before.st_size >= MAX_SOURCE_FILE_BYTES:",
    ),
    (
        "up-front size check moved behind the reading pass",
        "    if before.st_size > MAX_SOURCE_FILE_BYTES:\n"
        "        _reject(MaterialProbeRejection.FILE_TOO_LARGE)\n"
        "    streams = read_stream_facts(tools, path)",
        "    streams = read_stream_facts(tools, path)\n"
        "    if before.st_size > MAX_SOURCE_FILE_BYTES:\n"
        "        _reject(MaterialProbeRejection.FILE_TOO_LARGE)",
    ),
    # --- what the facts are assembled from ---
    ("facts kind hard-wired", "kind=streams.kind,", "kind=ProbedMaterialKind.VIDEO,"),
    ("facts duration dropped", "duration_ms=streams.duration_ms,", "duration_ms=None,"),
    ("facts width and height swapped", "width=streams.width,", "width=streams.height,"),
    (
        "facts sound taken from the stream list",
        "has_audio=audio.has_audio,",
        "has_audio=streams.audio_codec is not None,",
    ),
    (
        "facts loudness dropped",
        "audio_loudness_lufs=audio.loudness_lufs,",
        "audio_loudness_lufs=None,",
    ),
    # --- the facts cannot be half-built or edited afterwards ---
    (
        "facts no longer frozen",
        "@dataclass(frozen=True, slots=True)\nclass MaterialFacts:",
        "@dataclass(slots=True)\nclass MaterialFacts:",
    ),
    (
        "facts digest given a default",
        "    audio_loudness_lufs: float | None\n    content_digest: str\n",
        '    audio_loudness_lufs: float | None\n    content_digest: str = ""\n',
    ),
    # --- the reason split the orchestration's own check belongs to ---
    (
        "swapped-at-open reason -> UNREADABLE",
        "        if not _names_the_same_file(expected, opened):\n"
        "            return MaterialProbeRejection.SOURCE_NOT_AT_REST",
        "        if not _names_the_same_file(expected, opened):\n"
        "            return MaterialProbeRejection.UNREADABLE",
    ),
    (
        "not-a-regular-file reason -> SOURCE_NOT_AT_REST",
        "        if not stat.S_ISREG(opened.st_mode):\n"
        "            return MaterialProbeRejection.UNREADABLE",
        "        if not stat.S_ISREG(opened.st_mode):\n"
        "            return MaterialProbeRejection.SOURCE_NOT_AT_REST",
    ),
    (
        "IO failure reason -> SOURCE_NOT_AT_REST",
        "        outcome = MaterialProbeRejection.UNREADABLE\n    if isinstance(",
        "        outcome = MaterialProbeRejection.SOURCE_NOT_AT_REST\n    if isinstance(",
    ),
    # --- canary: must die ---
    (
        "CANARY facts renamed",
        "    return MaterialFacts(\n        kind=streams.kind,",
        "    return MaterialFacts(\n        kind=ProbedMaterialKind.AUDIO,",
    ),
]

GROUPS: list[tuple[str, str, list[tuple[str, str, str]]]] = [
    ("T4 the content digest", SELECTION, MUTATIONS),
    ("T5 the orchestration", T5_SELECTION, T5_MUTATIONS),
]


def _environment(clone_source: Path) -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": str(Path.home()),
        "PYTHONPATH": str(clone_source),
    }


def run_tests(clone_source: Path, selection: str) -> bool:
    """True when the suite passed. A hang counts as a detection, not a survival."""
    for cache in clone_source.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    completed = subprocess.run(
        [
            str(PYTHON),
            "-B",
            "-m",
            "pytest",
            str(TESTS),
            "-x",
            "-q",
            *(["-k", selection] if selection else []),
        ],
        cwd=BACKEND,
        capture_output=True,
        env=_environment(clone_source),
        timeout=300,
    )
    return completed.returncode == 0


def run_tests_guarded(clone_source: Path, selection: str) -> bool:
    try:
        return run_tests(clone_source, selection)
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

        total = 0
        for group, selection, mutations in GROUPS:
            print(f"--- {group} ---", flush=True)
            if not run_tests_guarded(clone_source, selection):
                print("BASELINE IS RED — stopping")
                return 1
            print("baseline green\n", flush=True)

            for label, old, new in mutations:
                total += 1
                if pristine.count(old) != 1:
                    print(
                        f"{label}: SKIPPED (anchor appears {pristine.count(old)} times)",
                        flush=True,
                    )
                    survivors.append(f"{label} (anchor)")
                    continue
                clone_module.write_text(pristine.replace(old, new), encoding="utf-8")
                alive = run_tests_guarded(clone_source, selection)
                print(f"{label}: {'SURVIVED' if alive else 'killed'}", flush=True)
                if alive:
                    survivors.append(label)
                clone_module.write_text(pristine, encoding="utf-8")
            print("", flush=True)

    print(f"\n{total - len(survivors)}/{total} killed")
    if survivors:
        print("survivors:", *survivors, sep="\n  ")

    unchanged = digest_of(tree_module) == before
    print("working tree module unchanged:", unchanged)
    return 0 if unchanged else 1


if __name__ == "__main__":
    sys.exit(main())
