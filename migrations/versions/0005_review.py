"""Human review: stable golden keys, approvals, field overrides, audit trail.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
NOW = sa.text("now()")


def upgrade() -> None:
    # Golden records are rebuilt by every reconcile run, so existing rows can be dropped.
    op.execute("DELETE FROM golden_records")
    op.add_column("golden_records", sa.Column("golden_key", sa.String(32), nullable=False))
    op.add_column(
        "golden_records",
        sa.Column("source_keys", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.alter_column("golden_records", "source_keys", server_default=None)
    op.create_index("ix_golden_records_golden_key", "golden_records", ["golden_key"])
    op.create_table(
        "golden_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pack", sa.String(64), nullable=False),
        sa.Column("golden_key", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source_keys", JSONB, nullable=False),
        sa.Column("reviewer", sa.String(64), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.UniqueConstraint("pack", "golden_key", name="uq_golden_reviews_key"),
    )
    op.create_table(
        "golden_overrides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pack", sa.String(64), nullable=False),
        sa.Column("golden_key", sa.String(32), nullable=False),
        sa.Column("field", sa.String(64), nullable=False),
        sa.Column("value", JSONB, nullable=True),
        sa.Column("source_keys", JSONB, nullable=False),
        sa.Column("reviewer", sa.String(64), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.UniqueConstraint("pack", "golden_key", "field", name="uq_golden_overrides_field"),
    )
    op.create_table(
        "review_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("pack", sa.String(64), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("field", sa.String(64), nullable=True),
        sa.Column("old", JSONB, nullable=True),
        sa.Column("new", JSONB, nullable=True),
        sa.Column("note", sa.Text(), nullable=False),
    )
    op.create_index("ix_review_events_pack_at", "review_events", ["pack", "at"])


def downgrade() -> None:
    op.drop_index("ix_review_events_pack_at", table_name="review_events")
    op.drop_table("review_events")
    op.drop_table("golden_overrides")
    op.drop_table("golden_reviews")
    op.drop_index("ix_golden_records_golden_key", table_name="golden_records")
    op.drop_column("golden_records", "source_keys")
    op.drop_column("golden_records", "golden_key")
