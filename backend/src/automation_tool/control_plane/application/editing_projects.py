"""The editing-project persistence boundary's failure vocabulary.

Four outcomes, because a caller has four different things to do about them: a
duplicate is the caller's own doing and retrying will never help; a missing row
is a question that got an answer; an unavailable database is worth retrying; and
a rejected argument is a bug upstairs. Collapsing them into one exception makes
every caller guess, and leaves the REST layer above answering 409, 404 and 503
with the same status.

Every message is a fixed string and no constructor takes an argument, so nothing
reaching a log through one of these can carry a connection string, a stored
value or a private path -- and `raise ... ("detail")` is a `TypeError` at the
call site rather than a leak.
"""

from __future__ import annotations


class _EditingProjectPersistenceFailure(RuntimeError):
    message = "Editing project persistence failed"

    def __init__(self) -> None:
        super().__init__(self.message)


class EditingProjectAlreadyRegistered(_EditingProjectPersistenceFailure):
    message = "Editing project is already registered"


class EditingProjectNotFound(_EditingProjectPersistenceFailure):
    message = "Editing project was not found"


class EditingProjectDataRejected(_EditingProjectPersistenceFailure):
    message = "Editing project data is rejected"


class EditingProjectPersistenceUnavailable(_EditingProjectPersistenceFailure):
    message = "Editing project persistence is unavailable"


__all__ = [
    "EditingProjectAlreadyRegistered",
    "EditingProjectDataRejected",
    "EditingProjectNotFound",
    "EditingProjectPersistenceUnavailable",
]
