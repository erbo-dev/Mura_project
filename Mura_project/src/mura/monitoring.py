"""Production queue health and operational monitoring services.

Queries queue depth, stuck jobs, pipeline statistics, and AI usage aggregates
using existing database indexes and lease configuration. Purely read-only;
never mutates jobs or collects private content.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import Field
from sqlalchemy import func, select

from mura.domain.book_models import TERMINAL_BOOK_JOB_STATUSES, BookJobStatus
from mura.domain.models import StrictModel
from mura.jobs import JobStatus
from mura.storage.ai_usage import AIUsageLedger
from mura.storage.book import BookJobRow
from mura.storage.cleanup import (
    TERMINAL_CLEANUP_STATUSES,
    StorageCleanupJobRow,
    StorageCleanupStatus,
)
from mura.storage.database import (
    TERMINAL_JOB_STATUSES,
    Database,
    ProcessingJobRow,
    utcnow,
)


def _as_utc(dt: datetime) -> datetime:
    """Normalize SQLite naive datetimes and PostgreSQL aware datetimes to UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)




class BookQueueMetrics(StrictModel):
    queued: int = Field(ge=0)
    running: int = Field(ge=0)
    failed: int = Field(ge=0)
    completed: int = Field(ge=0)
    oldest_queued_seconds: float | None = None
    expired_leases: int = Field(ge=0)


class BookStuckJobItem(StrictModel):
    job_id: str
    book_id: str
    family_id: str
    status: str
    stage: str
    attempts: int
    reason: str
    age_seconds: float

class StorageCleanupMetrics(StrictModel):
    queued: int = Field(ge=0)
    running: int = Field(ge=0)
    retry_waiting: int = Field(ge=0)
    failed: int = Field(ge=0)
    oldest_pending_seconds: float | None = None
    expired_leases: int = Field(ge=0)
    attempts_exhausted: int = Field(ge=0)


class StorageCleanupStuckItem(StrictModel):
    cleanup_job_id: str
    resource_type: str
    storage_kind: str
    backend: str
    status: str
    attempts: int
    reason: str
    age_seconds: float


class QueueMetrics(StrictModel):
    pending: int = Field(ge=0)
    running: int = Field(ge=0)
    failed: int = Field(ge=0)
    oldest_pending_seconds: float | None = None
    expired_leases: int = Field(ge=0)


class Pipeline24hMetrics(StrictModel):
    completed: int = Field(ge=0)
    failed: int = Field(ge=0)


class AI24hMetrics(StrictModel):
    requests_count: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(ge=0)
    audio_seconds: float = Field(ge=0.0)
    estimated_cost_usd: str


class StuckJobItem(StrictModel):
    job_id: str
    recording_id: str
    status: str
    stage: str
    attempts: int
    reason: str
    age_seconds: float


class MonitoringSummary(StrictModel):
    timestamp: datetime = Field(default_factory=utcnow)
    queue: QueueMetrics
    pipeline_24h: Pipeline24hMetrics
    ai_24h: AI24hMetrics
    stuck_jobs: list[StuckJobItem]
    book_queue: BookQueueMetrics | None = None
    book_stuck_jobs: list[BookStuckJobItem] = Field(default_factory=list)
    storage_cleanup: StorageCleanupMetrics | None = None
    storage_cleanup_stuck_jobs: list[StorageCleanupStuckItem] = Field(default_factory=list)


@dataclass(frozen=True)
class MonitoringThresholds:
    """Configurable thresholds derived from system lease & runtime configuration."""

    stuck_pending_threshold_seconds: float = 600.0
    lease_grace_seconds: float = 60.0
    max_attempts: int = 3
    stuck_book_pending_threshold_seconds: float = 1200.0
    book_lease_grace_seconds: float = 60.0
    book_max_attempts: int = 3
    cleanup_pending_threshold_seconds: float = 1800.0
    cleanup_lease_grace_seconds: float = 30.0
    cleanup_max_attempts: int = 8

    @classmethod
    def from_settings(
        cls,
        *,
        job_lease_seconds: float = 300.0,
        job_heartbeat_seconds: float = 60.0,
        asr_request_timeout_seconds: float = 900.0,
        book_job_lease_seconds: float = 600.0,
        book_job_heartbeat_seconds: float = 60.0,
        storage_cleanup_lease_seconds: float = 120.0,
        storage_cleanup_heartbeat_seconds: float = 30.0,
        storage_cleanup_max_attempts: int = 8,
    ) -> MonitoringThresholds:
        pending_thresh = max(job_lease_seconds * 2, asr_request_timeout_seconds / 2)
        book_pending_thresh = max(book_job_lease_seconds * 2, 600.0)
        return cls(
            stuck_pending_threshold_seconds=pending_thresh,
            lease_grace_seconds=job_heartbeat_seconds,
            max_attempts=3,
            stuck_book_pending_threshold_seconds=book_pending_thresh,
            book_lease_grace_seconds=book_job_heartbeat_seconds,
            book_max_attempts=3,
            cleanup_pending_threshold_seconds=max(storage_cleanup_lease_seconds * 4, 600.0),
            cleanup_lease_grace_seconds=storage_cleanup_heartbeat_seconds,
            cleanup_max_attempts=storage_cleanup_max_attempts,
        )


