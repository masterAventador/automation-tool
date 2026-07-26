#!/usr/bin/env python3
"""Check local cross-script imports without importing the provider modules.

Importing a script to inspect it is unsafe because acceptance entrypoints may
build artifacts, start services, or run other heavyweight work at import time.
The gate therefore uses Python's AST and compares imported names only with
runtime-reachable module-scope bindings.
"""

from __future__ import annotations

import argparse
import ast
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ImportViolation:
    importer: Path
    line: int
    module: str
    name: str

    def __str__(self) -> str:
        return (
            f"{self.importer}:{self.line}: "
            f"{self.module!r} has no runtime module symbol {self.name!r}"
        )


def _source_files(repository_root: Path) -> tuple[Path, ...]:
    roots = (repository_root / "scripts", repository_root / "backend" / "tests")
    return tuple(
        sorted(path for root in roots if root.is_dir() for path in root.rglob("*.py"))
    )


def _script_module_name(path: Path, scripts_root: Path) -> str:
    relative = path.relative_to(scripts_root).with_suffix("")
    parts = relative.parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _script_modules(repository_root: Path) -> dict[str, Path]:
    scripts_root = repository_root / "scripts"
    modules: dict[str, Path] = {}
    for path in sorted(scripts_root.rglob("*.py")):
        module = _script_module_name(path, scripts_root)
        if not module:
            modules["scripts"] = path
            continue
        modules[module] = path
        modules[f"scripts.{module}"] = path
    return modules


def _target_names(target: ast.expr) -> Iterable[str]:
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, (ast.List, ast.Tuple)):
        for element in target.elts:
            yield from _target_names(element)
    elif isinstance(target, ast.Starred):
        yield from _target_names(target.value)


def _condition_truth(expression: ast.expr) -> bool | None:
    if isinstance(expression, ast.Name) and expression.id == "TYPE_CHECKING":
        return False
    if (
        isinstance(expression, ast.Attribute)
        and isinstance(expression.value, ast.Name)
        and expression.value.id == "typing"
        and expression.attr == "TYPE_CHECKING"
    ):
        return False
    try:
        value = ast.literal_eval(expression)
    except (ValueError, TypeError):
        return None
    return bool(value)


def _abrupt_effects(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Break):
        return {"break"}
    if isinstance(node, ast.Continue):
        return {"continue"}
    if isinstance(node, ast.Raise):
        return {"raise"}
    if isinstance(node, ast.Return):
        return {"return"}
    if isinstance(
        node,
        (
            ast.FunctionDef,
            ast.AsyncFunctionDef,
            ast.ClassDef,
            ast.Lambda,
        ),
    ):
        return set()
    if isinstance(node, ast.If):
        truth = _condition_truth(node.test)
        if truth is True:
            children: Iterable[ast.AST] = node.body
        elif truth is False:
            children = node.orelse
        else:
            children = (*node.body, *node.orelse)
        return {effect for child in children for effect in _abrupt_effects(child)}
    if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
        effects = {
            effect
            for child in (*node.body, *node.orelse)
            for effect in _abrupt_effects(child)
        }
        return effects - {"break", "continue"}
    if isinstance(node, (ast.Try, ast.TryStar)):
        body_effects = {
            effect for child in node.body for effect in _abrupt_effects(child)
        }
        if node.handlers:
            body_effects.discard("raise")
        return body_effects | {
            effect
            for child in (
                *node.handlers,
                *node.orelse,
                *node.finalbody,
            )
            for effect in _abrupt_effects(child)
        }
    if isinstance(node, (ast.With, ast.AsyncWith)):
        effects = {effect for child in node.body for effect in _abrupt_effects(child)}
        effects.discard("raise")
        return effects
    else:
        children = ast.iter_child_nodes(node)
    return {effect for child in children for effect in _abrupt_effects(child)}


def _handler_bindings(
    handler: ast.ExceptHandler,
    initial: Iterable[str],
) -> set[str]:
    bindings = _module_bindings(handler.body, initial)
    if handler.name is not None:
        bindings.discard(handler.name)
    return bindings


