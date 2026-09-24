"""Standalone recording and book worker.

The API process used to run the worker on a background thread, which coupled two
very different lifecycles: a request-serving process that should start instantly
and scale on demand, and a job runner that holds a lease for many minutes. This
entrypoint separates them. The API now only submits and reads jobs; this process
claims and executes them.

This process hosts three concurrent worker loops under a unified supervisor:
- RecordingJobWorker: claims and processes audio recording transcription & knowledge extraction
- BookJobWorker: claims and processes family book generation, chapter drafting, and exports
- StorageCleanupWorker: durably erases audio and Book artifacts after relational deletion

Safety rests on DB leases, not on shutdown bookkeeping. If this process dies
mid-job -- SIGTERM, power loss, anything -- the lease simply expires and another
worker reclaims the job. Nothing here ever marks in-flight work completed or
failed just because the process is stopping.
"""

from __future__ import annotations

import concurrent.futures
import logging
import signal
import sys
from types import FrameType
from typing import Any

from mura.asr.factory import build_asr_client
from mura.config import CoreSettings, WorkerQueue
from mura.deepseek import DeepSeekClient, DeepSeekPipelineService
from mura.logging import configure_logging
from mura.orchestration import RecordingJobWorker, build_audio_storage
from mura.orchestration.books import BookJobWorker
from mura.orchestration.cleanup import StorageCleanupWorker
from mura.pipeline import MuraPipeline
from mura.sentry import flush_sentry, init_sentry
from mura.storage.ai_usage import AIUsageLedger
from mura.storage.audio import (
    AudioStorageBackend,
    LegacyLocalAudioStorage,
    LocalAudioStorage,
    SupabaseAudioStorage,
)
from mura.storage.book_artifacts import (
    BookArtifactStorageBackend,
    LocalBookArtifactStorage,
    SupabaseBookArtifactStorage,
    build_book_artifact_storage,
)
from mura.storage.cleanup import StorageCleanupRepository, StorageKind
from mura.storage.database import Database, DatabaseRuntimeSettings, RecordingRepository

logger = logging.getLogger("mura.worker")


def build_recording_worker(
    settings: CoreSettings,
    database: Database | None = None,
    ai_ledger: AIUsageLedger | None = None,
) -> RecordingJobWorker:
    """Construct the RecordingJobWorker and everything it owns."""
    if database is None:
        database = Database(
            settings.database_url,
            runtime=DatabaseRuntimeSettings(
                pool_size=settings.db_pool_size,
                max_overflow=settings.db_max_overflow,
                pool_recycle_seconds=settings.db_pool_recycle_seconds,
                connect_timeout_seconds=settings.db_connect_timeout_seconds,
                statement_timeout_seconds=settings.db_statement_timeout_seconds,
            ),
        )
    if ai_ledger is None:
        ai_ledger = AIUsageLedger(database)

    def _deepseek_usage_hook(
        usage: Any,
        success: bool,
        operation: str,
        error_code: str | None,
        attempt: int,
    ) -> None:
        try:
            ai_ledger.record_usage(
                provider="deepseek",
                model=getattr(usage, "model", "deepseek-v4-flash"),
                operation=operation,
                latency_ms=int(getattr(usage, "request_seconds", 0.0) * 1000),
                success=success,
                input_tokens=getattr(usage, "prompt_tokens", None),
                output_tokens=getattr(usage, "completion_tokens", None),
                cached_input_tokens=getattr(usage, "prompt_cache_hit_tokens", None),
                attempt=attempt,
                error_code=error_code,
            )
        except Exception as exc:
            logger.warning("failed to record deepseek usage event: %s", exc)

    pipeline = MuraPipeline(
        DeepSeekPipelineService(
            DeepSeekClient(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
                primary_model=settings.deepseek_model,
                fallback_model=settings.deepseek_fallback_model,
                on_usage=_deepseek_usage_hook,
            ),
            focused_extraction=True,
        )
    )
    return RecordingJobWorker(
        repository=RecordingRepository(database),
        pipeline=pipeline,
        storage=build_audio_storage(settings),
        asr_client=build_asr_client(settings),
        poll_interval_seconds=settings.job_poll_interval_seconds,
        asr_retry_seconds=settings.asr_retry_seconds,
        lease_seconds=settings.job_lease_seconds,
        heartbeat_seconds=settings.job_heartbeat_seconds,
        ai_ledger=ai_ledger,
    )


