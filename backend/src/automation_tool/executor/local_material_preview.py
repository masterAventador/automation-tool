"""Opaque, identity-checked reads for local material previews."""

from __future__ import annotations

import os
import stat
import threading
from collections import OrderedDict
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO, Never
from uuid import UUID

from automation_tool.executor.material_probe import (
    MaterialPathRegistry,
    MaterialPathRegistryRejected,
    MaterialPathRegistryRejection,
    MaterialProbeRejected,
    MaterialProbeRejection,
    MediaStreamFacts,
    PackagedMediaTools,
    ProbedMaterialKind,
    read_stream_facts,
    require_source_unchanged,
)

_HEADER_BYTES = 4096
_MAX_CONTENT_TYPE_CACHE_ENTRIES = 2048


class LocalMaterialPreviewFailureCode(StrEnum):
    UNUSABLE_IDENTIFIER = MaterialPathRegistryRejection.UNUSABLE_IDENTIFIER.value
    NOT_REGISTERED = MaterialPathRegistryRejection.NOT_REGISTERED.value
    FILE_MISSING = MaterialPathRegistryRejection.FILE_MISSING.value
    FILE_UNREADABLE = MaterialPathRegistryRejection.FILE_UNREADABLE.value
    FILE_CHANGED = MaterialPathRegistryRejection.FILE_CHANGED.value
    REGISTRY_UNREADABLE = MaterialPathRegistryRejection.REGISTRY_UNREADABLE.value
    REGISTRY_UNWRITABLE = MaterialPathRegistryRejection.REGISTRY_UNWRITABLE.value
    REGISTRY_FULL = MaterialPathRegistryRejection.REGISTRY_FULL.value
    UNSUPPORTED_MEDIA = "unsupported_media"


class LocalMaterialPreviewRejected(RuntimeError):
    """One path-free preview failure from a closed vocabulary."""

    def __init__(self, code: LocalMaterialPreviewFailureCode) -> None:
        super().__init__("local material preview rejected")
        self.code = code


def _reject(code: LocalMaterialPreviewFailureCode) -> Never:
    raise LocalMaterialPreviewRejected(code) from None


def _preview_failure(rejection: MaterialPathRegistryRejection) -> Never:
    _reject(LocalMaterialPreviewFailureCode(rejection.value))


def _probe_failure(rejection: MaterialProbeRejection) -> Never:
    if rejection is MaterialProbeRejection.UNREADABLE:
        _reject(LocalMaterialPreviewFailureCode.FILE_UNREADABLE)
    if rejection is MaterialProbeRejection.SOURCE_NOT_AT_REST:
        _reject(LocalMaterialPreviewFailureCode.FILE_CHANGED)
    _reject(LocalMaterialPreviewFailureCode.UNSUPPORTED_MEDIA)


def _same_opened_identity(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        stat.S_IFMT(before.st_mode) == stat.S_IFMT(after.st_mode)
        and stat.S_IMODE(before.st_mode) == stat.S_IMODE(after.st_mode)
        and before.st_dev == after.st_dev
        and before.st_ino == after.st_ino
        and before.st_mtime_ns == after.st_mtime_ns
        and before.st_size == after.st_size
    )


