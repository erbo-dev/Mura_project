"""Durable book persistence and its job queue.

Split out from `storage/database.py` for the same reason the domain is split: a
recording job and a book job are different things that happen to share an
algorithm. `processing_jobs.recording_id` is NOT NULL and its stages describe
ASR, so putting books there would have meant weakening the recording queue's own
invariant to make room. `book_jobs` mirrors the column set instead, and the
claim/lease/finalize code below is deliberately a near-copy of the recording
implementation rather than an abstraction over it -- one shared base class across
two lifecycles is how a change made for books quietly alters recordings.

Every family-scoped read carries `family_id` inside the query predicate. A book
belonging to another family is therefore never selected and then compared; it is
simply not found, which is what keeps an id from becoming a probe.

Source eligibility is a persisted fact, not a guess: a recording counts only once
its job is `completed` **and** a `pipeline_results` row exists. A book cannot be
written from work that has not finished grounding itself.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    and_,
    delete,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from mura.domain.book_models import (
    TERMINAL_BOOK_JOB_STATUSES,
    TERMINAL_BOOK_STATUSES,
    BookJobStatus as BookJobStatusEnum,
    BookStage as BookStageEnum,
    BookStatus as BookStatusEnum,
    ChapterStatus as ChapterStatusEnum,
    CompiledSnapshot,
    ExportFormat,
)
from mura.jobs import JobStatus
from mura.leases import LeaseOwnershipLost
from mura.storage.book_artifacts import build_book_storage_key
from mura.storage.cleanup import (
    StorageCleanupRepository,
    StorageCleanupResourceType,
    StorageKind,
)
from mura.storage.database import (
    JSON_VALUE,
    Base,
    Database,
    PipelineResultRow,
    ProcessingJobRow,
    RecordingRow,
    utcnow,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult

BOOK_ID_PREFIX = "book_"
SNAPSHOT_ID_PREFIX = "snap_"
PLAN_ID_PREFIX = "plan_"
CHAPTER_ID_PREFIX = "chp_"
CONTINUITY_ID_PREFIX = "cnt_"
EXPORT_ID_PREFIX = "exp_"
BOOK_JOB_ID_PREFIX = "bjob_"


def new_book_id() -> str:
    return f"{BOOK_ID_PREFIX}{uuid.uuid4().hex}"


def new_snapshot_id() -> str:
    return f"{SNAPSHOT_ID_PREFIX}{uuid.uuid4().hex}"


def new_plan_id() -> str:
    return f"{PLAN_ID_PREFIX}{uuid.uuid4().hex}"


def new_chapter_id() -> str:
    return f"{CHAPTER_ID_PREFIX}{uuid.uuid4().hex}"


def new_continuity_id() -> str:
    return f"{CONTINUITY_ID_PREFIX}{uuid.uuid4().hex}"


def new_export_id() -> str:
    return f"{EXPORT_ID_PREFIX}{uuid.uuid4().hex}"


def new_book_job_id() -> str:
    return f"{BOOK_JOB_ID_PREFIX}{uuid.uuid4().hex}"


def is_book_id(value: str) -> bool:
    """Canonical shape check, mirroring the frontend proxy's allowlist."""
    if not value.startswith(BOOK_ID_PREFIX):
        return False
    suffix = value[len(BOOK_ID_PREFIX) :]
    return len(suffix) == 32 and all(char in "0123456789abcdef" for char in suffix)


# =====================================================================
# ORM Models (matching Alembic revision 20260918_0013 exactly)
# =====================================================================


class BookRow(Base):
    __tablename__ = "books"

    book_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    family_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("families.family_id", ondelete="CASCADE"), index=True
    )
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(400))
    subtitle: Mapped[str | None] = mapped_column(String(400), nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    stage: Mapped[str] = mapped_column(String(64))
    output_language: Mapped[str] = mapped_column(String(8))
    target_word_count: Mapped[int] = mapped_column(Integer)
    source_snapshot_version: Mapped[int] = mapped_column(Integer, default=1)
    chapters_total: Mapped[int] = mapped_column(Integer, default=0)
    chapters_approved: Mapped[int] = mapped_column(Integer, default=0)
    current_chapter_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    supersedes_book_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("books.book_id", ondelete="SET NULL"), nullable=True, index=True
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class BookSourceSnapshotRow(Base):
    __tablename__ = "book_source_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    book_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("books.book_id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    family_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    compiler_version: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    source_recording_count: Mapped[int] = mapped_column(Integer, default=0)
    source_story_count: Mapped[int] = mapped_column(Integer, default=0)
    source_claim_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BookPlanRow(Base):
    __tablename__ = "book_plans"

    plan_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    book_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("books.book_id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    plan_version: Mapped[int] = mapped_column(Integer, default=1)
    blueprint: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    validation_report: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    central_theme: Mapped[str | None] = mapped_column(Text, nullable=True)
    narrative_voice: Mapped[str | None] = mapped_column(String(32), nullable=True)
    material_anchor: Mapped[str | None] = mapped_column(String(256), nullable=True)
    chapter_count: Mapped[int] = mapped_column(Integer, default=0)
    target_total_words: Mapped[int] = mapped_column(Integer, default=0)
    planner_prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    planner_model: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class BookChapterRow(Base):
    __tablename__ = "book_chapters"
    __table_args__ = (
        UniqueConstraint("book_id", "chapter_number", name="uq_book_chapter_number"),
    )

    chapter_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    book_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("books.book_id", ondelete="CASCADE"), index=True, nullable=False
    )
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(400), nullable=True)
    target_word_count: Mapped[int] = mapped_column(Integer, default=0)
    plan: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    draft_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON_VALUE, nullable=True)
    final_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    review: Mapped[dict[str, Any] | None] = mapped_column(JSON_VALUE, nullable=True)
    gate_report: Mapped[dict[str, Any] | None] = mapped_column(JSON_VALUE, nullable=True)
    person_ids: Mapped[list[str]] = mapped_column(JSON_VALUE, default=list)
    place_names: Mapped[list[str]] = mapped_column(JSON_VALUE, default=list)
    claim_ids: Mapped[list[str]] = mapped_column(JSON_VALUE, default=list)
    source_recording_ids: Mapped[list[str]] = mapped_column(JSON_VALUE, default=list)
    source_story_ids: Mapped[list[str]] = mapped_column(JSON_VALUE, default=list)
    evidence_refs: Mapped[list[str]] = mapped_column(JSON_VALUE, default=list)
    writer_prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewer_prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    writer_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewer_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    repair_attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class BookContinuityStateRow(Base):
    __tablename__ = "book_continuity_states"
    __table_args__ = (
        UniqueConstraint("book_id", "after_chapter_number", name="uq_book_continuity_position"),
    )

    continuity_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    book_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("books.book_id", ondelete="CASCADE"), nullable=False
    )
    after_chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class BookExportRow(Base):
    __tablename__ = "book_exports"
    __table_args__ = (
        UniqueConstraint("book_id", "format", name="uq_book_export_format"),
    )

    export_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    book_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("books.book_id", ondelete="CASCADE"), index=True, nullable=False
    )
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_backend: Mapped[str | None] = mapped_column(String(32), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    chapter_count: Mapped[int] = mapped_column(Integer, default=0)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    engine: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class BookJobRow(Base):
    __tablename__ = "book_jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    book_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("books.book_id", ondelete="CASCADE"), index=True, nullable=False
    )
    family_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


