"""`Material`'s description protection must not be reachable around the side.

`Material.with_ai_description` returns `self` unchanged when a person already
wrote the description. That rule lives in one method precisely so that no future
describe pass has to remember it -- and one that forgets destroys a user's own
words with model output, silently and irreversibly.

Two routes go around the method, and both are ordinary Python that no test of
behaviour would ever notice:

* `dataclasses.replace(material, ai_description=...)` -- the same call the
  method itself makes, minus the check in front of it;
* `Material(...)` built field by field from parts of an existing one.

So the guard is structural: outside `material.py`, production code may not do
either. This is what the local-editing roadmap means by "a structural boundary
test for the description-protection rule"; there is no behavioural test that
can express "nobody wrote this line".

**Scope is `backend/src` only.** Tests construct `Material` freely -- that is
how a domain object gets tested at all, and LE-02's own suite would be illegal
under a wider sweep. The rule is about production code, and the exemption below
is about the one production caller that legitimately builds one from parts.

**What this can and cannot see.** Neither route is decided exactly, and the
approximations are written out here rather than implied.

Route two -- construction -- is matched by the *name being called*, resolved
against what the module actually bound: the class's own spelling, whatever
`from ... import Material as X` bound, and any chain of `Y = X`,
`Y: type = X` or `Y = module.Material` bindings after it. **It is not exhaustive
either**, and every widening so far was prompted by a one-line rewrite that had
sailed through the version before it:

* matching the single spelling `Material` missed `as M`, `as _Mat`,
  `Rebuild = Material` and a two-step chain -- and the aliased-import case was
  warned about in this file's own comments while the check still missed it;
* following only `ast.Assign` missed `Rebuild: type = Material`, where adding a
  type annotation reads as complying with "plain `Y = X`" while stepping outside
  it;
* following only `Name` values missed `Rebuild = module.Material`, so binding
  the attribute to a name first defeated the one route that had looked reliable.

The pattern is worth naming: each fix closed the shape that had just been
demonstrated, and the next shape was always one line away. What stops the
regress is not another branch but the blind-spot list below -- the honest
statement of where name-following ends.

Route one -- `replace` -- cannot be resolved at all, because it takes any
dataclass and AST has no types. Two approximations cover the realistic shapes:

* a module that imports `Material` under any name may not call `replace` at
  all -- if you can name the type you are in a position to rebuild one, and the
  four existing `replace` call sites in `backend/src` are all in modules that
  do not;
* a `replace` whose first argument is *named* like a material is refused
  wherever it appears, which catches the one shape that does not need the
  import: `replace(material, ...)` on a value handed back by the repository.

**Registered blind spots, on both routes.** Each has a test below that records
it, so closing one later fails loudly and sends someone back to this list:

1. **A name produced at run time.** `getattr(module, "Material")(...)`, a lookup
   through a dict, or a factory returning the class. Nothing static resolves
   these, and no amount of name-following changes that.
2. **A name bound by unpacking or through a container.** `Rebuild, = (Material,)`
   binds without either node shape the binding walk understands. Closing it
   means matching tuple and list targets against tuple and list values position
   by position -- the first step towards evaluating the module rather than
   reading it, and the next container shape reopens it anyway.
3. **A `replace` on a `Material` under an unrelated name in a module that never
   imports the type.** Same root cause as route one generally.

None of the three is worth closing by this technique -- the first and third
cannot be, and the second costs more than it returns. Recording them is the
honest alternative to implying the boundary is airtight, and it is what keeps
the next reader from assuming a check they have not read actually stops them.

**What this guard is therefore for.** It makes the sanctioned path the easy one
and turns an accidental bypass into a failing test. It is not a barrier against
someone determined to get around it -- three of those are listed above and each
is one line. The rule it enforces is a design rule, and design rules are kept by
people who know why they exist; this file's job is to make sure they find out.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SOURCE_ROOT = Path(__file__).resolve().parents[3] / "src" / "automation_tool"
DOMAIN_MODULE = SOURCE_ROOT / "control_plane" / "domain" / "material.py"

# The single exemption, and it is a pair rather than a file: the repository may
# construct a `Material` inside `_hydrate` and nowhere else, not even elsewhere
# in the same module.
#
# The alternative the plan offered -- moving hydration into `material.py` so
# nothing outside needs the constructor -- was rejected after writing it out. A
# rebuild function there is a public second constructor in the very module this
# rule protects: anyone could call it from anywhere and reach exactly the field
# combination the rule exists to prevent, and the guard would have nothing left
# to match on. Naming one function in one file keeps the exemption smaller than
# the hole that would replace it.
#
# Hydration is also the one place where building from parts is the correct
# operation: a stored row is not a described material being re-described, it is
# a material being reconstituted -- and it must go through the constructor,
# because a row is input rather than truth.
HYDRATION_EXEMPTION = (
    SOURCE_ROOT / "control_plane" / "infrastructure" / "database" / "material_repository.py",
    "_hydrate",
)

PROTECTED_CLASS = "Material"
# `dataclasses.replace` and, since 3.13, `copy.replace` -- one name covers both,
# and the attribute form covers them however they were imported.
REBUILD_FUNCTION = "replace"


def _called_name(call: ast.Call) -> str | None:
    """The bare name of what is being called, for `f()` and for `mod.f()` alike.

    Matching is on the exact name, never a substring: `BilibiliPublishMaterial`
    and `OpaqueBearerMaterial` are real classes in this package, and a
    `str.endswith` test would report both as violations forever.
    """
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _imports_protected_class(tree: ast.AST) -> bool:
    """Whether this module pulled `Material` in from anywhere.

    An aliased import counts: renaming the class on the way in does not make a
    module less able to rebuild one, and reading the alias as an exemption is
    the kind of hole that only shows up after it has been used.
    """
    return any(
        isinstance(node, ast.ImportFrom)
        and any(alias.name == PROTECTED_CLASS for alias in node.names)
        for node in ast.walk(tree)
    )


def _local_names_for_protected_class(tree: ast.AST) -> set[str]:
    """Every local name this module can call the protected class by.

    The class's own spelling is always in the set, because that is what an
    unaliased import binds and what an attribute access spells. On top of it:

    * `from ... import Material as M` binds `M`, and matching only the original
      spelling misses it entirely -- renaming on import is the first thing
      anyone reaches for when a check complains about a name;
    * `Rebuild = Material` binds another one, and `Build = Rebuild` another
      after that, so the assignments are followed to a fixed point rather than
      one level. Stopping at one level leaves a two-line evasion that is
      obvious the moment the rule is read.

    Three shapes of binding are followed, each because leaving it out left a
    one-line way around the rule:

    * `Rebuild = Material` -- the plain case;
    * `Rebuild: type = Material` -- an annotated binding is still a binding, and
      an earlier version followed only `ast.Assign`, so adding a type annotation
      read as complying while stepping outside the letter of the rule;
    * `Rebuild = material_module.Material` -- calling the attribute directly is
      caught on the attribute name, but binding it to a plain name first was
      not, which made "give it another name" work on the one route that was
      supposed to be the reliable half.

    Not followed: a rebinding produced by unpacking, by a container, or at run
    time. Those are in the module docstring's blind-spot list, each with a test.
    """
    names = {PROTECTED_CLASS}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(
                alias.asname or alias.name for alias in node.names if alias.name == PROTECTED_CLASS
            )
    while True:
        discovered: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and _binds_protected_class(node.value, names):
                discovered.update(
                    target.id for target in node.targets if isinstance(target, ast.Name)
                )
            elif (
                isinstance(node, ast.AnnAssign)
                and node.value is not None
                and isinstance(node.target, ast.Name)
                and _binds_protected_class(node.value, names)
            ):
                discovered.add(node.target.id)
        if discovered <= names:
            return names
        names |= discovered


def _binds_protected_class(value: ast.expr, names: set[str]) -> bool:
    """Whether the right-hand side of an assignment is the protected class."""
    if isinstance(value, ast.Name):
        return value.id in names
    if isinstance(value, ast.Attribute):
        return value.attr == PROTECTED_CLASS
    return False


def _named_like_a_material(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return "material" in node.id.lower()
    if isinstance(node, ast.Attribute):
        return "material" in node.attr.lower()
    return False


def _exempt_calls(tree: ast.AST, function_name: str | None) -> set[int]:
    """Identities of the `Call` nodes inside the exempted function, if any."""
    if function_name is None:
        return set()
    exempt: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == function_name:
            exempt.update(id(inner) for inner in ast.walk(node) if isinstance(inner, ast.Call))
    return exempt


def _constructs_protected_class(call: ast.Call, local_names: set[str]) -> bool:
    """Whether this call builds the protected class.

    A `Name` is matched against the module's local bindings, so an import alias
    or an assignment alias counts. An `Attribute` is matched on the attribute
    itself, because `some_module.Material(...)` spells the class's own name
    however the module was reached.
    """
    if isinstance(call.func, ast.Name):
        return call.func.id in local_names
    if isinstance(call.func, ast.Attribute):
        return call.func.attr == PROTECTED_CLASS
    return False


def construction_violations(
    source: str,
    *,
    path: str,
    exempt_function: str | None = None,
) -> list[str]:
    """Every place in one module that builds or rebuilds a `Material`."""
    tree = ast.parse(source)
    local_names = _local_names_for_protected_class(tree)
    imports_material = _imports_protected_class(tree)
    exempt = _exempt_calls(tree, exempt_function)
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or id(node) in exempt:
            continue
        if _constructs_protected_class(node, local_names):
            violations.append(f"{path}:{node.lineno}: constructs {PROTECTED_CLASS} directly")
        elif _called_name(node) == REBUILD_FUNCTION and (
            imports_material or (node.args and _named_like_a_material(node.args[0]))
        ):
            violations.append(
                f"{path}:{node.lineno}: rebuilds a {PROTECTED_CLASS} with {REBUILD_FUNCTION}()"
            )
    return violations


def test_the_exempted_hydration_function_still_exists() -> None:
    """An exemption naming something that is gone is a permanent free pass.

    Exemption lists rot exactly this way -- the entry outlives what it was
    written for, and the next module to take that path inherits the pass. This
    fails the moment the file is renamed or the function disappears, which
    forces whoever moved it to re-read the reasoning above.
    """
    path, function_name = HYDRATION_EXEMPTION
    assert path.is_file(), path
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assert any(
        isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == function_name
        for node in ast.walk(tree)
    ), f"{path} no longer defines {function_name}()"


VIOLATING_SOURCES = {
    "direct-construction": """