def build_book_worker(
    settings: CoreSettings,
    database: Database | None = None,
    ai_ledger: AIUsageLedger | None = None,
) -> BookJobWorker:
    """Construct the BookJobWorker and everything it owns."""
    if database is None:
        database = Database(
            settings.database_url,
            runtime=DatabaseRuntimeSettings(
                pool_size=settings.db_pool_size,
                max_overflow=settings.db_max_overflow,
                pool_recycle_seconds=settings.db_pool_recycle_seconds,
                connect_timeout_seconds=settings.db_connect_timeout_seconds,
                statement_timeout_seconds=settings.db_statement_timeout_seconds,
            ),
        )
    if ai_ledger is None:
        ai_ledger = AIUsageLedger(database)

    def _deepseek_usage_hook(
        usage: Any,
        success: bool,
        operation: str,
        error_code: str | None,
        attempt: int,
    ) -> None:
        try:
            ai_ledger.record_usage(
                provider="deepseek",
                model=getattr(usage, "model", "deepseek-v4-flash"),
                operation=operation,
                latency_ms=int(getattr(usage, "request_seconds", 0.0) * 1000),
                success=success,
                input_tokens=getattr(usage, "prompt_tokens", None),
                output_tokens=getattr(usage, "completion_tokens", None),
                cached_input_tokens=getattr(usage, "prompt_cache_hit_tokens", None),
                attempt=attempt,
                error_code=error_code,
            )
        except Exception as exc:
            logger.warning("failed to record deepseek book usage event: %s", exc)

    deepseek_client = DeepSeekClient(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        primary_model=settings.deepseek_model,
        fallback_model=settings.deepseek_fallback_model,
        on_usage=_deepseek_usage_hook,
    )

    artifact_storage = build_book_artifact_storage(settings)

    return BookJobWorker(
        db=database,
        deepseek_client=deepseek_client,
        artifact_storage=artifact_storage,
        ai_ledger=ai_ledger,
        poll_interval_seconds=settings.book_job_poll_interval_seconds,
        lease_seconds=settings.book_job_lease_seconds,
        heartbeat_seconds=settings.book_job_heartbeat_seconds,
        retry_base_seconds=settings.book_retry_base_seconds,
        retry_max_seconds=settings.book_retry_max_seconds,
    )


def build_cleanup_worker(
    settings: CoreSettings,
    database: Database | None = None,
) -> StorageCleanupWorker:
    if database is None:
        database = Database(
            settings.database_url,
            runtime=DatabaseRuntimeSettings(
                pool_size=settings.db_pool_size,
                max_overflow=settings.db_max_overflow,
                pool_recycle_seconds=settings.db_pool_recycle_seconds,
                connect_timeout_seconds=settings.db_connect_timeout_seconds,
                statement_timeout_seconds=settings.db_statement_timeout_seconds,
            ),
        )

    targets: dict[tuple[str, str], Any] = {
        (
            StorageKind.AUDIO.value,
            "legacy_local",
        ): LegacyLocalAudioStorage(),
        (
            StorageKind.AUDIO.value,
            AudioStorageBackend.LOCAL.value,
        ): LocalAudioStorage(
            settings.audio_storage_dir,
            max_upload_bytes=settings.core_max_upload_mb * 1024 * 1024,
        ),
        (
            StorageKind.BOOK_ARTIFACT.value,
            BookArtifactStorageBackend.LOCAL.value,
        ): LocalBookArtifactStorage(settings.book_storage_dir),
    }
    # Keep both backend implementations available when credentials exist. A
    # cleanup job must use the backend recorded when the object was created,
    # even if the application's current write backend later changes.
    if settings.supabase_url and settings.supabase_service_role_key:
        targets[
            (StorageKind.AUDIO.value, AudioStorageBackend.SUPABASE.value)
        ] = SupabaseAudioStorage(
            url=settings.supabase_url,
            service_role_key=settings.supabase_service_role_key,
            bucket=settings.supabase_storage_bucket,
            max_upload_bytes=settings.core_max_upload_mb * 1024 * 1024,
            timeout_seconds=settings.supabase_storage_timeout_seconds,
        )
        targets[
            (StorageKind.BOOK_ARTIFACT.value, BookArtifactStorageBackend.SUPABASE.value)
        ] = SupabaseBookArtifactStorage(
            url=settings.supabase_url,
            service_role_key=settings.supabase_service_role_key,
            bucket=settings.supabase_books_bucket,
            timeout_seconds=settings.supabase_storage_timeout_seconds,
        )

    return StorageCleanupWorker(
        repository=StorageCleanupRepository(database),
        storage_targets=targets,
        poll_interval_seconds=settings.storage_cleanup_poll_interval_seconds,
        lease_seconds=settings.storage_cleanup_lease_seconds,
        heartbeat_seconds=settings.storage_cleanup_heartbeat_seconds,
        retry_base_seconds=settings.storage_cleanup_retry_base_seconds,
        retry_max_seconds=settings.storage_cleanup_retry_max_seconds,
    )


def build_worker(settings: CoreSettings) -> RecordingJobWorker:
    """Legacy helper returning RecordingJobWorker for backwards compatibility."""
    return build_recording_worker(settings)