def safe_preview_content_type(kind: ProbedMaterialKind, header: bytes) -> str:
    """Intersect trusted stream kind with a small browser-safe magic allowlist."""

    if not isinstance(kind, ProbedMaterialKind) or not isinstance(header, bytes):
        _reject(LocalMaterialPreviewFailureCode.UNSUPPORTED_MEDIA)
    if kind is ProbedMaterialKind.IMAGE:
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if header.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if header.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
            return "image/webp"
        _reject(LocalMaterialPreviewFailureCode.UNSUPPORTED_MEDIA)

    if len(header) >= 12 and header[4:8] == b"ftyp":
        if kind is ProbedMaterialKind.AUDIO:
            return "audio/mp4"
        if kind is ProbedMaterialKind.VIDEO:
            return "video/quicktime" if header[8:12] == b"qt  " else "video/mp4"
    if header.startswith(b"\x1aE\xdf\xa3"):
        lowered = header.lower()
        if b"webm" in lowered:
            return "audio/webm" if kind is ProbedMaterialKind.AUDIO else "video/webm"
        if b"matroska" in lowered and kind is ProbedMaterialKind.VIDEO:
            return "video/x-matroska"
    if header.startswith(b"OggS"):
        return "audio/ogg" if kind is ProbedMaterialKind.AUDIO else "video/ogg"
    if kind is ProbedMaterialKind.AUDIO:
        if header.startswith((b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")):
            return "audio/mpeg"
        if header.startswith(b"fLaC"):
            return "audio/flac"
        if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WAVE":
            return "audio/wav"
        if len(header) >= 2 and header[0] == 0xFF and header[1] & 0xF6 == 0xF0:
            return "audio/aac"
    _reject(LocalMaterialPreviewFailureCode.UNSUPPORTED_MEDIA)


class LocalMaterialPreviewLease:
    """A redacted file descriptor whose reads re-check the opened identity."""

    __slots__ = (
        "_closed",
        "_identity",
        "_lock",
        "_stream",
        "content_type",
        "size_bytes",
    )

    def __init__(
        self,
        stream: BinaryIO,
        identity: os.stat_result,
        content_type: str,
    ) -> None:
        self._stream = stream
        self._identity = identity
        self._closed = False
        self._lock = threading.Lock()
        self.content_type = content_type
        self.size_bytes = identity.st_size

    def __repr__(self) -> str:
        return "LocalMaterialPreviewLease(<redacted>)"

    def read(self, start: int, length: int) -> bytes:
        if (
            type(start) is not int
            or type(length) is not int
            or start < 0
            or length <= 0
            or start > self.size_bytes
            or length > self.size_bytes - start
        ):
            _reject(LocalMaterialPreviewFailureCode.FILE_CHANGED)
        with self._lock:
            if self._closed:
                _reject(LocalMaterialPreviewFailureCode.FILE_CHANGED)
            self._require_unchanged()
            try:
                self._stream.seek(start)
                body = self._stream.read(length)
            except OSError:
                _reject(LocalMaterialPreviewFailureCode.FILE_UNREADABLE)
            self._require_unchanged()
            if not isinstance(body, bytes) or len(body) != length:
                _reject(LocalMaterialPreviewFailureCode.FILE_CHANGED)
            return body

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._stream.close()
            except OSError:
                pass

    def _require_unchanged(self) -> None:
        try:
            current = os.fstat(self._stream.fileno())
        except (OSError, ValueError):
            _reject(LocalMaterialPreviewFailureCode.FILE_CHANGED)
        if not _same_opened_identity(self._identity, current):
            _reject(LocalMaterialPreviewFailureCode.FILE_CHANGED)


class LocalMaterialPreviewSource:
    """Resolve a Material ID locally and return no path-bearing object."""

    __slots__ = (
        "_content_types",
        "_media_tools",
        "_state_directory",
        "_stream_facts_reader",
        "_types_lock",
    )

    def __init__(
        self,
        *,
        state_directory: Path,
        media_tools: PackagedMediaTools,
        stream_facts_reader: Callable[
            [PackagedMediaTools, Path], MediaStreamFacts
        ] = read_stream_facts,
    ) -> None:
        self._state_directory = state_directory
        self._media_tools = media_tools
        self._stream_facts_reader = stream_facts_reader
        self._content_types: OrderedDict[
            UUID,
            tuple[
                tuple[int, int, int, int],
                ProbedMaterialKind,
                str | None,
            ],
        ] = OrderedDict()
        self._types_lock = threading.Lock()

    def __repr__(self) -> str:
        return "LocalMaterialPreviewSource(<redacted>)"

    def open(self, material_id: UUID) -> LocalMaterialPreviewLease:
        try:
            path, approved = MaterialPathRegistry(state_directory=self._state_directory).resolve(
                material_id
            )
        except MaterialPathRegistryRejected as error:
            _preview_failure(error.rejection)
        identity_key = (
            approved.st_dev,
            approved.st_ino,
            approved.st_mtime_ns,
            approved.st_size,
        )
        with self._types_lock:
            cached = self._content_types.get(material_id)
            if cached is not None and cached[0] == identity_key:
                _, kind, cached_type = cached
                self._content_types.move_to_end(material_id)
            else:
                try:
                    facts = self._stream_facts_reader(self._media_tools, path)
                    _, approved = require_source_unchanged(path, approved)
                except MaterialProbeRejected as error:
                    _probe_failure(error.rejection)
                kind = facts.kind
                if not isinstance(kind, ProbedMaterialKind):
                    _reject(LocalMaterialPreviewFailureCode.UNSUPPORTED_MEDIA)
                cached_type = None
                self._content_types[material_id] = (identity_key, kind, None)
                self._content_types.move_to_end(material_id)
                while len(self._content_types) > _MAX_CONTENT_TYPE_CACHE_ENTRIES:
                    self._content_types.popitem(last=False)

        stream: BinaryIO | None = None
        try:
            stream = path.open("rb", buffering=0)
            opened = os.fstat(stream.fileno())
        except OSError:
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
            try:
                MaterialPathRegistry(state_directory=self._state_directory).resolve(material_id)
            except MaterialPathRegistryRejected as error:
                _preview_failure(error.rejection)
            _reject(LocalMaterialPreviewFailureCode.FILE_UNREADABLE)
        if not stat.S_ISREG(opened.st_mode) or not _same_opened_identity(approved, opened):
            stream.close()
            _reject(LocalMaterialPreviewFailureCode.FILE_CHANGED)
        try:
            header = stream.read(_HEADER_BYTES)
            after_header = os.fstat(stream.fileno())
        except OSError:
            stream.close()
            _reject(LocalMaterialPreviewFailureCode.FILE_UNREADABLE)
        if not _same_opened_identity(opened, after_header):
            stream.close()
            _reject(LocalMaterialPreviewFailureCode.FILE_CHANGED)
        try:
            content_type = (
                safe_preview_content_type(kind, header) if cached_type is None else cached_type
            )
        except LocalMaterialPreviewRejected:
            stream.close()
            raise
        with self._types_lock:
            current = self._content_types.get(material_id)
            if current is not None and current[:2] == (identity_key, kind):
                self._content_types[material_id] = (
                    identity_key,
                    kind,
                    content_type,
                )
                self._content_types.move_to_end(material_id)
        try:
            current_path, current_identity = MaterialPathRegistry(
                state_directory=self._state_directory
            ).resolve(material_id)
        except MaterialPathRegistryRejected as error:
            stream.close()
            _preview_failure(error.rejection)
        if current_path != path or not _same_opened_identity(opened, current_identity):
            stream.close()
            _reject(LocalMaterialPreviewFailureCode.FILE_CHANGED)
        return LocalMaterialPreviewLease(stream, opened, content_type)


__all__ = [
    "LocalMaterialPreviewFailureCode",
    "LocalMaterialPreviewLease",
    "LocalMaterialPreviewRejected",
    "LocalMaterialPreviewSource",
    "safe_preview_content_type",
]
