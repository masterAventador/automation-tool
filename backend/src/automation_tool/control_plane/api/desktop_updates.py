"""Unauthenticated generic desktop updater feed outside the business OpenAPI."""

from typing import Annotated, Literal

from fastapi import APIRouter, Path, Request, Response
from fastapi.responses import JSONResponse
from pydantic import AfterValidator

from automation_tool.control_plane.application.desktop_updates import (
    DesktopUpdateCatalog,
    validate_canonical_semver,
)

router = APIRouter(prefix="/desktop-updates/v1", include_in_schema=False)
CanonicalVersion = Annotated[str, AfterValidator(validate_canonical_semver)]
Channel = Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]{0,31}$")]


@router.get("/{channel}/{target}/{arch}/{current_version}")
async def desktop_update_feed(
    request: Request,
    channel: Channel,
    target: Literal["darwin", "windows"],
    arch: Literal["aarch64", "x86_64"],
    current_version: CanonicalVersion,
) -> Response:
    """Return the highest matching official dynamic updater response or 204."""

    catalog: DesktopUpdateCatalog = request.app.state.desktop_update_catalog
    release = catalog.find_update(
        channel=channel,
        target=target,
        arch=arch,
        current_version=current_version,
    )
    headers = {"cache-control": "public, max-age=60"}
    if release is None:
        return Response(status_code=204, headers=headers)
    return JSONResponse(release.feed_document(), headers=headers)
