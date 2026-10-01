"""Shared pieces for all analyzers."""

from dataclasses import dataclass, field
from pathlib import PurePosixPath

from sqlalchemy import delete
from sqlalchemy.orm import Session

from repo_scorer.db.models import Metric

# Paths that ship with a repo but aren't the library itself.
AUXILIARY_DIRS = {"docs", "doc", "examples", "example", "scripts", "benchmarks", "tools"}
AUXILIARY_FILES = {"setup.py", "conf.py", "noxfile.py", "fabfile.py", "manage.py"}


@dataclass
class MetricResult:
    value: float
    details: dict | None = field(default=None)


def is_auxiliary_path(path: str) -> bool:
    """True for docs/examples/build scripts that shouldn't count against quality."""
    p = PurePosixPath(path)
    return p.name in AUXILIARY_FILES or any(part in AUXILIARY_DIRS for part in p.parts[:-1])


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
