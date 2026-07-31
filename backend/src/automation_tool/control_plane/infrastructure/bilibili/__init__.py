"""Bilibili open-platform infrastructure adapters for PB-03 publishing."""

from automation_tool.control_plane.infrastructure.bilibili.material import (
    FilesystemBilibiliCoverSource,
    FilesystemBilibiliPublishMaterial,
)
from automation_tool.control_plane.infrastructure.bilibili.open_api_client import (
    BilibiliGatewayEndpoints,
    BilibiliQueryGatewayEndpoints,
    HttpxBilibiliArchiveQueryGateway,
    HttpxBilibiliOpenApiGateway,
)
from automation_tool.control_plane.infrastructure.bilibili.signing import (
    BilibiliApiCredentials,
    build_signed_headers,
)
from automation_tool.control_plane.infrastructure.bilibili.token_provider import (
    BilibiliTokenSnapshot,
    HttpxBilibiliAccessTokenProvider,
)

__all__ = [
    "BilibiliApiCredentials",
    "BilibiliGatewayEndpoints",
    "BilibiliQueryGatewayEndpoints",
    "BilibiliTokenSnapshot",
    "FilesystemBilibiliCoverSource",
    "FilesystemBilibiliPublishMaterial",
    "HttpxBilibiliAccessTokenProvider",
    "HttpxBilibiliArchiveQueryGateway",
    "HttpxBilibiliOpenApiGateway",
    "build_signed_headers",
]