class WorkerSupervisor:
    """Supervise the selected durable queue workers in one process."""

    def __init__(
        self,
        recording_worker: RecordingJobWorker | None,
        book_worker: BookJobWorker | None,
        cleanup_worker: StorageCleanupWorker | None,
    ) -> None:
        self.recording_worker = recording_worker
        self.book_worker = book_worker
        self.cleanup_worker = cleanup_worker

    def _workers(self) -> list[RecordingJobWorker | BookJobWorker | StorageCleanupWorker]:
        return [
            worker
            for worker in (
                self.recording_worker,
                self.book_worker,
                self.cleanup_worker,
            )
            if worker is not None
        ]

    def request_stop(self) -> None:
        """Signal selected workers to stop claiming new jobs."""
        for worker in self._workers():
            worker.request_stop()

    def stop(self, timeout_seconds: float = 5.0) -> None:
        """Stop selected workers gracefully."""
        self.request_stop()
        for worker in self._workers():
            worker.stop(timeout_seconds=timeout_seconds)

    def run_forever(self) -> None:
        """Run selected worker loops until stopped or one crashes."""
        workers = self._workers()
        if not workers:
            raise RuntimeError("worker supervisor requires at least one queue")

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(workers), thread_name_prefix="mura-worker"
        ) as executor:
            futures = [executor.submit(worker.run_forever) for worker in workers]

            try:
                while True:
                    done, _ = concurrent.futures.wait(
                        futures,
                        timeout=0.5,
                        return_when=concurrent.futures.FIRST_COMPLETED,
                    )
                    if done:
                        break
            finally:
                self.request_stop()

            for future in futures:
                if future.done():
                    exc = future.exception()
                    if exc:
                        logger.error("worker crashed with exception: %s", exc, exc_info=exc)
                        raise exc

            for future in futures:
                future.result()


def build_worker_supervisor(settings: CoreSettings) -> WorkerSupervisor:
    """Construct only the queue workers selected for this process."""
    database = Database(
        settings.database_url,
        runtime=DatabaseRuntimeSettings(
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_recycle_seconds=settings.db_pool_recycle_seconds,
            connect_timeout_seconds=settings.db_connect_timeout_seconds,
            statement_timeout_seconds=settings.db_statement_timeout_seconds,
        ),
    )
    selected = set(settings.worker_queues)
    ai_ledger = (
        AIUsageLedger(database)
        if selected & {WorkerQueue.RECORDING, WorkerQueue.BOOK}
        else None
    )
    recording_worker = (
        build_recording_worker(settings, database=database, ai_ledger=ai_ledger)
        if WorkerQueue.RECORDING in selected
        else None
    )
    book_worker = (
        build_book_worker(settings, database=database, ai_ledger=ai_ledger)
        if WorkerQueue.BOOK in selected
        else None
    )
    cleanup_worker = (
        build_cleanup_worker(settings, database=database)
        if WorkerQueue.CLEANUP in selected
        else None
    )
    return WorkerSupervisor(
        recording_worker=recording_worker,
        book_worker=book_worker,
        cleanup_worker=cleanup_worker,
    )


def install_signal_handlers(
    target: WorkerSupervisor | RecordingJobWorker | BookJobWorker | StorageCleanupWorker | Any,
) -> None:
    """Stop claiming new work on termination. Works on Windows and POSIX."""

    def handle(signum: int, _frame: FrameType | None) -> None:
        logger.info("worker received signal %s; finishing current work", signum)
        # Only stops the claim loop. In-flight processing is left alone and its
        # lease expiry, not this handler, is what makes the job recoverable.
        target.request_stop()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        received = getattr(signal, name, None)
        if received is not None:
            try:
                signal.signal(received, handle)
            except (OSError, ValueError):
                # Not all signals are settable on every platform or thread.
                logger.debug("signal %s unavailable in this environment", name)


def main() -> int:
    try:
        settings = CoreSettings()  # type: ignore[call-arg]
    except Exception:
        # Never echo the validation error: it can quote supplied secrets.
        logger.error("worker is not configured; refusing to start")
        return 2

    configure_logging(
        "mura-worker",
        environment=settings.environment.value,
        log_level=settings.log_level,
        log_format=settings.log_format,
    )
    init_sentry(
        "mura-worker",
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment or settings.environment.value,
        traces_sample_rate=settings.sentry_traces_sample_rate,
    )

    supervisor = build_worker_supervisor(settings)
    install_signal_handlers(supervisor)
    logger.info(
        "worker_started",
        extra={
            "event": "worker_started",
            "worker_queues": [queue.value for queue in settings.worker_queues],
            "recording_worker_id": (
                supervisor.recording_worker.worker_id
                if supervisor.recording_worker is not None
                else None
            ),
            "book_worker_id": (
                supervisor.book_worker.worker_id
                if supervisor.book_worker is not None
                else None
            ),
            "cleanup_worker_id": (
                supervisor.cleanup_worker.worker_id
                if supervisor.cleanup_worker is not None
                else None
            ),
            "recording_lease_seconds": settings.job_lease_seconds,
            "book_lease_seconds": settings.book_job_lease_seconds,
            "recording_heartbeat_seconds": settings.job_heartbeat_seconds,
            "book_heartbeat_seconds": settings.book_job_heartbeat_seconds,
            "cleanup_lease_seconds": settings.storage_cleanup_lease_seconds,
            "cleanup_heartbeat_seconds": settings.storage_cleanup_heartbeat_seconds,
        },
    )
    try:
        supervisor.run_forever()
    except KeyboardInterrupt:
        logger.info("worker interrupted")
    finally:
        supervisor.stop()
        flush_sentry()
    logger.info("worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
