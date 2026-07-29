"""The material persistence boundary's failure vocabulary.

Four outcomes, because a caller has four different things to do about them: a
duplicate is either the same file arriving twice or the same identifier reused,
and neither gets better on a retry; a missing row is a question that got an
answer; an unavailable database is worth retrying; and a rejected argument is a
bug upstairs. Collapsing them into one exception makes every caller guess, and
leaves the REST layer above answering 409, 404 and 503 with the same status.

Every message is a fixed string and no constructor takes an argument, so nothing
reaching a log through one of these can carry a connection string, a content
digest or a private path -- and `raise ... ("detail")` is a `TypeError` at the
call site rather than a leak. The digest matters here specifically: it is
derived from a file on the user's own disk, and a duplicate-import refusal is
exactly the moment something would be tempted to name it.
"""

from __future__ import annotations


class _MaterialPersistenceFailure(RuntimeError):
    message = "Material persistence failed"

    def __init__(self) -> None:
        super().__init__(self.message)


class MaterialAlreadyRegistered(_MaterialPersistenceFailure):
    message = "Material is already registered"


class MaterialNotFound(_MaterialPersistenceFailure):
    message = "Material was not found"


class MaterialDataRejected(_MaterialPersistenceFailure):
    message = "Material data is rejected"


class MaterialPersistenceUnavailable(_MaterialPersistenceFailure):
    message = "Material persistence is unavailable"


__all__ = [
    "MaterialAlreadyRegistered",
    "MaterialDataRejected",
    "MaterialNotFound",
    "MaterialPersistenceUnavailable",
]
