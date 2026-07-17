"""Public entry points for the independently deployable Control Plane."""

from automation_tool.control_plane.bootstrap.app import create_app

__all__ = ["create_app"]
