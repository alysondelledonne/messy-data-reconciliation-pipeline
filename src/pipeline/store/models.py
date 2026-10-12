"""SQLAlchemy models. Every schema change goes through an Alembic migration in `migrations/`."""

from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        dict[str, Any]: JSONB,
        list[str]: JSONB,
        list[int]: JSONB,
        list[dict[str, Any]]: JSONB,
    }


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


# --- extraction (migration 0002) ---------------------------------------------------------


class LLMCall(Base):
    """Every model call: the response cache (D-026) and the token and cost log."""

    __tablename__ = "llm_calls"
    __table_args__ = (Index("ix_llm_calls_pack_model", "pack", "model_served"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    cache_key: Mapped[str | None] = mapped_column(String(64), unique=True)  # null: not reusable
    pack: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(32))  # document | column_mapping
    prompt_version: Mapped[str] = mapped_column(String(16))
    model_requested: Mapped[str] = mapped_column(String(64))
    model_served: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))  # ok | refusal | max_tokens | invalid | error
    stop_reason: Mapped[str | None] = mapped_column(String(32))
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cache_read_tokens: Mapped[int] = mapped_column(Integer)
    cache_write_tokens: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict[str, Any] | None]
    error: Mapped[str | None] = mapped_column(Text)
    raw_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw_records.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ColumnMapping(Base):
    """A model-proposed column -> field mapping for one header layout (D-023)."""

    __tablename__ = "column_mappings"
    __table_args__ = (
        UniqueConstraint("pack", "signature", "prompt_version", name="uq_column_mappings_sig"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    pack: Mapped[str] = mapped_column(String(64))
    signature: Mapped[str] = mapped_column(String(32))
    prompt_version: Mapped[str] = mapped_column(String(16))
    columns: Mapped[list[str]]
    mapping: Mapped[dict[str, Any]]
    llm_call_id: Mapped[int | None] = mapped_column(ForeignKey("llm_calls.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExtractionResult(Base):
    """The outcome of extracting one raw record with one extractor version."""

    __tablename__ = "extraction_results"
    __table_args__ = (
        UniqueConstraint("raw_record_id", "extractor_version", name="uq_extraction_results_rec"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_record_id: Mapped[int] = mapped_column(ForeignKey("raw_records.id", ondelete="CASCADE"))
    pack: Mapped[str] = mapped_column(String(64))
    extractor_version: Mapped[str] = mapped_column(String(32))
    method: Mapped[str] = mapped_column(String(16))  # llm | column_mapping | skipped
    status: Mapped[str] = mapped_column(String(16))  # ok | empty | failed | skipped
    model: Mapped[str | None] = mapped_column(String(64))
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    reasons: Mapped[list[str]] = mapped_column(default=list)
    llm_call_ids: Mapped[list[int]] = mapped_column(default=list)
    column_mapping_id: Mapped[int | None] = mapped_column(ForeignKey("column_mappings.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    records: Mapped[list["ExtractedRecord"]] = relationship(
        back_populates="result", cascade="all, delete-orphan"
    )


class ExtractedRecord(Base):
    """One schema-shaped record pulled out of a raw record."""

    __tablename__ = "extracted_records"
    __table_args__ = (Index("ix_extracted_records_result", "result_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    result_id: Mapped[int] = mapped_column(ForeignKey("extraction_results.id", ondelete="CASCADE"))
    record_index: Mapped[int] = mapped_column(Integer)
    fields: Mapped[dict[str, Any]]
    field_meta: Mapped[dict[str, Any]]
    flags: Mapped[list[str]] = mapped_column(default=list)

    result: Mapped[ExtractionResult] = relationship(back_populates="records")


# --- validation (migration 0003) -----------------------------------------------------------


class Validation(Base):
    """Machine verdict for one extracted record under one validator version (D-032).

    Recomputed on every `validate` run. Human decisions (Milestone 6) are stored
    separately, so re-validating never overwrites them.
    """

    __tablename__ = "validations"
    __table_args__ = (
        UniqueConstraint("extracted_record_id", "validator_version", name="uq_validations_rec"),
        Index("ix_validations_pack_status", "pack", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    extracted_record_id: Mapped[int] = mapped_column(
        ForeignKey("extracted_records.id", ondelete="CASCADE")
    )
    pack: Mapped[str] = mapped_column(String(64))
    validator_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))  # auto_approved | needs_review | rejected
    findings: Mapped[list[dict[str, Any]]] = mapped_column(default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --- reconciliation (migration 0004) -------------------------------------------------------


class Entity(Base):
    """A canonical organization (vendor, company) and the spellings it appeared as."""

    __tablename__ = "entities"
    __table_args__ = (Index("ix_entities_pack", "pack"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    pack: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))
    canonical_name: Mapped[str] = mapped_column(Text)
    normalized: Mapped[str] = mapped_column(Text)
    aliases: Mapped[list[dict[str, Any]]] = mapped_column(default=list)  # [{name, count}]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GoldenRecord(Base):
    """One real-world record, merged from every source that mentions it (D-034)."""

    __tablename__ = "golden_records"
    __table_args__ = (Index("ix_golden_records_pack_status", "pack", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    pack: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[int | None] = mapped_column(ForeignKey("entities.id", ondelete="SET NULL"))
    fields: Mapped[dict[str, Any]]
    provenance: Mapped[dict[str, Any]]  # field -> {winner, rule, support, alternatives}
    conflicts: Mapped[list[dict[str, Any]]] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(16))
    findings: Mapped[list[dict[str, Any]]] = mapped_column(default=list)
    source_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    links: Mapped[list["RecordLink"]] = relationship(
        back_populates="golden", cascade="all, delete-orphan"
    )


class RecordLink(Base):
    """Which extracted records a golden record was built from, and why they were joined."""

    __tablename__ = "record_links"
    __table_args__ = (Index("ix_record_links_extracted", "extracted_record_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    golden_id: Mapped[int] = mapped_column(ForeignKey("golden_records.id", ondelete="CASCADE"))
    extracted_record_id: Mapped[int] = mapped_column(
        ForeignKey("extracted_records.id", ondelete="CASCADE")
    )
    reason: Mapped[str] = mapped_column(String(64))

    golden: Mapped[GoldenRecord] = relationship(back_populates="links")


class MatchDecision(Base):
    """A decision on whether two names are the same organization. Survives re-runs:
    human decisions beat the model's, and pending rows form the review queue (D-036)."""

    __tablename__ = "match_decisions"
    __table_args__ = (UniqueConstraint("pack", "kind", "a", "b", name="uq_match_decisions_pair"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    pack: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))  # entity
    a: Mapped[str] = mapped_column(Text)  # normalized names, a < b
    b: Mapped[str] = mapped_column(Text)
    decision: Mapped[str] = mapped_column(String(16))  # same | different | pending
    decided_by: Mapped[str] = mapped_column(String(64))  # human:<name> | llm:<model> | system
    confidence: Mapped[float | None]
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
