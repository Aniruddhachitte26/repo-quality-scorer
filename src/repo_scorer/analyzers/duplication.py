"""Duplication analyzer.

Two complementary detectors:

1. Structural clones (always on): functions whose normalized AST hash matches,
   i.e. the same code with only names/literals changed (copy-paste-rename).
2. Semantic duplicates (needs the `embeddings` extra): each function is embedded
   with a local code-embedding model, and pgvector finds pairs whose vectors are
   very close (same behavior, different code).

Metrics:
  structural_clone_groups   groups of 2+ structurally identical functions
  duplicated_lines_pct      % of function lines that are redundant clone copies
  semantic_duplicate_pairs  near-duplicate pairs above SIMILARITY_THRESHOLD
"""

import hashlib
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from repo_scorer.analyzers.base import MetricResult, get_library_paths
from repo_scorer.db.models import CodeUnit, CodeUnitEmbedding, EmbeddingCache, Repo, SourceFile

NAME = "duplication"
MIN_LINES = 6  # ignore tiny functions: short getters look alike legitimately
MAX_EXAMPLES = 15
MAX_CHARS = 2000  # truncate very long functions before embedding (speed)
EMBEDDING_MODEL = "jinaai/jina-embeddings-v2-base-code"
# Cosine similarity at/above which two functions count as near-duplicates.
# A heuristic: check `closest_pairs` in details to calibrate it.
SIMILARITY_THRESHOLD = 0.95


# ---------------------------------------------------------------- structural


def find_clone_groups(units: list) -> list[list]:
    """Group units by normalized_hash; keep groups with 2+ members, biggest waste first."""
    groups: dict[str, list] = defaultdict(list)
    for u in units:
        if u.normalized_hash:
            groups[u.normalized_hash].append(u)
    clones = [g for g in groups.values() if len(g) > 1]
    return sorted(clones, key=lambda g: g[0].loc * (len(g) - 1), reverse=True)


def overlaps(a, b) -> bool:
    """True if two units share lines in the same file (e.g. a function nested in another)."""
    return a.path == b.path and a.lineno <= b.end_lineno and b.lineno <= a.end_lineno


def is_override_pair(a, b) -> bool:
    """Same method name in two different classes: polymorphism, not copy-paste.

    e.g. BaseAdapter.send vs HTTPAdapter.send are meant to look alike.
    """
    a_parts, b_parts = a.qualname.split("."), b.qualname.split(".")
    return (
        len(a_parts) > 1 and len(b_parts) > 1
        and a_parts[-1] == b_parts[-1]
        and a_parts[:-1] != b_parts[:-1]
    )


def _loc_str(u) -> str:
    return f"{u.path}:{u.lineno}"


# ---------------------------------------------------------------- semantic


@lru_cache(maxsize=1)
def _load_model():
    """Load the embedding model once per process (it's ~600 MB), reused across repos."""
    from fastembed import TextEmbedding

    return TextEmbedding(EMBEDDING_MODEL)


_PAIRS_SQL = text("""
    SELECT a.code_unit_id AS a_id,
           b.code_unit_id AS b_id,
           1 - (a.embedding <=> b.embedding) AS similarity
    FROM code_unit_embeddings a
    JOIN code_unit_embeddings b
      ON b.repo_id = a.repo_id AND a.code_unit_id < b.code_unit_id
    WHERE a.repo_id = :repo_id
    ORDER BY a.embedding <=> b.embedding
    LIMIT 2000
""")


