"""Dependency analyzer.

Finds the project's *runtime* dependencies, then checks each one against
PyPI (latest version, last release date) and OSV (known vulnerabilities).

Metrics:
  dependencies          number of runtime dependencies
  unpinned_pct          % with no version constraint at all
  exact_pinned_pct      % pinned to one exact version (==)
  excludes_latest_pct   % whose constraint rules out the latest release
  vulnerable_deps       deps whose newest *allowed* version has known vulnerabilities
  stale_deps            deps with no release in the last 2 years
"""

import ast
import configparser
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version
from sqlalchemy.orm import Session

from repo_scorer.analyzers.base import MetricResult
from repo_scorer.db.models import Repo

NAME = "dependencies"
STALE_AFTER = timedelta(days=730)
DEV_HINTS = ("dev", "test", "doc", "lint", "ci", "build", "type")


# ---------------------------------------------------------------- parsing declarations


def parse_requirement_lines(lines: list[str]) -> list[Requirement]:
    reqs = []
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        # Skip blanks, pip options (-r, -e, --hash) and direct URLs.
        if not line or line.startswith("-") or "://" in line:
            continue
        try:
            reqs.append(Requirement(line))
        except InvalidRequirement:
            continue
    return reqs


def _poetry_to_pep508(name: str, version: str) -> str:
    """Approximate Poetry constraints ('^1.2', '~1.2', '1.2') as PEP 508."""
    version = version.strip()
    if not version or version == "*":
        return name
    if version[0] in "^~":
        return f"{name}>={version[1:]}"
    if version[0].isdigit():
        return f"{name}=={version}"
    return f"{name}{version}"


def parse_pyproject(text: str) -> list[Requirement]:
    data = tomllib.loads(text)
    lines = list(data.get("project", {}).get("dependencies", []))
    poetry = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
    for name, spec in poetry.items():
        if name.lower() == "python":
            continue
        version = spec if isinstance(spec, str) else (spec.get("version", "") if isinstance(spec, dict) else "")
        lines.append(_poetry_to_pep508(name, version))
    return parse_requirement_lines(lines)


def parse_setup_cfg(text: str) -> list[Requirement]:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(text)
    if not parser.has_option("options", "install_requires"):
        return []
    return parse_requirement_lines(parser.get("options", "install_requires").splitlines())


def parse_setup_py(source: str) -> list[Requirement]:
    """Read install_requires=[...] from setup(), following one level of variable."""
    tree = ast.parse(source)
    assigned = {
        node.targets[0].id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name != "setup":
            continue
        for kw in node.keywords:
            if kw.arg != "install_requires":
                continue
            value = assigned.get(kw.value.id) if isinstance(kw.value, ast.Name) else kw.value
            if isinstance(value, (ast.List, ast.Tuple)):
                return parse_requirement_lines([
                    e.value for e in value.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)
                ])
    return []


def collect_dependencies(root: Path) -> tuple[list[Requirement], str | None]:
    """Find runtime deps, preferring modern declarations. Returns (deps, source file)."""
    parsers = [
        ("pyproject.toml", parse_pyproject),
        ("setup.cfg", parse_setup_cfg),
        ("setup.py", parse_setup_py),
    ]
    for filename, parse in parsers:
        path = root / filename
        if path.exists():
            try:
                reqs = parse(path.read_text(encoding="utf-8", errors="replace"))
            except (tomllib.TOMLDecodeError, configparser.Error, SyntaxError):
                reqs = []
            if reqs:
                return _dedupe(reqs), filename

    # Fallback: top-level requirements files that don't look dev-only.
    for path in sorted(root.glob("requirements*.txt")):
        if any(hint in path.stem.lower() for hint in DEV_HINTS):
            continue
        reqs = parse_requirement_lines(path.read_text(errors="replace").splitlines())
        if reqs:
            return _dedupe(reqs), path.name
    return [], None


def _dedupe(reqs: list[Requirement]) -> list[Requirement]:
    seen: dict[str, Requirement] = {}
    for r in reqs:
        seen.setdefault(canonicalize_name(r.name), r)
    return list(seen.values())


def pin_category(req: Requirement) -> str:
    ops = {spec.operator for spec in req.specifier}
    if not ops:
        return "unpinned"
    if ops & {"==", "==="}:
        return "exact"
    if ops & {"<", "<=", "~="}:
        return "bounded"
    return "lower_bound"


