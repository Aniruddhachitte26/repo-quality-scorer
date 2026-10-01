"""Documentation analyzer.

Metrics (all 0-100 except the raw counts):
  docstring_coverage   % of public functions/methods/classes with a docstring
  type_hint_coverage   % of public functions/methods with full type hints
  readme_score         heuristic README quality
  has_docs_dir         1 if the repo has a docs/ or doc/ directory, else 0
"""

import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from repo_scorer.analyzers.base import MetricResult, get_library_paths, is_public
from repo_scorer.db.models import CodeUnit, Repo, SourceFile

NAME = "documentation"
MAX_EXAMPLES = 15  # how many undocumented units to keep in details for the agent


# ---------------------------------------------------------------- README


@dataclass
class ReadmeStats:
    filename: str | None
    words: int = 0
    sections: int = 0
    has_code_example: bool = False
    mentions_install: bool = False
    mentions_usage: bool = False


_MD_HEADER = re.compile(r"^#{1,6}\s+\S", re.MULTILINE)
_RST_UNDERLINE = re.compile(r"^\S.*\n[=\-~^\"'#*+]{3,}\s*$", re.MULTILINE)
_CODE = re.compile(r"```|^\s*>>>|\.\. code(-block)?::|::\s*$", re.MULTILINE)
_INSTALL = re.compile(r"\binstall", re.IGNORECASE)
_USAGE = re.compile(r"\b(usage|example|quick ?start|getting started)\b", re.IGNORECASE)


def find_readme(root: Path) -> Path | None:
    candidates = sorted(p for p in root.iterdir() if p.is_file() and p.stem.lower() == "readme")
    return candidates[0] if candidates else None


def analyze_readme(text: str, filename: str) -> ReadmeStats:
    return ReadmeStats(
        filename=filename,
        words=len(text.split()),
        sections=len(_MD_HEADER.findall(text)) + len(_RST_UNDERLINE.findall(text)),
        has_code_example=bool(_CODE.search(text)),
        mentions_install=bool(_INSTALL.search(text)),
        mentions_usage=bool(_USAGE.search(text)),
    )


def readme_score(stats: ReadmeStats) -> float:
    """0-100. Existence 20, length 20, structure 20, code example 20, install 10, usage 10."""
    if stats.filename is None:
        return 0.0
    score = 20.0
    score += 20 * min(stats.words / 300, 1)
    score += 20 * min(stats.sections / 3, 1)
    score += 20 * stats.has_code_example
    score += 10 * stats.mentions_install
    score += 10 * stats.mentions_usage
    return round(score, 1)


# ---------------------------------------------------------------- analyzer


def _pct(part: int, total: int) -> float:
    return round(100 * part / total, 1) if total else 0.0


def run(session: Session, repo: Repo) -> dict[str, MetricResult]:
    rows = session.execute(
        select(
            CodeUnit.kind, CodeUnit.qualname, CodeUnit.loc, CodeUnit.lineno,
            CodeUnit.has_docstring, CodeUnit.has_type_hints, SourceFile.path,
        )
        .join(SourceFile)
        .where(SourceFile.repo_id == repo.id, SourceFile.is_test.is_(False))
    ).all()

    library = get_library_paths(session, repo.id)
    public = [r for r in rows if is_public(r.qualname) and r.path in library]
    callables = [r for r in public if r.kind in ("function", "method")]

    documented = sum(r.has_docstring for r in public)
    typed = sum(r.has_type_hints for r in callables)

    # Biggest undocumented units first: those are the most valuable to fix.
    undocumented = sorted(
        (r for r in public if not r.has_docstring), key=lambda r: r.loc, reverse=True
    )
    examples = [
        {"location": f"{r.path}:{r.lineno}", "name": r.qualname, "kind": r.kind, "loc": r.loc}
        for r in undocumented[:MAX_EXAMPLES]
    ]

    root = Path(repo.local_path)
    readme_path = find_readme(root)
    if readme_path:
        text = readme_path.read_text(encoding="utf-8", errors="replace")
        stats = analyze_readme(text, readme_path.name)
    else:
        stats = ReadmeStats(filename=None)

    has_docs = any((root / d).is_dir() for d in ("docs", "doc"))

    return {
        "docstring_coverage": MetricResult(
            _pct(documented, len(public)),
            {"documented": documented, "public_units": len(public),
             "largest_undocumented": examples},
        ),
        "type_hint_coverage": MetricResult(
            _pct(typed, len(callables)),
            {"fully_typed": typed, "public_callables": len(callables)},
        ),
        "readme_score": MetricResult(readme_score(stats), stats.__dict__),
        "has_docs_dir": MetricResult(float(has_docs)),
    }
