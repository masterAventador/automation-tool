"""Conversions every repository needs on the way back out of a stored row.

Extracted from `editing_project_repository.py` when the material repository
needed the same guard. Two copies of a rule about when *not* to convert is the
duplication most likely to drift: one of them gets "simplified" into the
straightforward version, and the simplification is silent because the
straightforward version passes every test written against the other copy.
"""

from __future__ import annotations

from datetime import UTC, datetime


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


__all__ = ["normalise_timestamp"]
