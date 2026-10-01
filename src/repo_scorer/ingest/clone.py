"""Shallow-clone repositories to a local working directory."""

import shutil
from pathlib import Path

from git import Repo as GitRepo

from repo_scorer.ingest.github import RepoRef

# Cloned repos live in ./data/repos (git-ignored).
REPOS_DIR = Path("data") / "repos"


def clone_repo(ref: RepoRef, force: bool = False) -> tuple[Path, str]:
    """Clone `ref` (depth 1) and return (local path, HEAD commit sha).

    If the repo was already cloned, it is reused unless force=True.
    """
    dest = REPOS_DIR / f"{ref.owner}__{ref.name}"

    if dest.exists():
        if not force:
            return dest, GitRepo(dest).head.commit.hexsha
        shutil.rmtree(dest)

    dest.parent.mkdir(parents=True, exist_ok=True)
    repo = GitRepo.clone_from(ref.clone_url, dest, depth=1, single_branch=True)
    return dest, repo.head.commit.hexsha
