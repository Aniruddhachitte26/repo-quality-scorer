"""Test analyzer (static: reads tests, never runs them).

Coverage is estimated by *name reachability*: a source function counts as
tested if a test file mentions its name, or if it is called (by name) from
another function that tests reach. Matching is by simple name, so a common
name like `get` can match the wrong function: treat the numbers as an
upper-bound estimate of real line coverage.

Metrics:
  test_files                 number of test files
  test_functions             number of test_* functions/methods
  test_to_source_ratio       test lines of code / source lines of code
  directly_tested_pct        % of public source functions mentioned in tests
  reachable_from_tests_pct   % of public source functions reachable from tests
  asserts_per_test           average assertions per test function
  has_ci                     1 if a CI configuration exists, else 0
"""

import ast
import warnings
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from repo_scorer.analyzers.base import MetricResult, get_library_paths, is_public
from repo_scorer.db.models import CodeUnit, Repo, SourceFile

NAME = "tests"
MAX_EXAMPLES = 15
FUNC_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)
ASSERT_CALLS = {"raises", "warns", "approx"}  # pytest helpers that act as checks
CI_PATHS = [
    ".github/workflows", ".gitlab-ci.yml", ".circleci", ".travis.yml",
    "azure-pipelines.yml", "Jenkinsfile", ".buildkite",
]


# ---------------------------------------------------------------- AST helpers


def identifiers(node: ast.AST) -> set[str]:
    """Every name mentioned under node: variables, attributes, imported names."""
    found: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            found.add(n.id)
        elif isinstance(n, ast.Attribute):
            found.add(n.attr)
        elif isinstance(n, ast.alias):
            found.add(n.name.split(".")[-1])
    return found


def function_references(tree: ast.Module) -> dict[str, set[str]]:
    """{qualname: names referenced in its body}, using the parser's qualname scheme."""
    refs: dict[str, set[str]] = {}

    def visit(node: ast.AST, scope: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, [*scope, child.name])
            elif isinstance(child, FUNC_NODES):
                qual = [*scope, child.name]
                refs[".".join(qual)] = identifiers(child)
                visit(child, qual)
            else:
                visit(child, scope)

    visit(tree, [])
    return refs


def count_asserts(func: ast.AST) -> int:
    """`assert`, self.assertX(...), mock.assert_called(...), pytest.raises(...)."""
    count = 0
    for node in ast.walk(func):
        if isinstance(node, ast.Assert):
            count += 1
        elif isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name.startswith("assert") or name in ASSERT_CALLS:
                count += 1
    return count


def asserts_per_test_function(tree: ast.Module) -> list[int]:
    """Assertion count for every test_* function/method in a test file."""
    return [
        count_asserts(node)
        for node in ast.walk(tree)
        if isinstance(node, FUNC_NODES) and node.name.startswith("test")
    ]


def unit_names(qualname: str) -> set[str]:
    """Names a function can be invoked by.

    Dunder methods (__init__, __call__, __enter__, __iter__, ...) are run implicitly
    by Python, so they also answer to their class name: using the class reaches them.
    """
    parts = qualname.split(".")
    names = {parts[-1]}
    is_dunder = parts[-1].startswith("__") and parts[-1].endswith("__")
    if is_dunder and len(parts) > 1:
        names.add(parts[-2])
    return names


def reachable_units(
    units: dict[str, tuple[set[str], set[str]]], test_ids: set[str]
) -> tuple[set[str], set[str]]:
    """units: key -> (names it answers to, names it references).

    Returns (directly referenced by tests, reachable via the name-based call graph).
    """
    by_name: dict[str, set[str]] = defaultdict(set)
    for key, (names, _) in units.items():
        for name in names:
            by_name[name].add(key)

    direct = {key for name in test_ids for key in by_name.get(name, ())}
    seen = set(direct)
    frontier = list(direct)
    while frontier:
        key = frontier.pop()
        for ident in units[key][1]:
            for nxt in by_name.get(ident, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    frontier.append(nxt)
    return direct, seen


def _parse(path: Path) -> ast.Module | None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            return ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError, OSError):
        return None


# ---------------------------------------------------------------- analyzer


def _pct(part: int, total: int) -> float:
    return round(100 * part / total, 1) if total else 0.0


def run(session: Session, repo: Repo) -> dict[str, MetricResult]:
    root = Path(repo.local_path)
    files = session.scalars(
        select(SourceFile).where(
            SourceFile.repo_id == repo.id, SourceFile.parse_error.is_(None)
        )
    ).all()
    test_files = [f for f in files if f.is_test]
    library = get_library_paths(session, repo.id)
    src_files = [f for f in files if not f.is_test and f.path in library]

    # --- what the tests mention, and how many checks they make
    test_ids: set[str] = set()
    assert_counts: list[int] = []
    for f in test_files:
        tree = _parse(root / f.path)
        if tree is not None:
            test_ids |= identifiers(tree)
            assert_counts += asserts_per_test_function(tree)

    # --- name-based call graph of the source code
    units: dict[str, tuple[set[str], set[str]]] = {}
    for f in src_files:
        tree = _parse(root / f.path)
        if tree is None:
            continue
        for qual, refs in function_references(tree).items():
            units[f"{f.path}::{qual}"] = (unit_names(qual), refs)

    direct, reachable = reachable_units(units, test_ids)
    public = [k for k in units if is_public(k.split("::", 1)[1])]

    # Largest public functions that tests never reach: the best places to add tests.
    info = {
        f"{path}::{qual}": (loc, lineno)
        for qual, loc, lineno, path in session.execute(
            select(CodeUnit.qualname, CodeUnit.loc, CodeUnit.lineno, SourceFile.path)
            .join(SourceFile)
            .where(SourceFile.repo_id == repo.id, CodeUnit.kind.in_(["function", "method"]))
        )
    }
    untested = sorted(
        (k for k in public if k not in reachable and k in info),
        key=lambda k: info[k][0], reverse=True,
    )
    untested_examples = [
        {"location": f"{k.split('::')[0]}:{info[k][1]}", "name": k.split("::")[1], "loc": info[k][0]}
        for k in untested[:MAX_EXAMPLES]
    ]

    test_loc = sum(f.loc for f in test_files)
    src_loc = sum(f.loc for f in src_files)
    ci_found = [p for p in CI_PATHS if (root / p).exists()]

    return {
        "test_files": MetricResult(
            float(len(test_files)),
            {"frameworks": {"pytest": "pytest" in test_ids, "unittest": "unittest" in test_ids}},
        ),
        "test_functions": MetricResult(float(len(assert_counts))),
        "test_to_source_ratio": MetricResult(
            round(test_loc / src_loc, 2) if src_loc else 0.0,
            {"test_loc": test_loc, "source_loc": src_loc},
        ),
        "directly_tested_pct": MetricResult(
            _pct(sum(k in direct for k in public), len(public))
        ),
        "reachable_from_tests_pct": MetricResult(
            _pct(sum(k in reachable for k in public), len(public)),
            {"public_functions": len(public), "largest_untested": untested_examples},
        ),
        "asserts_per_test": MetricResult(
            round(sum(assert_counts) / len(assert_counts), 2) if assert_counts else 0.0,
            {"tests_without_asserts": sum(c == 0 for c in assert_counts)},
        ),
        "has_ci": MetricResult(float(bool(ci_found)), {"found": ci_found}),
    }
