from pathlib import Path

import typer

app = typer.Typer(help="Score the quality of Python repositories.")


@app.command()
def version():
    """Show the current version."""
    typer.echo("repo-scorer 0.1.0")


@app.command()
def db_check():
    """Verify the database connection and pgvector extension."""
    from repo_scorer.db.session import check_connection

    try:
        pg_version, vector_version = check_connection()
    except Exception as exc:
        typer.secho(f"Database connection failed: {exc}", fg=typer.colors.RED)
        typer.echo("Is Docker running? Try: docker compose up -d")
        raise typer.Exit(code=1)

    typer.secho(f"Connected to Postgres {pg_version}", fg=typer.colors.GREEN)
    if vector_version:
        typer.secho(f"pgvector {vector_version} is enabled", fg=typer.colors.GREEN)
    else:
        typer.secho("pgvector extension is NOT enabled", fg=typer.colors.YELLOW)


@app.command()
def init_db():
    """Create all database tables (safe to run repeatedly)."""
    from repo_scorer.db.models import Base
    from repo_scorer.db.session import engine, migrate

    Base.metadata.create_all(engine)
    migrate()
    typer.secho("Tables created: " + ", ".join(Base.metadata.tables), fg=typer.colors.GREEN)


@app.command()
def ingest(
    repo_url: str,
    force: bool = typer.Option(False, "--force", help="Re-clone even if already cloned."),
):
    """Fetch GitHub metadata, clone the repo, and save it to the database."""
    from repo_scorer.ingest.service import ingest_repo

    try:
        repo = ingest_repo(repo_url, force=force)
    except (ValueError, RuntimeError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.secho(f"Ingested {repo.owner}/{repo.name} (id={repo.id})", fg=typer.colors.GREEN)
    typer.echo(f"  stars={repo.stars}  forks={repo.forks}  license={repo.license}")
    pushed = repo.pushed_at.strftime("%Y-%m-%d") if repo.pushed_at else "unknown"
    typer.echo(f"  last push={pushed}  commit={repo.commit_sha[:10]}")
    typer.echo(f"  cloned to {repo.local_path}")


@app.command()
def parse(repo_url: str):
    """Parse all Python files of an ingested repo into files / code_units."""
    from sqlalchemy import select

    from repo_scorer.db.models import CodeUnit, SourceFile
    from repo_scorer.db.session import SessionLocal
    from repo_scorer.ingest.service import get_repo_by_url
    from repo_scorer.parsing.service import parse_repo

    repo = get_repo_by_url(repo_url)
    if repo is None:
        typer.secho("Repo not ingested yet. Run: repo-scorer ingest <url>", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    s = parse_repo(repo.id)
    typer.secho(f"Parsed {repo.owner}/{repo.name}", fg=typer.colors.GREEN)
    typer.echo(f"  files={s.files} (tests={s.test_files}, parse errors={s.parse_errors})")
    typer.echo(f"  lines of code={s.total_loc}")
    typer.echo(f"  functions={s.functions}  methods={s.methods}  classes={s.classes}")

    with SessionLocal() as session:
        rows = session.execute(
            select(CodeUnit.qualname, CodeUnit.complexity, CodeUnit.loc, SourceFile.path)
            .join(SourceFile)
            .where(
                SourceFile.repo_id == repo.id,
                SourceFile.is_test.is_(False),
                CodeUnit.kind.in_(["function", "method"]),
            )
            .order_by(CodeUnit.complexity.desc())
            .limit(5)
        ).all()

    if rows:
        typer.echo("\n  Most complex (non-test) functions:")
        for qualname, complexity, loc, path in rows:
            typer.echo(f"    complexity {complexity:>3}  {loc:>4} lines  {path}::{qualname}")


@app.command()
def analyze(
    repo_url: str,
    only: list[str] = typer.Option(None, "--only", help="Run just these analyzers."),
):
    """Run analyzers on a parsed repo and store the metrics."""
    from repo_scorer.analyzers.runner import run_analyzers
    from repo_scorer.ingest.service import get_repo_by_url

    repo = get_repo_by_url(repo_url)
    if repo is None:
        typer.secho("Repo not ingested yet. Run: repo-scorer ingest <url>", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    try:
        results = run_analyzers(repo.id, only=only)
    except (ValueError, RuntimeError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.secho(f"Analyzed {repo.owner}/{repo.name}", fg=typer.colors.GREEN)
    for analyzer, metrics in results.items():
        typer.echo(f"\n  [{analyzer}]")
        for name, m in metrics.items():
            typer.echo(f"    {name:<22} {m.value:>6}")


@app.command()
def details(
    repo_url: str,
    analyzer: str = typer.Argument(..., help="e.g. documentation, architecture"),
    metric: str = typer.Argument(None, help="Optional: just one metric."),
):
    """Show the stored details behind an analyzer's metrics."""
    import json

    from sqlalchemy import select

    from repo_scorer.db.models import Metric
    from repo_scorer.db.session import SessionLocal
    from repo_scorer.ingest.service import get_repo_by_url

    repo = get_repo_by_url(repo_url)
    if repo is None:
        typer.secho("Repo not ingested yet.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    query = select(Metric).where(Metric.repo_id == repo.id, Metric.analyzer == analyzer)
    if metric:
        query = query.where(Metric.name == metric)
    with SessionLocal() as session:
        rows = session.scalars(query.order_by(Metric.name)).all()

    if not rows:
        typer.secho("No metrics found. Run `analyze` first.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)

    for m in rows:
        typer.secho(f"\n{m.name} = {m.value}", fg=typer.colors.CYAN, bold=True)
        if m.details:
            typer.echo(json.dumps(m.details, indent=2, default=str))


@app.command()
def score(repo_url: str):
    """Compute category scores, overall score and grade from stored metrics."""
    from repo_scorer.ingest.service import get_repo_by_url
    from repo_scorer.scoring.scorecard import biggest_losses
    from repo_scorer.scoring.service import score_repo

    repo = get_repo_by_url(repo_url)
    if repo is None:
        typer.secho("Repo not ingested yet. Run: repo-scorer ingest <url>", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    try:
        result = score_repo(repo.id)
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.secho(
        f"\n{repo.owner}/{repo.name}: {result['overall']} / 100  (grade {result['grade']})",
        fg=typer.colors.GREEN, bold=True,
    )
    from repo_scorer.maintenance import LABELS, maintenance_status

    status = maintenance_status(repo.archived, repo.pushed_at)
    color = typer.colors.GREEN if status == "active" else typer.colors.YELLOW
    typer.secho(f"  Maintenance: {LABELS[status]}  (shown separately, not part of the score)",
                fg=color)
    typer.echo()
    for name, cat in result["categories"].items():
        bar = "\u2588" * int(cat["score"] // 5)
        typer.echo(
            f"  {name:<14} {cat['score']:>5}  {cat['grade']}  "
            f"(weight {int(cat['weight'] * 100)}%)  {bar}"
        )

    losses = biggest_losses(result)
    if losses:
        typer.echo("\n  Biggest point losses:")
        for p in losses:
            typer.echo(
                f"    -{p['overall_points_lost']:<5} {p['category']}.{p['label']} = {p['value']:g}"
                f"  (best: {p['best']:g})"
            )


@app.command()
def recommend(repo_url: str):
    """Run the Claude agent to investigate the scores and write a remediation report."""
    from repo_scorer.agent.service import generate_report
    from repo_scorer.ingest.service import get_repo_by_url

    repo = get_repo_by_url(repo_url)
    if repo is None:
        typer.secho("Repo not ingested yet. Run: repo-scorer ingest <url>", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    def show_call(name: str, args: dict) -> None:
        arg_text = ", ".join(f"{k}={v}" for k, v in args.items())
        typer.secho(f"  -> {name}({arg_text})", fg=typer.colors.CYAN)

    typer.echo(f"Agent investigating {repo.owner}/{repo.name}...")
    try:
        path, report, usage, model = generate_report(repo.id, on_tool_call=show_call)
    except (ValueError, RuntimeError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.echo("\n" + report)
    cost = usage.cost(model)
    typer.secho(
        f"Saved to {path}  |  {len(usage.tool_calls)} tool calls, {usage.turns} turns, "
        f"{usage.input_tokens + usage.cache_read_tokens} in / {usage.output_tokens} out tokens"
        + (f", ~${cost}" if cost is not None else ""),
        fg=typer.colors.GREEN,
    )


@app.command()
def batch(
    repo_list: Path = typer.Argument(..., exists=True, help="Text file: one GitHub URL per line."),
    recommend: bool = typer.Option(
        False, "--recommend/--no-recommend",
        help="Also run the Claude agent (costs API credits). Off by default.",
    ),
    force: bool = typer.Option(False, "--force", help="Re-run repos that are already scored."),
):
    """Run the whole pipeline for every repo in a list. Free unless --recommend is given."""
    from repo_scorer.pipeline import read_repo_list, run_one

    urls = read_repo_list(repo_list.read_text())
    mode = "WITH Claude reports (uses credits)" if recommend else "scores only (free)"
    typer.secho(f"Batch: {len(urls)} repos, {mode}\n", bold=True)

    results = []
    for i, url in enumerate(urls, 1):
        typer.echo(f"[{i}/{len(urls)}] {url}")
        res = run_one(url, recommend=recommend, force=force,
                      log=lambda step: typer.echo(f"    {step}..."))
        results.append(res)
        if res.status == "failed":
            typer.secho(f"    FAILED: {res.error}", fg=typer.colors.RED)
        else:
            label = "skipped (already scored)" if res.status == "skipped" else f"{res.seconds}s"
            cost = f", ${res.cost}" if res.cost else ""
            typer.secho(f"    {res.overall} ({res.grade})  {label}{cost}", fg=typer.colors.GREEN)

    done = sorted((r for r in results if r.overall is not None),
                  key=lambda r: r.overall, reverse=True)
    typer.secho("\nLeaderboard", bold=True)
    for rank, r in enumerate(done, 1):
        typer.echo(f"  {rank:>2}. {r.overall:>5}  {r.grade}  {r.name}")

    failed = [r for r in results if r.status == "failed"]
    if failed:
        typer.secho(f"\n{len(failed)} failed:", fg=typer.colors.RED)
        for r in failed:
            typer.echo(f"  {r.url}: {r.error}")

    total_cost = sum(r.cost or 0 for r in results)
    typer.secho(
        f"\nDone: {len(done)} scored, {len(failed)} failed"
        + (f", total Claude cost ${total_cost:.4f}" if recommend else ""),
        bold=True,
    )


@app.command()
def refresh_metadata():
    """Re-fetch GitHub metadata (stars, archived, last push) for every repo. No re-clone."""
    from sqlalchemy import select

    from repo_scorer.db.models import Repo
    from repo_scorer.db.session import SessionLocal
    from repo_scorer.ingest.github import RepoRef, fetch_metadata
    from repo_scorer.ingest.service import apply_metadata
    from repo_scorer.maintenance import maintenance_status

    with SessionLocal() as session:
        repos = session.scalars(select(Repo)).all()
        for i, repo in enumerate(repos, 1):
            try:
                apply_metadata(repo, fetch_metadata(RepoRef(repo.owner, repo.name)))
                session.commit()
                status = maintenance_status(repo.archived, repo.pushed_at)
                typer.echo(f"[{i}/{len(repos)}] {repo.owner}/{repo.name}: {status}")
            except (ValueError, RuntimeError) as exc:
                typer.secho(f"[{i}/{len(repos)}] {repo.owner}/{repo.name}: {exc}",
                            fg=typer.colors.RED)
                if "rate limit" in str(exc):
                    break


@app.command()
def dashboard():
    """Open the read-only Streamlit dashboard in your browser."""
    import subprocess
    import sys

    try:
        import streamlit  # noqa: F401
    except ImportError:
        typer.secho('Streamlit not installed. Run: pip install -e ".[dashboard]"',
                    fg=typer.colors.RED)
        raise typer.Exit(code=1)

    app_path = Path(__file__).parent / "dashboard" / "app.py"
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(app_path)], check=False)


@app.command()
def reanalyze():
    """Re-run analyzers + scoring for every ingested repo (no GitHub calls, no Claude).

    Use after changing analyzer or scoring rules.
    """
    from sqlalchemy import select

    from repo_scorer.analyzers.runner import run_analyzers
    from repo_scorer.db.models import Repo
    from repo_scorer.db.session import SessionLocal
    from repo_scorer.scoring.service import score_repo

    with SessionLocal() as session:
        repos = session.execute(select(Repo.id, Repo.owner, Repo.name)).all()

    results = []
    for i, (repo_id, owner, name) in enumerate(repos, 1):
        typer.echo(f"[{i}/{len(repos)}] {owner}/{name}")
        try:
            run_analyzers(repo_id)
            scored = score_repo(repo_id)
            results.append((scored["overall"], scored["grade"], f"{owner}/{name}"))
        except Exception as exc:
            typer.secho(f"    FAILED: {type(exc).__name__}: {exc}", fg=typer.colors.RED)

    typer.secho("\nLeaderboard", bold=True)
    for rank, (overall, grade_, full) in enumerate(sorted(results, reverse=True), 1):
        typer.echo(f"  {rank:>2}. {overall:>5}  {grade_}  {full}")


if __name__ == "__main__":
    app()
