"""Conversions every repository needs on the way back out of a stored row.

Each function lands here on its second user, never in anticipation of one:
`normalise_timestamp` came out of `editing_project_repository.py` when the
material repository needed the same guard, and `enumeration_member` out of
`timeline_repository.py` when the editing job repository did.

Both are rules about when *not* to convert, which is the duplication most
likely to drift: one copy gets "simplified" into the straightforward version --
`.astimezone(UTC)` without the guard, `members(stored)` without the lookup --
and the simplification is silent, because the straightforward version passes
every test written against the other copy.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum


def enumeration_member[MemberT: StrEnum](
    members: type[MemberT], stored: object, refusal: type[Exception]
) -> MemberT:
    """Parse a stored string back into a member, or refuse the row.

    Leaving the raw text on the object is the failure LE-04 recorded on
    `EditingJobStatus`: a bare string silently loses every `is` comparison
    against a member, and it loses them in the direction that reads as "carry
    on". `EditingJobStateMachine.is_terminal` answers `False` for the string
    `"succeeded"`, and a finished render would quietly go on looking unfinished.

    Compares against the members rather than calling `members(stored)`, which is
    the obvious spelling and does not type-check here: through `type[MemberT]`
    the call resolves to `StrEnum.__new__`, which is annotated as taking `str`,
    while what arrives from a stored row is `object`. Casting it to `str` to get
    past that would be a claim about the one value most likely to be something
    else -- `None`, a number, a nested object. Equality is honest about
    accepting anything, and it is the same lookup by value that calling the
    enumeration would have performed.

    The refusal is a parameter because each persistence module answers with its
    own domain error, and a caller should not have to catch two exceptions to
    mean "this row is not a timeline" or "this row is not an editing job". It is
    raised as a class, so every one of them has to keep taking no arguments --
    which is what stops an offending value being attached on the way out.
    """
    for member in members:
        if member.value == stored:
            return member
    raise refusal


def normalise_timestamp(value: object) -> object:
    """Normalise an already-valid timestamp; hand anything else on untouched.

    The order matters. `.astimezone(UTC)` on a *naive* datetime does not fail --
    it reinterprets it in the host's local timezone, moves the instant by that
    offset and hands back something aware, which then sails through the domain's
    check. Normalising before validating would launder exactly the value the
    domain exists to refuse. `None` and text are worse: they would raise a bare
    `AttributeError` from inside a repository, which is neither the domain's
    error nor one of the persistence module's. So the guard runs first, and only
    a timestamp that is already aware gets converted.

    Handing `None` straight back is what makes this usable for a nullable
    column: whether NULL is an ordinary value or a broken row is the domain's
    call, and it differs per column -- `editing_projects.created_at` refuses it,
    `materials.described_at` accepts it -- so this function must not decide.
    """
    if isinstance(value, datetime) and value.utcoffset() is not None:
        return value.astimezone(UTC)
    return value


__all__ = ["enumeration_member", "normalise_timestamp"]
