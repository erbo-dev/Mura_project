"""Create family invitations table with secure token hash and lifecycle audit.

Revision ID: 20260926_0019
Revises: 20260923_0018
Create Date: 2026-09-26

Supports secure family member invitations with bearer token hashing,
status lifecycle (pending/accepted/revoked/expired), expiration timestamps,
and full attribution for creator, acceptor, and revoker.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260926_0019"
down_revision: str | None = "20260923_0018"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "family_invitations",
        sa.Column("invitation_id", sa.String(length=64), nullable=False),
        sa.Column("family_id", sa.String(length=128), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=64), nullable=True),
        sa.Column("intended_role", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by_user_id", sa.String(length=64), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_user_id", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "intended_role IN ('editor', 'viewer')",
            name="ck_family_invitations_intended_role",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'revoked', 'expired')",
            name="ck_family_invitations_status",
        ),
        sa.ForeignKeyConstraint(
            ["family_id"],
            ["families.family_id"],
            name="fk_family_invitations_family_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.user_id"],
            name="fk_family_invitations_created_by_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by_user_id"],
            ["users.user_id"],
            name="fk_family_invitations_accepted_by_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"],
            ["users.user_id"],
            name="fk_family_invitations_revoked_by_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("invitation_id", name="pk_family_invitations"),
    )
    op.create_index(
        "ix_family_invitations_token_hash",
        "family_invitations",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_family_invitations_family_id",
        "family_invitations",
        ["family_id"],
        unique=False,
    )
    op.create_index(
        "ix_family_invitations_created_by",
        "family_invitations",
        ["created_by_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_family_invitations_status",
        "family_invitations",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_family_invitations_expires_at",
        "family_invitations",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_family_invitations_family_status",
        "family_invitations",
        ["family_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_family_invitations_family_status", table_name="family_invitations")
    op.drop_index("ix_family_invitations_expires_at", table_name="family_invitations")
    op.drop_index("ix_family_invitations_status", table_name="family_invitations")
    op.drop_index("ix_family_invitations_created_by", table_name="family_invitations")
    op.drop_index("ix_family_invitations_family_id", table_name="family_invitations")
    op.drop_index("ix_family_invitations_token_hash", table_name="family_invitations")
    op.drop_table("family_invitations")