from automation_tool.control_plane.domain import Material

def describe(existing, text):
    return Material(
        material_id=existing.material_id,
        kind=existing.kind,
        ai_description=text,
    )
""",
    "qualified-construction": """
from automation_tool.control_plane.domain import material as material_module

def describe(existing, text):
    return material_module.Material(material_id=existing.material_id, ai_description=text)
""",
    "replace-in-a-module-that-imports-material": """
from dataclasses import replace

from automation_tool.control_plane.domain import Material

def describe(existing: Material, text: str) -> Material:
    return replace(existing, ai_description=text)
""",
    "replace-without-the-import": """
from dataclasses import replace

async def describe(repository, material_id, text):
    material = await repository.get(material_id)
    return replace(material, ai_description=text)
""",
    "qualified-replace": """
import dataclasses

async def describe(repository, material_id, text):
    stored_material = await repository.get(material_id)
    return dataclasses.replace(stored_material, ai_description=text)
""",
    # The four forms below rebind the class to another name. Matching the
    # spelling `Material` alone misses every one of them, and an import alias is
    # the first thing anyone reaches for when a check complains about a name.
    "aliased-import": """
from automation_tool.control_plane.domain import Material as M

def describe(existing, text):
    return M(material_id=existing.material_id, ai_description=text)
