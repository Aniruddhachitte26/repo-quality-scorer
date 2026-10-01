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
def score(repo_url: str):
    """Score a repository (not implemented yet)."""
    typer.echo(f"Would score: {repo_url}")


if __name__ == "__main__":
    app()
