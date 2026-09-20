from __future__ import annotations

from datetime import UTC, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mura.orchestration.cleanup import StorageCleanupWorker
from mura.storage.cleanup import (
    StorageCleanupRepository,
    StorageCleanupResourceType,
    StorageCleanupStatus,
    StorageKind,
)
from mura.storage.database import Database, utcnow
from mura.storage.storage_errors import StorageDeleteError


def _aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _db() -> Database:
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    return database


def _queued(repo: StorageCleanupRepository, *, backend: str = "local"):
    return repo.enqueue_cleanup(
        resource_type=StorageCleanupResourceType.RECORDING_AUDIO.value,
        storage_kind=StorageKind.AUDIO.value,
        storage_backend=backend,
        storage_key="family/fam/recordings/rec/original.wav",
        max_attempts=3,
    )


def test_cleanup_worker_completes_deleted_or_already_absent() -> None:
    for result in (True, False):
        db = _db()
        repo = StorageCleanupRepository(db)
        job = _queued(repo)
        storage = MagicMock()
        storage.delete.return_value = result
        worker = StorageCleanupWorker(
            repository=repo,
            storage_targets={(StorageKind.AUDIO.value, "local"): storage},
            worker_id="worker_cleanup",
        )
        assert worker.process_once() is True
        completed = repo.get_job(job.cleanup_job_id)
        assert completed is not None
        assert completed.status == StorageCleanupStatus.COMPLETED.value


def test_cleanup_worker_retries_transient_failure() -> None:
    db = _db()
    repo = StorageCleanupRepository(db)
    job = _queued(repo)
    storage = MagicMock()
    storage.delete.side_effect = StorageDeleteError(
        code="storage_unavailable",
        retryable=True,
        message="temporarily unavailable",
    )
    worker = StorageCleanupWorker(
        repository=repo,
        storage_targets={(StorageKind.AUDIO.value, "local"): storage},
        worker_id="worker_cleanup",
        retry_base_seconds=1,
        retry_max_seconds=10,
    )
    before = utcnow()
    assert worker.process_once() is True
    deferred = repo.get_job(job.cleanup_job_id)
    assert deferred is not None
    assert deferred.status == StorageCleanupStatus.QUEUED.value
    assert _aware(deferred.next_attempt_at) > _aware(before)
    assert deferred.error_code == "storage_unavailable"


def test_cleanup_worker_terminal_auth_failure_does_not_retry() -> None:
    db = _db()
    repo = StorageCleanupRepository(db)
    job = _queued(repo)
    storage = MagicMock()
    storage.delete.side_effect = StorageDeleteError(
        code="storage_auth_failed",
        retryable=False,
        status_code=403,
        message="auth failed",
    )
    worker = StorageCleanupWorker(
        repository=repo,
        storage_targets={(StorageKind.AUDIO.value, "local"): storage},
        worker_id="worker_cleanup",
    )
    assert worker.process_once() is True
    failed = repo.get_job(job.cleanup_job_id)
    assert failed is not None
    assert failed.status == StorageCleanupStatus.FAILED.value
    assert failed.error_code == "storage_auth_failed"


def test_cleanup_worker_crash_recovery_reclaims_expired_lease() -> None:
    db = _db()
    repo = StorageCleanupRepository(db)
    job = _queued(repo)
    now = utcnow()
    first = repo.claim_next_job(
        lease_owner="worker_dead", lease_seconds=5, now=now
    )
    assert first is not None

    storage = MagicMock()
    storage.delete.return_value = True
    worker = StorageCleanupWorker(
        repository=repo,
        storage_targets={(StorageKind.AUDIO.value, "local"): storage},
        worker_id="worker_recovery",
    )
    # process_once uses the real clock, so backdate the persisted expiry.
    with db.session_factory.begin() as session:
        row = session.get(type(first), job.cleanup_job_id)
        assert row is not None
        row.lease_expires_at = utcnow() - timedelta(seconds=1)

    assert worker.process_once() is True
    completed = repo.get_job(job.cleanup_job_id)
    assert completed is not None
    assert completed.status == StorageCleanupStatus.COMPLETED.value
    assert completed.attempts == 2


def test_cleanup_worker_fails_closed_when_recorded_backend_is_unconfigured() -> None:
    db = _db()
    repo = StorageCleanupRepository(db)
    job = _queued(repo, backend="supabase")
    worker = StorageCleanupWorker(
        repository=repo,
        storage_targets={},
        worker_id="worker_cleanup",
    )
    assert worker.process_once() is True
    failed = repo.get_job(job.cleanup_job_id)
    assert failed is not None
    assert failed.status == StorageCleanupStatus.FAILED.value
    assert failed.error_code == "storage_backend_unconfigured"
