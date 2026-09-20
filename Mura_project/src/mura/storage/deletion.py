"""Transactional recording deletion with durable physical cleanup."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select

from mura.observability import ProcessingTraceRow
from mura.storage.archive import ArchiveClaimRow
from mura.storage.audio import AudioStorage
from mura.storage.cleanup import (
    StorageCleanupRepository,
    StorageCleanupResourceType,
    StorageKind,
)
from mura.storage.database import (
    Database,
    PipelineResultRow,
    ProcessingJobRow,
    RecordingRow,
)


@dataclass(frozen=True)
class RecordingDeletion:
    recording_id: str
    rows_deleted: bool
    cleanup_enqueued: bool
    cleanup_job_id: str | None = None
    # Kept for compatibility with callers that used the old synchronous result.
    # Physical erasure now belongs exclusively to StorageCleanupWorker.
    audio_deleted: bool = False
    audio_missing: bool = False


class RecordingDeletionService:
    def __init__(
        self,
        database: Database,
        storage: AudioStorage | None = None,
        *,
        cleanup_repository: StorageCleanupRepository | None = None,
        default_storage_backend: str | None = None,
        cleanup_max_attempts: int = 8,
    ) -> None:
        self.database = database
        self.cleanup_repository = cleanup_repository or StorageCleanupRepository(database)
        backend = getattr(storage, "backend", None)
        self.default_storage_backend = (
            default_storage_backend
            or getattr(backend, "value", backend)
            or "local"
        )
        self.cleanup_max_attempts = cleanup_max_attempts

    def delete_recording(self, *, family_id: str, recording_id: str) -> RecordingDeletion:
        """Atomically make a recording unreachable and queue physical erasure."""

        with self.database.session_factory.begin() as session:
            recording = session.scalar(
                select(RecordingRow)
                .where(
                    RecordingRow.recording_id == recording_id,
                    RecordingRow.family_id == family_id,
                )
                .with_for_update()
            )
            if recording is None:
                return RecordingDeletion(
                    recording_id=recording_id,
                    rows_deleted=False,
                    cleanup_enqueued=False,
                )

            cleanup_job_id: str | None = None
            physical_key = recording.storage_key or recording.audio_path
            if physical_key:
                # Pre-storage-abstraction rows contain an absolute audio_path and
                # no storage_key. Keep those independently retryable too.
                backend = (
                    recording.storage_backend or self.default_storage_backend
                    if recording.storage_key
                    else "legacy_local"
                )
                cleanup = self.cleanup_repository.enqueue_cleanup(
                    resource_type=StorageCleanupResourceType.RECORDING_AUDIO.value,
                    storage_kind=StorageKind.AUDIO.value,
                    storage_backend=str(backend),
                    storage_key=str(physical_key),
                    max_attempts=self.cleanup_max_attempts,
                    session=session,
                )
                cleanup_job_id = cleanup.cleanup_job_id

            # Removing the job is the cancellation fence for an in-flight
            # RecordingJobWorker. Its final archive transaction requires and
            # locks this row; after this commit finalization fails and the
            # archive transaction rolls back instead of resurrecting data.
            session.execute(
                delete(ProcessingJobRow).where(ProcessingJobRow.recording_id == recording_id)
            )
            session.execute(
                delete(PipelineResultRow).where(PipelineResultRow.recording_id == recording_id)
            )
            session.execute(
                delete(ProcessingTraceRow).where(ProcessingTraceRow.recording_id == recording_id)
            )
            session.execute(
                delete(ArchiveClaimRow).where(ArchiveClaimRow.recording_id == recording_id)
            )
            session.delete(recording)

            return RecordingDeletion(
                recording_id=recording_id,
                rows_deleted=True,
                cleanup_enqueued=cleanup_job_id is not None,
                cleanup_job_id=cleanup_job_id,
            )
