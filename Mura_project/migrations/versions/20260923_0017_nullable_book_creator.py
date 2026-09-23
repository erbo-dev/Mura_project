"""Preserve family books when their creator deletes their MURA account.

Revision ID: 20260923_0017
Revises: 20260923_0016
Create Date: 2026-09-23

A Book belongs to a family archive, not to the account that happened to create
it. Account deletion therefore nulls creator attribution rather than deleting
the Book or blocking forever on a RESTRICT foreign key.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260923_0017"
down_revision: str | None = "20260923_0016"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_constraint(
        "books_created_by_user_id_fkey",
        "books",
        type_="foreignkey",
    )
    op.alter_column(
        "books",
        "created_by_user_id",
        existing_type=sa.String(length=64),
        nullable=True,
    )
    op.create_foreign_key(
        "books_created_by_user_id_fkey",
        "books",
        "users",
        ["created_by_user_id"],
        ["user_id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    # Downgrade is valid only if no account deletion left NULL creator
    # attribution. Refuse implicitly through the NOT NULL conversion rather
    # than inventing a creator.
    op.drop_constraint(
        "books_created_by_user_id_fkey",
        "books",
        type_="foreignkey",
    )
    op.alter_column(
        "books",
        "created_by_user_id",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.create_foreign_key(
        "books_created_by_user_id_fkey",
        "books",
        "users",
        ["created_by_user_id"],
        ["user_id"],
        ondelete="RESTRICT",
    )
