"""Strong, canonical identifiers shared by Control Plane resources."""

from dataclasses import dataclass
from typing import ClassVar, Self, final
from uuid import UUID, uuid4


class InvalidResourceId(ValueError):
    """A resource identifier failed type, format, or version validation."""

    def __init__(self, resource: str) -> None:
        super().__init__(f"Invalid {resource} resource ID")
        self.resource = resource


@dataclass(frozen=True, slots=True)
class ResourceId:
    """An immutable UUIDv4 resource identifier with canonical text encoding."""

    _resource: ClassVar[str] = "resource"
    _value: UUID

    def __post_init__(self) -> None:
        if not isinstance(self._value, UUID) or self._value.version != 4:
            raise InvalidResourceId(self._resource)

    @classmethod
    def new(cls) -> Self:
        """Generate a new cryptographically random identifier."""
        return cls(uuid4())

    @classmethod
    def parse(cls, value: object) -> Self:
        """Parse a UUID object or its exact canonical lowercase representation."""
        if isinstance(value, cls):
            return value
        if isinstance(value, ResourceId):
            raise InvalidResourceId(cls._resource)

        parsed: UUID
        if isinstance(value, UUID):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = UUID(value)
            except ValueError as error:
                raise InvalidResourceId(cls._resource) from error
            if value != str(parsed):
                raise InvalidResourceId(cls._resource)
        else:
            raise InvalidResourceId(cls._resource)

        return cls(parsed)

    @property
    def uuid(self) -> UUID:
        """Return the validated UUID for persistence and protocol adapters."""
        return self._value

    def __str__(self) -> str:
        return str(self._value)


@final
class InstallationId(ResourceId):
    """A stable App installation identifier."""

    __slots__ = ()
    _resource = "installation"


@final
class ExecutorId(ResourceId):
    """A stable Local Executor identifier."""

    __slots__ = ()
    _resource = "executor"


@final
class ExecutorConnectionId(ResourceId):
    """An ephemeral identifier for one authenticated Executor connection."""

    __slots__ = ()
    _resource = "executor connection"


@final
class TaskId(ResourceId):
    """A stable task identifier."""

    __slots__ = ()
    _resource = "task"


@final
class TargetId(ResourceId):
    """A stable identifier for one persisted Task target row."""

    __slots__ = ()
    _resource = "target"


@final
class ExecutionAttemptId(ResourceId):
    """A stable identifier for one execution attempt."""

    __slots__ = ()
    _resource = "execution attempt"


@final
class ActionId(ResourceId):
    """A stable identifier for one externally observable action."""

    __slots__ = ()
    _resource = "action"


@final
class ArtifactId(ResourceId):
    """A stable local or cloud artifact identifier."""

    __slots__ = ()
    _resource = "artifact"


__all__ = [
    "ActionId",
    "ArtifactId",
    "ExecutionAttemptId",
    "ExecutorConnectionId",
    "ExecutorId",
    "InstallationId",
    "InvalidResourceId",
    "ResourceId",
    "TargetId",
    "TaskId",
]
