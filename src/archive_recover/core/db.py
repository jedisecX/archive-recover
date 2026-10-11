"""Database models and schema management using SQLAlchemy + Alembic-ready design."""

from __future__ import annotations

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
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


class Base(DeclarativeBase):
    pass


class Domain(Base):
    __tablename__ = "domains"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    normalized: Mapped[str] = mapped_column(String(255), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_checked: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class URL(Base):
    __tablename__ = "urls"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey("domains.id"), index=True)
    original: Mapped[str] = mapped_column(Text)
    normalized: Mapped[str] = mapped_column(Text, index=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    UniqueConstraint("domain_id", "normalized", name="uq_url_domain_norm")


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
    recovery_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    extra: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class Resource(Base):
    __tablename__ = "resources"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    capture_id: Mapped[int] = mapped_column(ForeignKey("captures.id"), index=True)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)  # SHA-256
    local_path: Mapped[Optional[str]] = mapped_column(Text)
    size: Mapped[Optional[int]] = mapped_column(Integer)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_notes: Mapped[Optional[str]] = mapped_column(Text)


class RecoveryJob(Base):
    __tablename__ = "recovery_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey("domains.id"))
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    config: Mapped[Optional[dict]] = mapped_column(JSON)


class JobCheckpoint(Base):
    __tablename__ = "job_checkpoints"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("recovery_jobs.id"), index=True)
    stage: Mapped[str] = mapped_column(String(64))
    resume_key: Mapped[Optional[str]] = mapped_column(Text)
    data: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ErrorRecord(Base):
    __tablename__ = "errors"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[Optional[int]] = mapped_column(ForeignKey("recovery_jobs.id"))
    capture_id: Mapped[Optional[int]] = mapped_column(ForeignKey("captures.id"))
    category: Mapped[str] = mapped_column(String(64), index=True)
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ReconstructionManifest(Base):
    __tablename__ = "reconstruction_manifests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey("domains.id"))
    snapshot_key: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def get_engine(db_path: str = "archive_recover.db"):
    return create_engine(f"sqlite:///{db_path}", echo=False)


def init_db(db_path: str = "archive_recover.db"):
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    return engine


SessionLocal = sessionmaker(autocommit=False, autoflush=False)
