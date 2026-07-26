#!/usr/bin/env python3
r"""The environment a frozen artifact is probed under.

Why this exists
---------------
Three call sites run a frozen executable with a deliberately stripped
environment, to show the artifact carries its own dependencies rather than
borrowing the machine that built it. That property is one of this
repository's single-build-path rules, and it is worth keeping. All three
bought it with ``env={"PATH": os.defpath}``, which is where they went wrong.

What was measured
-----------------
On the Windows acceptance machine (``C:\WINDOWS``, CPython 3.14.0) on
2026-07-27, running a real PyInstaller ``--onedir`` executable that opens a
socket. Identical results from a plain ``python.exe`` child:

===========================  ==========  ================================
PATH                         SystemRoot  result
===========================  ==========  ================================
``.;C:\bin`` (os.defpath)    absent      ``OSError: [WinError 10106]``
``.;C:\bin`` (os.defpath)    present     ok
``""`` (empty)               present     ok
``%SystemRoot%\System32;..`` absent      ``OSError: [WinError 10106]``
``%SystemRoot%\System32;..`` present     ok
===========================  ==========  ================================

Read row four before row one. **The value of PATH is not what breaks Windows;
the missing ``SystemRoot`` is.** ``env={"PATH": ...}`` hands the child an
environment holding nothing else, and Winsock resolves its provider catalog
through ``%SystemRoot%``, so the first socket dies with ``WSAEPROVIDERFAILEDINIT``
however correct the search path is. A change that only replaced ``os.defpath``
with a good PATH would have left the failure exactly where it was.

``os.defpath`` is replaced anyway, for reasons that stand on their own: on
Windows it is ``.;C:\bin``, where ``C:\bin`` does not exist (checked on the
acceptance machine) and ``.`` puts the caller's current directory on the search
path -- for the material-video probe that directory is the frozen bundle
itself. On POSIX it is ``/bin:/usr/bin`` and was never wrong; that exact string
is preserved below so moving the macOS call sites over changes nothing.

Choosing the Windows value
--------------------------
An empty PATH also works (row three), and is more minimal. The system
directories are used instead because the boundary being drawn is "nothing the
developer installed", not "nothing at all": everything here ships with Windows,
and nothing here can be a build machine's Python, uv, node or MSVC. It is also
the value ``run_p9_07_acceptance.py`` already runs on this same machine, so
this is not a second answer to a question the repository had already answered.
That function still builds the string itself; folding it in touches an
allowlist another work line is editing, so it is left alone deliberately.
"""

from __future__ import annotations

import os

__all__ = [
    "POSIX_SEARCH_PATH",
    "WINDOWS_SYSTEM_ROOT_NAMES",
    "frozen_artifact_environment",
    "minimal_search_path",
]

# The value `os.defpath` already had on POSIX, written down rather than
# inherited from a constant whose Windows spelling is junk.
POSIX_SEARCH_PATH = "/bin:/usr/bin"

# Windows sets both, to the same directory. Either one alone is enough.
WINDOWS_SYSTEM_ROOT_NAMES = ("SystemRoot", "WINDIR")

# Carried through because a frozen process without them falls back to
# `%SystemRoot%\Temp` -- measured, `C:\WINDOWS\Temp` -- and a probe should not
# be writing into a system directory.
WINDOWS_CARRIED_NAMES = ("TEMP", "TMP")


def minimal_search_path(*, os_name: str, system_root: str | None = None) -> str:
    """The smallest search path a frozen artifact can still run under.

    `os_name` takes `os.name` spellings (`"nt"` / `"posix"`). It is a
    parameter rather than a lookup so both branches are reachable from either
    host, which is the only reason the Windows branch could be checked at all.
    """
    if os_name != "nt":
        return POSIX_SEARCH_PATH
    if not system_root:
        raise ValueError(
            "a minimal Windows search path needs the system root; "
            "neither SystemRoot nor WINDIR was set"
        )
    root = system_root.rstrip("\\")
    return ";".join((f"{root}\\System32", root, f"{root}\\System32\\Wbem"))


def frozen_artifact_environment(
    *,
    environ: dict[str, str] | None = None,
    os_name: str | None = None,
) -> dict[str, str]:
    """The complete environment to run a frozen artifact under.

    An environment rather than a PATH string, because on Windows the search
    path is not the part that decides whether the artifact runs.
    """
    source = os.environ if environ is None else environ
    name = os.name if os_name is None else os_name
    if name != "nt":
        return {"PATH": minimal_search_path(os_name=name)}
    system_root = next(
        (source[key] for key in WINDOWS_SYSTEM_ROOT_NAMES if source.get(key)), None
    )
    if not system_root:
        raise ValueError(
            "a frozen artifact cannot be probed without the Windows system root; "
            "neither SystemRoot nor WINDIR was set"
        )
    environment = {
        "PATH": minimal_search_path(os_name=name, system_root=system_root),
        "SystemRoot": system_root,
        "WINDIR": system_root,
    }
    for key in WINDOWS_CARRIED_NAMES:
        value = source.get(key)
        if value:
            environment[key] = value
    return environment
