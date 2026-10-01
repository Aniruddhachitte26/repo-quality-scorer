"""GitHub URL parsing and repository metadata via the GitHub REST API."""

import re
from dataclasses import dataclass
from datetime import datetime

import httpx

from repo_scorer.config import get_settings

_GITHUB_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/([^/\s]+)/([^/\s#?]+?)(?:\.git)?/?$"
)


@dataclass(frozen=True)
class RepoRef:
    owner: str
    name: str

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}"

    @property
    def clone_url(self) -> str:
        return f"{self.url}.git"


@dataclass(frozen=True)
class RepoMetadata:
    ref: RepoRef  # canonical owner/name as GitHub reports them
    description: str | None
    default_branch: str | None
    stars: int
    forks: int
    open_issues: int
    license: str | None
    pushed_at: datetime | None


def parse_github_url(url: str) -> RepoRef:
    """Turn 'https://github.com/psf/requests' (or similar) into a RepoRef."""
    match = _GITHUB_URL_RE.match(url.strip())
    if not match:
        raise ValueError(f"Not a GitHub repository URL: {url!r}")
    return RepoRef(owner=match.group(1), name=match.group(2))


def fetch_metadata(ref: RepoRef) -> RepoMetadata:
    headers = {"Accept": "application/vnd.github+json"}
    token = get_settings().github_token
    if token and token != "your-token-here":
        headers["Authorization"] = f"Bearer {token}"

    resp = httpx.get(
        f"https://api.github.com/repos/{ref.owner}/{ref.name}",
        headers=headers,
        timeout=20,
        follow_redirects=True,  # renamed/transferred repos answer with 301
    )
    if resp.status_code == 404:
        raise ValueError(f"Repository not found (or private): {ref.url}")
    if resp.status_code == 403 and resp.headers.get("x-ratelimit-remaining") == "0":
        raise RuntimeError(
            "GitHub API rate limit reached. Add a GITHUB_TOKEN to .env to raise the limit."
        )
    resp.raise_for_status()
    data = resp.json()

    pushed_at = data.get("pushed_at")
    return RepoMetadata(
        ref=RepoRef(owner=data["owner"]["login"], name=data["name"]),
        description=data.get("description"),
        default_branch=data.get("default_branch"),
        stars=data.get("stargazers_count", 0),
        forks=data.get("forks_count", 0),
        open_issues=data.get("open_issues_count", 0),
        license=(data.get("license") or {}).get("spdx_id"),
        pushed_at=datetime.fromisoformat(pushed_at) if pushed_at else None,
    )
