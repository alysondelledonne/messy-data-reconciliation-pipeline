"""Reconciliation: entities, golden_records, record_links, match_decisions.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "entities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pack", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("normalized", sa.Text(), nullable=False),
        sa.Column("aliases", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    )
    op.create_index("ix_entities_pack", "entities", ["pack"])
    op.create_table(
        "golden_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pack", sa.String(64), nullable=False),
        sa.Column(
            "entity_id",
            sa.Integer(),
            sa.ForeignKey("entities.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("fields", JSONB, nullable=False),
        sa.Column("provenance", JSONB, nullable=False),
        sa.Column("conflicts", JSONB, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("findings", JSONB, nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    )
    op.create_index("ix_golden_records_pack_status", "golden_records", ["pack", "status"])
    op.create_table(
        "record_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "golden_id",
            sa.Integer(),
            sa.ForeignKey("golden_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "extracted_record_id",
            sa.Integer(),
            sa.ForeignKey("extracted_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(64), nullable=False),
    )
    op.create_index("ix_record_links_extracted", "record_links", ["extracted_record_id"])
    op.create_table(
        "match_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pack", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("a", sa.Text(), nullable=False),
        sa.Column("b", sa.Text(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("decided_by", sa.String(64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.UniqueConstraint("pack", "kind", "a", "b", name="uq_match_decisions_pair"),
    )


def downgrade() -> None:
    op.drop_table("match_decisions")
    op.drop_index("ix_record_links_extracted", table_name="record_links")
    op.drop_table("record_links")
    op.drop_index("ix_golden_records_pack_status", table_name="golden_records")
    op.drop_table("golden_records")
    op.drop_index("ix_entities_pack", table_name="entities")
    op.drop_table("entities")
