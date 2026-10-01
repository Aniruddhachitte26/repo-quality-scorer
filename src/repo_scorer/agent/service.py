"""Generate and save a remediation report for one repo."""

from datetime import datetime, timezone
from pathlib import Path

from repo_scorer.agent.runner import build_brief, run_agent
from repo_scorer.agent.tools import TOOL_SPECS, RepoTools
from repo_scorer.analyzers.base import MetricResult, save_metrics
from repo_scorer.config import get_settings
from repo_scorer.db.models import Repo
from repo_scorer.db.session import SessionLocal
from repo_scorer.scoring.scorecard import biggest_losses
from repo_scorer.scoring.service import score_repo

REPORTS_DIR = Path("reports")


def generate_report(repo_id: int, on_tool_call=None) -> tuple[Path, str, object, str]:
    settings = get_settings()
    key = settings.anthropic_api_key
    if not key or key == "your-key-here":
        raise ValueError("ANTHROPIC_API_KEY is not set in .env")

    import anthropic

    client = anthropic.Anthropic(api_key=key)
    result = score_repo(repo_id)  # fresh, deterministic scores

    with SessionLocal() as session:
        repo = session.get(Repo, repo_id)
        name = f"{repo.owner}/{repo.name}"
        brief = build_brief(name, result, biggest_losses(result, n=5))
        tools = RepoTools(session, repo)

        try:
            report, usage = run_agent(
                client, settings.anthropic_model, brief, TOOL_SPECS, tools.execute, on_tool_call
            )
        except anthropic.APIError as exc:
            raise RuntimeError(f"Claude API error: {exc}") from exc

        cost = usage.cost(settings.anthropic_model)
        generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        header = (
            f"# Remediation report: {name}\n\n"
            f"Score **{result['overall']} / 100 (grade {result['grade']})** | "
            f"commit `{(repo.commit_sha or '')[:10]}` | {generated} | "
            f"model `{settings.anthropic_model}`\n\n"
        )
        full = header + report + "\n"

        save_metrics(session, repo.id, "agent", {
            "report": MetricResult(
                cost or 0.0,
                {"markdown": full, "tool_calls": usage.tool_calls, "turns": usage.turns,
                 "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                 "cache_read_tokens": usage.cache_read_tokens, "cost_usd": cost},
            )
        })
        session.commit()

    REPORTS_DIR.mkdir(exist_ok=True)
    path = REPORTS_DIR / f"{repo.owner}__{repo.name}.md"
    path.write_text(full, encoding="utf-8")
    return path, full, usage, settings.anthropic_model