""",
    "aliased-import-private-name": """
from automation_tool.control_plane.domain import Material as _Mat

def describe(existing, text):
    return _Mat(material_id=existing.material_id, ai_description=text)
""",
    "assignment-alias": """
from automation_tool.control_plane.domain import Material

Rebuild = Material

def describe(existing, text):
    return Rebuild(material_id=existing.material_id, ai_description=text)
""",
    "chained-assignment-alias": """
from automation_tool.control_plane.domain import Material as M

Rebuild = M
Build = Rebuild

def describe(existing, text):
    return Build(material_id=existing.material_id, ai_description=text)
""",
    "replace-in-a-module-that-only-imports-an-alias": """
from dataclasses import replace

from automation_tool.control_plane.domain import Material as M

def describe(existing: M, text: str) -> M:
    return replace(existing, ai_description=text)
""",
    # An annotated binding is still a binding. The rule reads as "plain Y = X",
    # and adding a type annotation is exactly the sort of thing that reads as
    # complying while stepping outside the letter of it.
    "annotated-assignment-alias": """
from automation_tool.control_plane.domain import Material

Rebuild: type = Material

def describe(existing, text):
    return Rebuild(material_id=existing.material_id, ai_description=text)
""",
    # `material_module.Material(...)` is caught on the attribute; binding that
    # same attribute to a name first is not, unless the binding is followed.
    "attribute-bound-to-a-name": """
from automation_tool.control_plane.domain import material as material_module

Rebuild = material_module.Material

def describe(existing, text):
    return Rebuild(material_id=existing.material_id, ai_description=text)
