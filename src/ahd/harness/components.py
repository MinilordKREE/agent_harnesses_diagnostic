"""Component manifest: the WHERE vocabulary, with line spans derived from the harness tree.

No reference source: written fresh for ahd (see docs/reuse/M2.md). The layer taxonomy
(ETCLOVG) and the manifest content are in ``configs/harness/seed_components.yaml``.

Symbol grammar (see the YAML header): ``path`` (whole file), ``path:Qual.name`` (class,
function, method or module/class-level assignment), ``path:Qual.name@text`` (the smallest
statement inside that symbol whose source contains ``text``), ``file.json:key`` (the line
declaring a key), and paths outside the tree (``evobench/...``), which are recorded as
``external`` and never located.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, ValidationError, model_validator

from ahd.core.config import StrictModel
from ahd.core.hashing import sha256_dir
from ahd.core.io import read_text
from ahd.errors import ConfigError
from ahd.harness.patch import parse_unified_diff

LAYERS: tuple[str, ...] = (
    "execution",
    "tooling",
    "context",
    "lifecycle",
    "observability",
    "verification",
    "governance",
)
type Layer = Literal[
    "execution", "tooling", "context", "lifecycle", "observability", "verification", "governance"
]
type SpanKind = Literal[
    "file", "config_key", "class", "function", "assignment", "statement", "external"
]


class ComponentSpec(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    layer: Layer
    role: str
    files: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    patchable: bool = True
    where_eligible: bool = True
    """False for observability components: never a WHERE candidate, never in a corruption pool."""
    ambiguous: bool = False
    note: str | None = None


class ComponentManifest(StrictModel):
    schema_version: Literal[1]
    harness: str
    harness_version: str
    source_sha: str
    components: tuple[ComponentSpec, ...]

    @model_validator(mode="after")
    def _unique_ids(self) -> ComponentManifest:
        ids = [c.id for c in self.components]
        if len(ids) != len(set(ids)):
            raise ValueError("component ids must be unique")
        return self

    @classmethod
    def load(cls, path: Path) -> ComponentManifest:
        try:
            raw = yaml.safe_load(read_text(path))
        except yaml.YAMLError as exc:
            raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
        try:
            return cls.model_validate(raw)
        except ValidationError as exc:
            raise ConfigError(f"invalid component manifest {path}:\n{exc}") from exc

    def ids(self) -> tuple[str, ...]:
        return tuple(c.id for c in self.components)

    def by_id(self, component_id: str) -> ComponentSpec:
        for component in self.components:
            if component.id == component_id:
                return component
        raise ConfigError(f"unknown component {component_id!r}")


class SymbolSpan(StrictModel):
    component_id: str
    symbol: str
    file: str
    start_line: int
    end_line: int
    kind: SpanKind
    resolved: bool
    detail: str | None = None

    def contains(self, line: int) -> bool:
        return self.resolved and self.start_line <= line <= self.end_line

    @property
    def width(self) -> int:
        return self.end_line - self.start_line


class Location(StrictModel):
    component_id: str
    candidates: tuple[str, ...]
    span: SymbolSpan | None
    exact: bool


class HunkMapping(StrictModel):
    file: str
    status: Literal["created", "deleted", "modified"]
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    component_ids: tuple[str, ...]
    exact: bool


class ParsedSymbol(StrictModel):
    path: str
    qualname: str | None
    anchor: str | None


def parse_symbol(symbol: str) -> ParsedSymbol:
    if ":" not in symbol:
        return ParsedSymbol(path=symbol, qualname=None, anchor=None)
    path, rest = symbol.split(":", 1)
    if "@" in rest:
        qualname, anchor = rest.split("@", 1)
        return ParsedSymbol(path=path, qualname=qualname, anchor=anchor)
    return ParsedSymbol(path=path, qualname=rest, anchor=None)


def _find_node(module: ast.Module, qualname: str) -> ast.AST | None:
    parts = qualname.split(".")
    scope: list[ast.stmt] = list(module.body)
    node: ast.AST | None = None
    for index, part in enumerate(parts):
        found: ast.AST | None = None
        for stmt in scope:
            if isinstance(stmt, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                if stmt.name == part:
                    found = stmt
                    break
            elif (
                isinstance(stmt, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == part for t in stmt.targets)
            ) or (
                isinstance(stmt, ast.AnnAssign)
                and isinstance(stmt.target, ast.Name)
                and stmt.target.id == part
            ):
                found = stmt
                break
        if found is None:
            return None
        node = found
        if index < len(parts) - 1:
            if not isinstance(found, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                return None
            scope = list(found.body)
    return node


def _node_span(node: ast.AST) -> tuple[int, int]:
    start = getattr(node, "lineno", 0)
    end = getattr(node, "end_lineno", start)
    decorators = getattr(node, "decorator_list", [])
    for decorator in decorators:
        start = min(start, getattr(decorator, "lineno", start))
    return start, end


def _smallest_statement(node: ast.AST, source: str, anchor: str) -> ast.stmt | None:
    best: ast.stmt | None = None
    best_key: tuple[int, int] | None = None
    for child in ast.walk(node):
        if child is node or not isinstance(child, ast.stmt):
            continue
        segment = ast.get_source_segment(source, child)
        if segment is None or anchor not in segment:
            continue
        start, end = _node_span(child)
        key = (end - start, start)
        if best_key is None or key < best_key:
            best, best_key = child, key
    return best


def _resolve_symbol(tree: Path, component_id: str, symbol: str) -> SymbolSpan:
    parsed = parse_symbol(symbol)
    base = SymbolSpan(
        component_id=component_id,
        symbol=symbol,
        file=parsed.path,
        start_line=0,
        end_line=0,
        kind="file",
        resolved=False,
    )
    file_path = tree / parsed.path
    if not file_path.is_file():
        kind: SpanKind = "external" if parsed.path.startswith("evobench/") else "file"
        return base.model_copy(update={"kind": kind, "detail": "not in tree"})
    text = file_path.read_text(encoding="utf-8")
    line_count = max(1, text.count("\n") + (0 if text.endswith("\n") else 1))
    if parsed.qualname is None:
        return base.model_copy(
            update={"start_line": 1, "end_line": line_count, "kind": "file", "resolved": True}
        )
    if parsed.path.endswith(".json"):
        pattern = re.compile(r'^\s*"' + re.escape(parsed.qualname) + r'"\s*:')
        for number, line in enumerate(text.splitlines(), start=1):
            if pattern.match(line):
                return base.model_copy(
                    update={
                        "start_line": number,
                        "end_line": number,
                        "kind": "config_key",
                        "resolved": True,
                    }
                )
        return base.model_copy(update={"kind": "config_key", "detail": "key not found"})
    if not parsed.path.endswith(".py"):
        return base.model_copy(update={"detail": "qualified symbols need a .py or .json file"})
    try:
        module = ast.parse(text)
    except SyntaxError as exc:
        return base.model_copy(update={"detail": f"syntax error: {exc}"})
    node = _find_node(module, parsed.qualname)
    if node is None:
        return base.model_copy(update={"detail": f"symbol {parsed.qualname!r} not found"})
    kind_by_node: SpanKind = (
        "class"
        if isinstance(node, ast.ClassDef)
        else "function"
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        else "assignment"
    )
    if parsed.anchor is not None:
        stmt = _smallest_statement(node, text, parsed.anchor)
        if stmt is None:
            return base.model_copy(
                update={"kind": "statement", "detail": f"anchor {parsed.anchor!r} not found"}
            )
        start, end = _node_span(stmt)
        return base.model_copy(
            update={"start_line": start, "end_line": end, "kind": "statement", "resolved": True}
        )
    start, end = _node_span(node)
    return base.model_copy(
        update={"start_line": start, "end_line": end, "kind": kind_by_node, "resolved": True}
    )


class ResolvedManifest(StrictModel):
    manifest: ComponentManifest
    tree_sha256: str
    spans: tuple[SymbolSpan, ...]

    def unresolved(self) -> tuple[SymbolSpan, ...]:
        return tuple(s for s in self.spans if not s.resolved and s.kind != "external")

    def _ordered_ids(self, ids: Iterable[str]) -> tuple[str, ...]:
        order = {cid: index for index, cid in enumerate(self.manifest.ids())}
        return tuple(sorted(set(ids), key=lambda cid: order[cid]))

    def components_for_file(self, path: str) -> tuple[str, ...]:
        return self._ordered_ids(c.id for c in self.manifest.components if path in c.files)

    def locate(self, path: str, line: int) -> Location | None:
        """The narrowest resolved span containing ``line``; ties keep every candidate."""
        in_file = [s for s in self.spans if s.file == path and s.resolved]
        containing = [s for s in in_file if s.kind != "file" and s.contains(line)]
        if containing:
            width = min(s.width for s in containing)
            narrowest = [s for s in containing if s.width == width]
            ids = self._ordered_ids(s.component_id for s in narrowest)
            span = next(s for s in narrowest if s.component_id == ids[0])
            return Location(component_id=ids[0], candidates=ids, span=span, exact=True)
        whole = [s for s in in_file if s.kind == "file"]
        if whole:
            ids = self._ordered_ids(s.component_id for s in whole)
            return Location(component_id=ids[0], candidates=ids, span=whole[0], exact=True)
        by_files = self.components_for_file(path)
        if by_files:
            return Location(component_id=by_files[0], candidates=by_files, span=None, exact=False)
        return None

    def diff_to_components(self, diff: str) -> tuple[HunkMapping, ...]:
        """Map every hunk of a unified diff (against this tree) to component ids."""
        mappings: list[HunkMapping] = []
        for file_diff in parse_unified_diff(diff):
            path = file_diff.path
            for hunk in file_diff.hunks:
                ids: list[str] = []
                exact = True
                if file_diff.status == "created":
                    ids.extend(self.components_for_file(path))
                    exact = False
                else:
                    last = hunk.old_start + max(hunk.old_lines, 1) - 1
                    for line in range(hunk.old_start, last + 1):
                        location = self.locate(path, line)
                        if location is None:
                            exact = False
                            continue
                        ids.extend(location.candidates)
                        exact = exact and location.exact
                mappings.append(
                    HunkMapping(
                        file=path,
                        status=file_diff.status,
                        old_start=hunk.old_start,
                        old_lines=hunk.old_lines,
                        new_start=hunk.new_start,
                        new_lines=hunk.new_lines,
                        component_ids=self._ordered_ids(ids),
                        exact=exact and bool(ids),
                    )
                )
        return tuple(mappings)


def resolve_spans(manifest: ComponentManifest, tree: Path) -> ResolvedManifest:
    spans = tuple(
        _resolve_symbol(tree, component.id, symbol)
        for component in manifest.components
        for symbol in component.symbols
    )
    return ResolvedManifest(manifest=manifest, tree_sha256=sha256_dir(tree), spans=spans)
