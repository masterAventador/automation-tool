"""Fail-closed Windows DACL validation for private Executor state."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path
from typing import Final

_SE_FILE_OBJECT: Final = 1
_DACL_SECURITY_INFORMATION: Final = 0x00000004
_ACCESS_ALLOWED_ACE_TYPE: Final = 0x00
_ACCESS_ALLOWED_COMPOUND_ACE_TYPE: Final = 0x04
_ACCESS_ALLOWED_OBJECT_ACE_TYPE: Final = 0x05
_ACCESS_ALLOWED_CALLBACK_ACE_TYPE: Final = 0x09
_ACCESS_ALLOWED_CALLBACK_OBJECT_ACE_TYPE: Final = 0x0B
_ACE_OBJECT_TYPE_PRESENT: Final = 0x00000001
_ACE_INHERITED_OBJECT_TYPE_PRESENT: Final = 0x00000002
_ALLOWED_ACE_TYPES: Final = frozenset(
    {
        _ACCESS_ALLOWED_ACE_TYPE,
        _ACCESS_ALLOWED_COMPOUND_ACE_TYPE,
        _ACCESS_ALLOWED_OBJECT_ACE_TYPE,
        _ACCESS_ALLOWED_CALLBACK_ACE_TYPE,
        _ACCESS_ALLOWED_CALLBACK_OBJECT_ACE_TYPE,
    }
)
_SUPPORTED_ALLOWED_ACE_TYPES: Final = frozenset(
    {
        _ACCESS_ALLOWED_ACE_TYPE,
        _ACCESS_ALLOWED_OBJECT_ACE_TYPE,
        _ACCESS_ALLOWED_CALLBACK_ACE_TYPE,
        _ACCESS_ALLOWED_CALLBACK_OBJECT_ACE_TYPE,
    }
)
_BROAD_WELL_KNOWN_SIDS: Final = (
    1,  # Everyone
    8,  # Dialup
    9,  # Network
    10,  # Batch
    11,  # Interactive
    12,  # Service
    13,  # Anonymous
    17,  # Authenticated Users
    18,  # Restricted Code
    19,  # Terminal Server
    20,  # Remote Logon
    27,  # BUILTIN Users
    28,  # BUILTIN Guests
)


class _Acl(ctypes.Structure):
    _fields_ = [
        ("revision", ctypes.c_ubyte),
        ("padding", ctypes.c_ubyte),
        ("size", ctypes.c_ushort),
        ("ace_count", ctypes.c_ushort),
        ("reserved", ctypes.c_ushort),
    ]


class _AceHeader(ctypes.Structure):
    _fields_ = [
        ("ace_type", ctypes.c_ubyte),
        ("ace_flags", ctypes.c_ubyte),
        ("ace_size", ctypes.c_ushort),
    ]


_ADVAPI32 = ctypes.CDLL("advapi32.dll", use_last_error=True)
_KERNEL32 = ctypes.CDLL("kernel32.dll", use_last_error=True)
_ADVAPI32.GetNamedSecurityInfoW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(ctypes.c_void_p),
]
_ADVAPI32.GetNamedSecurityInfoW.restype = wintypes.DWORD
_ADVAPI32.GetAce.argtypes = [
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(ctypes.c_void_p),
]
_ADVAPI32.GetAce.restype = wintypes.BOOL
_ADVAPI32.IsValidSid.argtypes = [ctypes.c_void_p]
_ADVAPI32.IsValidSid.restype = wintypes.BOOL
_ADVAPI32.IsWellKnownSid.argtypes = [ctypes.c_void_p, wintypes.INT]
_ADVAPI32.IsWellKnownSid.restype = wintypes.BOOL
_KERNEL32.LocalFree.argtypes = [ctypes.c_void_p]
_KERNEL32.LocalFree.restype = ctypes.c_void_p


def validate_private_acl(path: Path) -> None:
    """Reject a path whose DACL grants any broad Windows principal access."""

    if os.name != "nt" or not isinstance(path, Path):
        raise ValueError
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    result = _ADVAPI32.GetNamedSecurityInfoW(
        os.fspath(path),
        _SE_FILE_OBJECT,
        _DACL_SECURITY_INFORMATION,
        None,
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if result != 0 or not descriptor.value:
        raise ValueError
    try:
        if not dacl.value:
            raise ValueError
        acl = ctypes.cast(dacl, ctypes.POINTER(_Acl)).contents
        for index in range(acl.ace_count):
            ace = ctypes.c_void_p()
            if not _ADVAPI32.GetAce(dacl, index, ctypes.byref(ace)) or not ace.value:
                raise ValueError
            if _ace_grants_broad_access(ace):
                raise ValueError
    finally:
        _KERNEL32.LocalFree(descriptor)


def _ace_grants_broad_access(ace: ctypes.c_void_p) -> bool:
    address = ace.value
    if address is None:
        raise ValueError
    header = ctypes.cast(ace, ctypes.POINTER(_AceHeader)).contents
    if header.ace_type not in _ALLOWED_ACE_TYPES:
        return False
    if header.ace_type not in _SUPPORTED_ALLOWED_ACE_TYPES:
        raise ValueError
    if header.ace_size < 12:
        raise ValueError
    mask = ctypes.c_uint32.from_address(address + 4).value
    if mask == 0:
        return False
    sid_offset = 8
    if header.ace_type in {
        _ACCESS_ALLOWED_OBJECT_ACE_TYPE,
        _ACCESS_ALLOWED_CALLBACK_OBJECT_ACE_TYPE,
    }:
        flags = ctypes.c_uint32.from_address(address + 8).value
        sid_offset = 12
        if flags & _ACE_OBJECT_TYPE_PRESENT:
            sid_offset += 16
        if flags & _ACE_INHERITED_OBJECT_TYPE_PRESENT:
            sid_offset += 16
    if sid_offset >= header.ace_size:
        raise ValueError
    sid = ctypes.c_void_p(address + sid_offset)
    if not _ADVAPI32.IsValidSid(sid):
        raise ValueError
    return any(_ADVAPI32.IsWellKnownSid(sid, kind) for kind in _BROAD_WELL_KNOWN_SIDS)


__all__ = ["validate_private_acl"]
