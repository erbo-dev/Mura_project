from __future__ import annotations

from datetime import UTC, timedelta

from mura.storage.cleanup import (
    StorageCleanupRepository,
    StorageCleanupResourceType,
    StorageCleanupStatus,
    StorageKind,
)
from mura.storage.database import Database, utcnow


def _aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _db() -> Database:
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    return database


def test_cleanup_enqueue_is_idempotent_and_claimable() -> None:
    db = _db()
    repo = StorageCleanupRepository(db)

    first = repo.enqueue_cleanup(
        resource_type=StorageCleanupResourceType.RECORDING_AUDIO.value,
        storage_kind=StorageKind.AUDIO.value,
        storage_backend="local",
        storage_key="family/fam/recordings/rec/original.wav",
    )
    second = repo.enqueue_cleanup(
        resource_type=StorageCleanupResourceType.RECORDING_AUDIO.value,
        storage_kind=StorageKind.AUDIO.value,
        storage_backend="local",
        storage_key="family/fam/recordings/rec/original.wav",
    )
    assert first.cleanup_job_id == second.cleanup_job_id

    claimed = repo.claim_next_job(lease_owner="worker_a", lease_seconds=60)
    assert claimed is not None
    assert claimed.cleanup_job_id == first.cleanup_job_id
    assert claimed.status == StorageCleanupStatus.RUNNING.value
    assert claimed.attempts == 1
    assert claimed.lease_owner == "worker_a"


def test_cleanup_lease_heartbeat_and_complete() -> None:
    db = _db()
    repo = StorageCleanupRepository(db)
    job = repo.enqueue_cleanup(
        resource_type=StorageCleanupResourceType.BOOK_PDF.value,
        storage_kind=StorageKind.BOOK_ARTIFACT.value,
        storage_backend="local",
        storage_key="families/fam/books/book/book.pdf",
    )
    claimed = repo.claim_next_job(lease_owner="worker_a", lease_seconds=30)
    assert claimed is not None

    before = claimed.lease_expires_at
    later = utcnow() + timedelta(seconds=5)
    assert repo.renew_lease(
        job.cleanup_job_id,
        lease_owner="worker_a",
        lease_seconds=30,
        now=later,
    )
    renewed = repo.get_job(job.cleanup_job_id)
    assert renewed is not None
    assert renewed.lease_expires_at is not None
    assert before is not None
    assert _aware(renewed.lease_expires_at) > _aware(before)

    repo.complete(job.cleanup_job_id, lease_owner="worker_a")
    completed = repo.get_job(job.cleanup_job_id)
    assert completed is not None
    assert completed.status == StorageCleanupStatus.COMPLETED.value
    assert completed.lease_owner is None
    assert completed.completed_at is not None


def test_cleanup_defer_and_reclaim_after_due_time() -> None:
    db = _db()
    repo = StorageCleanupRepository(db)
    job = repo.enqueue_cleanup(
        resource_type=StorageCleanupResourceType.BOOK_EPUB.value,
        storage_kind=StorageKind.BOOK_ARTIFACT.value,
        storage_backend="local",
        storage_key="families/fam/books/book/book.epub",
    )
    claimed = repo.claim_next_job(lease_owner="worker_a", lease_seconds=30)
    assert claimed is not None

    due = utcnow() + timedelta(seconds=60)
    repo.defer(
        job.cleanup_job_id,
        next_attempt_at=due,
        error_code="storage_unavailable",
        error_detail="storage cleanup deferred",
        lease_owner="worker_a",
    )
    assert repo.claim_next_job(
        lease_owner="worker_b", lease_seconds=30, now=due - timedelta(seconds=1)
    ) is None
    reclaimed = repo.claim_next_job(
        lease_owner="worker_b", lease_seconds=30, now=due + timedelta(seconds=1)
    )
    assert reclaimed is not None
    assert reclaimed.attempts == 2


def test_cleanup_expired_lease_is_reclaimed() -> None:
    db = _db()
    repo = StorageCleanupRepository(db)
    job = repo.enqueue_cleanup(
        resource_type=StorageCleanupResourceType.RECORDING_AUDIO.value,
        storage_kind=StorageKind.AUDIO.value,
        storage_backend="local",
        storage_key="family/fam/recordings/rec-2/original.wav",
    )
    now = utcnow()
    claimed = repo.claim_next_job(
        lease_owner="worker_dead", lease_seconds=10, now=now
    )
    assert claimed is not None
    reclaimed = repo.claim_next_job(
        lease_owner="worker_recovery",
        lease_seconds=10,
        now=now + timedelta(seconds=11),
    )
    assert reclaimed is not None
    assert reclaimed.cleanup_job_id == job.cleanup_job_id
    assert reclaimed.lease_owner == "worker_recovery"
    assert reclaimed.attempts == 2


def test_cleanup_max_attempts_prevents_reclaim() -> None:
    db = _db()
    repo = StorageCleanupRepository(db)
    job = repo.enqueue_cleanup(
        resource_type=StorageCleanupResourceType.RECORDING_AUDIO.value,
        storage_kind=StorageKind.AUDIO.value,
        storage_backend="local",
        storage_key="family/fam/recordings/rec-3/original.wav",
        max_attempts=1,
    )
    now = utcnow()
    assert repo.claim_next_job(
        lease_owner="worker_dead", lease_seconds=10, now=now
    ) is not None
    assert repo.claim_next_job(
        lease_owner="worker_other",
        lease_seconds=10,
        now=now + timedelta(seconds=11),
    ) is None


def test_cleanup_terminal_failure_releases_lease() -> None:
    db = _db()
    repo = StorageCleanupRepository(db)
    job = repo.enqueue_cleanup(
        resource_type=StorageCleanupResourceType.RECORDING_AUDIO.value,
        storage_kind=StorageKind.AUDIO.value,
        storage_backend="local",
        storage_key="family/fam/recordings/rec-4/original.wav",
    )
    assert repo.claim_next_job(lease_owner="worker_a", lease_seconds=30) is not None
    repo.fail(
        job.cleanup_job_id,
        error_code="storage_auth_failed",
        error_detail="storage cleanup permanently failed",
        lease_owner="worker_a",
    )
    failed = repo.get_job(job.cleanup_job_id)
    assert failed is not None
    assert failed.status == StorageCleanupStatus.FAILED.value
    assert failed.lease_owner is None
    assert failed.completed_at is not None
