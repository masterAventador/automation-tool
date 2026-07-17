"""Control Plane domain contracts and errors."""

from automation_tool.control_plane.domain.errors import DependencyUnavailable
from automation_tool.control_plane.domain.ports import DatabaseLifecycle

__all__ = ["DatabaseLifecycle", "DependencyUnavailable"]
