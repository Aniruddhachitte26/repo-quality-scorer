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
    from repo_scorer.db.session import engine

    Base.metadata.create_all(engine)
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


if __name__ == "__main__":
    app()
