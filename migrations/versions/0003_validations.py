"""Validation verdicts per extracted record.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "validations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "extracted_record_id",
            sa.Integer(),
            sa.ForeignKey("extracted_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pack", sa.String(64), nullable=False),
        sa.Column("validator_version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("findings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("extracted_record_id", "validator_version", name="uq_validations_rec"),
    )
    op.create_index("ix_validations_pack_status", "validations", ["pack", "status"])


def downgrade() -> None:
    op.drop_index("ix_validations_pack_status", table_name="validations")
    op.drop_table("validations")
