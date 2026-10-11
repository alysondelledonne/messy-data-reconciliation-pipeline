"""Ingest tables: ingest_runs, source_files, raw_records.

Revision ID: 0001
Revises:
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "ingest_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pack", sa.String(64), nullable=False),
        sa.Column("inputs", JSONB, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("files_seen", sa.Integer(), nullable=False),
        sa.Column("files_ingested", sa.Integer(), nullable=False),
        sa.Column("files_skipped", sa.Integer(), nullable=False),
        sa.Column("files_failed", sa.Integer(), nullable=False),
        sa.Column("records", sa.Integer(), nullable=False),
    )
    op.create_table(
        "source_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ingest_run_id", sa.Integer(), sa.ForeignKey("ingest_runs.id"), nullable=False),
        sa.Column("pack", sa.String(64), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("format", sa.String(32), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("encoding", sa.String(32), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("meta", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.UniqueConstraint("pack", "path", "sha256", name="uq_source_files_pack_path_sha"),
    )
    op.create_index("ix_source_files_pack_sha", "source_files", ["pack", "sha256"])
    op.create_table(
        "raw_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_file_id",
            sa.Integer(),
            sa.ForeignKey("source_files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("location", JSONB, nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("raw", JSONB, nullable=False),
        sa.Column("normalized", JSONB, nullable=False),
        sa.Column("flags", JSONB, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    )
    op.create_index("ix_raw_records_source_file", "raw_records", ["source_file_id"])


def downgrade() -> None:
    op.drop_index("ix_raw_records_source_file", table_name="raw_records")
    op.drop_table("raw_records")
    op.drop_index("ix_source_files_pack_sha", table_name="source_files")
    op.drop_table("source_files")
    op.drop_table("ingest_runs")