def newest_allowed(req: Requirement, versions: list[Version]) -> Version | None:
    allowed = [v for v in versions if req.specifier.contains(v)]
    return max(allowed) if allowed else None


# ---------------------------------------------------------------- external APIs


@dataclass
class PackageInfo:
    latest: str
    latest_release: datetime | None
    versions: list[Version]  # stable, non-yanked


def fetch_pypi(client: httpx.Client, name: str) -> PackageInfo | None:
    resp = client.get(f"https://pypi.org/pypi/{name}/json")
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()

    versions = []
    for raw, files in data["releases"].items():
        if not files or all(f.get("yanked") for f in files):
            continue
        try:
            v = Version(raw)
        except InvalidVersion:
            continue
        if not v.is_prerelease:
            versions.append(v)

    latest = data["info"]["version"]
    times = [
        f["upload_time_iso_8601"]
        for f in data["releases"].get(latest, [])
        if f.get("upload_time_iso_8601")
    ]
    released = datetime.fromisoformat(min(times)) if times else None
    return PackageInfo(latest=latest, latest_release=released, versions=sorted(versions))


def query_osv(client: httpx.Client, packages: list[tuple[str, str]]) -> list[list[str]]:
    """Vulnerability IDs for each (name, version), in the same order."""
    if not packages:
        return []
    queries = [
        {"package": {"name": name, "ecosystem": "PyPI"}, "version": version}
        for name, version in packages
    ]
    resp = client.post("https://api.osv.dev/v1/querybatch", json={"queries": queries})
    resp.raise_for_status()
    return [[v["id"] for v in r.get("vulns", [])] for r in resp.json()["results"]]


# ---------------------------------------------------------------- analyzer


def _pct(part: int, total: int) -> float:
    return round(100 * part / total, 1) if total else 0.0


def run(session: Session, repo: Repo) -> dict[str, MetricResult]:
    reqs, source = collect_dependencies(Path(repo.local_path))
    now = datetime.now(UTC)
    rows: list[dict] = []

    try:
        with httpx.Client(timeout=20, headers={"User-Agent": "repo-quality-scorer"}) as client:
            for req in reqs:
                row = {
                    "name": canonicalize_name(req.name),
                    "specifier": str(req.specifier) or "*",
                    "pin": pin_category(req),
                }
                info = fetch_pypi(client, req.name)
                if info is None:
                    row["on_pypi"] = False
                else:
                    allowed = newest_allowed(req, info.versions)
                    row.update(
                        on_pypi=True,
                        latest=info.latest,
                        newest_allowed=str(allowed) if allowed else None,
                        excludes_latest=not req.specifier.contains(
                            Version(info.latest), prereleases=True
                        ),
                        last_release=info.latest_release.date().isoformat()
                        if info.latest_release else None,
                        stale=bool(info.latest_release)
                        and now - info.latest_release > STALE_AFTER,
                    )
                rows.append(row)

            checkable = [r for r in rows if r.get("newest_allowed")]
            vulns = query_osv(client, [(r["name"], r["newest_allowed"]) for r in checkable])
            for row, ids in zip(checkable, vulns, strict=True):
                row["vulns"] = ids
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Dependency check failed (network): {exc}") from exc

    n = len(rows)
    vulnerable = [r for r in rows if r.get("vulns")]
    stale = [r for r in rows if r.get("stale")]

    return {
        "dependencies": MetricResult(float(n), {"source": source, "packages": rows}),
        "unpinned_pct": MetricResult(_pct(sum(r["pin"] == "unpinned" for r in rows), n)),
        "exact_pinned_pct": MetricResult(_pct(sum(r["pin"] == "exact" for r in rows), n)),
        "excludes_latest_pct": MetricResult(
            _pct(sum(bool(r.get("excludes_latest")) for r in rows), n),
            {"packages": [r["name"] for r in rows if r.get("excludes_latest")]},
        ),
        "vulnerable_deps": MetricResult(
            float(len(vulnerable)),
            {"packages": [
                {"name": r["name"], "version": r["newest_allowed"], "vulns": r["vulns"]}
                for r in vulnerable
            ]},
        ),
        "stale_deps": MetricResult(
            float(len(stale)),
            {"packages": [{"name": r["name"], "last_release": r["last_release"]} for r in stale]},
        ),
    }
