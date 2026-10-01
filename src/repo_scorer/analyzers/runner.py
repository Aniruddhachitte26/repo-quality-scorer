"""Run analyzers for a repo and store their metrics.

To add an analyzer: write a module with NAME and run(session, repo) -> dict[str, MetricResult],
then register it in ANALYZERS below.
"""

from collections.abc import Callable

from sqlalchemy.orm import Session

from repo_scorer.analyzers import architecture, dependencies, documentation, testing
from repo_scorer.analyzers.base import MetricResult, save_metrics
from repo_scorer.db.models import Repo
from repo_scorer.db.session import SessionLocal

AnalyzerFn = Callable[[Session, Repo], dict[str, MetricResult]]

ANALYZERS: dict[str, AnalyzerFn] = {
    documentation.NAME: documentation.run,
    architecture.NAME: architecture.run,
    dependencies.NAME: dependencies.run,
    testing.NAME: testing.run,
}


def run_analyzers(
    repo_id: int, only: list[str] | None = None
) -> dict[str, dict[str, MetricResult]]:
    names = only or list(ANALYZERS)
    unknown = set(names) - set(ANALYZERS)
    if unknown:
        raise ValueError(f"Unknown analyzer(s): {', '.join(sorted(unknown))}")

    results: dict[str, dict[str, MetricResult]] = {}
    with SessionLocal() as session:
        repo = session.get(Repo, repo_id)
        if repo is None:
            raise ValueError(f"Repo {repo_id} not found.")
        for name in names:
            metrics = ANALYZERS[name](session, repo)
            save_metrics(session, repo.id, name, metrics)
            results[name] = metrics
        session.commit()
    return results
