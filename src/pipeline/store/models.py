"""SQLAlchemy models. Every schema change goes through an Alembic migration in `migrations/`."""

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSONB, list[str]: JSONB}


class IngestRun(Base):
    __tablename__ = "ingest_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    pack: Mapped[str] = mapped_column(String(64))
    inputs: Mapped[list[str]] = mapped_column(default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    files_seen: Mapped[int] = mapped_column(Integer, default=0)
    files_ingested: Mapped[int] = mapped_column(Integer, default=0)
    files_skipped: Mapped[int] = mapped_column(Integer, default=0)
    files_failed: Mapped[int] = mapped_column(Integer, default=0)
    records: Mapped[int] = mapped_column(Integer, default=0)

    files: Mapped[list["SourceFile"]] = relationship(back_populates="run")


class SourceFile(Base):
    """One version of one input file. Re-ingesting the same path and content is a no-op."""

    __tablename__ = "source_files"
    __table_args__ = (
        UniqueConstraint("pack", "path", "sha256", name="uq_source_files_pack_path_sha"),
        Index("ix_source_files_pack_sha", "pack", "sha256"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ingest_run_id: Mapped[int] = mapped_column(ForeignKey("ingest_runs.id"))
    pack: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(Text)
    format: Mapped[str] = mapped_column(String(32))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    encoding: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))  # ok | unsupported | error
    error: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    run: Mapped[IngestRun] = relationship(back_populates="files")
    records: Mapped[list["RawRecordRow"]] = relationship(
        back_populates="source_file", cascade="all, delete-orphan"
    )


class RawRecordRow(Base):
    """One ingested unit (row, line, document, email part, paragraph...)."""

    __tablename__ = "raw_records"
    __table_args__ = (Index("ix_raw_records_source_file", "source_file_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_file_id: Mapped[int] = mapped_column(ForeignKey("source_files.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))
    location: Mapped[dict[str, Any]]
    text: Mapped[str] = mapped_column(Text)
    raw: Mapped[dict[str, Any]] = mapped_column(default=dict)
    normalized: Mapped[dict[str, Any]] = mapped_column(default=dict)
    flags: Mapped[list[str]] = mapped_column(default=list)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    source_file: Mapped[SourceFile] = relationship(back_populates="records")
