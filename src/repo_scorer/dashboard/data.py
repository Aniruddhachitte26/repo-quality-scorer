"""Read-only data access for the dashboard. No Streamlit imports, so it's testable."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from repo_scorer.db.models import Metric, Repo
from repo_scorer.maintenance import LABELS, maintenance_status

CATEGORIES = ["tests", "architecture", "documentation", "dependencies", "duplication"]


def percentile_ranks(scores: dict[int, float]) -> dict[int, float]:
    """% of *other* repos each repo scores strictly higher than (0 = last, 100 = first)."""
    n = len(scores)
    if n <= 1:
        return {k: 100.0 for k in scores}
    values = list(scores.values())
    return {
        k: round(100 * sum(other < v for other in values) / (n - 1), 1)
        for k, v in scores.items()
    }


def load_leaderboard(session: Session) -> list[dict]:
    repos = {r.id: r for r in session.scalars(select(Repo))}
    scores: dict[int, dict] = {}
    for repo_id, name, value, details in session.execute(
        select(Metric.repo_id, Metric.name, Metric.value, Metric.details)
        .where(Metric.analyzer == "score")
    ):
        entry = scores.setdefault(repo_id, {})
        entry[name] = value
        if name == "overall":
            entry["grade"] = (details or {}).get("grade")
    reports = set(session.scalars(
        select(Metric.repo_id).where(Metric.analyzer == "agent", Metric.name == "report")
    ))

    scored = {rid: s["overall"] for rid, s in scores.items() if "overall" in s and rid in repos}
    pct = percentile_ranks(scored)

    rows = []
    for rid in sorted(scored, key=scored.get, reverse=True):
        repo, s = repos[rid], scores[rid]
        status = maintenance_status(repo.archived, repo.pushed_at)
        rows.append({
            "id": rid,
            "repo": f"{repo.owner}/{repo.name}",
            "url": repo.url,
            "score": s["overall"],
            "grade": s.get("grade"),
            "beats_pct": pct[rid],
            **{c: s.get(c) for c in CATEGORIES},
            "maintenance": status,
            "maintenance_label": LABELS[status],
            "stars": repo.stars,
            "has_report": rid in reports,
        })
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank
    return rows


def load_repo_detail(session: Session, repo_id: int) -> dict | None:
    repo = session.get(Repo, repo_id)
    if repo is None:
        return None
    metrics = session.scalars(select(Metric).where(Metric.repo_id == repo_id)).all()

    by_analyzer: dict[str, dict[str, tuple[float, dict | None]]] = {}
    for m in metrics:
        by_analyzer.setdefault(m.analyzer, {})[m.name] = (m.value, m.details)

    score = by_analyzer.pop("score", {})
    agent = by_analyzer.pop("agent", {})
    result = None
    if "overall" in score:
        result = {
            "overall": score["overall"][0],
            "grade": (score["overall"][1] or {}).get("grade"),
            "categories": {c: score[c][1] for c in CATEGORIES if c in score},
        }
    report = agent.get("report")
    status = maintenance_status(repo.archived, repo.pushed_at)
    return {
        "repo": {
            "name": f"{repo.owner}/{repo.name}",
            "url": repo.url,
            "description": repo.description,
            "stars": repo.stars,
            "license": repo.license,
            "pushed_at": repo.pushed_at,
            "commit_sha": repo.commit_sha,
        },
        "result": result,
        "metrics": by_analyzer,
        "report_markdown": (report[1] or {}).get("markdown") if report else None,
        "report_cost": report[0] if report else None,
        "maintenance": status,
        "maintenance_label": LABELS[status],
    }
