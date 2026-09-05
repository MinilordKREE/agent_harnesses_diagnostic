"""Snapshot validation: the runner refuses invalid harness trees, never adjusts them.

No reference source: written fresh for ahd (see docs/reuse/M2.md). Rules (owner decision,
M2 addendum):

1. ``harness.json`` budget keys equal the frozen ``Budget`` values.
2. ``harness.json:tools`` names exactly the tools registered in ``tools/__init__.py``'s
   ``TOOL_SCHEMAS`` (resolved statically from the tool modules' schema dict literals).
3. Imports are restricted to the standard library, modules inside the tree, and the two
   Evo-Bench modules the seed uses; a new third-party dependency would break the worker.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

from ahd.core.config import Budget, StrictModel
from ahd.errors import ConfigError

ALLOWED_EVOBENCH_MODULES: frozenset[str] = frozenset(
    {"evobench.models.client", "evobench.policy.injected_tools"}
)
BUDGET_KEYS: tuple[str, ...] = ("max_steps", "rollout_wall_clock_seconds")


class SnapshotInvalidError(ConfigError):
    """The tree violates a governance rule; see ``problems``."""

    def __init__(self, problems: tuple[str, ...]) -> None:
        super().__init__("invalid harness snapshot: " + "; ".join(problems))
        self.problems = problems


class ValidationReport(StrictModel):
    ok: bool
    problems: tuple[str, ...]
    tools_declared: tuple[str, ...]
    tools_registered: tuple[str, ...]
    external_imports: tuple[str, ...]


def _local_roots(tree: Path) -> set[str]:
    roots: set[str] = set()
    for path in tree.iterdir():
        if path.name.startswith(".") or path.name == "__pycache__":
            continue
        if path.is_dir() or path.suffix == ".py":
            roots.add(path.stem if path.is_file() else path.name)
    return roots


def _schema_names(tools_dir: Path) -> dict[str, str]:
    """Map module-level schema constants (``RUN_SHELL_TOOL``) to their ``function.name``."""
    names: dict[str, str] = {}
    for module_path in sorted(tools_dir.glob("*.py")):
        try:
            module = ast.parse(module_path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for stmt in module.body:
            targets: list[str] = []
            value: ast.expr | None = None
            if isinstance(stmt, ast.Assign):
                targets = [t.id for t in stmt.targets if isinstance(t, ast.Name)]
                value = stmt.value
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                targets = [stmt.target.id]
                value = stmt.value
            if not targets or not isinstance(value, ast.Dict):
                continue
            try:
                literal = ast.literal_eval(value)
            except (ValueError, SyntaxError):
                continue
            function = literal.get("function") if isinstance(literal, dict) else None
            if isinstance(function, dict) and isinstance(function.get("name"), str):
                for target in targets:
                    names[target] = function["name"]
    return names


def registered_tools(tree: Path) -> tuple[tuple[str, ...], list[str]]:
    """Tool names from ``tools/__init__.py:TOOL_SCHEMAS``, or problems when not statically
    resolvable."""
    problems: list[str] = []
    init_path = tree / "tools" / "__init__.py"
    if not init_path.is_file():
        return (), ["tools/__init__.py missing"]
    try:
        module = ast.parse(init_path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return (), [f"tools/__init__.py: syntax error: {exc}"]
    schema_names = _schema_names(tree / "tools")
    registry: ast.expr | None = None
    for stmt in module.body:
        if (
            isinstance(stmt, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "TOOL_SCHEMAS" for t in stmt.targets)
        ) or (
            isinstance(stmt, ast.AnnAssign)
            and isinstance(stmt.target, ast.Name)
            and stmt.target.id == "TOOL_SCHEMAS"
        ):
            registry = stmt.value
    if registry is None:
        return (), ["tools/__init__.py: TOOL_SCHEMAS assignment not found"]
    if not isinstance(registry, ast.List):
        return (), ["tools/__init__.py: TOOL_SCHEMAS must be a list literal of schema names"]
    names: list[str] = []
    for element in registry.elts:
        if not isinstance(element, ast.Name):
            problems.append("tools/__init__.py: TOOL_SCHEMAS elements must be plain names")
            continue
        if element.id not in schema_names:
            problems.append(
                f"tools/__init__.py: {element.id} is not a statically resolvable schema literal"
            )
            continue
        names.append(schema_names[element.id])
    return tuple(names), problems


def external_imports(tree: Path) -> tuple[tuple[str, ...], list[str]]:
    """Imports outside stdlib, the tree and the allowed Evo-Bench modules."""
    problems: list[str] = []
    external: set[str] = set()
    local = _local_roots(tree)
    stdlib = sys.stdlib_module_names
    for path in sorted(tree.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            module = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            problems.append(f"{path.relative_to(tree).as_posix()}: syntax error: {exc}")
            continue
        for node in ast.walk(module):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue  # relative import inside the tree
                base = node.module or ""
                if base.split(".")[0] == "evobench":
                    modules = [f"{base}.{alias.name}" for alias in node.names] + [base]
                else:
                    modules = [base]
            for name in modules:
                root = name.split(".")[0]
                if root in stdlib or root in local:
                    continue
                if root == "evobench":
                    if name in ALLOWED_EVOBENCH_MODULES or any(
                        name.startswith(allowed + ".") for allowed in ALLOWED_EVOBENCH_MODULES
                    ):
                        continue
                    if isinstance(node, ast.ImportFrom) and name == (node.module or ""):
                        # `from evobench.policy import injected_tools`: judged by the full names
                        continue
                external.add(name)
                problems.append(f"{path.relative_to(tree).as_posix()}: disallowed import {name}")
    return tuple(sorted(external)), problems


def validate_tree(tree: Path, *, budget: Budget | None = None) -> ValidationReport:
    frozen = budget or Budget()
    problems: list[str] = []
    harness_json = tree / "harness.json"
    declared: tuple[str, ...] = ()
    if not (tree / "harness.py").is_file():
        problems.append("harness.py missing")
    if not harness_json.is_file():
        problems.append("harness.json missing")
    else:
        try:
            config = json.loads(harness_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            config = None
            problems.append(f"harness.json: invalid JSON: {exc}")
        if isinstance(config, dict):
            expected = {
                "max_steps": frozen.max_steps,
                "rollout_wall_clock_seconds": frozen.rollout_wall_clock_seconds,
            }
            for key in BUDGET_KEYS:
                if config.get(key) != expected[key]:
                    problems.append(
                        f"harness.json:{key} is {config.get(key)!r}, "
                        f"frozen value is {expected[key]}"
                    )
            prompt = config.get("system_prompt")
            if not isinstance(prompt, str) or not (tree / prompt).is_file():
                problems.append("harness.json:system_prompt does not name a file in the tree")
            tools = config.get("tools")
            if isinstance(tools, list) and all(isinstance(t, str) for t in tools):
                declared = tuple(tools)
            else:
                problems.append("harness.json:tools must be a list of names")
    registered, tool_problems = registered_tools(tree)
    problems.extend(tool_problems)
    if not tool_problems and declared and sorted(declared) != sorted(registered):
        problems.append(
            f"harness.json:tools {sorted(declared)} differs from TOOL_SCHEMAS {sorted(registered)}"
        )
    external, import_problems = external_imports(tree)
    problems.extend(import_problems)
    return ValidationReport(
        ok=not problems,
        problems=tuple(problems),
        tools_declared=declared,
        tools_registered=registered,
        external_imports=external,
    )


def require_valid(tree: Path, *, budget: Budget | None = None) -> ValidationReport:
    report = validate_tree(tree, budget=budget)
    if not report.ok:
        raise SnapshotInvalidError(report.problems)
    return report
