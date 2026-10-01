"""Shared pieces for all analyzers."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from repo_scorer.db.models import Metric, SourceFile

# Paths that ship with a repo but aren't the library itself.
AUXILIARY_DIRS = {"docs", "doc", "examples", "example", "scripts", "benchmarks", "tools"}
AUXILIARY_FILES = {"setup.py", "conf.py", "noxfile.py", "fabfile.py", "manage.py"}
SOURCE_ROOTS = {"src", "lib"}


@dataclass
class MetricResult:
    value: float
    details: dict | None = field(default=None)


def is_auxiliary_path(path: str) -> bool:
    """True for docs/examples/build scripts that shouldn't count against quality."""
    p = PurePosixPath(path)
    return p.name in AUXILIARY_FILES or any(part in AUXILIARY_DIRS for part in p.parts[:-1])


def library_paths(paths: Iterable[str]) -> set[str]:
    """The subset of non-test paths that make up the importable library.

    A file counts if it is a top-level module (optionally under src/ or lib/), or if
    every directory above it is a package (has __init__.py). Loose scripts in folders
    like `failures-to-investigate/` or `notebooks/` are not importable, so they are
    excluded without needing a list of folder names. Repos with no packages at all
    fall back to the simple auxiliary-name rule.
    """
    paths = list(paths)
    package_dirs = {
        PurePosixPath(p).parent.as_posix()
        for p in paths
        if PurePosixPath(p).name == "__init__.py"
    }
    if not package_dirs - {"."}:
        return {p for p in paths if not is_auxiliary_path(p)}

    library = set()
    for p in paths:
        if is_auxiliary_path(p):
            continue
        parts = PurePosixPath(p).parts[:-1]
        start = 1 if parts and parts[0] in SOURCE_ROOTS else 0
        if all(
            PurePosixPath(*parts[: i + 1]).as_posix() in package_dirs
            for i in range(start, len(parts))
        ):
            library.add(p)
    return library


def get_library_paths(session: Session, repo_id: int) -> set[str]:
    paths = session.scalars(
        select(SourceFile.path).where(
            SourceFile.repo_id == repo_id, SourceFile.is_test.is_(False)
        )
    ).all()
    return library_paths(paths)


def is_public(qualname: str) -> bool:
    """Public = no part of the dotted name starts with '_' (so dunders are excluded too)."""
    return not any(part.startswith("_") for part in qualname.split("."))


def save_metrics(
    session: Session, repo_id: int, analyzer: str, metrics: dict[str, MetricResult]
) -> None:
    """Replace all metrics of one analyzer for one repo."""
    session.execute(
        delete(Metric).where(Metric.repo_id == repo_id, Metric.analyzer == analyzer)
    )
    session.add_all(
        Metric(repo_id=repo_id, analyzer=analyzer, name=name, value=m.value, details=m.details)
        for name, m in metrics.items()
    )
