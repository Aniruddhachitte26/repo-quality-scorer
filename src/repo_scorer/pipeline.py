"""Run the full pipeline for one or many repos.

ingest -> parse -> analyze -> score -> (optional) recommend

Repos that already have a score are skipped unless force=True, so an interrupted
batch can simply be re-run. One repo failing never stops the others.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select

from repo_scorer.db.models import Metric
from repo_scorer.db.session import SessionLocal


@dataclass
class PipelineResult:
    url: str
    name: str | None = None
    overall: float | None = None
    grade: str | None = None
    status: str = "ok"  # ok | skipped | failed
    error: str | None = None
    seconds: float = 0.0
    cost: float | None = None


def read_repo_list(text: str) -> list[str]:
    """One URL per line; blank lines and # comments ignored; duplicates removed."""
    urls: list[str] = []
    for line in text.splitlines():
        url = line.split("#", 1)[0].strip()
        if url and url not in urls:
            urls.append(url)
    return urls


def _stored(repo_id: int, analyzer: str, name: str) -> Metric | None:
    with SessionLocal() as session:
        return session.scalar(
            select(Metric).where(
                Metric.repo_id == repo_id, Metric.analyzer == analyzer, Metric.name == name
            )
        )


def run_one(
    url: str,
    recommend: bool = False,
    force: bool = False,
    log: Callable[[str], None] = lambda _: None,
) -> PipelineResult:
    # Imported here so `--help` stays fast and import errors surface per repo.
    from repo_scorer.analyzers.runner import run_analyzers
    from repo_scorer.ingest.service import get_repo_by_url, ingest_repo
    from repo_scorer.parsing.service import parse_repo
    from repo_scorer.scoring.service import score_repo

    result = PipelineResult(url=url)
    start = time.monotonic()
    try:
        existing = get_repo_by_url(url)
        if existing and not force:
            score = _stored(existing.id, "score", "overall")
            report_ok = not recommend or _stored(existing.id, "agent", "report") is not None
            if score is not None and report_ok:
                result.name = f"{existing.owner}/{existing.name}"
                result.overall, result.grade = score.value, (score.details or {}).get("grade")
                result.status = "skipped"
                return result

        log("ingest")
        repo = ingest_repo(url)
        result.name = f"{repo.owner}/{repo.name}"

        log("parse")
        summary = parse_repo(repo.id)
        if summary.files - summary.test_files == 0:
            raise ValueError("no non-test Python files found (is this a Python repo?)")

        log("analyze")
        run_analyzers(repo.id)

        log("score")
        scored = score_repo(repo.id)
        result.overall, result.grade = scored["overall"], scored["grade"]

        if recommend:
            from repo_scorer.agent.service import generate_report

            log("recommend")
            _, _, usage, model = generate_report(repo.id)
            result.cost = usage.cost(model)
    except Exception as exc:  # keep the batch going; report the failure at the end
        result.status = "failed"
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        result.seconds = round(time.monotonic() - start, 1)
    return result
