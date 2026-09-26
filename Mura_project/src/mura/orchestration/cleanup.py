"""Lease-based worker for durable physical object erasure."""

from __future__ import annotations

import logging
import threading
from datetime import timedelta
from typing import Any

from mura.leases import LeaseHeartbeat, LeaseOwnershipLost, new_worker_id
from mura.reliability.failures import calculate_retry_delay, classify_failure
from mura.sentry import capture_exception
from mura.storage.cleanup import (
    StorageCleanupJobRow,
    StorageCleanupRepository,
    StorageKind,
)
from mura.storage.database import utcnow
from mura.storage.storage_errors import StorageDeleteError

logger = logging.getLogger(__name__)


class StorageCleanupWorker:
    def __init__(
        self,
        *,
        repository: StorageCleanupRepository,
        storage_targets: dict[tuple[str, str], Any],
        poll_interval_seconds: float = 2.0,
        lease_seconds: float = 120.0,
        heartbeat_seconds: float = 30.0,
        retry_base_seconds: float = 5.0,
        retry_max_seconds: float = 3600.0,
        worker_id: str | None = None,
    ) -> None:
        self.repository = repository
        self.storage_targets = storage_targets
        self.poll_interval_seconds = poll_interval_seconds
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.worker_id = worker_id or new_worker_id()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def request_stop(self) -> None:
        self._stop_event.set()

    def stop(self, timeout_seconds: float = 5.0) -> None:
        self.request_stop()
        if self._thread is not None:
            self._thread.join(timeout_seconds)

    def run_forever(self) -> None:
        while not self._stop_event.is_set():
            if not self.process_once():
                self._stop_event.wait(self.poll_interval_seconds)

    def process_once(self) -> bool:
        job = self.repository.claim_next_job(
            lease_owner=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if job is None:
            return False

        heartbeat = LeaseHeartbeat(
            job_id=job.cleanup_job_id,
            renew=lambda: self.repository.renew_lease(
                job.cleanup_job_id,
                lease_owner=self.worker_id,
                lease_seconds=self.lease_seconds,
            ),
            interval_seconds=self.heartbeat_seconds,
        )
        try:
            with heartbeat:
                self._process_job(job)
        except LeaseOwnershipLost:
            logger.warning(
                "storage_cleanup_lease_lost",
                extra={
                    "event": "storage_cleanup_lease_lost",
                    "cleanup_job_id": job.cleanup_job_id,
                    "resource_type": job.resource_type,
                    "storage_kind": job.storage_kind,
                    "backend": job.storage_backend,
                    "attempt": job.attempts,
                },
            )
        return True

    def _storage_for(self, job: StorageCleanupJobRow) -> Any:
        storage = self.storage_targets.get((job.storage_kind, job.storage_backend))
        if storage is None:
            raise StorageDeleteError(
                code="storage_backend_unconfigured",
                retryable=False,
                message="storage backend required by cleanup job is not configured",
            )
        return storage

    def _delete(self, job: StorageCleanupJobRow) -> None:
        storage = self._storage_for(job)
        if job.storage_kind == StorageKind.BOOK_ARTIFACT.value:
            storage.delete(storage_key=job.storage_key)
        elif job.storage_kind == StorageKind.AUDIO.value:
            storage.delete(job.storage_key)
        else:
            raise StorageDeleteError(
                code="storage_kind_invalid",
                retryable=False,
                message="cleanup job storage kind is invalid",
            )

    def _process_job(self, job: StorageCleanupJobRow) -> None:
        logger.info(
            "storage_cleanup_claimed",
            extra={
                "event": "storage_cleanup_claimed",
                "cleanup_job_id": job.cleanup_job_id,
                "resource_type": job.resource_type,
                "storage_kind": job.storage_kind,
                "backend": job.storage_backend,
                "attempt": job.attempts,
            },
        )
        try:
            # Both True (deleted now) and False (already absent / 404) are the
            # same successful final state.
            self._delete(job)
            self.repository.complete(
                job.cleanup_job_id,
                lease_owner=self.worker_id,
            )
            logger.info(
                "storage_cleanup_completed",
                extra={
                    "event": "storage_cleanup_completed",
                    "cleanup_job_id": job.cleanup_job_id,
                    "resource_type": job.resource_type,
                    "storage_kind": job.storage_kind,
                    "backend": job.storage_backend,
                    "attempt": job.attempts,
                },
            )
        except LeaseOwnershipLost:
            raise
        except Exception as exc:
            classified = classify_failure(exc)
            capture_exception(
                exc,
                extra={
                    "cleanup_job_id": job.cleanup_job_id,
                    "resource_type": job.resource_type,
                    "storage_kind": job.storage_kind,
                    "backend": job.storage_backend,
                    "attempt": job.attempts,
                    "error_code": classified.error_code,
                },
            )
            # Never persist raw provider text or object keys in lifecycle error
            # detail. The code and Sentry correlation are sufficient.
            safe_detail = "storage cleanup failed; inspect privacy-safe error metadata"
            if classified.is_retryable and job.attempts < job.max_attempts:
                delay = calculate_retry_delay(
                    job.attempts,
                    base_seconds=self.retry_base_seconds,
                    max_seconds=self.retry_max_seconds,
                    retry_after=classified.retry_after_seconds,
                )
                self.repository.defer(
                    job.cleanup_job_id,
                    next_attempt_at=utcnow() + timedelta(seconds=delay),
                    error_code=classified.error_code,
                    error_detail=safe_detail,
                    lease_owner=self.worker_id,
                )
                logger.warning(
                    "storage_cleanup_deferred",
                    extra={
                        "event": "storage_cleanup_deferred",
                        "cleanup_job_id": job.cleanup_job_id,
                        "resource_type": job.resource_type,
                        "storage_kind": job.storage_kind,
                        "backend": job.storage_backend,
                        "attempt": job.attempts,
                        "error_code": classified.error_code,
                        "delay_seconds": delay,
                    },
                )
                return

            self.repository.fail(
                job.cleanup_job_id,
                error_code=classified.error_code,
                error_detail=safe_detail,
                lease_owner=self.worker_id,
            )
            logger.error(
                "storage_cleanup_failed",
                extra={
                    "event": "storage_cleanup_failed",
                    "cleanup_job_id": job.cleanup_job_id,
                    "resource_type": job.resource_type,
                    "storage_kind": job.storage_kind,
                    "backend": job.storage_backend,
                    "attempt": job.attempts,
                    "error_code": classified.error_code,
                },
            )
