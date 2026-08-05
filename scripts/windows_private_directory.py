#!/usr/bin/env python3
"""Verify that a directory is private to this user, the way Windows says it.

macOS expresses "only I can read this" as mode `0o700` plus an owner check, and
EB-11 asserts exactly that of every Profile directory it touches — those hold
platform session cookies, so who else can read them is the whole question.

Windows has no mode bits. CPython reports `st_mode` `0o777` and `st_uid` `0` for
every directory on the volume, so porting the macOS comparison would produce a
check that can never be true. The property survives the port even though the
fields do not: `frontend/src-tauri/src/browser_profiles_windows.rs` builds a
security descriptor whose DACL holds exactly one access-allowed ACE for the
token user and sets `SE_DACL_PROTECTED`, which stops the parent's entries being
inherited. This module reads that back.

That distinction is not academic on this machine. Measured 2026-08-05:

    ...\\Roaming\\com.aventador.automationtool\\embedded-browser-profiles
        AVENTADOR\\Aventador:(OI)(CI)(F)                     <- one entry, no (I)

    %LOCALAPPDATA%
        AVENTADOR\\CodexSandboxUsers:(I)(OI)(CI)(RX)         <- another group reads
        NT AUTHORITY\\SYSTEM:(I)(OI)(CI)(F)
        BUILTIN\\Administrators:(I)(OI)(CI)(F)
        AVENTADOR\\Aventador:(I)(OI)(CI)(F)

A Profile created without breaking inheritance would carry that `(RX)` entry,
and every process in that group could read the session cookies.

Threat model, unchanged from the macOS side: this is a misoperation and
regression gate inside a flow that already trusts the machine, not a defence
against an active same-user attacker. The directory walk below is therefore
sequential rather than atomic — see `open_private_directory`.
"""

from __future__ import annotations

import ctypes
import msvcrt
import os
from ctypes import wintypes
from pathlib import Path
from typing import Final

GENERIC_READ: Final = 0x80000000
FILE_SHARE_ALL: Final = 0x00000007
OPEN_EXISTING: Final = 3
FILE_FLAG_BACKUP_SEMANTICS: Final = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT: Final = 0x00200000
FILE_ATTRIBUTE_DIRECTORY: Final = 0x00000010
FILE_ATTRIBUTE_REPARSE_POINT: Final = 0x00000400
INVALID_FILE_ATTRIBUTES: Final = 0xFFFFFFFF
INVALID_HANDLE: Final = ctypes.c_void_p(-1).value

SE_FILE_OBJECT: Final = 1
OWNER_SECURITY_INFORMATION: Final = 0x00000001
DACL_SECURITY_INFORMATION: Final = 0x00000004
SE_DACL_PROTECTED: Final = 0x1000
ACCESS_ALLOWED_ACE_TYPE: Final = 0x0
FILE_ALL_ACCESS: Final = 0x001F01FF
TOKEN_QUERY: Final = 0x0008
TOKEN_USER_CLASS: Final = 1
ACL_SIZE_INFORMATION: Final = 2


class PrivateDirectoryRejected(RuntimeError):
    """The directory is not private to this user, or cannot be shown to be."""


class _AclSizeInformation(ctypes.Structure):
    _fields_ = (
        ("AceCount", wintypes.DWORD),
        ("AclBytesInUse", wintypes.DWORD),
        ("AclBytesFree", wintypes.DWORD),
    )


class _AceHeader(ctypes.Structure):
    _fields_ = (
        ("AceType", ctypes.c_ubyte),
        ("AceFlags", ctypes.c_ubyte),
        ("AceSize", wintypes.WORD),
    )


class _AccessAllowedAce(ctypes.Structure):
    """The fixed head of an access-allowed ACE; the SID follows `SidStart`."""

    _fields_ = (
        ("Header", _AceHeader),
        ("Mask", wintypes.DWORD),
        ("SidStart", wintypes.DWORD),
    )


