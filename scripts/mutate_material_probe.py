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
MEDIA_TESTS = BACKEND / "tests/unit/executor/test_material_probe_media.py"
PYTHON = BACKEND / ".venv/bin/python"

SELECTION = (
    "TestContentDigest or TestContentDigestRejectsAnUnusableSource or "
    "TestContentDigestByteLimit or TestContentDigestNeedsTheFileToHoldStill or "
    "TestContentDigestNeedsMoreThanTheInode or "
    "TestContentDigestLeaksNoPath or "
    # The digest's own Windows guard is asserted from the registry's platform
    # class, because both flags are the same question. Left out of this filter
    # the mutation for it survived while the test that kills it sat in the file
    # unselected — a narrow `-k` is its own way of proving nothing.
    "TestMaterialPathRegistryOnAPlatformWithoutTheseFlags"
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
        "digest O_NONBLOCK not guarded for Windows",
        'os.O_RDONLY | cast(int, getattr(os, "O_NONBLOCK", 0)))',
        "os.O_RDONLY | os.O_NONBLOCK)",
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
        "    if isinstance(outcome, MaterialProbeRejection):\n        _reject(outcome)\n"
        "    return outcome",
        "    if not isinstance(outcome, MaterialProbeRejection):\n        _reject(outcome)\n"
        "    return outcome",
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
        'os.O_RDONLY | cast(int, getattr(os, "O_NONBLOCK", 0)))',
        "os.O_RDONLY)",
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
    # The closing check is `require_source_unchanged`'s body since T7; the
    # orchestration calls it rather than restating it, so these mutate the one
    # implementation both sides use.
    (
        "closing check deleted",
        "    require_source_unchanged(path, before)\n",
        "",
    ),
    (
        "closing check inverted",
        "    if not _held_still(approved, after):",
        "    if _held_still(approved, after):",
    ),
    (
        "closing check compares the file with itself",
        "    if not _held_still(approved, after):",
        "    if not _held_still(after, after):",
    ),
    (
        "closing reason SOURCE_NOT_AT_REST -> UNREADABLE",
        "    if not _held_still(approved, after):\n"
        "        _reject(MaterialProbeRejection.SOURCE_NOT_AT_REST)",
        "    if not _held_still(approved, after):\n"
        "        _reject(MaterialProbeRejection.UNREADABLE)",
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
        "_held_still(approved, after)",
        "_held_still(after, approved)",
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

T6_SELECTION = (
    "MaterialPathRegistry or RegisteredIdentity or RegisteredFileLeaksNoPath or "
    "MaterialFactsCannotCarryAPathInTheSource or TheProbeStaysOnThisMachine"
)

T6_MUTATIONS: list[tuple[str, str, str]] = [
    # --- the identity that says the file is still the registered one ---
    (
        "identity forgets the device",
        "        device=metadata.st_dev,\n        inode=metadata.st_ino,",
        "        device=0,\n        inode=metadata.st_ino,",
    ),
    (
        "identity forgets the inode",
        "        inode=metadata.st_ino,\n        modified_ns=metadata.st_mtime_ns,",
        "        inode=0,\n        modified_ns=metadata.st_mtime_ns,",
    ),
    (
        "identity forgets the timestamp",
        "        modified_ns=metadata.st_mtime_ns,\n        size_bytes=metadata.st_size,",
        "        modified_ns=0,\n        size_bytes=metadata.st_size,",
    ),
    (
        "identity forgets the length",
        "        size_bytes=metadata.st_size,\n    )\n\n\n@dataclass",
        "        size_bytes=0,\n    )\n\n\n@dataclass",
    ),
    (
        "identity gains the access time",
        "class _FileIdentity:",
        "class _FileIdentity:\n    accessed_ns: int = 0",
    ),
    # --- the check itself, and the two reasons it can give ---
    (
        "resolve stops checking the identity",
        "        if _identity_of(metadata) != entry.identity:\n"
        "            _reject_registry(MaterialPathRegistryRejection.FILE_CHANGED)",
        "        if False:\n"
        "            _reject_registry(MaterialPathRegistryRejection.FILE_CHANGED)",
    ),
    (
        "changed reported as missing",
        "            _reject_registry(MaterialPathRegistryRejection.FILE_CHANGED)",
        "            _reject_registry(MaterialPathRegistryRejection.FILE_MISSING)",
    ),
    (
        "missing reported as changed",
        "        _reject_registry(_why_the_file_cannot_be_used(entry.path))",
        "        _reject_registry(MaterialPathRegistryRejection.FILE_CHANGED)",
    ),
    (
        "an unregistered material reported as missing",
        "            _reject_registry(MaterialPathRegistryRejection.NOT_REGISTERED)",
        "            _reject_registry(MaterialPathRegistryRejection.FILE_MISSING)",
    ),
    # --- a damaged document must not be rebuilt empty ---
    (
        "damaged document rebuilt empty",
        "        entries = _parsed_document(payload)\n"
        "        if entries is None:\n"
        "            _reject_registry(MaterialPathRegistryRejection.REGISTRY_UNREADABLE)",
        "        entries = _parsed_document(payload)\n        if entries is None:\n"
        "            return {}",
    ),
    (
        "unreadable document taken for a first run",
        "        if isinstance(payload, MaterialPathRegistryRejection):\n"
        "            _reject_registry(payload)",
        "        if isinstance(payload, MaterialPathRegistryRejection):\n            return {}",
    ),
    (
        "absent document refused instead of empty",
        "        payload = _read_document(self._document)\n"
        "        if payload is None:\n            return {}",
        "        payload = _read_document(self._document)\n        if payload is None:\n"
        "            _reject_registry(MaterialPathRegistryRejection.REGISTRY_UNREADABLE)",
    ),
    (
        "any open failure taken for an absent document",
        "    except FileNotFoundError:\n        return None\n"
        "    except OSError:\n        return MaterialPathRegistryRejection.REGISTRY_UNREADABLE",
        "    except OSError:\n        return None",
    ),
    (
        "the version stamp is not checked",
        '        or document.get("version") != MATERIAL_PATH_REGISTRY_VERSION\n',
        "",
    ),
    (
        "an entry may carry keys nothing reads",
        "    if not isinstance(value, dict) or set(value) != _REGISTRY_ENTRY_KEYS:",
        "    if not isinstance(value, dict) or not set(value) >= _REGISTRY_ENTRY_KEYS:",
    ),
    (
        "a boolean passes for a device number",
        "    return type(value) is int and value >= 0",
        "    return isinstance(value, int) and value >= 0",
    ),
    (
        "a negative number passes for a length",
        "    return type(value) is int and value >= 0",
        "    return type(value) is int",
    ),
    (
        "the document's paths are not shape-checked",
        "        return _require_path_shape(Path(value))",
        "        return Path(value)",
    ),
    # --- the identifier ---
    (
        "any UUID version accepted",
        "    if not isinstance(value, UUID) or value.version != 4 or value.variant != RFC_4122:",
        "    if not isinstance(value, UUID):",
    ),
    # --- writing ---
    (
        "the registration is visible before it is stored",
        "        self._write(_serialized(entries))",
        "        self._entries = entries\n        self._write(_serialized(entries))",
    ),
    (
        "a moved material keeps its old path",
        "        entries[identifier] = _RegisteredFile(path=path, identity=_identity_of(metadata))",
        "        entries.setdefault(\n"
        "            identifier, _RegisteredFile(path=path, identity=_identity_of(metadata))\n"
        "        )",
    ),
    (
        "the scratch file is published directly",
        '            with os.fdopen(descriptor, "wb") as sink:',
        "            scratch = os.fspath(self._document)\n"
        "            os.close(descriptor)\n"
        "            descriptor = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)\n"
        '            with os.fdopen(descriptor, "wb") as sink:',
    ),
    (
        "the scratch file is left behind",
        "            if scratch is not None:\n"
        "                with suppress(OSError):\n                    os.unlink(scratch)",
        "            if scratch is None:\n"
        "                with suppress(OSError):\n                    os.unlink(scratch)",
    ),
    (
        "an unwritable directory raises OSError instead",
        "        except OSError:\n"
        "            outcome = MaterialPathRegistryRejection.REGISTRY_UNWRITABLE",
        "        except InterruptedError:\n"
        "            outcome = MaterialPathRegistryRejection.REGISTRY_UNWRITABLE",
    ),
    # --- the byte limit, both endpoints and both directions ---
    (
        "registry limit `>` -> `>=`",
        "        if len(payload) > MAX_MATERIAL_PATH_REGISTRY_BYTES:",
        "        if len(payload) >= MAX_MATERIAL_PATH_REGISTRY_BYTES:",
    ),
    (
        "registry limit off by one up",
        "        if len(payload) > MAX_MATERIAL_PATH_REGISTRY_BYTES:",
        "        if len(payload) > MAX_MATERIAL_PATH_REGISTRY_BYTES + 1:",
    ),
    (
        "read limit `>` -> `>=`",
        "            or metadata.st_size > MAX_MATERIAL_PATH_REGISTRY_BYTES",
        "            or metadata.st_size >= MAX_MATERIAL_PATH_REGISTRY_BYTES",
    ),
    (
        "read limit off by one up",
        "            or metadata.st_size > MAX_MATERIAL_PATH_REGISTRY_BYTES",
        "            or metadata.st_size > MAX_MATERIAL_PATH_REGISTRY_BYTES + 1",
    ),
    (
        "the short read is not noticed",
        "            if not chunk:\n"
        "                return MaterialPathRegistryRejection.REGISTRY_UNREADABLE",
        "            if not chunk:\n                break",
    ),
    # --- the state directory, and what may be read as one ---
    (
        "a file passes for the state directory",
        "    if not stat.S_ISDIR(metadata.st_mode) or _is_reparse_point(metadata):\n"
        "        _reject_registry(MaterialPathRegistryRejection.REGISTRY_UNREADABLE)\n",
        "",
    ),
    (
        "a directory passes for the document",
        "            not stat.S_ISREG(metadata.st_mode)\n",
        "            False\n",
    ),
    (
        "the document open waits for a writer",
        '        | cast(int, getattr(os, "O_NONBLOCK", 0))\n'
        '        | cast(int, getattr(os, "O_NOFOLLOW", 0))',
        '        | cast(int, getattr(os, "O_NOFOLLOW", 0))',
    ),
    (
        "the document open follows a link",
        '        | cast(int, getattr(os, "O_NONBLOCK", 0))\n'
        '        | cast(int, getattr(os, "O_NOFOLLOW", 0))',
        '        | cast(int, getattr(os, "O_NONBLOCK", 0))',
    ),
    # --- the paths that must never be rendered ---
    (
        "the registry repr states its directory",
        '        return "MaterialPathRegistry(<redacted>)"',
        '        return f"MaterialPathRegistry({self._state_directory})"',
    ),
    (
        "the record repr states its path",
        '        return "_RegisteredFile(<redacted>)"',
        '        return f"_RegisteredFile({self.path})"',
    ),
    (
        "the registry rejection chains what it handled",
        "    raise MaterialPathRegistryRejected(rejection) from None",
        "    raise MaterialPathRegistryRejected(rejection)",
    ),
    # --- the structural boundary the AST test guards ---
    (
        "MaterialFacts gains a path",
        "    has_audio: bool\n    audio_loudness_lufs: float | None\n    content_digest: str\n",
        "    has_audio: bool\n    audio_loudness_lufs: float | None\n    content_digest: str\n"
        "    source_path: Path\n",
    ),
    (
        "MaterialFacts gains a path under another name",
        "    kind: ProbedMaterialKind\n    duration_ms: int | None\n    width: int | None\n"
        "    height: int | None\n    video_codec: str | None\n    audio_codec: str | None\n"
        "    has_audio: bool\n",
        "    kind: ProbedMaterialKind\n    origin: Path\n    duration_ms: int | None\n"
        "    width: int | None\n    height: int | None\n    video_codec: str | None\n"
        "    audio_codec: str | None\n    has_audio: bool\n",
    ),
    (
        "the module reaches for the product layer",
        "from automation_tool.protocol.safe_text import contains_control_or_bidi",
        "import socket\n\nfrom automation_tool.protocol.safe_text import contains_control_or_bidi",
    ),
    # --- what resolve hands back (fix round Q1) ---
    (
        "resolve hands back the path alone",
        "        return entry.path, metadata\n\n    def __repr__(self)",
        "        return entry.path\n\n    def __repr__(self)",
    ),
    (
        "resolve hands back a stat it did not compare",
        "        if _identity_of(metadata) != entry.identity:",
        "        metadata = entry.path.stat()\n"
        "        if _identity_of(metadata) != entry.identity:",
    ),
    # --- gone against unusable (fix round Q5) ---
    (
        "everything unreadable reported as missing",
        "        _reject_registry(_why_the_file_cannot_be_used(entry.path))",
        "        _reject_registry(MaterialPathRegistryRejection.FILE_MISSING)",
    ),
    (
        "any stat failure reported as missing",
        "    except (FileNotFoundError, NotADirectoryError):",
        "    except OSError:",
    ),
    (
        "a broken path component reported as unreadable",
        "    except (FileNotFoundError, NotADirectoryError):\n"
        "        return MaterialPathRegistryRejection.FILE_MISSING",
        "    except FileNotFoundError:\n        return MaterialPathRegistryRejection.FILE_MISSING",
    ),
    (
        "gone and unusable swapped",
        "    except (FileNotFoundError, NotADirectoryError):\n"
        "        return MaterialPathRegistryRejection.FILE_MISSING\n"
        "    except OSError:\n        pass\n"
        "    return MaterialPathRegistryRejection.FILE_UNREADABLE",
        "    except (FileNotFoundError, NotADirectoryError):\n"
        "        return MaterialPathRegistryRejection.FILE_UNREADABLE\n"
        "    except OSError:\n        pass\n"
        "    return MaterialPathRegistryRejection.FILE_MISSING",
    ),
    (
        "the unreadable stat escapes as an OSError",
        "    except OSError:\n        pass\n"
        "    return MaterialPathRegistryRejection.FILE_UNREADABLE",
        "    return MaterialPathRegistryRejection.FILE_UNREADABLE",
    ),
    # --- the scratch sweep (fix round Q6) ---
    (
        "leftovers are never swept",
        "        self._sweep_scratch()\n        payload = _read_document(self._document)",
        "        payload = _read_document(self._document)",
    ),
    (
        "the sweep takes everything in the directory",
        '        pattern = f".{MATERIAL_PATH_REGISTRY_FILE_NAME}.*.tmp"',
        '        pattern = "*"',
    ),
    (
        "the sweep takes the document too",
        '        pattern = f".{MATERIAL_PATH_REGISTRY_FILE_NAME}.*.tmp"',
        '        pattern = f"*{MATERIAL_PATH_REGISTRY_FILE_NAME}*"',
    ),
    (
        "a leftover that will not go stops the registry",
        "            for leftover in self._state_directory.glob(pattern):\n"
        "                with suppress(OSError):\n                    leftover.unlink()",
        "            for leftover in self._state_directory.glob(pattern):\n"
        "                leftover.unlink()",
    ),
    # --- the Windows-absent flags (fix round Q3) ---
    (
        "document O_NONBLOCK not guarded for Windows",
        '        | cast(int, getattr(os, "O_NONBLOCK", 0))\n'
        '        | cast(int, getattr(os, "O_NOFOLLOW", 0))',
        '        | os.O_NONBLOCK\n        | cast(int, getattr(os, "O_NOFOLLOW", 0))',
    ),
    (
        "document O_NOFOLLOW not guarded for Windows",
        '        | cast(int, getattr(os, "O_NONBLOCK", 0))\n'
        '        | cast(int, getattr(os, "O_NOFOLLOW", 0))',
        '        | cast(int, getattr(os, "O_NONBLOCK", 0))\n        | os.O_NOFOLLOW',
    ),
    # --- the AST guard must see past a block (fix round Q4) ---
    (
        "MaterialFacts gains a path inside a block",
        "    has_audio: bool\n    audio_loudness_lufs: float | None\n    content_digest: str\n",
        "    has_audio: bool\n    audio_loudness_lufs: float | None\n    content_digest: str\n"
        "    if True:\n        source_path: Path\n",
    ),
    # --- canary: must die ---
    (
        "CANARY resolve returns the wrong file",
        "        return entry.path, metadata\n\n    def __repr__(self)",
        "        return Path('/canary'), metadata\n\n    def __repr__(self)",
    ),
]

T7_SELECTION = ""

T7_MUTATIONS: list[tuple[str, str, str]] = [
    # --- the bound on what a pass may write, shared by both of them ---
    (
        "the size is only looked at once the tool has exited",
        "                if not outgrown:\n                    continue",
        "                continue",
    ),
    (
        "mid-flight limit `>` -> `>=`",
        "                    outgrown = output.stat().st_size > limit",
        "                    outgrown = output.stat().st_size >= limit",
    ),
    (
        "mid-flight limit off by one up",
        "                    outgrown = output.stat().st_size > limit",
        "                    outgrown = output.stat().st_size > limit + 1",
    ),
    (
        "the poll waits out the whole timeout",
        "process.wait(timeout=min(OUTPUT_POLL_SECONDS, remaining))",
        "process.wait(timeout=remaining)",
    ),
    (
        "the timeout is ten seconds longer than asked for",
        "        deadline = time.monotonic() + seconds",
        "        deadline = time.monotonic() + seconds + 10",
    ),
    (
        "the child is left running on the way out",
        "        process.kill()\n        raise",
        "        raise",
    ),
    (
        "the child inherits the parent's stdin",
        "                stdin=subprocess.DEVNULL,\n                stdout=sink,",
        "                stdout=sink,",
    ),
    # --- the answer, and when it may be read ---
    (
        "the answer's size is not checked before it is read",
        "        if output.stat().st_size > limit:",
        "        if False:",
    ),
    (
        "after-the-exit limit `>` -> `>=`",
        "        if output.stat().st_size > limit:",
        "        if output.stat().st_size >= limit:",
    ),
    (
        "a failed pass's output is read anyway",
        '        return _PassOutcome(ended, b"")',
        "        return _PassOutcome(ended, output.read_bytes())",
    ),
    (
        "a signalled probe reported as an undecodable file",
        "        # diagnostic text is involved, so nothing here drifts with locale.\n"
        "        _reject(MaterialProbeRejection.PROBE_CRASHED)",
        "        # diagnostic text is involved, so nothing here drifts with locale.\n"
        "        _reject(MaterialProbeRejection.UNDECODABLE)",
    ),
    (
        "the reading pass's own timeout ignored",
        "        seconds=PROBE_TIMEOUT_SECONDS,",
        "        seconds=MEASURE_TIMEOUT_SECONDS,",
    ),
    (
        "the reading pass borrows the measuring pass's limit",
        "        limit=MAX_PROBE_OUTPUT_BYTES,",
        "        limit=MAX_MEASURE_OUTPUT_BYTES,",
    ),
    # --- the scratch space, and the failures that are not the file's (C1) ---
    (
        "a scratch root that cannot be written reads as the tool misbehaving",
        "    except OSError:\n"
        "        # Not the file's fault and not the tool's: this module needs somewhere to\n"
        "        # put a few hundred bytes, and a full volume is what stops it.\n"
        "        return MaterialProbeRejection.WORKSPACE_UNUSABLE",
        "    except OSError:\n        return MaterialProbeRejection.PROBE_FAILED",
    ),
    (
        "the scratch file not opening reads as the tool misbehaving",
        '        sink = output.open("wb")\n    except OSError:\n'
        "        return MaterialProbeRejection.WORKSPACE_UNUSABLE",
        '        sink = output.open("wb")\n    except OSError:\n'
        "        return MaterialProbeRejection.PROBE_FAILED",
    ),
    (
        "a tool that will not start reads as a full disk",
        "        except OSError:\n            return MaterialProbeRejection.PROBE_FAILED",
        "        except OSError:\n            return MaterialProbeRejection.WORKSPACE_UNUSABLE",
    ),
    (
        "the in-flight stat failure reads as the tool misbehaving",
        "                except OSError:\n                    process.kill()\n"
        "                    return MaterialProbeRejection.WORKSPACE_UNUSABLE",
        "                except OSError:\n                    process.kill()\n"
        "                    return MaterialProbeRejection.PROBE_FAILED",
    ),
    (
        "the answer that cannot be read reads as the tool misbehaving",
        "    except OSError:\n"
        "        return MaterialProbeRejection.WORKSPACE_UNUSABLE\n\n\ndef _waited_for",
        "    except OSError:\n"
        "        return MaterialProbeRejection.PROBE_FAILED\n\n\ndef _waited_for",
    ),
    (
        "clearing up can rewrite the verdict again",
        "        with suppress(OSError):\n            shutil.rmtree(workspace)",
        "        shutil.rmtree(workspace)",
    ),
    (
        "the scratch directory is left behind",
        "        with suppress(OSError):\n            shutil.rmtree(workspace)",
        "        pass",
    ),
    # --- canary: must die. Every group needs one of its own, or a harness that
    # has stopped running what it thinks it runs reads as a clean sweep.
    (
        "CANARY the answer always comes back empty",
        "        return _PassOutcome(ended, output.read_bytes())",
        '        return _PassOutcome(ended, b"")',
    ),
]

# The second half of the same round, split only so that either can be rerun on
# its own: the group filter matches on the name, so "T7" still selects both.
T7B_MUTATIONS: list[tuple[str, str, str]] = [
    # --- the public check a consumer closes its window with ---
    (
        "the public check skips the import guard",
        "    path, after = _require_source_file(source)\n    if not _held_still(approved, after):",
        "    path, after = source, source.stat()\n    if not _held_still(approved, after):",
    ),
    (
        "the orchestration checks the window itself instead of calling it",
        "    require_source_unchanged(path, before)",
        "    _, again = _require_source_file(path)\n"
        "    if not _held_still(before, again):\n"
        "        _reject(MaterialProbeRejection.SOURCE_NOT_AT_REST)",
    ),
    (
        "the recipe's first step drops the guard",
        "def approve_source(source: Path) -> tuple[Path, os.stat_result]:",
        "def approve_source(source: Path) -> tuple[Path, os.stat_result]:\n"
        "    return source, source.stat()\n\n\ndef _unused_approve(source: Path) "
        "-> tuple[Path, os.stat_result]:",
    ),
    # Returns the stat it was handed rather than the one it took. Expected to
    # survive, and kept for what it says: the function only returns when the two
    # describe one unchanged file, so every field `_held_still` compares is equal
    # between them by construction. Only the access and change times differ, and
    # nothing reads those — deliberately, since being read is not a change.
    (
        "the public check hands back the stat it was given",
        "    return path, after\n\n\ndef probe_material",
        "    return path, approved\n\n\ndef probe_material",
    ),
    # --- the private state directory the whole threat model rests on ---
    (
        "the state directory's mode is not checked",
        "        or stat.S_IMODE(metadata.st_mode) != _PRIVATE_DIRECTORY_MODE\n",
        "",
    ),
    (
        "the state directory's mode may be anything more open",
        "        or stat.S_IMODE(metadata.st_mode) != _PRIVATE_DIRECTORY_MODE",
        "        or stat.S_IMODE(metadata.st_mode) < _PRIVATE_DIRECTORY_MODE",
    ),
    (
        "the state directory's mode checked ledger's looser way",
        "        or stat.S_IMODE(metadata.st_mode) != _PRIVATE_DIRECTORY_MODE",
        "        or stat.S_IMODE(metadata.st_mode) & 0o077",
    ),
    (
        "the private mode is world-readable",
        "_PRIVATE_DIRECTORY_MODE: Final = 0o700",
        "_PRIVATE_DIRECTORY_MODE: Final = 0o755",
    ),
    (
        "the state directory's owner is not checked",
        '        metadata.st_uid != cast(Callable[[], int], vars(os)["getuid"])()\n        or ',
        "        ",
    ),
    (
        "the state directory is stated through the link",
        "        metadata = state_directory.lstat()",
        "        metadata = state_directory.stat()",
    ),
    (
        "a state path that cannot be stated escapes as an OSError",
        "    try:\n        metadata = state_directory.lstat()\n    except OSError:",
        "    if True:\n        metadata = state_directory.lstat()\n    if False:",
    ),
    (
        "the state directory may be given as text",
        "    if not isinstance(state_directory, Path):\n"
        "        _reject_registry(MaterialPathRegistryRejection.REGISTRY_UNREADABLE)\n",
        "",
    ),
    (
        "a reparse point passes for a private directory",
        " or _is_reparse_point(metadata):",
        ":",
    ),
    (
        "every directory reads as a reparse point",
        '    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)\n'
        '    return bool(flag and getattr(metadata, "st_file_attributes", 0) & flag)',
        '    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)\n    return bool(flag)',
    ),
    (
        "the Windows ACL is never asked about",
        "        try:\n            validate_private_acl(state_directory)\n"
        "        except ValueError:\n"
        "            _reject_registry(MaterialPathRegistryRejection.REGISTRY_UNREADABLE)",
        "        validate_private_acl",
    ),
    (
        "a refused Windows ACL is swallowed",
        "        except ValueError:\n"
        "            _reject_registry(MaterialPathRegistryRejection.REGISTRY_UNREADABLE)",
        "        except ValueError:\n            pass",
    ),
    (
        "the POSIX mode is demanded on Windows too",
        '    if os.name != "nt" and (',
        "    if True and (",
    ),
    # --- and it is asked again before every operation (fix round #17) ---
    (
        "the directory is trusted for the life of the process",
        "        _require_state_directory(self._state_directory)\n\n    def _load",
        "        pass\n\n    def _load",
    ),
    (
        "only registering re-checks the directory",
        "        self._require_its_directory()\n"
        "        identifier = _usable_identifier(material_id)\n"
        "        if identifier is None:\n"
        "            _reject_registry(MaterialPathRegistryRejection.UNUSABLE_IDENTIFIER)\n"
        "        entry = self._entries.get(identifier)",
        "        identifier = _usable_identifier(material_id)\n"
        "        if identifier is None:\n"
        "            _reject_registry(MaterialPathRegistryRejection.UNUSABLE_IDENTIFIER)\n"
        "        entry = self._entries.get(identifier)",
    ),
    # --- canary: must die ---
    (
        "CANARY the public check never refuses",
        "    if not _held_still(approved, after):",
        "    if False:",
    ),
]

GROUPS: list[tuple[str, str, list[tuple[str, str, str]], tuple[Path, ...]]] = [
    ("T4 the content digest", SELECTION, MUTATIONS, (TESTS,)),
    ("T5 the orchestration", T5_SELECTION, T5_MUTATIONS, (TESTS,)),
    ("T6 the path registry", T6_SELECTION, T6_MUTATIONS, (TESTS,)),
    # The only group that runs the real-material file as well. Several of its
    # mutations are about what the packaged tools are actually asked for, and a
    # stub answers those the same either way — which is the failure mode the
    # acceptance file exists for.
    ("T7a the bounds and the answer", T7_SELECTION, T7_MUTATIONS, (TESTS, MEDIA_TESTS)),
    (
        "T7b the public check and the private directory",
        T7_SELECTION,
        T7B_MUTATIONS,
        (TESTS, MEDIA_TESTS),
    ),
]


def _environment(clone_source: Path) -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": str(Path.home()),
        "PYTHONPATH": str(clone_source),
    }


def run_tests(clone_source: Path, selection: str, tests: tuple[Path, ...]) -> bool:
    """True when the suite passed. A hang counts as a detection, not a survival."""
    for cache in clone_source.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    completed = subprocess.run(
        [
            str(PYTHON),
            "-B",
            "-m",
            "pytest",
            *(str(test) for test in tests),
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


def run_tests_guarded(clone_source: Path, selection: str, tests: tuple[Path, ...]) -> bool:
    try:
        return run_tests(clone_source, selection, tests)
    except subprocess.TimeoutExpired:
        print("    (suite hung — counted as killed)", flush=True)
        return False


def digest_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    """Run every group, or only the ones whose name contains the given text.

    The filter exists because the whole set is 160 mutations over five groups and
    takes some fifteen minutes: iterating on one group's anchors should not mean
    rerunning the other four. A filtered run says so in its own output, so its
    total cannot be mistaken for the whole set's.
    """
    wanted = sys.argv[1] if len(sys.argv) > 1 else ""
    groups = [group for group in GROUPS if wanted in group[0]]
    if not groups:
        print(f"no group matches {wanted!r}")
        return 1

    tree_module = BACKEND / "src" / RELATIVE_MODULE
    before = digest_of(tree_module)
    print("module sha256:", before)
    if wanted:
        print("running only:", ", ".join(group[0] for group in groups))

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
            print(f"tests would import {resolved}, not the copy — refusing to report anything")
            return 1
        print("mutating a copy at", clone_module, flush=True)

        total = 0
        for group, selection, mutations, tests in groups:
            print(f"--- {group} ---", flush=True)
            if not run_tests_guarded(clone_source, selection, tests):
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
                alive = run_tests_guarded(clone_source, selection, tests)
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
