"""Durable physical-object cleanup queue.

Cleanup rows intentionally have no foreign key to the Recording, Book, or Family
that caused them. The user-facing relational resource may be deleted in the same
transaction that enqueues cleanup, while this row must remain independently
executable until physical erasure succeeds.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import (
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    and_,
    or_,
    select,
    update,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from mura.leases import LeaseOwnershipLost
from mura.storage.database import Base, Database, utcnow

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult


class StorageCleanupStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class StorageKind(StrEnum):
    AUDIO = "audio"
    BOOK_ARTIFACT = "book_artifact"


class StorageCleanupResourceType(StrEnum):
    RECORDING_AUDIO = "recording_audio"
    BOOK_PDF = "book_pdf"
    BOOK_EPUB = "book_epub"


TERMINAL_CLEANUP_STATUSES = (
    StorageCleanupStatus.COMPLETED.value,
    StorageCleanupStatus.FAILED.value,
)


class StorageCleanupJobRow(Base):
    __tablename__ = "storage_cleanup_jobs"
    __table_args__ = (
        UniqueConstraint(
            "storage_kind",
            "storage_backend",
            "storage_key",
            name="uq_storage_cleanup_object",
        ),
        Index(
            "ix_storage_cleanup_jobs_status_next_attempt",
            "status",
            "next_attempt_at",
        ),
    )

    cleanup_job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=StorageCleanupStatus.QUEUED.value, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=8)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def new_cleanup_job_id() -> str:
    return f"cleanup_{uuid.uuid4().hex}"


class StorageCleanupRepository:
    """Repository for the durable cleanup queue.

    Enqueue methods accept a caller-owned Session because the cleanup decision
    must commit atomically with the relational deletion that made the object
    unreachable to the user.
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    def enqueue_cleanup(
        self,
        *,
        resource_type: str,
        storage_kind: str,
        storage_backend: str,
        storage_key: str,
        max_attempts: int = 8,
        session: Session | None = None,
    ) -> StorageCleanupJobRow:
        def persist(target: Session) -> StorageCleanupJobRow:
            existing = target.scalar(
                select(StorageCleanupJobRow).where(
                    StorageCleanupJobRow.storage_kind == storage_kind,
                    StorageCleanupJobRow.storage_backend == storage_backend,
                    StorageCleanupJobRow.storage_key == storage_key,
                )
            )
            if existing is not None:
                return existing

            now = utcnow()
            row = StorageCleanupJobRow(
                cleanup_job_id=new_cleanup_job_id(),
                resource_type=resource_type,
                storage_kind=storage_kind,
                storage_backend=storage_backend,
                storage_key=storage_key,
                status=StorageCleanupStatus.QUEUED.value,
                attempts=0,
                max_attempts=max_attempts,
                next_attempt_at=now,
                created_at=now,
                updated_at=now,
            )
            target.add(row)
            target.flush()
            return row

        if session is not None:
            return persist(session)

        with self.database.session_factory.begin() as owned:
            row = persist(owned)
            owned.flush()
            owned.expunge(row)
            return row

    def enqueue_many(
        self,
        items: Iterable[dict[str, Any]],
        *,
        max_attempts: int = 8,
        session: Session | None = None,
    ) -> list[StorageCleanupJobRow]:
        # A family deletion can discover the same deterministic object from both
        # export metadata and the canonical key. Collapse it before persistence.
        unique: dict[tuple[str, str, str], dict[str, Any]] = {}
        for item in items:
            key = (
                str(item["storage_kind"]),
                str(item["storage_backend"]),
                str(item["storage_key"]),
            )
            unique.setdefault(key, item)

        def persist_all(target: Session) -> list[StorageCleanupJobRow]:
            return [
                self.enqueue_cleanup(
                    resource_type=str(item["resource_type"]),
                    storage_kind=str(item["storage_kind"]),
                    storage_backend=str(item["storage_backend"]),
                    storage_key=str(item["storage_key"]),
                    max_attempts=max_attempts,
                    session=target,
                )
                for item in unique.values()
            ]

        if session is not None:
            return persist_all(session)

        with self.database.session_factory.begin() as owned:
            rows = persist_all(owned)
            owned.flush()
            for row in rows:
                if row in owned:
                    owned.expunge(row)
            return rows

    def get_job(self, cleanup_job_id: str) -> StorageCleanupJobRow | None:
        with self.database.session_factory() as session:
            return session.get(StorageCleanupJobRow, cleanup_job_id)

    def claim_next_job(
        self,
        *,
        lease_owner: str,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> StorageCleanupJobRow | None:
        moment = now or utcnow()
        with self.database.session_factory.begin() as session:
            due_queued = and_(
                StorageCleanupJobRow.status == StorageCleanupStatus.QUEUED.value,
                StorageCleanupJobRow.next_attempt_at <= moment,
                or_(
                    StorageCleanupJobRow.lease_expires_at.is_(None),
                    StorageCleanupJobRow.lease_expires_at <= moment,
                ),
            )
            expired_running = and_(
                StorageCleanupJobRow.status == StorageCleanupStatus.RUNNING.value,
                StorageCleanupJobRow.lease_expires_at.is_not(None),
                StorageCleanupJobRow.lease_expires_at <= moment,
            )
            statement = (
                select(StorageCleanupJobRow)
                .where(
                    or_(
                        and_(
                            due_queued,
                            StorageCleanupJobRow.attempts < StorageCleanupJobRow.max_attempts,
                        ),
                        # A process crash is not a provider failure. Even when
                        # the last allowed attempt was in flight, reclaim it so
                        # an idempotent delete can observe "already absent" and
                        # durably complete instead of leaving RUNNING forever.
                        expired_running,
                    )
                )
                .order_by(StorageCleanupJobRow.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            job = session.scalar(statement)
            if job is None:
                return None
            reclaimed_running = job.status == StorageCleanupStatus.RUNNING.value
            job.status = StorageCleanupStatus.RUNNING.value
            job.lease_owner = lease_owner
            job.claimed_at = moment
            job.lease_expires_at = moment + timedelta(seconds=lease_seconds)
            job.last_heartbeat_at = moment
            if not reclaimed_running:
                job.attempts += 1
            job.error_code = None
            job.error_detail = None
            job.updated_at = moment
            session.flush()
            session.expunge(job)
            return job

    def renew_lease(
        self,
        cleanup_job_id: str,
        *,
        lease_owner: str,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> bool:
        moment = now or utcnow()
        with self.database.session_factory.begin() as session:
            result = session.execute(
                update(StorageCleanupJobRow)
                .where(
                    StorageCleanupJobRow.cleanup_job_id == cleanup_job_id,
                    StorageCleanupJobRow.lease_owner == lease_owner,
                    StorageCleanupJobRow.status == StorageCleanupStatus.RUNNING.value,
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
        cleanup_job_id: str,
        lease_owner: str | None,
    ) -> StorageCleanupJobRow:
        job = session.scalar(
            select(StorageCleanupJobRow)
            .where(StorageCleanupJobRow.cleanup_job_id == cleanup_job_id)
            .with_for_update()
        )
        if job is None:
            raise LookupError(f"unknown cleanup job: {cleanup_job_id}")
        if lease_owner is not None and job.lease_owner != lease_owner:
            raise LeaseOwnershipLost(cleanup_job_id)
        return job

    @staticmethod
    def _release_lease(job: StorageCleanupJobRow) -> None:
        job.lease_owner = None
        job.claimed_at = None
        job.lease_expires_at = None
        job.last_heartbeat_at = None

    def complete(
        self,
        cleanup_job_id: str,
        *,
        lease_owner: str | None = None,
    ) -> None:
        now = utcnow()
        with self.database.session_factory.begin() as session:
            job = self._owned_job(session, cleanup_job_id, lease_owner)
            job.status = StorageCleanupStatus.COMPLETED.value
            job.error_code = None
            job.error_detail = None
            job.completed_at = now
            job.updated_at = now
            self._release_lease(job)

    def defer(
        self,
        cleanup_job_id: str,
        *,
        next_attempt_at: datetime,
        error_code: str,
        error_detail: str,
        lease_owner: str | None = None,
    ) -> None:
        now = utcnow()
        with self.database.session_factory.begin() as session:
            job = self._owned_job(session, cleanup_job_id, lease_owner)
            job.status = StorageCleanupStatus.QUEUED.value
            job.next_attempt_at = next_attempt_at
            job.error_code = error_code
            job.error_detail = error_detail
            job.updated_at = now
            self._release_lease(job)

    def fail(
        self,
        cleanup_job_id: str,
        *,
        error_code: str,
        error_detail: str,
        lease_owner: str | None = None,
    ) -> None:
        now = utcnow()
        with self.database.session_factory.begin() as session:
            job = self._owned_job(session, cleanup_job_id, lease_owner)
            job.status = StorageCleanupStatus.FAILED.value
            job.error_code = error_code
            job.error_detail = error_detail
            job.completed_at = now
            job.updated_at = now
            self._release_lease(job)
