"""Add optional interest tag to waitlist_signups."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260720_waitlist_interest"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "waitlist_signups",
        sa.Column("interest", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("waitlist_signups", "interest")
