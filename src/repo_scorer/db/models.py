"""Database schema.

repos       one row per GitHub repository
files       one row per Python file in a repo
code_units  one row per function / method / class (filled by the AST parser)
metrics     one row per computed metric (filled by the analyzers)
"""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMBEDDING_DIM = 768  # jina-embeddings-v2-base-code


class Base(DeclarativeBase):
    pass


class Repo(Base):
    __tablename__ = "repos"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(500), unique=True)
    owner: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    default_branch: Mapped[str | None] = mapped_column(String(200))
    stars: Mapped[int | None]
    forks: Mapped[int | None]
    open_issues: Mapped[int | None]
    license: Mapped[str | None] = mapped_column(String(100))
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    commit_sha: Mapped[str | None] = mapped_column(String(40))
    local_path: Mapped[str | None] = mapped_column(String(1000))
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    files: Mapped[list["SourceFile"]] = relationship(
        back_populates="repo", cascade="all, delete-orphan"
    )
    metrics: Mapped[list["Metric"]] = relationship(
        back_populates="repo", cascade="all, delete-orphan"
    )


class SourceFile(Base):
    __tablename__ = "files"
    __table_args__ = (UniqueConstraint("repo_id", "path"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id", ondelete="CASCADE"))
    path: Mapped[str] = mapped_column(String(1000))  # relative to repo root
    loc: Mapped[int] = mapped_column(default=0)
    is_test: Mapped[bool] = mapped_column(default=False)
    parse_error: Mapped[str | None] = mapped_column(Text)

    repo: Mapped[Repo] = relationship(back_populates="files")
    code_units: Mapped[list["CodeUnit"]] = relationship(
        back_populates="file", cascade="all, delete-orphan"
    )


class CodeUnit(Base):
    __tablename__ = "code_units"

    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))  # function | method | class
    name: Mapped[str] = mapped_column(String(300))
    qualname: Mapped[str] = mapped_column(String(1000))  # e.g. Session.request
    lineno: Mapped[int]
    end_lineno: Mapped[int]
    loc: Mapped[int]
    complexity: Mapped[int | None]
    arg_count: Mapped[int] = mapped_column(default=0)
    has_docstring: Mapped[bool] = mapped_column(default=False)
    has_type_hints: Mapped[bool] = mapped_column(default=False)
    normalized_hash: Mapped[str | None] = mapped_column(String(64))  # for clone detection

    file: Mapped[SourceFile] = relationship(back_populates="code_units")


class EmbeddingCache(Base):
    """One vector per distinct piece of code text, so nothing is embedded twice."""

    __tablename__ = "embedding_cache"

    content_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    embedding = mapped_column(Vector(EMBEDDING_DIM), nullable=False)


class CodeUnitEmbedding(Base):
    """Vector for each function in a repo, searched with pgvector."""

    __tablename__ = "code_unit_embeddings"

    code_unit_id: Mapped[int] = mapped_column(
        ForeignKey("code_units.id", ondelete="CASCADE"), primary_key=True
    )
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id", ondelete="CASCADE"), index=True)
    embedding = mapped_column(Vector(EMBEDDING_DIM), nullable=False)


class Metric(Base):
    __tablename__ = "metrics"
    __table_args__ = (UniqueConstraint("repo_id", "analyzer", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id", ondelete="CASCADE"))
    analyzer: Mapped[str] = mapped_column(String(50))  # tests | duplication | architecture ...
    name: Mapped[str] = mapped_column(String(100))  # e.g. docstring_coverage
    value: Mapped[float] = mapped_column(Float)
    details: Mapped[dict | None] = mapped_column(JSON)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    repo: Mapped[Repo] = relationship(back_populates="metrics")