class _SidAndAttributes(ctypes.Structure):
    _fields_ = (("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD))


def _kernel32() -> ctypes.WinDLL:
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _advapi32() -> ctypes.WinDLL:
    return ctypes.WinDLL("advapi32", use_last_error=True)


def _reject(message: str) -> None:
    raise PrivateDirectoryRejected(f"EB-11 {message}")


def current_user_sid() -> bytes:
    """This process's token user, as a copy of the raw SID bytes."""
    advapi32 = _advapi32()
    kernel32 = _kernel32()
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        ctypes.c_void_p(kernel32.GetCurrentProcess()),
        TOKEN_QUERY,
        ctypes.byref(token),
    ):
        _reject("could not read this process's own identity")
    try:
        size = wintypes.DWORD(0)
        advapi32.GetTokenInformation(token, TOKEN_USER_CLASS, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(
            token, TOKEN_USER_CLASS, buffer, size, ctypes.byref(size)
        ):
            _reject("could not read this process's own identity")
        user = _SidAndAttributes.from_buffer(buffer)
        return _sid_bytes(advapi32, user.Sid)
    finally:
        kernel32.CloseHandle(token)


def _sid_bytes(advapi32: ctypes.WinDLL, sid: int | None) -> bytes:
    if not sid:
        _reject("read a security identifier that is not there")
    length = advapi32.GetLengthSid(ctypes.c_void_p(sid))
    if not length:
        _reject("read a security identifier of no length")
    return ctypes.string_at(ctypes.c_void_p(sid), length)


def open_private_directory(path: Path) -> int:
    """Open `path` as a directory, refusing a reparse point at any level.

    The macOS counterpart walks with `O_NOFOLLOW` and `dir_fd`, so each step is
    atomic against the previous one. Windows has no `openat` outside the Nt
    layer, so this checks each component's attributes and then opens the whole
    path with `FILE_FLAG_OPEN_REPARSE_POINT`, which refuses to traverse *into* a
    reparse point at the leaf.

    The gap — a component swapped between its check and the open — is the same
    class of race the macOS side already treats as out of scope: this is a
    misoperation gate inside a trusted flow, not a defence against an active
    same-user attacker. Stated here rather than left for a reader to discover.
    """
    if not path.is_absolute():
        _reject("directory path must be absolute")
    kernel32 = _kernel32()
    kernel32.GetFileAttributesW.restype = wintypes.DWORD
    walked = Path(path.parts[0])
    for part in path.parts[1:]:
        walked = walked / part
        attributes = kernel32.GetFileAttributesW(str(walked))
        if attributes == INVALID_FILE_ATTRIBUTES:
            _reject(f"directory component is unavailable: {part}")
        if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
            _reject("refuses a path with a reparse point component")
        if not attributes & FILE_ATTRIBUTE_DIRECTORY:
            _reject("directory component is not a directory")
    kernel32.CreateFileW.restype = wintypes.HANDLE
    handle = kernel32.CreateFileW(
        str(path),
        GENERIC_READ,
        FILE_SHARE_ALL,
        None,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle in (0, INVALID_HANDLE, -1):
        _reject("App-owned directory could not be opened")
    # Handed to the CRT so the caller closes it with `os.close` and reads it
    # with `os.fstat`, exactly as on macOS. Measured: `os.fstat` on a directory
    # descriptor made this way reports `S_ISDIR` and the same `(st_dev, st_ino)`
    # as `os.stat` on the path.
    return msvcrt.open_osfhandle(handle, os.O_RDONLY)


def require_private_dacl(descriptor: int) -> None:
    """Refuse anything but "owned by me, and readable only by me".

    Four things have to hold, and they are exactly what the product writes:
    the owner is this token's user, the DACL exists, it is protected so nothing
    is inherited, and its only entry grants that same user full access.
    """
    advapi32 = _advapi32()
    handle = msvcrt.get_osfhandle(descriptor)
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    security = ctypes.c_void_p()
    status = advapi32.GetSecurityInfo(
        wintypes.HANDLE(handle),
        SE_FILE_OBJECT,
        OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(security),
    )
    if status != 0:
        _reject("App-owned directory security could not be read")
    try:
        expected = current_user_sid()
        if _sid_bytes(advapi32, owner.value) != expected:
            _reject("App-owned directory is owned by someone else")
        if not dacl.value:
            # A null DACL grants everyone everything. It is the most permissive
            # state there is, and it is *not* the same as an empty one.
            _reject("App-owned directory grants access to everyone")

        control = wintypes.WORD(0)
        revision = wintypes.DWORD(0)
        if not advapi32.GetSecurityDescriptorControl(
            security, ctypes.byref(control), ctypes.byref(revision)
        ):
            _reject("App-owned directory security could not be read")
        if not control.value & SE_DACL_PROTECTED:
            _reject("App-owned directory inherits access from its parent")

        sizes = _AclSizeInformation()
        if not advapi32.GetAclInformation(
            dacl, ctypes.byref(sizes), ctypes.sizeof(sizes), ACL_SIZE_INFORMATION
        ):
            _reject("App-owned directory access list could not be read")
        if sizes.AceCount != 1:
            _reject(
                "App-owned directory grants access to more than this user: "
                f"{sizes.AceCount} entries"
            )
        entry = ctypes.c_void_p()
        if not advapi32.GetAce(dacl, 0, ctypes.byref(entry)):
            _reject("App-owned directory access list could not be read")
        ace = _AccessAllowedAce.from_address(entry.value or 0)
        if ace.Header.AceType != ACCESS_ALLOWED_ACE_TYPE:
            _reject("App-owned directory access list is not a simple grant")
        if ace.Mask != FILE_ALL_ACCESS:
            _reject("App-owned directory grants this user something other than full access")
        sid_address = (entry.value or 0) + _AccessAllowedAce.SidStart.offset
        if _sid_bytes(advapi32, sid_address) != expected:
            _reject("App-owned directory grants access to another identity")
    finally:
        if security.value:
            _kernel32().LocalFree(security)


__all__ = [
    "PrivateDirectoryRejected",
    "current_user_sid",
    "open_private_directory",
    "require_private_dacl",
]
