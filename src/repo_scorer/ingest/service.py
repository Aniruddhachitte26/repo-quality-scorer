"""Ingestion: GitHub metadata + local clone + a row in the repos table."""

from sqlalchemy import func, select

from repo_scorer.db.models import Repo
from repo_scorer.db.session import SessionLocal
from repo_scorer.ingest.clone import clone_repo
from repo_scorer.ingest.github import RepoMetadata, fetch_metadata, parse_github_url


def apply_metadata(repo: Repo, meta: RepoMetadata) -> None:
    repo.description = meta.description
    repo.default_branch = meta.default_branch
    repo.stars = meta.stars
    repo.forks = meta.forks
    repo.open_issues = meta.open_issues
    repo.license = meta.license
    repo.archived = meta.archived
    repo.pushed_at = meta.pushed_at


def get_repo_by_url(url: str) -> Repo | None:
    """Find an already-ingested repo (case-insensitive, any URL form)."""
    ref = parse_github_url(url)
    with SessionLocal() as session:
        return session.scalar(
            select(Repo).where(func.lower(Repo.url) == ref.url.lower())
        )


def ingest_repo(url: str, force: bool = False) -> Repo:
    meta = fetch_metadata(parse_github_url(url))
    ref = meta.ref  # canonical casing from GitHub
    path, sha = clone_repo(ref, force=force)

    with SessionLocal() as session:
        repo = session.scalar(select(Repo).where(Repo.url == ref.url))
        if repo is None:
            repo = Repo(url=ref.url, owner=ref.owner, name=ref.name)
            session.add(repo)

        apply_metadata(repo, meta)
        repo.commit_sha = sha
        repo.local_path = str(path.resolve())

        session.commit()
        session.refresh(repo)
        return repo