class QueueHealthService:
    def __init__(
        self,
        database: Database,
        *,
        thresholds: MonitoringThresholds | None = None,
        ai_ledger: AIUsageLedger | None = None,
    ) -> None:
        self.database = database
        self.thresholds = thresholds or MonitoringThresholds()
        self.ai_ledger = ai_ledger or AIUsageLedger(database)

    def get_queue_metrics(self, now: datetime | None = None) -> QueueMetrics:
        moment = now or utcnow()
        with self.database.session_factory() as session:
            # Aggregate status counts using existing index ix_processing_jobs_status
            counts: dict[str, int] = {
                str(row[0]): int(row[1])
                for row in session.execute(
                    select(
                        ProcessingJobRow.status,
                        func.count(ProcessingJobRow.job_id),
                    ).group_by(ProcessingJobRow.status)
                )
            }

            pending = counts.get(JobStatus.QUEUED.value, 0)
            failed = counts.get(JobStatus.FAILED.value, 0)
            running = sum(
                count
                for status, count in counts.items()
                if status not in TERMINAL_JOB_STATUSES and status != JobStatus.QUEUED.value
            )

            # Oldest pending job created_at
            oldest_created = session.scalar(
                select(func.min(ProcessingJobRow.created_at)).where(
                    ProcessingJobRow.status == JobStatus.QUEUED.value
                )
            )
            oldest_age = (
                round((moment - _as_utc(oldest_created)).total_seconds(), 2)
                if oldest_created is not None
                else None
            )

            # Expired leases: non-terminal jobs whose lease_expires_at is in the past
            expired_leases = (
                session.scalar(
                    select(func.count(ProcessingJobRow.job_id)).where(
                        ProcessingJobRow.status.notin_(TERMINAL_JOB_STATUSES),
                        ProcessingJobRow.lease_expires_at.is_not(None),
                        ProcessingJobRow.lease_expires_at <= moment,
                    )
                )
            ) or 0

            return QueueMetrics(
                pending=pending,
                running=running,
                failed=failed,
                oldest_pending_seconds=oldest_age,
                expired_leases=expired_leases,
            )

    def get_pipeline_24h(self, now: datetime | None = None) -> Pipeline24hMetrics:
        moment = now or utcnow()
        since = moment - timedelta(hours=24)

        with self.database.session_factory() as session:
            completed_24h = (
                session.scalar(
                    select(func.count(ProcessingJobRow.job_id)).where(
                        ProcessingJobRow.status == JobStatus.COMPLETED.value,
                        ProcessingJobRow.completed_at >= since,
                    )
                )
            ) or 0

            failed_24h = (
                session.scalar(
                    select(func.count(ProcessingJobRow.job_id)).where(
                        ProcessingJobRow.status == JobStatus.FAILED.value,
                        ProcessingJobRow.completed_at >= since,
                    )
                )
            ) or 0

            return Pipeline24hMetrics(completed=completed_24h, failed=failed_24h)

    def get_stuck_jobs(self, now: datetime | None = None) -> list[StuckJobItem]:
        moment = now or utcnow()
        pending_cutoff = moment - timedelta(seconds=self.thresholds.stuck_pending_threshold_seconds)
        lease_cutoff = moment - timedelta(seconds=self.thresholds.lease_grace_seconds)

        stuck_items: list[StuckJobItem] = []
        with self.database.session_factory() as session:
            # 1. Pending longer than threshold
            pending_jobs = session.scalars(
                select(ProcessingJobRow)
                .where(
                    ProcessingJobRow.status == JobStatus.QUEUED.value,
                    ProcessingJobRow.created_at <= pending_cutoff,
                )
                .order_by(ProcessingJobRow.created_at)
                .limit(50)
            ).all()

            for job in pending_jobs:
                age = (moment - _as_utc(job.created_at)).total_seconds()
                stuck_items.append(
                    StuckJobItem(
                        job_id=job.job_id,
                        recording_id=job.recording_id,
                        status=job.status,
                        stage=job.stage,
                        attempts=job.attempts,
                        reason="pending_too_long",
                        age_seconds=round(age, 2),
                    )
                )

            # 2. Running jobs with expired leases
            expired_running = session.scalars(
                select(ProcessingJobRow)
                .where(
                    ProcessingJobRow.status.notin_(TERMINAL_JOB_STATUSES),
                    ProcessingJobRow.status != JobStatus.QUEUED.value,
                    ProcessingJobRow.lease_expires_at.is_not(None),
                    ProcessingJobRow.lease_expires_at <= lease_cutoff,
                )
                .order_by(ProcessingJobRow.lease_expires_at)
                .limit(50)
            ).all()

            for job in expired_running:
                age = (moment - _as_utc(job.created_at)).total_seconds()
                stuck_items.append(
                    StuckJobItem(
                        job_id=job.job_id,
                        recording_id=job.recording_id,
                        status=job.status,
                        stage=job.stage,
                        attempts=job.attempts,
                        reason="lease_expired",
                        age_seconds=round(age, 2),
                    )
                )

            # 3. Repeated retry exhaustion without completing
            exhausted_jobs = session.scalars(
                select(ProcessingJobRow)
                .where(
                    ProcessingJobRow.status.notin_([JobStatus.COMPLETED.value]),
                    ProcessingJobRow.attempts >= self.thresholds.max_attempts,
                )
                .order_by(ProcessingJobRow.created_at)
                .limit(50)
            ).all()

            existing_ids = {item.job_id for item in stuck_items}
            for job in exhausted_jobs:
                if job.job_id not in existing_ids:
                    age = (moment - _as_utc(job.created_at)).total_seconds()
                    stuck_items.append(
                        StuckJobItem(
                            job_id=job.job_id,
                            recording_id=job.recording_id,
                            status=job.status,
                            stage=job.stage,
                            attempts=job.attempts,
                            reason="max_retries_exceeded",
                            age_seconds=round(age, 2),
                        )
                    )

        return stuck_items


    def get_book_queue_metrics(self, now: datetime | None = None) -> BookQueueMetrics:
        moment = now or utcnow()
        with self.database.session_factory() as session:
            counts: dict[str, int] = {
                str(row[0]): int(row[1])
                for row in session.execute(
                    select(
                        BookJobRow.status,
                        func.count(BookJobRow.job_id),
                    ).group_by(BookJobRow.status)
                )
            }

            queued = counts.get(BookJobStatus.QUEUED.value, 0)
            failed = counts.get(BookJobStatus.FAILED.value, 0)
            completed = counts.get(BookJobStatus.COMPLETED.value, 0)
            running = counts.get(BookJobStatus.RUNNING.value, 0)

            oldest_created = session.scalar(
                select(func.min(BookJobRow.created_at)).where(
                    BookJobRow.status == BookJobStatus.QUEUED.value
                )
            )
            oldest_age = (
                round((moment - _as_utc(oldest_created)).total_seconds(), 2)
                if oldest_created is not None
                else None
            )

            expired_leases = (
                session.scalar(
                    select(func.count(BookJobRow.job_id)).where(
                        BookJobRow.status.notin_(TERMINAL_BOOK_JOB_STATUSES),
                        BookJobRow.lease_expires_at.is_not(None),
                        BookJobRow.lease_expires_at <= moment,
                    )
                )
            ) or 0

            return BookQueueMetrics(
                queued=queued,
                running=running,
                failed=failed,
                completed=completed,
                oldest_queued_seconds=oldest_age,
                expired_leases=expired_leases,
            )

    def get_book_stuck_jobs(self, now: datetime | None = None) -> list[BookStuckJobItem]:
        moment = now or utcnow()
        pending_cutoff = moment - timedelta(
            seconds=self.thresholds.stuck_book_pending_threshold_seconds
        )
        lease_cutoff = moment - timedelta(seconds=self.thresholds.book_lease_grace_seconds)

        stuck_items: list[BookStuckJobItem] = []
        with self.database.session_factory() as session:
            # 1. Pending longer than threshold
            pending_jobs = session.scalars(
                select(BookJobRow)
                .where(
                    BookJobRow.status == BookJobStatus.QUEUED.value,
                    BookJobRow.created_at <= pending_cutoff,
                )
                .order_by(BookJobRow.created_at)
                .limit(50)
            ).all()

            for job in pending_jobs:
                age = (moment - _as_utc(job.created_at)).total_seconds()
                stuck_items.append(
                    BookStuckJobItem(
                        job_id=job.job_id,
                        book_id=job.book_id,
                        family_id=job.family_id,
                        status=job.status,
                        stage=job.stage,
                        attempts=job.attempts,
                        reason="pending_too_long",
                        age_seconds=round(age, 2),
                    )
                )

            # 2. Running jobs with expired leases
            expired_running = session.scalars(
                select(BookJobRow)
                .where(
                    BookJobRow.status.notin_(TERMINAL_BOOK_JOB_STATUSES),
                    BookJobRow.status != BookJobStatus.QUEUED.value,
                    BookJobRow.lease_expires_at.is_not(None),
                    BookJobRow.lease_expires_at <= lease_cutoff,
                )
                .order_by(BookJobRow.lease_expires_at)
                .limit(50)
            ).all()

            for job in expired_running:
                age = (moment - _as_utc(job.created_at)).total_seconds()
                stuck_items.append(
                    BookStuckJobItem(
                        job_id=job.job_id,
                        book_id=job.book_id,
                        family_id=job.family_id,
                        status=job.status,
                        stage=job.stage,
                        attempts=job.attempts,
                        reason="lease_expired",
                        age_seconds=round(age, 2),
                    )
                )

            # 3. Repeated retry exhaustion
            exhausted_jobs = session.scalars(
                select(BookJobRow)
                .where(
                    BookJobRow.status.notin_([BookJobStatus.COMPLETED.value]),
                    BookJobRow.attempts >= self.thresholds.book_max_attempts,
                )
                .order_by(BookJobRow.created_at)
                .limit(50)
            ).all()

            existing_ids = {item.job_id for item in stuck_items}
            for job in exhausted_jobs:
                if job.job_id not in existing_ids:
                    age = (moment - _as_utc(job.created_at)).total_seconds()
                    stuck_items.append(
                        BookStuckJobItem(
                            job_id=job.job_id,
                            book_id=job.book_id,
                            family_id=job.family_id,
                            status=job.status,
                            stage=job.stage,
                            attempts=job.attempts,
                            reason="max_retries_exceeded",
                            age_seconds=round(age, 2),
                        )
                    )

        return stuck_items

    def get_storage_cleanup_metrics(self, now: datetime | None = None) -> StorageCleanupMetrics:
        moment = now or utcnow()
        with self.database.session_factory() as session:
            queued = (
                session.scalar(
                    select(func.count(StorageCleanupJobRow.cleanup_job_id)).where(
                        StorageCleanupJobRow.status == StorageCleanupStatus.QUEUED.value,
                        StorageCleanupJobRow.next_attempt_at <= moment,
                    )
                )
                or 0
            )
            retry_waiting = (
                session.scalar(
                    select(func.count(StorageCleanupJobRow.cleanup_job_id)).where(
                        StorageCleanupJobRow.status == StorageCleanupStatus.QUEUED.value,
                        StorageCleanupJobRow.next_attempt_at > moment,
                    )
                )
                or 0
            )
            running = (
                session.scalar(
                    select(func.count(StorageCleanupJobRow.cleanup_job_id)).where(
                        StorageCleanupJobRow.status == StorageCleanupStatus.RUNNING.value
                    )
                )
                or 0
            )
            failed = (
                session.scalar(
                    select(func.count(StorageCleanupJobRow.cleanup_job_id)).where(
                        StorageCleanupJobRow.status == StorageCleanupStatus.FAILED.value
                    )
                )
            ) or 0
            oldest = session.scalar(
                select(func.min(StorageCleanupJobRow.created_at)).where(
                    StorageCleanupJobRow.status.notin_(TERMINAL_CLEANUP_STATUSES)
                )
            )
            expired = (
                session.scalar(
                    select(func.count(StorageCleanupJobRow.cleanup_job_id)).where(
                        StorageCleanupJobRow.status == StorageCleanupStatus.RUNNING.value,
                        StorageCleanupJobRow.lease_expires_at.is_not(None),
                        StorageCleanupJobRow.lease_expires_at <= moment,
                    )
                )
                or 0
            )
            exhausted = (
                session.scalar(
                    select(func.count(StorageCleanupJobRow.cleanup_job_id)).where(
                        StorageCleanupJobRow.status.notin_(TERMINAL_CLEANUP_STATUSES),
                        StorageCleanupJobRow.attempts >= StorageCleanupJobRow.max_attempts,
                    )
                )
            ) or 0
            oldest_age = (
                round((moment - _as_utc(oldest)).total_seconds(), 2) if oldest is not None else None
            )
            return StorageCleanupMetrics(
                queued=queued,
                running=running,
                retry_waiting=retry_waiting,
                failed=failed,
                oldest_pending_seconds=oldest_age,
                expired_leases=expired,
                attempts_exhausted=exhausted,
            )

    def get_storage_cleanup_stuck_jobs(
        self, now: datetime | None = None
    ) -> list[StorageCleanupStuckItem]:
        moment = now or utcnow()
        pending_cutoff = moment - timedelta(
            seconds=self.thresholds.cleanup_pending_threshold_seconds
        )
        lease_cutoff = moment - timedelta(seconds=self.thresholds.cleanup_lease_grace_seconds)
        reasons: dict[str, str] = {}
        with self.database.session_factory() as session:
            for row in session.scalars(
                select(StorageCleanupJobRow).where(
                    StorageCleanupJobRow.status.notin_(TERMINAL_CLEANUP_STATUSES),
                    StorageCleanupJobRow.created_at <= pending_cutoff,
                )
            ):
                reasons[row.cleanup_job_id] = "pending_too_long"
            for row in session.scalars(
                select(StorageCleanupJobRow).where(
                    StorageCleanupJobRow.status == StorageCleanupStatus.RUNNING.value,
                    StorageCleanupJobRow.lease_expires_at.is_not(None),
                    StorageCleanupJobRow.lease_expires_at <= lease_cutoff,
                )
            ):
                reasons[row.cleanup_job_id] = "lease_expired"
            for row in session.scalars(
                select(StorageCleanupJobRow).where(
                    StorageCleanupJobRow.status.notin_(TERMINAL_CLEANUP_STATUSES),
                    StorageCleanupJobRow.attempts >= StorageCleanupJobRow.max_attempts,
                )
            ):
                reasons[row.cleanup_job_id] = "max_retries_exceeded"

            if not reasons:
                return []
            rows = list(
                session.scalars(
                    select(StorageCleanupJobRow)
                    .where(StorageCleanupJobRow.cleanup_job_id.in_(list(reasons)))
                    .order_by(StorageCleanupJobRow.created_at)
                    .limit(50)
                ).all()
            )
            return [
                StorageCleanupStuckItem(
                    cleanup_job_id=row.cleanup_job_id,
                    resource_type=row.resource_type,
                    storage_kind=row.storage_kind,
                    backend=row.storage_backend,
                    status=row.status,
                    attempts=row.attempts,
                    reason=reasons[row.cleanup_job_id],
                    age_seconds=round((moment - _as_utc(row.created_at)).total_seconds(), 2),
                )
                for row in rows
            ]

    def get_summary(self, now: datetime | None = None) -> MonitoringSummary:
        moment = now or utcnow()
        queue = self.get_queue_metrics(moment)
        pipeline = self.get_pipeline_24h(moment)
        ai_summary_raw = self.ai_ledger.get_summary_24h(moment)
        ai_metrics = AI24hMetrics(**ai_summary_raw)
        stuck = self.get_stuck_jobs(moment)
        book_queue = self.get_book_queue_metrics(moment)
        book_stuck = self.get_book_stuck_jobs(moment)
        storage_cleanup = self.get_storage_cleanup_metrics(moment)
        storage_cleanup_stuck = self.get_storage_cleanup_stuck_jobs(moment)

        return MonitoringSummary(
            timestamp=moment,
            queue=queue,
            pipeline_24h=pipeline,
            ai_24h=ai_metrics,
            stuck_jobs=stuck,
            book_queue=book_queue,
            book_stuck_jobs=book_stuck,
            storage_cleanup=storage_cleanup,
            storage_cleanup_stuck_jobs=storage_cleanup_stuck,
        )
