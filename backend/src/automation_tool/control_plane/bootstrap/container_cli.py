"""Fixed production container entry point for the single-instance Control Plane."""

import uvicorn

from automation_tool.control_plane.logging import install_control_plane_log_redaction
from automation_tool.protocol import MAX_EXECUTOR_MESSAGE_BYTES


def main() -> None:
    """Run exactly one private upstream worker on the C10 deployment port."""

    install_control_plane_log_redaction()
    uvicorn.run(
        "automation_tool.control_plane:create_app",
        factory=True,
        host="0.0.0.0",
        port=8000,
        workers=1,
        access_log=False,
        server_header=False,
        timeout_graceful_shutdown=30,
        ws="websockets-sansio",
        ws_max_size=MAX_EXECUTOR_MESSAGE_BYTES,
    )


__all__ = ["main"]