""",
}


@pytest.mark.parametrize("source", list(VIOLATING_SOURCES.values()), ids=list(VIOLATING_SOURCES))
def test_the_checker_catches_a_module_that_goes_around_the_rule(source: str) -> None:
    """The checker proves it can fail before it is trusted for passing.

    A structural guard that has only ever been run against a clean tree is
    indistinguishable from one that reports nothing at all -- the failure mode
    that made this test's own subject worth writing. Each source here is a
    plausible way the next feature would reach the field combination the rule
    forbids.
    """
    assert construction_violations(source, path="synthetic.py") != []


CLEAN_SOURCES = {
    # The rule is about `Material`, and the exact-name matching that keeps these
    # two out is load-bearing: both are real classes in this package.
    "a-differently-named-material": """
from automation_tool.control_plane.application.opaque_bearers import OpaqueBearerMaterial

def issue():
    return OpaqueBearerMaterial(secret="x")
""",
    "replace-on-an-unrelated-dataclass": """
from dataclasses import replace

def touch(record, status):
    return replace(record, status=status)
""",
    "the-methods-the-rule-exists-to-funnel-callers-into": """
def describe(existing, text, tags, at):
    return existing.with_ai_description(text, tags, at)
""",
}


@pytest.mark.parametrize("source", list(CLEAN_SOURCES.values()), ids=list(CLEAN_SOURCES))
def test_the_checker_leaves_legitimate_code_alone(source: str) -> None:
    assert construction_violations(source, path="synthetic.py") == []


# Both of these go around the rule and both are reported clean. They are listed
# so the boundary is a recorded fact rather than an unexamined assumption -- and
# so that anyone who later finds a way to close one is told to come back here
# and update the docstring, by this test turning red.
BLIND_SPOTS = {
    "constructor-named-at-run-time": """
from automation_tool.control_plane.domain import material as material_module

def describe(existing, text):
    build = getattr(material_module, "Material")
    return build(material_id=existing.material_id, ai_description=text)
""",
    "replace-on-an-unrelated-name-without-the-import": """
from dataclasses import replace

async def describe(repository, identifier, text):
    subject = await repository.get(identifier)
    return replace(subject, ai_description=text)
""",
    # Unpacking binds a name without either of the two node shapes that binding
    # detection walks. Closing it means teaching the checker to evaluate tuple
    # and list targets against tuple and list values position by position, which
    # is the first step down the road of writing an interpreter -- and the next
    # container shape after that reopens it. Left registered instead.
    "alias-bound-by-unpacking": """
from automation_tool.control_plane.domain import Material

Rebuild, = (Material,)

def describe(existing, text):
    return Rebuild(material_id=existing.material_id, ai_description=text)
""",
}


@pytest.mark.parametrize("source", list(BLIND_SPOTS.values()), ids=list(BLIND_SPOTS))
def test_a_name_this_technique_cannot_resolve_is_a_recorded_blind_spot(source: str) -> None:
    """These are not caught, and asserting so is the point.

    A guard whose limits are undocumented gets read as airtight, and the next
    person to route around it does so believing the check would have stopped
    them. Static analysis cannot resolve a class fetched by `getattr` or a
    dataclass reached under a name that says nothing about its type; what it can
    do is say which of the two it is looking at.

    If this test ever fails, the checker got stronger -- delete the case and
    strike the matching entry from the module docstring's blind-spot list.
    """
    assert construction_violations(source, path="synthetic.py") == []


def test_the_exemption_is_what_makes_the_repository_pass() -> None:
    """Without the exemption the repository is a violation, and it must be.

    Otherwise the exemption could be deleted with nothing turning red, which
    would mean the guard was not reaching that file in the first place.
    """
    path, function_name = HYDRATION_EXEMPTION
    source = path.read_text(encoding="utf-8")
    assert construction_violations(source, path=str(path)) != []
    assert construction_violations(source, path=str(path), exempt_function=function_name) == []


def test_no_module_outside_the_domain_builds_a_material_from_parts() -> None:
    exempt_path, exempt_function = HYDRATION_EXEMPTION
    violations: list[str] = []
    scanned: set[Path] = set()
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if path == DOMAIN_MODULE:
            continue
        scanned.add(path)
        violations.extend(
            construction_violations(
                path.read_text(encoding="utf-8"),
                path=str(path.relative_to(SOURCE_ROOT)),
                exempt_function=exempt_function if path == exempt_path else None,
            )
        )
    # A sweep that silently found nothing to read would pass this test while
    # checking nothing, which is how a structural guard stops guarding.
    assert len(scanned) > 100
    # And the exempted file has to be among what was read. Skipping it outright
    # would look identical from the outside while handing that whole module a
    # free pass -- measured: making the loop `continue` past it instead of
    # exempting one function inside it left every other test here green,
    # including the one above that checks the exemption is load-bearing.
    assert exempt_path in scanned
    assert violations == []
