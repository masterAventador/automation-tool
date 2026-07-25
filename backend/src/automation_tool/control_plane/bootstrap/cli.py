"""Safe local-development entry point for the Control Plane."""

import uvicorn
from fastapi import FastAPI

from automation_tool.control_plane import create_app
from automation_tool.control_plane.bootstrap.local_provisioning import (
    local_app_data_directory,
    provision_local_registration_bootstrap,
)
from automation_tool.control_plane.logging import install_control_plane_log_redaction
from automation_tool.protocol import MAX_EXECUTOR_MESSAGE_BYTES


def local_app() -> FastAPI:
    """Issue this start's registration grant, then build the ordinary app.

    The App on this machine has no other way to obtain a first device
    credential, so the loopback deployment hands one over. The grant is signed
    by a key that exists only for the lifetime of this call; the service keeps
    only the public half, through the same registration wiring a Demo
    deployment uses.
    """

    provisioned = provision_local_registration_bootstrap(local_app_data_directory())
    return create_app(local_registration_bootstrap=provisioned)


def main() -> None:
    """Run the app factory on loopback for local desktop development."""

    install_control_plane_log_redaction()
    uvicorn.run(
        "automation_tool.control_plane.bootstrap.cli:local_app",
        factory=True,
        host="127.0.0.1",
        port=8765,
        access_log=False,
        ws="websockets-sansio",
        ws_max_size=MAX_EXECUTOR_MESSAGE_BYTES,
    )


__all__ = ["local_app", "main"]
