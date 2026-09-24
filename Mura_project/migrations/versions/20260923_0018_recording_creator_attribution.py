"""Attribute recordings to the account that created them for quota enforcement.

Revision ID: 20260923_0018
Revises: 20260923_0017
Create Date: 2026-09-23

Existing rows remain NULL. New API-created recordings persist the authenticated
MURA user id. Account deletion clears attribution without deleting family audio.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260923_0018"
down_revision: str | None = "20260923_0017"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "recordings",
        sa.Column("created_by_user_id", sa.String(length=64), nullable=True),
    )
    op.create_foreign_key(
        "recordings_created_by_user_id_fkey",
        "recordings",
        "users",
        ["created_by_user_id"],
        ["user_id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_recordings_created_by_user_id",
        "recordings",
        ["created_by_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_recordings_created_by_user_id", table_name="recordings")
    op.drop_constraint(
        "recordings_created_by_user_id_fkey",
        "recordings",
        type_="foreignkey",
    )
    op.drop_column("recordings", "created_by_user_id")
