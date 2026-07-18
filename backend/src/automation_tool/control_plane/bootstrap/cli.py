"""Safe local-development entry point for the Control Plane."""

import uvicorn

from automation_tool.protocol import MAX_EXECUTOR_MESSAGE_BYTES


def main() -> None:
    """Run the app factory on loopback for local desktop development."""

    uvicorn.run(
        "automation_tool.control_plane:create_app",
        factory=True,
        host="127.0.0.1",
        port=8765,
        ws="websockets-sansio",
        ws_max_size=MAX_EXECUTOR_MESSAGE_BYTES,
    )
