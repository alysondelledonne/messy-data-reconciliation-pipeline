"""Index record_links.golden_id: the review app looks up a golden record's sources by
it, and rebuilding golden records cascades deletes through it.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_record_links_golden", "record_links", ["golden_id"])


def downgrade() -> None:
    op.drop_index("ix_record_links_golden", table_name="record_links")
