"""In-memory ownership of the Rust-authorized browser executable and private Profile."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Self

from automation_tool.executor.browser_runtime import BrowserLaunchRequest


class BrowserLaunchAuthorityRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("authorized browser launch is unavailable")


class BrowserLaunchLease:
    def __init__(
        self,
        request: BrowserLaunchRequest,
        operation_lock: threading.Lock,
    ) -> None:
        self.request = request
        self._operation_lock = operation_lock
        self._closed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exception: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._operation_lock.release()

    def __repr__(self) -> str:
        return "BrowserLaunchLease(<redacted>)"


class BrowserLaunchAuthority:
    """Retain no browser identity outside the Executor process and lease it exclusively."""

    def __init__(self) -> None:
        self._state_lock = threading.Lock()
        self._operation_lock = threading.Lock()
        self._request: BrowserLaunchRequest | None = None

    def __repr__(self) -> str:
        return "BrowserLaunchAuthority(<redacted>)"

    def authorize(self, request: BrowserLaunchRequest) -> None:
        if not isinstance(request, BrowserLaunchRequest):
            raise BrowserLaunchAuthorityRejected
        acquired = self._operation_lock.acquire(blocking=False)
        if not acquired:
            raise BrowserLaunchAuthorityRejected
        try:
            request.revalidate()
            with self._state_lock:
                self._request = request
        except Exception:
            raise BrowserLaunchAuthorityRejected from None
        finally:
            self._operation_lock.release()

    def revoke(self) -> None:
        acquired = self._operation_lock.acquire(blocking=False)
        if not acquired:
            raise BrowserLaunchAuthorityRejected
        try:
            with self._state_lock:
                self._request = None
        finally:
            self._operation_lock.release()

    @contextmanager
    def lease(self) -> Iterator[BrowserLaunchRequest]:
        lease = self.acquire()
        try:
            yield lease.request
        finally:
            lease.close()

    def acquire(self) -> BrowserLaunchLease:
        acquired = self._operation_lock.acquire(blocking=False)
        if not acquired:
            raise BrowserLaunchAuthorityRejected
        try:
            with self._state_lock:
                request = self._request
            if request is None:
                raise BrowserLaunchAuthorityRejected
            request.revalidate()
            return BrowserLaunchLease(request, self._operation_lock)
        except BrowserLaunchAuthorityRejected:
            self._operation_lock.release()
            raise
        except Exception:
            self._operation_lock.release()
            raise BrowserLaunchAuthorityRejected from None


__all__ = [
    "BrowserLaunchAuthority",
    "BrowserLaunchAuthorityRejected",
    "BrowserLaunchLease",
]
