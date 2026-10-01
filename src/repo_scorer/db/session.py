"""Database engine and session setup."""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from repo_scorer.config import get_settings

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

# Columns added after their table was first created. create_all() never alters
# existing tables, so these run on every init-db (IF NOT EXISTS makes them safe).
# A larger project would use Alembic migrations instead.
MIGRATIONS = [
    "ALTER TABLE repos ADD COLUMN IF NOT EXISTS archived BOOLEAN",
]


def migrate() -> None:
    with engine.begin() as conn:
        for statement in MIGRATIONS:
            conn.execute(text(statement))


def check_connection() -> tuple[str, str | None]:
    """Return (postgres version, pgvector version or None)."""
    with engine.connect() as conn:
        pg_version = conn.execute(text("SHOW server_version")).scalar_one()
        vector_version = conn.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        ).scalar_one_or_none()
    return pg_version, vector_version
