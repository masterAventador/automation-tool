"""Safe local-development entry point for the Control Plane."""

import uvicorn


def main() -> None:
    """Run the app factory on loopback for local desktop development."""

    uvicorn.run(
        "automation_tool.control_plane:create_app",
        factory=True,
        host="127.0.0.1",
        port=8765,
    )
