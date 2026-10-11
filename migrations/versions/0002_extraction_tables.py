"""Extraction tables: llm_calls, column_mappings, extraction_results, extracted_records.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cache_key", sa.String(64), nullable=True, unique=True),
        sa.Column("pack", sa.String(64), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("prompt_version", sa.String(16), nullable=False),
        sa.Column("model_requested", sa.String(64), nullable=False),
        sa.Column("model_served", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("stop_reason", sa.String(32), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("response", JSONB, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "raw_record_id",
            sa.Integer(),
            sa.ForeignKey("raw_records.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    )
    op.create_index("ix_llm_calls_pack_model", "llm_calls", ["pack", "model_served"])
    op.create_table(
        "column_mappings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pack", sa.String(64), nullable=False),
        sa.Column("signature", sa.String(32), nullable=False),
        sa.Column("prompt_version", sa.String(16), nullable=False),
        sa.Column("columns", JSONB, nullable=False),
        sa.Column("mapping", JSONB, nullable=False),
        sa.Column("llm_call_id", sa.Integer(), sa.ForeignKey("llm_calls.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.UniqueConstraint("pack", "signature", "prompt_version", name="uq_column_mappings_sig"),
    )
    op.create_table(
        "extraction_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "raw_record_id",
            sa.Integer(),
            sa.ForeignKey("raw_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pack", sa.String(64), nullable=False),
        sa.Column("extractor_version", sa.String(32), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("escalated", sa.Boolean(), nullable=False),
        sa.Column("reasons", JSONB, nullable=False),
        sa.Column("llm_call_ids", JSONB, nullable=False),
        sa.Column(
            "column_mapping_id", sa.Integer(), sa.ForeignKey("column_mappings.id"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.UniqueConstraint("raw_record_id", "extractor_version", name="uq_extraction_results_rec"),
    )
    op.create_table(
        "extracted_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "result_id",
            sa.Integer(),
            sa.ForeignKey("extraction_results.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("record_index", sa.Integer(), nullable=False),
        sa.Column("fields", JSONB, nullable=False),
        sa.Column("field_meta", JSONB, nullable=False),
        sa.Column("flags", JSONB, nullable=False),
    )
    op.create_index("ix_extracted_records_result", "extracted_records", ["result_id"])


def downgrade() -> None:
    op.drop_index("ix_extracted_records_result", table_name="extracted_records")
    op.drop_table("extracted_records")
    op.drop_table("extraction_results")
    op.drop_table("column_mappings")
    op.drop_index("ix_llm_calls_pack_model", table_name="llm_calls")
    op.drop_table("llm_calls")
