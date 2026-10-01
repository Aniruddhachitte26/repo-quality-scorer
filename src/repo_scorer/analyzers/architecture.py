"""Architecture analyzer.

Metrics:
  modules               number of internal (non-test) modules
  import_cycles         strongly-connected groups of modules that import each other
  modules_in_cycles     % of modules involved in some import cycle
  avg_fan_out           average number of internal modules each module imports
  avg_complexity        mean cyclomatic complexity of functions/methods
  high_complexity_pct   % of functions/methods with complexity > 10
  long_function_pct     % of functions/methods longer than 60 lines
  god_classes           classes with >= 20 methods or >= 500 lines
"""

import ast
import warnings
from collections import Counter
from pathlib import Path, PurePosixPath

import networkx as nx
from sqlalchemy import select
from sqlalchemy.orm import Session

from repo_scorer.analyzers.base import MetricResult, is_auxiliary_path
from repo_scorer.db.models import CodeUnit, Repo, SourceFile

NAME = "architecture"
HIGH_COMPLEXITY = 10
LONG_FUNCTION_LINES = 60
GOD_CLASS_METHODS = 20
GOD_CLASS_LINES = 500
MAX_EXAMPLES = 10
SOURCE_ROOTS = {"src", "lib"}


# ---------------------------------------------------------------- import graph