def _guard_worker_write(
    session: Session,
    *,
    book_id: str,
    job_id: str | None,
    lease_owner: str | None,
) -> None:
    """Fence a Book worker mutation against lease loss and deletion.

    Lock ordering is always BookRow -> BookJobRow, matching Book deletion's
    BookRow-first durability boundary. Administrative/non-worker callers omit
    both job_id and lease_owner; worker callers must provide both.
    """

    if job_id is None and lease_owner is None:
        return
    if job_id is None or lease_owner is None:
        raise ValueError("job_id and lease_owner must be provided together")

    book = session.scalar(
        select(BookRow).where(BookRow.book_id == book_id).with_for_update()
    )
    if book is None:
        raise LookupError(f"unknown book: {book_id}")

    job = session.scalar(
        select(BookJobRow)
        .where(
            BookJobRow.job_id == job_id,
            BookJobRow.book_id == book_id,
        )
        .with_for_update()
    )
    if job is None:
        raise LookupError(f"unknown book job: {job_id}")
    if job.lease_owner != lease_owner:
        raise LeaseOwnershipLost(job_id)


# =====================================================================
# Source Eligibility Query Helper
# =====================================================================


def get_eligible_recordings(
    session: Session,
    *,
    family_id: str,
    recording_ids: list[str] | None = None,
) -> list[RecordingRow]:
    """Retrieve recordings that are eligible for book generation.

    A recording is eligible ONLY when:
    1. It belongs to family_id.
    2. It has an associated ProcessingJobRow with status == 'completed'.
    3. It has an associated PipelineResultRow.
    """
    stmt = (
        select(RecordingRow)
        .join(ProcessingJobRow, ProcessingJobRow.recording_id == RecordingRow.recording_id)
        .join(PipelineResultRow, PipelineResultRow.recording_id == RecordingRow.recording_id)
        .where(
            RecordingRow.family_id == family_id,
            ProcessingJobRow.status == JobStatus.COMPLETED.value,
        )
    )
    if recording_ids is not None:
        stmt = stmt.where(RecordingRow.recording_id.in_(recording_ids))

    stmt = stmt.order_by(RecordingRow.created_at.asc())
    return list(session.scalars(stmt).all())


# =====================================================================
# Repositories
# =====================================================================


class BookRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_book(
        self,
        *,
        book_id: str | None = None,
        family_id: str,
        created_by_user_id: str,
        title: str,
        subtitle: str | None = None,
        output_language: str,
        target_word_count: int,
        supersedes_book_id: str | None = None,
        source_snapshot_version: int = 1,
    ) -> BookRow:
        now = utcnow()
        bid = book_id or new_book_id()
        row = BookRow(
            book_id=bid,
            family_id=family_id,
            created_by_user_id=created_by_user_id,
            title=title,
            subtitle=subtitle,
            status=BookStatusEnum.QUEUED.value,
            stage=BookStageEnum.PREPARING_SOURCES.value,
            output_language=output_language,
            target_word_count=target_word_count,
            source_snapshot_version=source_snapshot_version,
            chapters_total=0,
            chapters_approved=0,
            current_chapter_number=None,
            word_count=0,
            supersedes_book_id=supersedes_book_id,
            created_at=now,
            updated_at=now,
        )
        with self.database.session_factory.begin() as session:
            session.add(row)
            session.flush()
            session.expunge(row)
            return row

    def get_book(self, *, family_id: str, book_id: str) -> BookRow | None:
        with self.database.session_factory() as session:
            stmt = select(BookRow).where(
                BookRow.family_id == family_id,
                BookRow.book_id == book_id,
            )
            return session.scalar(stmt)

    def delete_family_book(
        self,
        *,
        family_id: str,
        book_id: str,
        default_storage_backend: str = "local",
        cleanup_repository: StorageCleanupRepository | None = None,
        cleanup_max_attempts: int = 8,
    ) -> list[str] | None:
        """Delete a Book and persist physical erasure in the same transaction.

        ExportService takes the same Book row lock at its artifact durability
        boundary. Either deletion wins and prevents the upload, or export wins
        first and deletion observes/queues the resulting object.
        """

        cleanup_repo = cleanup_repository or StorageCleanupRepository(self.database)
        with self.database.session_factory.begin() as session:
            book = session.scalar(
                select(BookRow)
                .where(
                    BookRow.book_id == book_id,
                    BookRow.family_id == family_id,
                )
                .with_for_update()
            )
            if book is None:
                return None

            book.cancel_requested_at = utcnow()
            exports = list(
                session.scalars(
                    select(BookExportRow).where(BookExportRow.book_id == book_id)
                ).all()
            )
            cleanup_items: list[dict[str, str]] = []
            for export in exports:
                if not export.storage_key:
                    continue
                cleanup_items.append(
                    {
                        "resource_type": (
                            StorageCleanupResourceType.BOOK_PDF.value
                            if export.format == "pdf"
                            else StorageCleanupResourceType.BOOK_EPUB.value
                        ),
                        "storage_kind": StorageKind.BOOK_ARTIFACT.value,
                        "storage_backend": export.storage_backend or default_storage_backend,
                        "storage_key": export.storage_key,
                    }
                )

            # Canonical keys are deterministic. Queue both formats even when
            # metadata is missing (e.g. a crash after upload but before commit).
            for export_format, resource_type in (
                (ExportFormat.PDF, StorageCleanupResourceType.BOOK_PDF),
                (ExportFormat.EPUB, StorageCleanupResourceType.BOOK_EPUB),
            ):
                cleanup_items.append(
                    {
                        "resource_type": resource_type.value,
                        "storage_kind": StorageKind.BOOK_ARTIFACT.value,
                        "storage_backend": default_storage_backend,
                        "storage_key": build_book_storage_key(
                            family_id=family_id,
                            book_id=book_id,
                            export_format=export_format,
                        ),
                    }
                )

            cleanup_rows = cleanup_repo.enqueue_many(
                cleanup_items,
                max_attempts=cleanup_max_attempts,
                session=session,
            )

            session.execute(delete(BookExportRow).where(BookExportRow.book_id == book_id))
            session.execute(
                delete(BookContinuityStateRow).where(
                    BookContinuityStateRow.book_id == book_id
                )
            )
            session.execute(delete(BookChapterRow).where(BookChapterRow.book_id == book_id))
            session.execute(delete(BookPlanRow).where(BookPlanRow.book_id == book_id))
            session.execute(
                delete(BookSourceSnapshotRow).where(BookSourceSnapshotRow.book_id == book_id)
            )
            session.execute(delete(BookJobRow).where(BookJobRow.book_id == book_id))
            session.delete(book)
            return [row.cleanup_job_id for row in cleanup_rows]

    def get_book_unscoped(self, book_id: str) -> BookRow | None:
        with self.database.session_factory() as session:
            return session.get(BookRow, book_id)

    def list_books(
        self,
        *,
        family_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[BookRow], int]:
        with self.database.session_factory() as session:
            total = session.scalar(
                select(func.count()).select_from(BookRow).where(BookRow.family_id == family_id)
            ) or 0
            items = list(
                session.scalars(
                    select(BookRow)
                    .where(BookRow.family_id == family_id)
                    .order_by(BookRow.created_at.desc())
                    .limit(limit)
                    .offset(offset)
                ).all()
            )
            return items, total

    def update_stage(
        self,
        book_id: str,
        *,
        stage: str,
        status: str | None = None,
        current_chapter_number: int | None = None,
        chapters_total: int | None = None,
        chapters_approved: int | None = None,
        word_count: int | None = None,
        job_id: str | None = None,
        lease_owner: str | None = None,
    ) -> None:
        now = utcnow()
        with self.database.session_factory.begin() as session:
            _guard_worker_write(
                session,
                book_id=book_id,
                job_id=job_id,
                lease_owner=lease_owner,
            )
            book = session.get(BookRow, book_id)
            if book is None:
                raise LookupError(f"unknown book: {book_id}")
            book.stage = stage
            if status is not None:
                book.status = status
            if current_chapter_number is not None:
                book.current_chapter_number = current_chapter_number
            if chapters_total is not None:
                book.chapters_total = chapters_total
            if chapters_approved is not None:
                book.chapters_approved = chapters_approved
            if word_count is not None:
                book.word_count = word_count
            book.updated_at = now

    def request_cancel(self, *, family_id: str, book_id: str) -> bool:
        now = utcnow()
        with self.database.session_factory.begin() as session:
            book = session.scalar(
                select(BookRow).where(
                    BookRow.family_id == family_id,
                    BookRow.book_id == book_id,
                )
            )
            if book is None:
                return False
            if book.status in TERMINAL_BOOK_STATUSES:
                return False
            book.cancel_requested_at = now
            book.updated_at = now
            return True

    def is_cancel_requested(self, book_id: str) -> bool:
        with self.database.session_factory() as session:
            book = session.get(BookRow, book_id)
            if book is None:
                return False
            return book.cancel_requested_at is not None

    def complete_book(self, book_id: str, *, word_count: int) -> None:
        now = utcnow()
        with self.database.session_factory.begin() as session:
            book = session.get(BookRow, book_id)
            if book is None:
                raise LookupError(f"unknown book: {book_id}")
            book.status = BookStatusEnum.COMPLETED.value
            book.stage = BookStageEnum.COMPLETED.value
            book.word_count = word_count
            book.completed_at = now
            book.updated_at = now

    def fail_book(self, book_id: str, *, error_code: str, error_detail: str) -> None:
        now = utcnow()
        with self.database.session_factory.begin() as session:
            book = session.get(BookRow, book_id)
            if book is None:
                raise LookupError(f"unknown book: {book_id}")
            book.status = BookStatusEnum.FAILED.value
            book.stage = BookStageEnum.FAILED.value
            book.error_code = error_code
            book.error_detail = error_detail
            book.updated_at = now

    def cancel_book(self, book_id: str) -> None:
        now = utcnow()
        with self.database.session_factory.begin() as session:
            book = session.get(BookRow, book_id)
            if book is None:
                raise LookupError(f"unknown book: {book_id}")
            book.status = BookStatusEnum.CANCELLED.value
            book.stage = BookStageEnum.CANCELLED.value
            book.updated_at = now