def _semantic(session: Session, repo: Repo, units: list) -> dict[str, MetricResult]:
    try:
        import fastembed  # noqa: F401
    except ImportError:
        return {"semantic_duplicate_pairs": MetricResult(
            0.0, {"status": "skipped: install with  pip install -e '.[embeddings]'"}
        )}

    root = Path(repo.local_path)
    file_lines: dict[str, list[str]] = {}
    unit_text: dict[int, tuple[str, str]] = {}  # unit id -> (content hash, text)
    for u in units:
        if u.path not in file_lines:
            file_lines[u.path] = (root / u.path).read_text(errors="replace").splitlines()
        snippet = "\n".join(file_lines[u.path][u.lineno - 1 : u.end_lineno])[:MAX_CHARS]
        digest = hashlib.sha256(f"{EMBEDDING_MODEL}\n{snippet}".encode()).hexdigest()
        unit_text[u.id] = (digest, snippet)

    # Reuse cached vectors; embed only code we have never seen.
    hashes = {h for h, _ in unit_text.values()}
    vectors = dict(session.execute(
        select(EmbeddingCache.content_hash, EmbeddingCache.embedding)
        .where(EmbeddingCache.content_hash.in_(hashes))
    ).all())
    missing = {h: t for h, t in unit_text.values() if h not in vectors}
    if missing:
        model = _load_model()
        embeddings = model.embed(list(missing.values()), batch_size=16)
        for h, vec in zip(missing, embeddings, strict=True):
            vectors[h] = vec
            session.add(EmbeddingCache(content_hash=h, embedding=vec))

    session.execute(delete(CodeUnitEmbedding).where(CodeUnitEmbedding.repo_id == repo.id))
    session.add_all(
        CodeUnitEmbedding(code_unit_id=uid, repo_id=repo.id, embedding=vectors[h])
        for uid, (h, _) in unit_text.items()
    )
    session.flush()

    by_id = {u.id: u for u in units}
    candidates = []
    for a_id, b_id, sim in session.execute(_PAIRS_SQL, {"repo_id": repo.id}):
        a, b = by_id[a_id], by_id[b_id]
        if a.normalized_hash == b.normalized_hash or overlaps(a, b) or is_override_pair(a, b):
            continue  # structural clone, nested, or a method override
        candidates.append((round(float(sim), 4), a, b))

    pair_info = [
        {"similarity": sim, "a": f"{_loc_str(a)} {a.qualname}", "b": f"{_loc_str(b)} {b.qualname}"}
        for sim, a, b in candidates[:MAX_EXAMPLES]
    ]
    above = [c for c in candidates if c[0] >= SIMILARITY_THRESHOLD]
    return {"semantic_duplicate_pairs": MetricResult(
        float(len(above)),
        {"model": EMBEDDING_MODEL, "threshold": SIMILARITY_THRESHOLD,
         "functions_embedded": len(units), "newly_embedded": len(missing),
         "closest_pairs": pair_info},
    )}


# ---------------------------------------------------------------- analyzer


def run(session: Session, repo: Repo) -> dict[str, MetricResult]:
    rows = session.execute(
        select(
            CodeUnit.id, CodeUnit.qualname, CodeUnit.loc, CodeUnit.lineno,
            CodeUnit.end_lineno, CodeUnit.normalized_hash, SourceFile.path,
        )
        .join(SourceFile)
        .where(
            SourceFile.repo_id == repo.id,
            SourceFile.is_test.is_(False),
            CodeUnit.kind.in_(["function", "method"]),
        )
    ).all()
    library = get_library_paths(session, repo.id)
    rows = [r for r in rows if r.path in library]
    eligible = [r for r in rows if r.loc >= MIN_LINES]

    groups = find_clone_groups(eligible)
    total_lines = sum(r.loc for r in rows)
    redundant = sum(u.loc for g in groups for u in g[1:])  # every copy beyond the first

    metrics = {
        "structural_clone_groups": MetricResult(
            float(len(groups)),
            {"groups": [
                {"loc": g[0].loc, "copies": [f"{_loc_str(u)} {u.qualname}" for u in g]}
                for g in groups[:MAX_EXAMPLES]
            ]},
        ),
        "duplicated_lines_pct": MetricResult(
            round(100 * redundant / total_lines, 1) if total_lines else 0.0,
            {"redundant_lines": redundant, "function_lines": total_lines},
        ),
    }
    metrics.update(_semantic(session, repo, eligible))
    return metrics
