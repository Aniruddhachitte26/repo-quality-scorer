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
def score(repo_url: str):
    """Score a repository (not implemented yet)."""
    typer.echo(f"Would score: {repo_url}")


if __name__ == "__main__":
    app()
