"""Durable AI usage ledger and cost accounting.

Revision ID: 20260917_0012
Revises: 20260913_0011
Create Date: 2026-09-17

Stores operational and cost metadata for DeepSeek and Whisper invocations.
Contains strictly aggregate and operational metrics (tokens, audio duration,
latency, success/failure, estimated cost). Never contains transcripts, prompts,
evidence quotes, or personal data.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260917_0012"
down_revision: str | None = "20260913_0011"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "ai_usage_events",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("recording_id", sa.String(length=64), nullable=True),
        sa.Column("family_id", sa.String(length=128), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=True),
        sa.Column("audio_seconds", sa.Numeric(precision=10, scale=3), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("pricing_version", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(
        "ix_ai_usage_events_created_at",
        "ai_usage_events",
        ["created_at"],
    )
    op.create_index(
        "ix_ai_usage_events_provider",
        "ai_usage_events",
        ["provider"],
    )
    op.create_index(
        "ix_ai_usage_events_job_id",
        "ai_usage_events",
        ["job_id"],
    )
    op.create_index(
        "ix_ai_usage_events_recording_id",
        "ai_usage_events",
        ["recording_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_usage_events_recording_id", table_name="ai_usage_events")
    op.drop_index("ix_ai_usage_events_job_id", table_name="ai_usage_events")
    op.drop_index("ix_ai_usage_events_provider", table_name="ai_usage_events")
    op.drop_index("ix_ai_usage_events_created_at", table_name="ai_usage_events")
    op.drop_table("ai_usage_events")