def module_name(path: str) -> str:
    """'src/requests/models.py' -> 'requests.models'; '.../__init__.py' -> package name."""
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if len(parts) > 1 and parts[0] in SOURCE_ROOTS:
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def module_level_imports(tree: ast.Module) -> list[tuple[str | None, list[str], int]]:
    """Imports that run at import time, as (module, imported names, relative level).

    Skips imports inside functions/classes and inside `if TYPE_CHECKING:`,
    since those can't cause import-time cycles.
    """
    found: list[tuple[str | None, list[str], int]] = []

    def walk(stmts: list[ast.stmt]) -> None:
        for node in stmts:
            if isinstance(node, ast.Import):
                found.extend((alias.name, [], 0) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                found.append((node.module, [a.name for a in node.names], node.level))
            elif isinstance(node, ast.If):
                if not _is_type_checking(node.test):
                    walk(node.body)
                walk(node.orelse)
            elif isinstance(node, (ast.Try, getattr(ast, "TryStar", ast.Try))):
                walk(node.body)
                for handler in node.handlers:
                    walk(handler.body)
                walk(node.orelse)
                walk(node.finalbody)
            elif isinstance(node, ast.With):
                walk(node.body)

    walk(tree.body)
    return found


def _longest_prefix(name: str, modules: set[str]) -> str | None:
    parts = name.split(".")
    for i in range(len(parts), 0, -1):
        candidate = ".".join(parts[:i])
        if candidate in modules:
            return candidate
    return None


def resolve_imports(
    current: str,
    is_package: bool,
    imports: list[tuple[str | None, list[str], int]],
    modules: set[str],
) -> set[str]:
    """Map raw imports to the internal modules they refer to."""
    targets: set[str] = set()
    for mod, names, level in imports:
        if level:  # relative import: resolve against the current package
            base_parts = current.split(".") if current else []
            if not is_package:
                base_parts = base_parts[:-1]
            if level > 1:
                base_parts = base_parts[: max(0, len(base_parts) - (level - 1))]
            base = ".".join(base_parts + ([mod] if mod else []))
        else:
            base = mod or ""

        if names:
            for name in names:
                candidate = f"{base}.{name}" if base else name
                if candidate in modules:  # `from pkg import submodule`
                    targets.add(candidate)
                elif base and (hit := _longest_prefix(base, modules)):
                    targets.add(hit)
        elif hit := _longest_prefix(base, modules):
            targets.add(hit)

    targets.discard(current)
    return targets


def build_import_graph(sources: dict[str, str]) -> nx.DiGraph:
    """Build module -> imported-module graph from {repo-relative path: source}."""
    names = {path: module_name(path) for path in sources}
    modules = {n for n in names.values() if n}
    graph = nx.DiGraph()
    graph.add_nodes_from(modules)

    for path, source in sources.items():
        current = names[path]
        if not current:
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(source)
        except (SyntaxError, ValueError):
            continue
        is_pkg = PurePosixPath(path).name == "__init__.py"
        for target in resolve_imports(current, is_pkg, module_level_imports(tree), modules):
            graph.add_edge(current, target)
    return graph


def find_cycles(graph: nx.DiGraph) -> list[list[str]]:
    """Groups of modules that (directly or indirectly) import each other."""
    groups = [sorted(c) for c in nx.strongly_connected_components(graph) if len(c) > 1]
    return sorted(groups, key=len, reverse=True)


# ---------------------------------------------------------------- analyzer


def _pct(part: int, total: int) -> float:
    return round(100 * part / total, 1) if total else 0.0


def run(session: Session, repo: Repo) -> dict[str, MetricResult]:
    root = Path(repo.local_path)

    files = session.scalars(
        select(SourceFile).where(
            SourceFile.repo_id == repo.id,
            SourceFile.is_test.is_(False),
            SourceFile.parse_error.is_(None),
        )
    ).all()
    files = [f for f in files if not is_auxiliary_path(f.path)]

    # --- import graph
    sources = {
        f.path: (root / f.path).read_text(encoding="utf-8", errors="replace")
        for f in files
        if (root / f.path).exists()
    }
    graph = build_import_graph(sources)
    cycles = find_cycles(graph)
    in_cycles = sum(len(c) for c in cycles)
    n_modules = graph.number_of_nodes()

    cycle_examples = []
    for group in cycles[:MAX_EXAMPLES]:
        edges = nx.find_cycle(graph.subgraph(group))
        cycle_examples.append({"modules": group, "example_path": [a for a, _ in edges] + [edges[-1][1]]})

    fan_in = sorted(graph.in_degree, key=lambda x: x[1], reverse=True)[:5]
    fan_out = sorted(graph.out_degree, key=lambda x: x[1], reverse=True)[:5]

    # --- complexity & size from the parsed code units
    paths = {f.path for f in files}
    units = session.execute(
        select(
            CodeUnit.kind, CodeUnit.qualname, CodeUnit.loc, CodeUnit.lineno,
            CodeUnit.complexity, SourceFile.path,
        )
        .join(SourceFile)
        .where(SourceFile.repo_id == repo.id)
    ).all()
    units = [u for u in units if u.path in paths]

    callables = [u for u in units if u.kind in ("function", "method")]
    complexities = [u.complexity for u in callables]
    complex_ones = sorted(
        (u for u in callables if u.complexity > HIGH_COMPLEXITY),
        key=lambda u: u.complexity, reverse=True,
    )
    long_ones = [u for u in callables if u.loc > LONG_FUNCTION_LINES]

    # --- god classes: count direct methods per class
    method_counts = Counter(
        (u.path, u.qualname.rsplit(".", 1)[0]) for u in units if u.kind == "method"
    )
    god_classes = sorted(
        (
            {"location": f"{u.path}:{u.lineno}", "name": u.qualname, "loc": u.loc,
             "methods": method_counts[(u.path, u.qualname)]}
            for u in units
            if u.kind == "class"
            and (method_counts[(u.path, u.qualname)] >= GOD_CLASS_METHODS
                 or u.loc >= GOD_CLASS_LINES)
        ),
        key=lambda c: c["loc"], reverse=True,
    )

    return {
        "modules": MetricResult(float(n_modules)),
        "import_cycles": MetricResult(float(len(cycles)), {"cycles": cycle_examples}),
        "modules_in_cycles": MetricResult(_pct(in_cycles, n_modules)),
        "avg_fan_out": MetricResult(
            round(sum(d for _, d in graph.out_degree) / n_modules, 2) if n_modules else 0.0,
            {"most_imported": dict(fan_in), "most_importing": dict(fan_out)},
        ),
        "avg_complexity": MetricResult(
            round(sum(complexities) / len(complexities), 2) if complexities else 0.0
        ),
        "high_complexity_pct": MetricResult(
            _pct(len(complex_ones), len(callables)),
            {"threshold": HIGH_COMPLEXITY, "worst": [
                {"location": f"{u.path}:{u.lineno}", "name": u.qualname,
                 "complexity": u.complexity, "loc": u.loc}
                for u in complex_ones[:MAX_EXAMPLES]
            ]},
        ),
        "long_function_pct": MetricResult(
            _pct(len(long_ones), len(callables)), {"threshold_lines": LONG_FUNCTION_LINES}
        ),
        "god_classes": MetricResult(float(len(god_classes)), {"classes": god_classes[:MAX_EXAMPLES]}),
    }
