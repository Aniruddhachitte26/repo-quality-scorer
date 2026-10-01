"""Score a repo from its stored metrics and save the result."""

from sqlalchemy import select

from repo_scorer.analyzers.base import MetricResult, save_metrics
from repo_scorer.db.models import Metric
from repo_scorer.db.session import SessionLocal
from repo_scorer.scoring.scorecard import Metrics, compute_scores, missing_analyzers

SCORE_ANALYZER = "score"


def score_repo(repo_id: int) -> dict:
    with SessionLocal() as session:
        rows = session.scalars(
            select(Metric).where(Metric.repo_id == repo_id, Metric.analyzer != SCORE_ANALYZER)
        ).all()
        ms: Metrics = {(m.analyzer, m.name): (m.value, m.details) for m in rows}

        missing = missing_analyzers(ms)
        if missing:
            raise ValueError(
                f"Missing analyzer results: {', '.join(missing)}. Run `analyze` first."
            )

        result = compute_scores(ms)

        to_save = {"overall": MetricResult(result["overall"], {"grade": result["grade"]})}
        for name, cat in result["categories"].items():
            to_save[name] = MetricResult(cat["score"], cat)
        save_metrics(session, repo_id, SCORE_ANALYZER, to_save)
        session.commit()
    return result
