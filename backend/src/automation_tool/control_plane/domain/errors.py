"""Domain failures that are safe to translate at application boundaries."""


class DependencyUnavailable(RuntimeError):
    """A required dependency could not serve the current operation."""

    def __init__(self, dependency: str) -> None:
        super().__init__("Required dependency is unavailable")
        self.dependency = dependency
