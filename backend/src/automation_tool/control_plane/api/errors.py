"""Fail-closed public error envelopes and request correlation."""

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Final, Literal
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from starlette.exceptions import HTTPException
from starlette.responses import Response

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER: Final = "x-request-id"
_REQUEST_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class TimelineRevisionConflictDetails(BaseModel):
    """The complete public context for a timeline revision conflict."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    kind: Literal["timeline_revision_conflict.v1"]
    current_revision: StrictInt = Field(alias="currentRevision", ge=1)


type PublicErrorDetails = TimelineRevisionConflictDetails


class PublicError(BaseModel):
    """The only error details allowed to cross the API boundary."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    code: str
    message: str
    retryable: bool
    request_id: str = Field(alias="requestId")
    details: PublicErrorDetails | None = None


class ErrorEnvelope(BaseModel):
    """Stable top-level shape for every Control Plane error."""

    model_config = ConfigDict(extra="forbid")

    error: PublicError


class AppError(Exception):
    """An expected application failure with an explicitly public message."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        retryable: bool = False,
        details: PublicErrorDetails | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details


def _new_request_id() -> str:
    return str(uuid4())


def _request_id(request: Request) -> str:
    current = getattr(request.state, "request_id", None)
    if isinstance(current, str) and _REQUEST_ID_PATTERN.fullmatch(current):
        return current
    generated = _new_request_id()
    request.state.request_id = generated
    return generated


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    retryable: bool = False,
    details: PublicErrorDetails | None = None,
) -> JSONResponse:
    request_id = _request_id(request)
    envelope = ErrorEnvelope(
        error=PublicError(
            code=code,
            message=message,
            retryable=retryable,
            requestId=request_id,
            details=details,
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=envelope.model_dump(mode="json", by_alias=True, exclude_none=True),
        headers={REQUEST_ID_HEADER: request_id, "cache-control": "no-store"},
    )


def install_request_context(app: FastAPI) -> None:
    """Attach one bounded request ID without trusting arbitrary header content."""

    @app.middleware("http")
    async def request_context(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        supplied = request.headers.get(REQUEST_ID_HEADER)
        request.state.request_id = (
            supplied
            if supplied is not None and _REQUEST_ID_PATTERN.fullmatch(supplied)
            else _new_request_id()
        )
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request.state.request_id
        return response


def register_error_handlers(app: FastAPI) -> None:
    """Normalize framework and application failures without reflecting private data."""

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, error: AppError) -> JSONResponse:
        return _error_response(
            request,
            status_code=error.status_code,
            code=error.code,
            message=error.message,
            retryable=error.retryable,
            details=error.details,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        _error: RequestValidationError,
    ) -> JSONResponse:
        return _error_response(
            request,
            status_code=422,
            code="validation",
            message="Request validation failed",
        )

    @app.exception_handler(HTTPException)
    async def handle_http_error(request: Request, error: HTTPException) -> JSONResponse:
        if error.status_code == 404:
            return _error_response(
                request,
                status_code=404,
                code="not_found",
                message="Resource not found",
            )
        return _error_response(
            request,
            status_code=error.status_code,
            code="request_rejected",
            message="Request failed",
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, _error: Exception) -> JSONResponse:
        logger.error(
            "Unhandled Control Plane error",
            extra={"request_id": _request_id(request)},
        )
        return _error_response(
            request,
            status_code=500,
            code="internal",
            message="Internal server error",
        )