def _module_bindings(
    statements: Iterable[ast.stmt],
    initial: Iterable[str] = (),
) -> set[str]:
    bindings = set(initial)
    for statement in statements:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bindings.add(statement.name)
        elif isinstance(statement, ast.Assign):
            for target in statement.targets:
                bindings.update(_target_names(target))
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            bindings.update(_target_names(statement.target))
        elif isinstance(statement, ast.Import):
            bindings.update(
                alias.asname or alias.name.split(".", 1)[0] for alias in statement.names
            )
        elif isinstance(statement, ast.ImportFrom):
            bindings.update(
                alias.asname or alias.name
                for alias in statement.names
                if alias.name != "*"
            )
        elif isinstance(statement, ast.Delete):
            for target in statement.targets:
                bindings.difference_update(_target_names(target))
        elif isinstance(statement, ast.TypeAlias):
            bindings.update(_target_names(statement.name))
        elif isinstance(statement, ast.If):
            truth = _condition_truth(statement.test)
            if truth is True:
                bindings = _module_bindings(statement.body, bindings)
            elif truth is False:
                bindings = _module_bindings(statement.orelse, bindings)
            else:
                body_bindings = _module_bindings(statement.body, bindings)
                else_bindings = _module_bindings(statement.orelse, bindings)
                bindings = body_bindings & else_bindings
        elif isinstance(statement, (ast.Try, ast.TryStar)):
            normal_bindings = _module_bindings(statement.body, bindings)
            normal_bindings = _module_bindings(statement.orelse, normal_bindings)
            successful_paths = [normal_bindings]
            if statement.handlers:
                possible_exception_bindings = [set(bindings)]
                progressed_bindings = set(bindings)
                for body_statement in statement.body:
                    progressed_bindings = _module_bindings(
                        (body_statement,),
                        progressed_bindings,
                    )
                    possible_exception_bindings.append(progressed_bindings)
                successful_paths.extend(
                    _handler_bindings(handler, exception_bindings)
                    for handler in statement.handlers
                    for exception_bindings in possible_exception_bindings
                )
            bindings = set.intersection(*successful_paths)
            bindings = _module_bindings(statement.finalbody, bindings)
        elif isinstance(statement, (ast.With, ast.AsyncWith)):
            for item in statement.items:
                if item.optional_vars is not None:
                    bindings.update(_target_names(item.optional_vars))
            successful_paths = [set(bindings)]
            progressed_bindings = set(bindings)
            for body_statement in statement.body:
                progressed_bindings = _module_bindings(
                    (body_statement,),
                    progressed_bindings,
                )
                successful_paths.append(progressed_bindings)
            bindings = set.intersection(*successful_paths)
        elif isinstance(statement, (ast.For, ast.AsyncFor)):
            iteration_bindings = set(bindings)
            iteration_bindings.update(_target_names(statement.target))
            iteration_bindings = _module_bindings(
                statement.body,
                iteration_bindings,
            )
            successful_paths = [
                _module_bindings(statement.orelse, bindings),
                iteration_bindings,
                _module_bindings(statement.orelse, iteration_bindings),
            ]
            bindings = set.intersection(*successful_paths)
        elif isinstance(statement, ast.While):
            truth = _condition_truth(statement.test)
            if truth is False:
                bindings = _module_bindings(statement.orelse, bindings)
            else:
                iteration_bindings = _module_bindings(statement.body, bindings)
                successful_paths = [
                    iteration_bindings,
                    _module_bindings(statement.orelse, iteration_bindings),
                ]
                if truth is None:
                    successful_paths.append(
                        _module_bindings(statement.orelse, bindings)
                    )
                bindings = set.intersection(*successful_paths)
        elif isinstance(statement, ast.Match):
            successful_paths = [set(bindings)]
            successful_paths.extend(
                _module_bindings(case.body, bindings) for case in statement.cases
            )
            bindings = set.intersection(*successful_paths)
        if _abrupt_effects(statement):
            break
    return bindings


def _runtime_imports(node: ast.AST) -> Iterable[ast.ImportFrom]:
    if isinstance(node, ast.ImportFrom):
        yield node
        return
    if isinstance(node, ast.If):
        truth = _condition_truth(node.test)
        if truth is True:
            children: Iterable[ast.AST] = node.body
        elif truth is False:
            children = node.orelse
        else:
            children = (*node.body, *node.orelse)
    else:
        children = ast.iter_child_nodes(node)
    for child in children:
        yield from _runtime_imports(child)


def _is_local_script_package(module: str, modules: dict[str, Path]) -> bool:
    prefix = f"{module}."
    return any(candidate.startswith(prefix) for candidate in modules)


def find_missing_imported_symbols(repository_root: Path) -> tuple[ImportViolation, ...]:
    repository_root = Path(repository_root)
    modules = _script_modules(repository_root)
    parsed = {
        path: ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for path in _source_files(repository_root)
    }
    provider_paths = set(modules.values())
    bindings_by_path = {
        path: _module_bindings(tree.body)
        for path, tree in parsed.items()
        if path in provider_paths
    }
    violations: list[ImportViolation] = []
    for importer, tree in parsed.items():
        for node in _runtime_imports(tree):
            if node.level or not node.module:
                continue
            provider = modules.get(node.module)
            local_package = _is_local_script_package(node.module, modules)
            if provider is None and not local_package:
                continue
            bindings = bindings_by_path.get(provider, set())
            for alias in node.names:
                if alias.name == "*":
                    continue
                child_module = f"{node.module}.{alias.name}"
                if alias.name not in bindings and child_module not in modules:
                    violations.append(
                        ImportViolation(
                            importer=importer.relative_to(repository_root),
                            line=node.lineno,
                            module=node.module,
                            name=alias.name,
                        )
                    )
    return tuple(
        sorted(
            violations,
            key=lambda violation: (
                violation.importer.as_posix(),
                violation.line,
                violation.module,
                violation.name,
            ),
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "repository_root",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    arguments = parser.parse_args(argv)
    violations = find_missing_imported_symbols(arguments.repository_root)
    for violation in violations:
        print(violation)
    if violations:
        print(f"{len(violations)} broken local import symbol(s)")
        return 1
    print("all local script import symbols resolve to runtime module bindings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