@dataclass(frozen=True)
class QueuedBookResult:
    book: BookRow
    snapshot: BookSourceSnapshotRow
    job: BookJobRow


class BookCreationRepository:
    """Create a Book, immutable source snapshot, and execution job atomically."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def create_queued_book(
        self,
        *,
        family_id: str,
        created_by_user_id: str,
        title: str,
        subtitle: str | None = None,
        output_language: str,
        target_word_count: int,
        compiled_snapshot: CompiledSnapshot,
        supersedes_book_id: str | None = None,
        source_snapshot_version: int = 1,
        session: Session | None = None,
    ) -> QueuedBookResult:
        now = utcnow()
        book_id = new_book_id()
        snapshot_id = new_snapshot_id()
        job_id = new_book_job_id()

        book = BookRow(
            book_id=book_id,
            family_id=family_id,
            created_by_user_id=created_by_user_id,
            title=title,
            subtitle=subtitle,
            status=BookStatusEnum.QUEUED.value,
            stage=BookStageEnum.PREPARING_SOURCES.value,
            output_language=output_language,
            target_word_count=target_word_count,
            source_snapshot_version=source_snapshot_version,
            chapters_total=0,
            chapters_approved=0,
            current_chapter_number=None,
            word_count=0,
            supersedes_book_id=supersedes_book_id,
            created_at=now,
            updated_at=now,
        )

        snapshot_model = compiled_snapshot.snapshot
        snapshot = BookSourceSnapshotRow(
            snapshot_id=snapshot_id,
            book_id=book_id,
            family_id=family_id,
            compiler_version=snapshot_model.compiler_version,
            content_hash=compiled_snapshot.content_hash,
            payload=snapshot_model.model_dump(mode="json"),
            manifest=snapshot_model.manifest.model_dump(mode="json"),
            source_recording_count=len(snapshot_model.manifest.source_recording_ids),
            source_story_count=len(snapshot_model.manifest.source_story_ids),
            source_claim_count=len(snapshot_model.manifest.source_claim_ids),
            created_at=now,
        )

        job = BookJobRow(
            job_id=job_id,
            book_id=book_id,
            family_id=family_id,
            status=BookJobStatusEnum.QUEUED.value,
            stage=BookStageEnum.PREPARING_SOURCES.value,
            attempts=0,
            max_attempts=3,
            next_attempt_at=now,
            created_at=now,
            updated_at=now,
        )

        def persist(target: Session) -> None:
            # These rows are connected by scalar FK ids rather than ORM
            # relationships, so SQLAlchemy has no dependency graph it can use
            # to order all three INSERTs. PostgreSQL checks the FKs immediately:
            # make the parent durable inside the transaction before inserting
            # the snapshot and queue row that reference it.
            target.add(book)
            target.flush()
            target.add(snapshot)
            target.add(job)
            target.flush()
            target.expunge(book)
            target.expunge(snapshot)
            target.expunge(job)

        if session is None:
            with self.database.session_factory.begin() as owned_session:
                persist(owned_session)
        else:
            # The caller owns commit/rollback. This lets the family quota row
            # lock remain held through Book + Snapshot + Job insertion.
            persist(session)

        return QueuedBookResult(book=book, snapshot=snapshot, job=job)


class BookSourceSnapshotRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def save_snapshot(
        self,
        *,
        snapshot_id: str | None = None,
        book_id: str,
        family_id: str,
        compiler_version: str,
        content_hash: str,
        payload: dict[str, Any],
        manifest: dict[str, Any],
        source_recording_count: int = 0,
        source_story_count: int = 0,
        source_claim_count: int = 0,
    ) -> BookSourceSnapshotRow:
        now = utcnow()
        sid = snapshot_id or new_snapshot_id()
        row = BookSourceSnapshotRow(
            snapshot_id=sid,
            book_id=book_id,
            family_id=family_id,
            compiler_version=compiler_version,
            content_hash=content_hash,
            payload=payload,
            manifest=manifest,
            source_recording_count=source_recording_count,
            source_story_count=source_story_count,
            source_claim_count=source_claim_count,
            created_at=now,
        )
        with self.database.session_factory.begin() as session:
            session.add(row)
            session.flush()
            session.expunge(row)
            return row

    def get_snapshot(self, book_id: str) -> BookSourceSnapshotRow | None:
        with self.database.session_factory() as session:
            stmt = select(BookSourceSnapshotRow).where(BookSourceSnapshotRow.book_id == book_id)
            return session.scalar(stmt)


class BookPlanRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def save_plan(
        self,
        *,
        plan_id: str | None = None,
        book_id: str,
        blueprint: dict[str, Any],
        validation_report: dict[str, Any],
        central_theme: str | None = None,
        narrative_voice: str | None = None,
        material_anchor: str | None = None,
        chapter_count: int = 0,
        target_total_words: int = 0,
        planner_prompt_version: str,
        planner_model: str,
        plan_version: int = 1,
        job_id: str | None = None,
        lease_owner: str | None = None,
    ) -> BookPlanRow:
        now = utcnow()
        pid = plan_id or new_plan_id()
        with self.database.session_factory.begin() as session:
            _guard_worker_write(
                session,
                book_id=book_id,
                job_id=job_id,
                lease_owner=lease_owner,
            )
            existing = session.scalar(
                select(BookPlanRow).where(BookPlanRow.book_id == book_id)
            )
            if existing is None:
                row = BookPlanRow(
                    plan_id=pid,
                    book_id=book_id,
                    plan_version=plan_version,
                    blueprint=blueprint,
                    validation_report=validation_report,
                    central_theme=central_theme,
                    narrative_voice=narrative_voice,
                    material_anchor=material_anchor,
                    chapter_count=chapter_count,
                    target_total_words=target_total_words,
                    planner_prompt_version=planner_prompt_version,
                    planner_model=planner_model,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                existing.plan_version = plan_version
                existing.blueprint = blueprint
                existing.validation_report = validation_report
                existing.central_theme = central_theme
                existing.narrative_voice = narrative_voice
                existing.material_anchor = material_anchor
                existing.chapter_count = chapter_count
                existing.target_total_words = target_total_words
                existing.planner_prompt_version = planner_prompt_version
                existing.planner_model = planner_model
                existing.updated_at = now
                row = existing
            session.flush()
            session.expunge(row)
            return row

    def get_plan(self, book_id: str) -> BookPlanRow | None:
        with self.database.session_factory() as session:
            stmt = select(BookPlanRow).where(BookPlanRow.book_id == book_id)
            return session.scalar(stmt)


class BookChapterRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_chapter_stubs(
        self,
        *,
        book_id: str,
        chapter_plans: list[dict[str, Any]],
        job_id: str | None = None,
        lease_owner: str | None = None,
    ) -> list[BookChapterRow]:
        now = utcnow()
        rows: list[BookChapterRow] = []
        with self.database.session_factory.begin() as session:
            _guard_worker_write(
                session,
                book_id=book_id,
                job_id=job_id,
                lease_owner=lease_owner,
            )
            for plan_dict in chapter_plans:
                ch_num = plan_dict.get("chapter_number", 1)
                existing = session.scalar(
                    select(BookChapterRow).where(
                        BookChapterRow.book_id == book_id,
                        BookChapterRow.chapter_number == ch_num,
                    )
                )
                if existing is not None:
                    rows.append(existing)
                    continue

                row = BookChapterRow(
                    chapter_id=new_chapter_id(),
                    book_id=book_id,
                    chapter_number=ch_num,
                    status=ChapterStatusEnum.PLANNED.value,
                    title=plan_dict.get("title", f"Глава {ch_num}"),
                    target_word_count=plan_dict.get("target_word_count", 0),
                    plan=plan_dict,
                    draft_text=None,
                    draft_payload=None,
                    final_text=None,
                    word_count=0,
                    review=None,
                    gate_report=None,
                    person_ids=plan_dict.get("person_ids", []),
                    place_names=plan_dict.get("place_names", []),
                    claim_ids=plan_dict.get("claim_ids", []),
                    source_recording_ids=plan_dict.get("source_recording_ids", []),
                    source_story_ids=plan_dict.get("source_story_ids", []),
                    evidence_refs=plan_dict.get("evidence_refs", []),
                    repair_attempts=0,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                rows.append(row)
            session.flush()
            for r in rows:
                session.expunge(r)
            return rows

    def get_chapter(self, *, book_id: str, chapter_number: int) -> BookChapterRow | None:
        with self.database.session_factory() as session:
            stmt = select(BookChapterRow).where(
                BookChapterRow.book_id == book_id,
                BookChapterRow.chapter_number == chapter_number,
            )
            return session.scalar(stmt)

    def list_chapters(self, *, book_id: str) -> list[BookChapterRow]:
        with self.database.session_factory() as session:
            stmt = (
                select(BookChapterRow)
                .where(BookChapterRow.book_id == book_id)
                .order_by(BookChapterRow.chapter_number.asc())
            )
            return list(session.scalars(stmt).all())

    def get_first_unfinished_chapter(self, *, book_id: str) -> BookChapterRow | None:
        with self.database.session_factory() as session:
            stmt = (
                select(BookChapterRow)
                .where(
                    BookChapterRow.book_id == book_id,
                    BookChapterRow.status != ChapterStatusEnum.APPROVED.value,
                )
                .order_by(BookChapterRow.chapter_number.asc())
                .limit(1)
            )
            return session.scalar(stmt)

    def update_chapter_draft(
        self,
        *,
        book_id: str,
        chapter_number: int,
        draft_text: str,
        word_count: int,
        writer_prompt_version: str,
        draft_payload: dict[str, Any] | None = None,
        writer_model: str,
        repair_attempts: int = 0,
        job_id: str | None = None,
        lease_owner: str | None = None,
    ) -> None:
        now = utcnow()
        with self.database.session_factory.begin() as session:
            _guard_worker_write(
                session,
                book_id=book_id,
                job_id=job_id,
                lease_owner=lease_owner,
            )
            stmt = select(BookChapterRow).where(
                BookChapterRow.book_id == book_id,
                BookChapterRow.chapter_number == chapter_number,
            )
            ch = session.scalar(stmt)
            if ch is None:
                raise LookupError(f"unknown chapter: {book_id} #{chapter_number}")
            ch.draft_text = draft_text
            ch.draft_payload = draft_payload
            ch.word_count = word_count
            ch.writer_prompt_version = writer_prompt_version
            ch.writer_model = writer_model
            ch.repair_attempts = repair_attempts
            ch.status = ChapterStatusEnum.REVIEWING.value
            ch.updated_at = now

    def update_chapter_review(
        self,
        *,
        book_id: str,
        chapter_number: int,
        status: str,
        review: dict[str, Any] | None,
        gate_report: dict[str, Any] | None,
        reviewer_prompt_version: str | None = None,
        reviewer_model: str | None = None,
        job_id: str | None = None,
        lease_owner: str | None = None,
    ) -> None:
        now = utcnow()
        with self.database.session_factory.begin() as session:
            _guard_worker_write(
                session,
                book_id=book_id,
                job_id=job_id,
                lease_owner=lease_owner,
            )
            stmt = select(BookChapterRow).where(
                BookChapterRow.book_id == book_id,
                BookChapterRow.chapter_number == chapter_number,
            )
            ch = session.scalar(stmt)
            if ch is None:
                raise LookupError(f"unknown chapter: {book_id} #{chapter_number}")
            ch.status = status
            ch.review = review
            ch.gate_report = gate_report
            if reviewer_prompt_version is not None:
                ch.reviewer_prompt_version = reviewer_prompt_version
            if reviewer_model is not None:
                ch.reviewer_model = reviewer_model
            ch.updated_at = now

    def approve_chapter(
        self,
        *,
        book_id: str,
        chapter_number: int,
        final_text: str,
        word_count: int,
        review: dict[str, Any] | None = None,
        gate_report: dict[str, Any] | None = None,
        job_id: str | None = None,
        lease_owner: str | None = None,
    ) -> None:
        now = utcnow()
        with self.database.session_factory.begin() as session:
            _guard_worker_write(
                session,
                book_id=book_id,
                job_id=job_id,
                lease_owner=lease_owner,
            )
            stmt = select(BookChapterRow).where(
                BookChapterRow.book_id == book_id,
                BookChapterRow.chapter_number == chapter_number,
            )
            ch = session.scalar(stmt)
            if ch is None:
                raise LookupError(f"unknown chapter: {book_id} #{chapter_number}")
            ch.status = ChapterStatusEnum.APPROVED.value
            ch.final_text = final_text
            ch.word_count = word_count
            if review is not None:
                ch.review = review
            if gate_report is not None:
                ch.gate_report = gate_report
            ch.approved_at = now
            ch.updated_at = now

    def fail_chapter(
        self,
        *,
        book_id: str,
        chapter_number: int,
        error_code: str,
        gate_report: dict[str, Any] | None = None,
        review: dict[str, Any] | None = None,
        job_id: str | None = None,
        lease_owner: str | None = None,
    ) -> None:
        now = utcnow()
        with self.database.session_factory.begin() as session:
            _guard_worker_write(
                session,
                book_id=book_id,
                job_id=job_id,
                lease_owner=lease_owner,
            )
            stmt = select(BookChapterRow).where(
                BookChapterRow.book_id == book_id,
                BookChapterRow.chapter_number == chapter_number,
            )
            ch = session.scalar(stmt)
            if ch is None:
                raise LookupError(f"unknown chapter: {book_id} #{chapter_number}")
            ch.status = ChapterStatusEnum.FAILED.value
            ch.error_code = error_code
            if gate_report is not None:
                ch.gate_report = gate_report
            if review is not None:
                ch.review = review
            ch.updated_at = now


class BookContinuityRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def save_continuity(
        self,
        *,
        continuity_id: str | None = None,
        book_id: str,
        after_chapter_number: int,
        state: dict[str, Any],
        prompt_version: str,
        model: str,
        job_id: str | None = None,
        lease_owner: str | None = None,
    ) -> BookContinuityStateRow:
        now = utcnow()
        cid = continuity_id or new_continuity_id()
        with self.database.session_factory.begin() as session:
            _guard_worker_write(
                session,
                book_id=book_id,
                job_id=job_id,
                lease_owner=lease_owner,
            )
            existing = session.scalar(
                select(BookContinuityStateRow).where(
                    BookContinuityStateRow.book_id == book_id,
                    BookContinuityStateRow.after_chapter_number == after_chapter_number,
                )
            )
            if existing is None:
                row = BookContinuityStateRow(
                    continuity_id=cid,
                    book_id=book_id,
                    after_chapter_number=after_chapter_number,
                    state=state,
                    prompt_version=prompt_version,
                    model=model,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                existing.state = state
                existing.prompt_version = prompt_version
                existing.model = model
                existing.updated_at = now
                row = existing
            session.flush()
            session.expunge(row)
            return row

    def get_latest_continuity(self, book_id: str) -> BookContinuityStateRow | None:
        with self.database.session_factory() as session:
            stmt = (
                select(BookContinuityStateRow)
                .where(BookContinuityStateRow.book_id == book_id)
                .order_by(BookContinuityStateRow.after_chapter_number.desc())
                .limit(1)
            )
            return session.scalar(stmt)

    def get_continuity_after(
        self, book_id: str, after_chapter_number: int
    ) -> BookContinuityStateRow | None:
        with self.database.session_factory() as session:
            stmt = select(BookContinuityStateRow).where(
                BookContinuityStateRow.book_id == book_id,
                BookContinuityStateRow.after_chapter_number == after_chapter_number,
            )
            return session.scalar(stmt)


class BookExportRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def save_export(
        self,
        *,
        export_id: str | None = None,
        book_id: str,
        format: str,
        status: str,
        storage_key: str | None = None,
        storage_backend: str | None = None,
        size_bytes: int | None = None,
        sha256: str | None = None,
        content_type: str | None = None,
        chapter_count: int = 0,
        word_count: int = 0,
        engine: str | None = None,
        error_code: str | None = None,
        session: Session | None = None,
        job_id: str | None = None,
        lease_owner: str | None = None,
    ) -> BookExportRow:
        now = utcnow()
        eid = export_id or new_export_id()

        def persist(target: Session) -> BookExportRow:
            _guard_worker_write(
                target,
                book_id=book_id,
                job_id=job_id,
                lease_owner=lease_owner,
            )
            existing = target.scalar(
                select(BookExportRow).where(
                    BookExportRow.book_id == book_id,
                    BookExportRow.format == format,
                )
            )
            if existing is None:
                row = BookExportRow(
                    export_id=eid,
                    book_id=book_id,
                    format=format,
                    status=status,
                    storage_key=storage_key,
                    storage_backend=storage_backend,
                    size_bytes=size_bytes,
                    sha256=sha256,
                    content_type=content_type,
                    chapter_count=chapter_count,
                    word_count=word_count,
                    engine=engine,
                    error_code=error_code,
                    created_at=now,
                    updated_at=now,
                )
                target.add(row)
            else:
                existing.status = status
                if storage_key is not None:
                    existing.storage_key = storage_key
                if storage_backend is not None:
                    existing.storage_backend = storage_backend
                if size_bytes is not None:
                    existing.size_bytes = size_bytes
                if sha256 is not None:
                    existing.sha256 = sha256
                if content_type is not None:
                    existing.content_type = content_type
                existing.chapter_count = chapter_count
                existing.word_count = word_count
                if engine is not None:
                    existing.engine = engine
                existing.error_code = error_code
                existing.updated_at = now
                row = existing
            target.flush()
            if session is None:
                target.expunge(row)
            return row

        if session is not None:
            return persist(session)
        with self.database.session_factory.begin() as owned:
            return persist(owned)

    def get_export(self, *, book_id: str, format: str) -> BookExportRow | None:
        with self.database.session_factory() as session:
            stmt = select(BookExportRow).where(
                BookExportRow.book_id == book_id,
                BookExportRow.format == format,
            )
            return session.scalar(stmt)

    def list_exports(self, book_id: str) -> list[BookExportRow]:
        with self.database.session_factory() as session:
            stmt = select(BookExportRow).where(BookExportRow.book_id == book_id)
            return list(session.scalars(stmt).all())


class BookJobRepository:
    """Queue and lease manager for book jobs.

    Identical concurrency and recovery algorithm to RecordingRepository:
    - SKIP LOCKED claim
    - Heartbeat renewal
    - Lease timeout recovery
    - Deferral with backoff
    - Terminal guard against stale lease writes
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    def create_job(
        self,
        *,
        job_id: str | None = None,
        book_id: str,
        family_id: str,
        stage: str = BookStageEnum.PREPARING_SOURCES.value,
        max_attempts: int = 3,
        status: str = BookJobStatusEnum.QUEUED.value,
    ) -> BookJobRow:
        now = utcnow()
        jid = job_id or new_book_job_id()
        row = BookJobRow(
            job_id=jid,
            book_id=book_id,
            family_id=family_id,
            status=status,
            stage=stage,
            attempts=0,
            max_attempts=max_attempts,
            next_attempt_at=now,
            created_at=now,
            updated_at=now,
        )
        with self.database.session_factory.begin() as session:
            session.add(row)
            session.flush()
            session.expunge(row)
            return row

    def get_job(self, job_id: str) -> BookJobRow | None:
        with self.database.session_factory() as session:
            return session.get(BookJobRow, job_id)

    def get_job_for_book(self, book_id: str) -> BookJobRow | None:
        with self.database.session_factory() as session:
            stmt = (
                select(BookJobRow)
                .where(BookJobRow.book_id == book_id)
                .order_by(BookJobRow.created_at.desc())
                .limit(1)
            )
            return session.scalar(stmt)

    def claim_next_job(
        self,
        *,
        lease_owner: str,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> BookJobRow | None:
        moment = now or utcnow()
        with self.database.session_factory.begin() as session:
            due_queued = and_(
                BookJobRow.status == BookJobStatusEnum.QUEUED.value,
                BookJobRow.next_attempt_at <= moment,
                or_(
                    BookJobRow.lease_expires_at.is_(None),
                    BookJobRow.lease_expires_at <= moment,
                ),
            )
            expired_lease = and_(
                BookJobRow.status.notin_(TERMINAL_BOOK_JOB_STATUSES),
                BookJobRow.lease_expires_at.is_not(None),
                BookJobRow.lease_expires_at <= moment,
            )
            stmt = (
                select(BookJobRow)
                .where(
                    and_(
                        BookJobRow.attempts < BookJobRow.max_attempts,
                        or_(due_queued, expired_lease),
                    )
                )
                .order_by(BookJobRow.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            job = session.scalar(stmt)
            if job is None:
                return None

            job.lease_owner = lease_owner
            job.claimed_at = moment
            job.lease_expires_at = moment + timedelta(seconds=lease_seconds)
            job.last_heartbeat_at = moment
            job.attempts += 1
            job.status = BookJobStatusEnum.RUNNING.value
            job.started_at = job.started_at or moment
            job.updated_at = moment
            job.error_code = None
            job.error_detail = None
            session.flush()
            session.expunge(job)
            return job

    def renew_lease(
        self,
        job_id: str,
        *,
        lease_owner: str,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> bool:
        moment = now or utcnow()
        with self.database.session_factory.begin() as session:
            result = session.execute(
                update(BookJobRow)
                .where(
                    BookJobRow.job_id == job_id,
                    BookJobRow.lease_owner == lease_owner,
                    BookJobRow.status.notin_(TERMINAL_BOOK_JOB_STATUSES),
                )
                .values(
                    lease_expires_at=moment + timedelta(seconds=lease_seconds),
                    last_heartbeat_at=moment,
                    updated_at=moment,
                )
            )
            return bool(cast("CursorResult[Any]", result).rowcount)

    def _owned_job(
        self,
        session: Session,
        job_id: str,
        lease_owner: str | None,
    ) -> BookJobRow:
        job = session.scalar(
            select(BookJobRow)
            .where(BookJobRow.job_id == job_id)
            .with_for_update()
        )
        if job is None:
            raise LookupError(f"unknown book job: {job_id}")
        if lease_owner is not None and job.lease_owner != lease_owner:
            raise LeaseOwnershipLost(job_id)
        return job

    def update_job_stage(
        self,
        job_id: str,
        stage: str,
        *,
        lease_owner: str | None = None,
    ) -> None:
        now = utcnow()
        with self.database.session_factory.begin() as session:
            job = self._owned_job(session, job_id, lease_owner)
            job.stage = stage
            job.updated_at = now

    def defer_job(
        self,
        job_id: str,
        *,
        error_code: str,
        error_detail: str,
        retry_after_seconds: float | None = None,
        next_attempt_at: datetime | None = None,
        lease_owner: str | None = None,
        stage: str | None = None,
    ) -> BookJobRow:
        now = utcnow()
        with self.database.session_factory.begin() as session:
            job = self._owned_job(session, job_id, lease_owner)
            job.status = BookJobStatusEnum.QUEUED.value
            job.error_code = error_code
            job.error_detail = error_detail
            if next_attempt_at is not None:
                job.next_attempt_at = next_attempt_at
            elif retry_after_seconds is not None:
                job.next_attempt_at = now + timedelta(seconds=retry_after_seconds)
            else:
                job.next_attempt_at = now
            job.lease_owner = None
            job.lease_expires_at = None
            job.last_heartbeat_at = None
            if stage is not None:
                job.stage = stage
            job.updated_at = now
            session.flush()
            session.expunge(job)
            return job

    def fail_job(
        self,
        job_id: str,
        *,
        error_code: str,
        error_detail: str,
        lease_owner: str | None = None,
    ) -> None:
        now = utcnow()
        with self.database.session_factory.begin() as session:
            job = self._owned_job(session, job_id, lease_owner)
            job.status = BookJobStatusEnum.FAILED.value
            job.stage = BookStageEnum.FAILED.value
            job.error_code = error_code
            job.error_detail = error_detail
            job.lease_owner = None
            job.lease_expires_at = None
            job.last_heartbeat_at = None
            job.updated_at = now

    def complete_book_and_job(
        self,
        job_id: str,
        *,
        book_id: str,
        word_count: int,
        lease_owner: str,
    ) -> None:
        """Atomically publish terminal Book and BookJob state under the lease."""

        now = utcnow()
        with self.database.session_factory.begin() as session:
            _guard_worker_write(
                session,
                book_id=book_id,
                job_id=job_id,
                lease_owner=lease_owner,
            )
            book = session.get(BookRow, book_id)
            job = session.get(BookJobRow, job_id)
            if book is None or job is None:
                raise LookupError(f"unknown book/job: {book_id}/{job_id}")

            book.status = BookStatusEnum.COMPLETED.value
            book.stage = BookStageEnum.COMPLETED.value
            book.word_count = word_count
            book.completed_at = now
            book.updated_at = now

            job.status = BookJobStatusEnum.COMPLETED.value
            job.stage = BookStageEnum.COMPLETED.value
            job.error_code = None
            job.error_detail = None
            job.completed_at = now
            job.lease_owner = None
            job.claimed_at = None
            job.lease_expires_at = None
            job.last_heartbeat_at = None
            job.updated_at = now

    def complete_job(
        self,
        job_id: str,
        *,
        lease_owner: str | None = None,
    ) -> None:
        now = utcnow()
        with self.database.session_factory.begin() as session:
            job = self._owned_job(session, job_id, lease_owner)
            job.status = BookJobStatusEnum.COMPLETED.value
            job.stage = BookStageEnum.COMPLETED.value
            job.error_code = None
            job.error_detail = None
            job.completed_at = now
            job.lease_owner = None
            job.lease_expires_at = None
            job.last_heartbeat_at = None
            job.updated_at = now

    def cancel_book_and_job(
        self,
        job_id: str,
        *,
        book_id: str,
        lease_owner: str,
    ) -> None:
        """Atomically cancel the durable Book and its owned queue job."""

        now = utcnow()
        with self.database.session_factory.begin() as session:
            _guard_worker_write(
                session,
                book_id=book_id,
                job_id=job_id,
                lease_owner=lease_owner,
            )
            book = session.get(BookRow, book_id)
            job = session.get(BookJobRow, job_id)
            if book is None or job is None:
                raise LookupError(f"unknown book/job: {book_id}/{job_id}")

            book.status = BookStatusEnum.CANCELLED.value
            book.stage = BookStageEnum.CANCELLED.value
            book.updated_at = now

            job.status = BookJobStatusEnum.CANCELLED.value
            job.stage = BookStageEnum.CANCELLED.value
            job.lease_owner = None
            job.claimed_at = None
            job.lease_expires_at = None
            job.last_heartbeat_at = None
            job.updated_at = now

    def cancel_job(
        self,
        job_id: str,
        *,
        lease_owner: str | None = None,
    ) -> None:
        now = utcnow()
        with self.database.session_factory.begin() as session:
            job = self._owned_job(session, job_id, lease_owner)
            job.status = BookJobStatusEnum.CANCELLED.value
            job.stage = BookStageEnum.CANCELLED.value
            job.lease_owner = None
            job.lease_expires_at = None
            job.last_heartbeat_at = None
            job.updated_at = now
