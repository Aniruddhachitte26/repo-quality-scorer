"""Tools the agent can call. Each returns a string for the model to read.

The agent can only *read* stored results and source code; it cannot change scores.
"""

import json
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from repo_scorer.db.models import CodeUnit, Metric, Repo, SourceFile

MAX_RESULT_CHARS = 6000
MAX_CODE_LINES = 150

TOOL_SPECS = [
    {
        "name": "get_metric_details",
        "description": (
            "Get the stored value and details of one analyzer metric, e.g. which functions are "
            "most complex or which classes are god classes. Analyzers: documentation, "
            "architecture, dependencies, tests, duplication."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "analyzer": {"type": "string"},
                "metric": {"type": "string", "description": "e.g. high_complexity_pct, god_classes"},
            },
            "required": ["analyzer", "metric"],
        },
    },
    {
        "name": "read_code",
        "description": (
            "Read the source of one function, method or class, with line numbers. "
            "Use the path and qualified name exactly as returned by other tools."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Repo-relative path, e.g. src/pkg/models.py"},
                "qualname": {"type": "string", "description": "e.g. Session.request or helper"},
            },
            "required": ["path", "qualname"],
        },
    },
    {
        "name": "find_similar_code",
        "description": (
            "Find the functions most semantically similar to the given one (embedding search). "
            "Useful to check whether duplicated logic could be shared."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "qualname": {"type": "string"},
            },
            "required": ["path", "qualname"],
        },
    },
    {
        "name": "list_functions",
        "description": "List non-test functions/methods sorted by complexity or length.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sort_by": {"type": "string", "enum": ["complexity", "loc"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 25},
            },
            "required": ["sort_by"],
        },
    },
]


def truncate(text_: str, limit: int = MAX_RESULT_CHARS) -> str:
    if len(text_) <= limit:
        return text_
    return text_[:limit] + f"\n... [truncated {len(text_) - limit} characters]"


def number_lines(lines: list[str], start: int) -> str:
    return "\n".join(f"{start + i:>5}  {line}" for i, line in enumerate(lines))


class RepoTools:
    """Executes tool calls against one repo's stored data."""

    def __init__(self, session: Session, repo: Repo):
        self.session = session
        self.repo = repo
        self.root = Path(repo.local_path)

    def execute(self, name: str, args: dict) -> str:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return f"Error: unknown tool {name!r}"
        try:
            return truncate(handler(**args))
        except Exception as exc:  # noqa: BLE001 - errors go back to the model, never crash the run
            return f"Error: {type(exc).__name__}: {exc}"

    # ------------------------------------------------------------ tools

    def _tool_get_metric_details(self, analyzer: str, metric: str) -> str:
        m = self.session.scalar(
            select(Metric).where(
                Metric.repo_id == self.repo.id, Metric.analyzer == analyzer, Metric.name == metric
            )
        )
        if m is None:
            names = self.session.scalars(
                select(Metric.name).where(Metric.repo_id == self.repo.id, Metric.analyzer == analyzer)
            ).all()
            return f"No metric {analyzer}.{metric}. Available: {', '.join(names) or 'none'}"
        return json.dumps({"value": m.value, "details": m.details}, indent=1, default=str)

    def _find_unit(self, path: str, qualname: str) -> tuple[CodeUnit, SourceFile]:
        row = self.session.execute(
            select(CodeUnit, SourceFile)
            .join(SourceFile)
            .where(
                SourceFile.repo_id == self.repo.id,
                SourceFile.path == path,
                CodeUnit.qualname == qualname,
            )
        ).first()
        if row is None:
            raise LookupError(f"{path}::{qualname} not found")
        return row

    def _tool_read_code(self, path: str, qualname: str) -> str:
        unit, source_file = self._find_unit(path, qualname)
        if unit.kind == "class":
            return self._class_outline(unit, source_file)
        lines = (self.root / path).read_text(errors="replace").splitlines()
        body = lines[unit.lineno - 1 : unit.end_lineno][:MAX_CODE_LINES]
        header = (
            f"{path}::{qualname} ({unit.kind}, lines {unit.lineno}-{unit.end_lineno}, "
            f"complexity {unit.complexity})"
        )
        return header + "\n" + number_lines(body, unit.lineno)

    def _class_outline(self, cls: CodeUnit, source_file: SourceFile) -> str:
        """A class's methods, not its full source: big classes would flood the context."""
        methods = self.session.scalars(
            select(CodeUnit)
            .where(
                CodeUnit.file_id == source_file.id,
                CodeUnit.kind == "method",
                CodeUnit.qualname.like(f"{cls.qualname}.%"),
            )
            .order_by(CodeUnit.lineno)
        ).all()
        direct = [m for m in methods if m.qualname.count(".") == cls.qualname.count(".") + 1]
        rows = [
            f"  line {m.lineno:>5}  {m.loc:>4} lines  complexity {m.complexity:>3}  {m.name}"
            for m in direct
        ]
        return (
            f"{source_file.path}::{cls.qualname} (class, lines {cls.lineno}-{cls.end_lineno}, "
            f"{len(direct)} methods). Use read_code on a method to see its source.\n"
            + "\n".join(rows)
        )

    def _tool_find_similar_code(self, path: str, qualname: str) -> str:
        unit, _ = self._find_unit(path, qualname)
        rows = self.session.execute(
            text("""
                SELECT f.path, cu.qualname, cu.lineno,
                       1 - (e.embedding <=> t.embedding) AS similarity
                FROM code_unit_embeddings e
                JOIN code_units cu ON cu.id = e.code_unit_id
                JOIN files f ON f.id = cu.file_id
                JOIN code_unit_embeddings t ON t.code_unit_id = :unit_id
                WHERE e.repo_id = :repo_id AND e.code_unit_id != :unit_id
                ORDER BY e.embedding <=> t.embedding
                LIMIT 5
            """),
            {"unit_id": unit.id, "repo_id": self.repo.id},
        ).all()
        if not rows:
            return "No embeddings for this function (it may be shorter than 6 lines)."
        return "\n".join(
            f"{sim:.3f}  {p}:{line} {q}" for p, q, line, sim in rows
        )

    def _tool_list_functions(self, sort_by: str, limit: int = 10) -> str:
        column = CodeUnit.complexity if sort_by == "complexity" else CodeUnit.loc
        rows = self.session.execute(
            select(SourceFile.path, CodeUnit.qualname, CodeUnit.lineno,
                   CodeUnit.complexity, CodeUnit.loc)
            .join(SourceFile)
            .where(
                SourceFile.repo_id == self.repo.id,
                SourceFile.is_test.is_(False),
                CodeUnit.kind.in_(["function", "method"]),
            )
            .order_by(column.desc())
            .limit(min(limit, 25))
        ).all()
        return "\n".join(
            f"complexity {c:>3}  {loc:>4} lines  {p}:{line} {q}" for p, q, line, c, loc in rows
        )
