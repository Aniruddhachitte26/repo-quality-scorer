"""Database engine and session setup."""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from repo_scorer.config import get_settings

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def check_connection() -> tuple[str, str | None]:
    """Return (postgres version, pgvector version or None)."""
    with engine.connect() as conn:
        pg_version = conn.execute(text("SHOW server_version")).scalar_one()
        vector_version = conn.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        ).scalar_one_or_none()
    return pg_version, vector_version
