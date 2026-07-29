"""The material persistence boundary's failure vocabulary.

Five outcomes, because a caller has five different things to do about them: a
duplicate is either the same file arriving twice or the same identifier reused,
and neither gets better on a retry; a missing row is a question that got an
answer; a description a person has taken over is a refusal aimed at one caller
and nobody else; an unavailable database is worth retrying; and a rejected
argument is a bug upstairs. Collapsing them into one exception makes every
caller guess, and leaves the REST layer above answering 409, 404 and 503 with
the same status.

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


class MaterialDescriptionProtected(_MaterialPersistenceFailure):
    """A describe pass tried to write over a description a person owns.

    Deliberately not `MaterialNotFound`, even though both surface as an update
    that matched nothing. The material is right there; what changed is who owns
    the field. A caller told "not found" stops retrying and may conclude the
    material was deleted, and the REST layer above answers 404 where 409 is the
    honest reply.

    **Contract for anything that writes a model-generated description** -- this
    is written here rather than in a task note because this is the file such a
    caller imports, and a note lives in a document nobody will have open:

    1. **This is a normal terminal outcome, not an error.** It means the user
       has taken the field over and this model result is discarded. Catch it
       explicitly and record that; do **not** re-queue the work as a failure.
       `description_source = USER` is terminal, so a retry can never succeed.
    2. **Do not fold it into `MaterialNotFound`.** That one means the material
       is gone, so stop. This one means the material is fine and this one field
       is no longer yours to write.
    3. **The absence of this exception does not mean the description was
       stored.** `Material.with_ai_description` returns the material *unchanged*
       when the snapshot already says `USER`, and persisting that unchanged
       object succeeds -- correctly, since the user is rewriting their own
       field. So the same underlying fact ("the user owns this") reaches the
       caller two different ways: as this exception when the snapshot was stale,
       and as a silent success when it was current. A caller that infers "no
       exception, therefore my description was written" will count a discarded
       description as a stored one. Compare what came back, or check
       `description_source`, rather than reading success as proof of a write.

    The window between loading a `Material` and writing its description spans a
    model call, so this is a real interleaving rather than a theoretical one --
    though on the current single-user, single-device product it takes a user
    editing that exact material during that exact call to produce it.
    """

    message = "Material description is owned by the user"


class MaterialDataRejected(_MaterialPersistenceFailure):
    message = "Material data is rejected"


class MaterialPersistenceUnavailable(_MaterialPersistenceFailure):
    message = "Material persistence is unavailable"


__all__ = [
    "MaterialAlreadyRegistered",
    "MaterialDataRejected",
    "MaterialDescriptionProtected",
    "MaterialNotFound",
    "MaterialPersistenceUnavailable",
]
