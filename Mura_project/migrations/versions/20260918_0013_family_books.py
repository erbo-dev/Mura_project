"""Family book generation: books, snapshots, plans, chapters, exports, jobs.

Revision ID: 20260918_0013
Revises: 20260917_0012
Create Date: 2026-09-18

Book work gets its own job table rather than reusing ``processing_jobs``.
``processing_jobs.recording_id`` is NOT NULL and its status vocabulary
(``transcribing``, ``cleaning``, ``extracting``, ``resolving``) describes ASR
stages a book has none of. Making that column nullable to fit books in would
weaken the recording queue's own invariant, so ``book_jobs`` mirrors the column
set instead: the claim algorithm, lease, heartbeat and crash recovery are
identical; only the row differs.

``book_source_snapshots.book_id`` is UNIQUE on purpose. A snapshot is the frozen
grounding a book was written from, and a retry that produced a second one would
silently change what an already-generated book is explainable against.

The AI usage ledger gains ``book_id`` and ``chapter_number`` so the cost of one
book -- and of one chapter inside it -- can be attributed without duplicating the
existing accounting.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

revision: str = "20260918_0013"
down_revision: str | None = "20260917_0012"
branch_labels: str | None = None
depends_on: str | None = None

JSON_VALUE = JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    _create_books()
    _create_source_snapshots()
    _create_plans()
    _create_chapters()
    _create_continuity()
    _create_exports()
    _create_book_jobs()
    _extend_ai_usage_ledger()


def _create_books() -> None:
    op.create_table(
        "books",
        sa.Column("book_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "family_id",
            sa.String(length=128),
            sa.ForeignKey("families.family_id", ondelete="CASCADE"),
            nullable=False,
        ),
        # RESTRICT: deleting an account must never cascade into a family's book.
        sa.Column(
            "created_by_user_id",
            sa.String(length=64),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=400), nullable=False),
        sa.Column("subtitle", sa.String(length=400), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("output_language", sa.String(length=8), nullable=False),
        sa.Column("target_word_count", sa.Integer(), nullable=False),
        sa.Column("source_snapshot_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("chapters_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chapters_approved", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_chapter_number", sa.Integer(), nullable=True),
        sa.Column("word_count", sa.Integer(), nullable=False, server_default="0"),
        # A regeneration never overwrites: it is a new book pointing at its parent.
        sa.Column(
            "supersedes_book_id",
            sa.String(length=64),
            sa.ForeignKey("books.book_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_books_family_id", "books", ["family_id"])
    op.create_index("ix_books_status", "books", ["status"])
    op.create_index("ix_books_created_at", "books", ["created_at"])
    op.create_index("ix_books_supersedes_book_id", "books", ["supersedes_book_id"])


def _create_source_snapshots() -> None:
    op.create_table(
        "book_source_snapshots",
        sa.Column("snapshot_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "book_id",
            sa.String(length=64),
            sa.ForeignKey("books.book_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("family_id", sa.String(length=128), nullable=False),
        sa.Column("compiler_version", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", JSON_VALUE, nullable=False),
        sa.Column("manifest", JSON_VALUE, nullable=False),
        sa.Column("source_recording_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_story_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_claim_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        # One snapshot per book. A retry cannot compile a second basis.
        sa.UniqueConstraint("book_id", name="uq_book_source_snapshot_book"),
    )
    op.create_index("ix_book_source_snapshots_family_id", "book_source_snapshots", ["family_id"])


def _create_plans() -> None:
    op.create_table(
        "book_plans",
        sa.Column("plan_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "book_id",
            sa.String(length=64),
            sa.ForeignKey("books.book_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plan_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("blueprint", JSON_VALUE, nullable=False),
        sa.Column("validation_report", JSON_VALUE, nullable=False),
        sa.Column("central_theme", sa.Text(), nullable=True),
        sa.Column("narrative_voice", sa.String(length=32), nullable=True),
        sa.Column("material_anchor", sa.String(length=256), nullable=True),
        sa.Column("chapter_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("target_total_words", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("planner_prompt_version", sa.String(length=64), nullable=False),
        sa.Column("planner_model", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("book_id", name="uq_book_plan_book"),
    )


def _create_chapters() -> None:
    op.create_table(
        "book_chapters",
        sa.Column("chapter_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "book_id",
            sa.String(length=64),
            sa.ForeignKey("books.book_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chapter_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=400), nullable=True),
        sa.Column("target_word_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("plan", JSON_VALUE, nullable=False),
        sa.Column("draft_text", sa.Text(), nullable=True),
        sa.Column("final_text", sa.Text(), nullable=True),
        sa.Column("word_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("review", JSON_VALUE, nullable=True),
        sa.Column("gate_report", JSON_VALUE, nullable=True),
        sa.Column("person_ids", JSON_VALUE, nullable=False, server_default="[]"),
        sa.Column("place_names", JSON_VALUE, nullable=False, server_default="[]"),
        sa.Column("claim_ids", JSON_VALUE, nullable=False, server_default="[]"),
        sa.Column("source_recording_ids", JSON_VALUE, nullable=False, server_default="[]"),
        sa.Column("source_story_ids", JSON_VALUE, nullable=False, server_default="[]"),
        sa.Column("evidence_refs", JSON_VALUE, nullable=False, server_default="[]"),
        sa.Column("writer_prompt_version", sa.String(length=64), nullable=True),
        sa.Column("reviewer_prompt_version", sa.String(length=64), nullable=True),
        sa.Column("writer_model", sa.String(length=64), nullable=True),
        sa.Column("reviewer_model", sa.String(length=64), nullable=True),
        sa.Column("repair_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        # The resume key. A reclaimed worker finds chapter N here instead of
        # writing a second copy of it.
        sa.UniqueConstraint("book_id", "chapter_number", name="uq_book_chapter_number"),
    )
    op.create_index("ix_book_chapters_book_id", "book_chapters", ["book_id"])
    op.create_index("ix_book_chapters_status", "book_chapters", ["status"])


def _create_continuity() -> None:
    op.create_table(
        "book_continuity_states",
        sa.Column("continuity_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "book_id",
            sa.String(length=64),
            sa.ForeignKey("books.book_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("after_chapter_number", sa.Integer(), nullable=False),
        sa.Column("state", JSON_VALUE, nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("book_id", "after_chapter_number", name="uq_book_continuity_position"),
    )


def _create_exports() -> None:
    op.create_table(
        "book_exports",
        sa.Column("export_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "book_id",
            sa.String(length=64),
            sa.ForeignKey("books.book_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("format", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=True),
        sa.Column("storage_backend", sa.String(length=32), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("chapter_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("word_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("engine", sa.String(length=32), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("book_id", "format", name="uq_book_export_format"),
    )
    op.create_index("ix_book_exports_book_id", "book_exports", ["book_id"])


def _create_book_jobs() -> None:
    """The book queue.

    Column set deliberately identical to ``processing_jobs`` so the same claim,
    lease and crash-recovery invariants hold without a second mental model.
    """

    op.create_table(
        "book_jobs",
        sa.Column("job_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "book_id",
            sa.String(length=64),
            sa.ForeignKey("books.book_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("family_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_book_jobs_book_id", "book_jobs", ["book_id"])
    op.create_index("ix_book_jobs_status", "book_jobs", ["status"])
    op.create_index("ix_book_jobs_lease_owner", "book_jobs", ["lease_owner"])
    # The claim predicate reads exactly this pair.
    op.create_index("ix_book_jobs_status_next_attempt", "book_jobs", ["status", "next_attempt_at"])


def _extend_ai_usage_ledger() -> None:
    """Correlate book spend without duplicating the ledger.

    ``book_id`` and ``chapter_number`` are the smallest extension that makes cost
    attributable per book and per chapter. Still metadata only: no prompt, no
    chapter text and no evidence quote ever reaches this table.
    """

    op.add_column("ai_usage_events", sa.Column("book_id", sa.String(length=64), nullable=True))
    op.add_column("ai_usage_events", sa.Column("chapter_number", sa.Integer(), nullable=True))
    op.create_index("ix_ai_usage_events_book_id", "ai_usage_events", ["book_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_usage_events_book_id", table_name="ai_usage_events")
    op.drop_column("ai_usage_events", "chapter_number")
    op.drop_column("ai_usage_events", "book_id")

    op.drop_index("ix_book_jobs_status_next_attempt", table_name="book_jobs")
    op.drop_index("ix_book_jobs_lease_owner", table_name="book_jobs")
    op.drop_index("ix_book_jobs_status", table_name="book_jobs")
    op.drop_index("ix_book_jobs_book_id", table_name="book_jobs")
    op.drop_table("book_jobs")

    op.drop_index("ix_book_exports_book_id", table_name="book_exports")
    op.drop_table("book_exports")

    op.drop_table("book_continuity_states")

    op.drop_index("ix_book_chapters_status", table_name="book_chapters")
    op.drop_index("ix_book_chapters_book_id", table_name="book_chapters")
    op.drop_table("book_chapters")

    op.drop_table("book_plans")

    op.drop_index("ix_book_source_snapshots_family_id", table_name="book_source_snapshots")
    op.drop_table("book_source_snapshots")

    op.drop_index("ix_books_supersedes_book_id", table_name="books")
    op.drop_index("ix_books_created_at", table_name="books")
    op.drop_index("ix_books_status", table_name="books")
    op.drop_index("ix_books_family_id", table_name="books")
    op.drop_table("books")
