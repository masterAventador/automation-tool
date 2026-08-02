"""The application layer's failure vocabulary must stay unable to carry detail.

Every `_...Failure` base in the application package fixes its message in a class
attribute and takes no constructor argument. That is not a style preference: it
is what makes `raise SomeFailure(row)` a `TypeError` at the call site instead of
a stored value, a connection string or a private path travelling into a log.

The property was previously only stated in a docstring. Widening a base to
`def __init__(self, *detail)` and passing it through to `super().__init__` broke
nothing in the whole suite, which means the guarantee had no regression cover at
all -- and LE-05's remaining tasks copy this base class shape.

Discovery is dynamic, so a module added later is covered the moment it lands
rather than when someone remembers to extend a list.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import pytest

import automation_tool.control_plane.application as application_package

# A base that is exempt would have to be listed here, with a reason. The list is
# empty on purpose: nothing in this vocabulary has a reason to accept detail.
EXEMPT_BASES: frozenset[str] = frozenset()


def _failure_bases() -> list[type]:
    """Every private `_...Failure` base declared in the application package.

    The `BaseException` check is not redundant with the name check. Matching on
    a name alone would sweep in a dataclass or an `Enum` that happened to end in
    `Failure` -- something like a `_RenderFailure` result value -- and it would
    fail loudly on `pytest.raises(TypeError)` for reasons having nothing to do
    with this guard, sending whoever reads the output down the wrong path.

    Two known limits of the sweep, neither of which bites today:

    * `iter_modules` does not recurse, so a failure vocabulary placed in a
      sub-package of `application/` would be skipped silently;
    * `__subclasses__()` returns direct subclasses only, so a grandchild -- a
      subclass of a concrete failure -- would not be checked.

    T2 through T4 are flat modules with one level of subclassing, so both hold.
    Whoever breaks either shape has to widen this function at the same time.
    """
    bases: list[type] = []
    for module_info in pkgutil.iter_modules(application_package.__path__):
        module = importlib.import_module(f"{application_package.__name__}.{module_info.name}")
        for name, value in vars(module).items():
            if (
                inspect.isclass(value)
                and issubclass(value, BaseException)
                and value.__module__ == module.__name__
                and name.startswith("_")
                and name.endswith("Failure")
                and name not in EXEMPT_BASES
            ):
                bases.append(value)
    return bases


def _failure_types() -> list[type]:
    """The bases plus every concrete failure that directly inherits from one."""
    types: list[type] = []
    for base in _failure_bases():
        types.append(base)
        types.extend(base.__subclasses__())
    return types


def test_discovery_finds_the_known_failure_vocabulary() -> None:
    """Without this, an empty discovery would make every check below vacuous.

    A parametrised test over an empty list is a passing test over nothing, which
    is exactly how a structural guard quietly stops guarding.
    """
    names = {found.__name__ for found in _failure_types()}
    assert "_EditingProjectPersistenceFailure" in names
    assert {
        "EditingProjectAlreadyRegistered",
        "EditingProjectNotFound",
        "EditingProjectDataRejected",
        "EditingProjectPersistenceUnavailable",
    } <= names
    # Three other modules already use the shape; the count only has to prove the
    # sweep reaches beyond the module this task happened to touch.
    assert len(_failure_bases()) >= 4


@pytest.mark.parametrize("failure", _failure_types(), ids=lambda failure: failure.__name__)
def test_a_failure_cannot_be_constructed_with_detail(failure: type) -> None:
    with pytest.raises(TypeError):
        failure("le05-private-detail")


@pytest.mark.parametrize("failure", _failure_types(), ids=lambda failure: failure.__name__)
def test_a_failure_carries_a_fixed_non_empty_message(failure: type) -> None:
    """A fixed message is the other half: empty text would leak nothing but also
    say nothing, and a subclass that forgot its own `message` would silently
    report its parent's."""
    instance = failure()
    assert str(instance) == failure.message  # type: ignore[attr-defined]
    assert str(instance).strip() != ""
