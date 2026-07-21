"""Add gateway_requests usage log table."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260721_gateway_requests"
down_revision = "20260720_waitlist_interest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gateway_requests",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_id", sa.String(length=200), nullable=False),
        sa.Column("tier", sa.String(length=64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("thread_id", sa.String(length=128), nullable=True),
        sa.Column("task_mode", sa.String(length=64), nullable=True),
        sa.Column("routing_policy_version", sa.String(length=32), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_index("ix_gateway_requests_user_id", "gateway_requests", ["user_id"])
    op.create_index("ix_gateway_requests_model_id", "gateway_requests", ["model_id"])
    op.create_index("ix_gateway_requests_created_at", "gateway_requests", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_gateway_requests_created_at", table_name="gateway_requests")
    op.drop_index("ix_gateway_requests_model_id", table_name="gateway_requests")
    op.drop_index("ix_gateway_requests_user_id", table_name="gateway_requests")
    op.drop_table("gateway_requests")
