"""Every package that loads its leaf modules lazily still behaves like a module.

The laziness exists so a leaf can be embedded without dragging in the rest of the
package — the Python 3.11 video Worker relies on it. What must not change is the
outside view: a public name resolves to the same object it would have had, and a
name that is not public raises `AttributeError` rather than importing anything.
"""

from __future__ import annotations

import pytest

from automation_tool import control_plane, executor, protocol
from automation_tool.control_plane import application, domain


def _package_id(value: object) -> str:
    return str(getattr(value, "__name__", value))


PACKAGES = (
    (executor, "ExecutorBootstrap"),
    (application, "InstallationRegistrationService"),
    (domain, "InstallationId"),
    (protocol, "ACTION_AUTHORIZATION_VERSION"),
    (control_plane, "create_app"),
)


@pytest.mark.parametrize(("package", "name"), PACKAGES, ids=_package_id)
def test_a_public_name_resolves_and_is_then_a_plain_module_attribute(
    package: object, name: str
) -> None:
    resolved = getattr(package, name)

    assert resolved is not None
    assert vars(package)[name] is resolved
    assert getattr(package, name) is resolved


@pytest.mark.parametrize(("package", "name"), PACKAGES, ids=_package_id)
def test_a_name_this_package_does_not_publish_is_an_attribute_error(
    package: object, name: str
) -> None:
    with pytest.raises(AttributeError, match="has no attribute"):
        getattr(package, "NotSomethingThisPackagePublishes")  # noqa: B009 - the lookup is the test


@pytest.mark.parametrize(
    ("package", "name"),
    (
        (executor, "ExecutorBootstrap"),
        (domain, "InstallationId"),
        (protocol, "TaskCommandEnvelope"),
    ),
    ids=_package_id,
)
def test_a_published_name_no_leaf_module_owns_is_still_an_attribute_error(
    package: object, name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`__all__` and the searched modules must agree; a mismatch is not a silent None."""
    monkeypatch.delitem(vars(package), name, raising=False)
    monkeypatch.setattr(package, "_PUBLIC_MODULES", ())

    with pytest.raises(AttributeError, match="has no attribute"):
        getattr(package, name)
