"""Parse every Python file of an ingested repo into the files / code_units tables."""

import ast
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import delete

from repo_scorer.db.models import CodeUnit, Repo, SourceFile
from repo_scorer.db.session import SessionLocal
from repo_scorer.parsing.discovery import is_test_path, iter_python_files
from repo_scorer.parsing.extractor import count_loc, extract_units


@dataclass
class ParseSummary:
    files: int = 0
    test_files: int = 0
    parse_errors: int = 0
    total_loc: int = 0
    functions: int = 0
    methods: int = 0
    classes: int = 0


def parse_repo(repo_id: int) -> ParseSummary:
    summary = ParseSummary()

    with SessionLocal() as session:
        repo = session.get(Repo, repo_id)
        if repo is None or not repo.local_path:
            raise ValueError(f"Repo {repo_id} not found or not cloned. Run `ingest` first.")
        root = Path(repo.local_path)
        if not root.exists():
            raise ValueError(f"Clone missing at {root}. Re-run `ingest --force`.")

        # Re-parsing replaces old results (code_units cascade-delete with files).
        session.execute(delete(SourceFile).where(SourceFile.repo_id == repo.id))

        for path in iter_python_files(root):
            rel = path.relative_to(root)
            source = path.read_text(encoding="utf-8", errors="replace")
            source_file = SourceFile(
                repo_id=repo.id,
                path=rel.as_posix(),
                loc=count_loc(source),
                is_test=is_test_path(rel),
            )
            summary.files += 1
            summary.test_files += source_file.is_test
            summary.total_loc += source_file.loc

            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", SyntaxWarning)
                    tree = ast.parse(source, filename=str(rel))
            except (SyntaxError, ValueError) as exc:
                # e.g. Python 2 files or templated code: record and move on.
                source_file.parse_error = f"{type(exc).__name__}: {exc}"
                summary.parse_errors += 1
            else:
                units = extract_units(tree)
                source_file.code_units = [CodeUnit(**asdict(u)) for u in units]
                for u in units:
                    if u.kind == "function":
                        summary.functions += 1
                    elif u.kind == "method":
                        summary.methods += 1
                    else:
                        summary.classes += 1

            session.add(source_file)

        session.commit()

    return summary
