"""Persist complete structured Family Book chapter drafts.

Revision ID: 20260923_0015
Revises: 20260920_0014
Create Date: 2026-09-23

The nullable JSON payload is additive for existing books. New worker attempts
persist the full ChapterDraft so a crash/reclaim resumes with the same semantic
inputs to deterministic gates. Legacy rows with only draft_text remain readable;
the new prose-derived gates do not depend on missing writer self-report metadata.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

revision: str = "20260923_0015"
down_revision: str | None = "20260920_0014"
branch_labels: str | None = None
depends_on: str | None = None

JSON_VALUE = JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    op.add_column(
        "book_chapters",
        sa.Column("draft_payload", JSON_VALUE, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("book_chapters", "draft_payload")
