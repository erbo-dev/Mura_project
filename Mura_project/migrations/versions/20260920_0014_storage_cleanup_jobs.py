"""Durable storage cleanup queue.

Revision ID: 20260920_0014
Revises: 20260918_0013
Create Date: 2026-09-20

The queue deliberately has no foreign key to recordings/books/families. Cleanup
must survive deletion of the relational resource whose physical object is being
erased.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260920_0014"
down_revision: str | None = "20260918_0013"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "storage_cleanup_jobs",
        sa.Column("cleanup_job_id", sa.String(length=64), primary_key=True),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("storage_kind", sa.String(length=32), nullable=False),
        sa.Column("storage_backend", sa.String(length=32), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "storage_kind",
            "storage_backend",
            "storage_key",
            name="uq_storage_cleanup_object",
        ),
    )
    op.create_index("ix_storage_cleanup_jobs_status", "storage_cleanup_jobs", ["status"])
    op.create_index(
        "ix_storage_cleanup_jobs_next_attempt_at",
        "storage_cleanup_jobs",
        ["next_attempt_at"],
    )
    op.create_index(
        "ix_storage_cleanup_jobs_lease_owner",
        "storage_cleanup_jobs",
        ["lease_owner"],
    )
    op.create_index(
        "ix_storage_cleanup_jobs_lease_expires_at",
        "storage_cleanup_jobs",
        ["lease_expires_at"],
    )
    op.create_index(
        "ix_storage_cleanup_jobs_status_next_attempt",
        "storage_cleanup_jobs",
        ["status", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_storage_cleanup_jobs_status_next_attempt",
        table_name="storage_cleanup_jobs",
    )
    op.drop_index(
        "ix_storage_cleanup_jobs_lease_expires_at",
        table_name="storage_cleanup_jobs",
    )
    op.drop_index(
        "ix_storage_cleanup_jobs_lease_owner",
        table_name="storage_cleanup_jobs",
    )
    op.drop_index(
        "ix_storage_cleanup_jobs_next_attempt_at",
        table_name="storage_cleanup_jobs",
    )
    op.drop_index("ix_storage_cleanup_jobs_status", table_name="storage_cleanup_jobs")
    op.drop_table("storage_cleanup_jobs")
