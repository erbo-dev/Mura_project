"""Add audio duration to recordings and create AI usage reservations table.

Revision ID: 20260926_0019
Revises: 20260923_0018
Create Date: 2026-09-26

Enforces hard audio limits and two-phase AI cost reservations for global
and family/user daily budgets.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260926_0019"
down_revision: str | None = "20260923_0018"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "recordings",
        sa.Column("audio_duration_seconds", sa.Numeric(precision=10, scale=3), nullable=True),
    )

    op.create_table(
        "ai_usage_reservations",
        sa.Column("reservation_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="RESERVED"),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("family_id", sa.String(length=128), nullable=True),
        sa.Column("user_id", sa.String(length=64), nullable=True),
        sa.Column("recording_id", sa.String(length=64), nullable=True),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("book_id", sa.String(length=64), nullable=True),
        sa.Column("reserved_cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("actual_cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("reserved_audio_seconds", sa.Numeric(precision=10, scale=3), nullable=True),
        sa.Column("actual_audio_seconds", sa.Numeric(precision=10, scale=3), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint("reservation_id"),
    )
    op.create_index(
        "ix_ai_usage_reservations_status",
        "ai_usage_reservations",
        ["status"],
    )
    op.create_index(
        "ix_ai_usage_reservations_expires_at",
        "ai_usage_reservations",
        ["expires_at"],
    )
    op.create_index(
        "ix_ai_usage_reservations_created_at",
        "ai_usage_reservations",
        ["created_at"],
    )
    op.create_index(
        "ix_ai_usage_reservations_family_id",
        "ai_usage_reservations",
        ["family_id"],
    )
    op.create_index(
        "ix_ai_usage_reservations_job_id",
        "ai_usage_reservations",
        ["job_id"],
    )
    op.create_index(
        "ix_ai_usage_reservations_idempotency_key",
        "ai_usage_reservations",
        ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_ai_usage_reservations_idempotency_key", table_name="ai_usage_reservations")
    op.drop_index("ix_ai_usage_reservations_job_id", table_name="ai_usage_reservations")
    op.drop_index("ix_ai_usage_reservations_family_id", table_name="ai_usage_reservations")
    op.drop_index("ix_ai_usage_reservations_created_at", table_name="ai_usage_reservations")
    op.drop_index("ix_ai_usage_reservations_expires_at", table_name="ai_usage_reservations")
    op.drop_index("ix_ai_usage_reservations_status", table_name="ai_usage_reservations")
    op.drop_table("ai_usage_reservations")
    op.drop_column("recordings", "audio_duration_seconds")
