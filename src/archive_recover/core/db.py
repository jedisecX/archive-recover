"""Database models and schema management (SQLAlchemy 2.x, SQLite).

Schema changes are applied by :func:`init_db` with a small additive migrator
(new tables/columns/indexes only), so databases created by v0.1.0 upgrade in place.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    inspect,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from archive_recover.core.utils import utcnow

SCHEMA_VERSION = 2


class Base(DeclarativeBase):
    pass


class Domain(Base):
    __tablename__ = "domains"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    normalized: Mapped[str] = mapped_column(String(255), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_checked: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class URL(Base):
    __tablename__ = "urls"
    __table_args__ = (UniqueConstraint("domain_id", "normalized", name="uq_url_domain_norm"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey("domains.id"), index=True)
    original: Mapped[str] = mapped_column(Text)
    normalized: Mapped[str] = mapped_column(Text, index=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Capture(Base):
    __tablename__ = "captures"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url_id: Mapped[int] = mapped_column(ForeignKey("urls.id"), index=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    timestamp: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    timestamp_str: Mapped[Optional[str]] = mapped_column(String(32))
    snapshot_url: Mapped[Optional[str]] = mapped_column(Text)
    mime_type: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    status_code: Mapped[Optional[int]] = mapped_column(Integer)
    digest: Mapped[Optional[str]] = mapped_column(String(128))
    length: Mapped[Optional[int]] = mapped_column(Integer)
    # pending | recovered | failed | skipped | metadata_only
    recovery_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    extra: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # v2
    capture_key: Mapped[Optional[str]] = mapped_column(String(40), unique=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    discovered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=utcnow)

    @staticmethod
    def make_key(source: str, original: str, timestamp_str: Optional[str], snapshot: Optional[str]) -> str:
        raw = "\x1f".join([source, original, timestamp_str or "", snapshot or ""])
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class Resource(Base):
    __tablename__ = "resources"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    capture_id: Mapped[int] = mapped_column(ForeignKey("captures.id"), index=True)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)  # SHA-256
    local_path: Mapped[Optional[str]] = mapped_column(Text)
    size: Mapped[Optional[int]] = mapped_column(Integer)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_notes: Mapped[Optional[str]] = mapped_column(Text)
    # v2
    sha1_b32: Mapped[Optional[str]] = mapped_column(String(40))
    http_status: Mapped[Optional[int]] = mapped_column(Integer)
    content_type: Mapped[Optional[str]] = mapped_column(String(255))
    final_url: Mapped[Optional[str]] = mapped_column(Text)
    retrieved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=utcnow)


class RecoveryJob(Base):
    __tablename__ = "recovery_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey("domains.id"))
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    config: Mapped[Optional[dict]] = mapped_column(JSON)
    # v2
    kind: Mapped[Optional[str]] = mapped_column(String(32))  # discover | recover | ...


class JobCheckpoint(Base):
    __tablename__ = "job_checkpoints"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("recovery_jobs.id"), index=True)
    stage: Mapped[str] = mapped_column(String(64))
    resume_key: Mapped[Optional[str]] = mapped_column(Text)
    data: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ErrorRecord(Base):
    __tablename__ = "errors"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[Optional[int]] = mapped_column(ForeignKey("recovery_jobs.id"))
    capture_id: Mapped[Optional[int]] = mapped_column(ForeignKey("captures.id"))
    category: Mapped[str] = mapped_column(String(64), index=True)
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ReconstructionManifest(Base):
    __tablename__ = "reconstruction_manifests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey("domains.id"))
    snapshot_key: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


Index("ix_captures_url_source", Capture.url_id, Capture.source)


# --------------------------------------------------------------------------- engine


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
    if dbapi_conn.__class__.__module__.startswith("sqlite3"):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.close()


def get_engine(db_path: str = "archive_recover.db") -> Engine:
    return create_engine(f"sqlite:///{db_path}", echo=False)


def _migrate(engine: Engine) -> None:
    """Additive migration: add columns/indexes present in models but missing on disk."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                coltype = col.type.compile(dialect=engine.dialect)
                default = ""
                if col.name == "attempts":
                    default = " DEFAULT 0"
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {coltype}{default}'))
        # Constraints that v0.1.0 never created
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_url_domain_norm_idx ON urls(domain_id, normalized)"
        ))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_captures_capture_key ON captures(capture_key)"
        ))
        conn.execute(text("CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT)"))
        conn.execute(
            text("INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('version', :v)"),
            {"v": str(SCHEMA_VERSION)},
        )


def init_db(db_path: str = "archive_recover.db") -> Engine:
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    _migrate(engine)
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


# Backwards-compatible name; bind with SessionLocal.configure(bind=engine).
SessionLocal = sessionmaker(autoflush=False, expire_on_commit=False)
