from __future__ import annotations

import logging
import threading
import time

from mura.asr import ASRClientError, RemoteASRClient
from mura.asr.whisper import WhisperASRClient
from mura.domain.models import (
    AudioLanguage,
    OutputLanguage,
    PipelineRequest,
    PipelineResult,
)
from mura.jobs import JobStatus
from mura.leases import LeaseHeartbeat, LeaseOwnershipLost, new_worker_id
from mura.logging import WorkerJobContextManager
from mura.observability import ProcessingTrace, TraceOutcome
from mura.pipeline import MuraPipeline
from mura.sentry import capture_exception
from mura.storage.ai_usage import AIUsageLedger
from mura.storage.archive import ArchiveRepository
from mura.storage.audio import AudioStorage
from mura.storage.completion import (
    defer_recording_job,
    fail_recording_job,
    finalize_recording_job,
)
from mura.storage.conflict_resolution import ConflictResolutionService
from mura.storage.database import ProcessingJobRow, RecordingRepository, RecordingRow
from mura.storage.generic_claims import persist_generic_claims
from mura.storage.recording_audio import materialize_recording_audio

logger = logging.getLogger(__name__)


class RecordingJobWorker:
    def __init__(
        self,
        *,
        repository: RecordingRepository,
        pipeline: MuraPipeline,
        asr_client: RemoteASRClient | WhisperASRClient,
        storage: AudioStorage | None = None,
        poll_interval_seconds: float = 1.0,
        asr_retry_seconds: float = 15.0,
        lease_seconds: float = 300.0,
        heartbeat_seconds: float = 60.0,
        worker_id: str | None = None,
        ai_ledger: AIUsageLedger | None = None,
    ) -> None:
        self.repository = repository
        self.archive_repository = ArchiveRepository(repository.database)
        self.conflict_resolution = ConflictResolutionService(repository.database)
        self.pipeline = pipeline
        self.asr_client = asr_client
        #: Optional so legacy recordings with only audio_path still process.
        self.storage = storage
        self.poll_interval_seconds = poll_interval_seconds
        self.asr_retry_seconds = asr_retry_seconds
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        #: Operational identity only. Never a user, never exposed publicly.
        self.worker_id = worker_id or new_worker_id()
        self.ai_ledger = ai_ledger or AIUsageLedger(repository.database)
        if hasattr(self.asr_client, "on_usage") and getattr(self.asr_client, "on_usage") is None:
            self.asr_client.on_usage = self._record_asr_usage
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def _record_asr_usage(
        self,
        *,
        provider: str,
        model: str,
        operation: str,
        latency_ms: int,
        success: bool,
        audio_seconds: float | None = None,
        error_code: str | None = None,
    ) -> None:
        try:
            self.ai_ledger.record_usage(
                provider=provider,
                model=model,
                operation=operation,
                latency_ms=latency_ms,
                success=success,
                audio_seconds=audio_seconds,
                error_code=error_code,
            )
        except Exception as exc:
            logger.warning("failed to record asr usage event: %s", exc)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            name="mura-recording-worker",
            daemon=True,
        )
        self._thread.start()

    def request_stop(self) -> None:
        """Stop claiming new work without disturbing the job in flight.

        Deliberately does not touch the current job: a shutdown must never mark
        in-progress work completed or failed. Lease expiry is the recovery
        mechanism if the process does not survive to finish it.
        """

        self._stop_event.set()

    def run_forever(self) -> None:
        """Claim and process until asked to stop. Used by the standalone worker."""

        self._run()

    def stop(self, timeout_seconds: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout_seconds)

    def process_once(self) -> bool:
        job = self.repository.claim_next_job(
            lease_owner=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if job is None:
            return False
        # The heartbeat must tick while ASR/DeepSeek block the main flow, so it
        # runs on its own thread with its own short-lived sessions.
        heartbeat = LeaseHeartbeat(
            job_id=job.job_id,
            renew=lambda: self.repository.renew_lease(
                job.job_id,
                lease_owner=self.worker_id,
                lease_seconds=self.lease_seconds,
            ),
            interval_seconds=self.heartbeat_seconds,
        )
        try:
            with heartbeat:
                self._process_job(job)
        except LeaseOwnershipLost:
            # Another worker legitimately reclaimed this job while we stalled.
            # Abandon the attempt rather than overwrite its work.
            logger.warning("lease ownership lost; abandoning attempt")
        return True

    def _run(self) -> None:
        while not self._stop_event.is_set():
            processed = self.process_once()
            if not processed:
                self._stop_event.wait(self.poll_interval_seconds)

    def _process_job(self, job: ProcessingJobRow) -> None:
        recording = self.repository.get_recording(job.recording_id)
        if recording is None:
            self.repository.fail_job(
                job.job_id,
                error_code="recording_missing",
                error_detail=f"recording {job.recording_id} does not exist",
            )
            return

        attempt = job.attempts + 1
        with WorkerJobContextManager(
            job_id=job.job_id,
            recording_id=recording.recording_id,
            family_id=recording.family_id,
            attempt=attempt,
            worker_id=self.worker_id,
        ):
            self._execute_job_attempt(job, recording, attempt)

    def _execute_job_attempt(
        self,
        job: ProcessingJobRow,
        recording: RecordingRow,
        attempt: int,
    ) -> None:
        logger.info(
            "job_claimed",
            extra={
                "event": "job_claimed",
                "job_id": job.job_id,
                "recording_id": recording.recording_id,
                "family_id": recording.family_id,
                "attempt": attempt,
                "worker_id": self.worker_id,
            },
        )
        trace = ProcessingTrace(
            job_id=job.job_id,
            recording_id=recording.recording_id,
            family_id=recording.family_id,
            attempt=attempt,
        )
        trace.instant(
            stage="job",
            event_name="job_claimed",
            attributes={"attempt": attempt},
        )

        # Only a tunnelled recogniser has to announce itself first. A hosted
        # one is reached directly, and deferring its jobs to wait for a
        # `worker_registrations` row that will never be written would park every
        # recording forever while the recogniser sat there working.
        worker_url: str | None = None
        # Defaults to True: a client that does not declare itself gets the
        # conservative path and waits, rather than silently skipping the gate.
        if getattr(self.asr_client, "requires_registered_worker", True):
            worker = self.repository.current_worker()
            if worker is None or worker.status != "ready":
                trace.instant(
                    stage="asr_transcription",
                    event_name="worker_unavailable",
                    outcome=TraceOutcome.DEFERRED,
                    attributes={"error_code": "asr_worker_unavailable"},
                )
                logger.info(
                    "job_retry_scheduled",
                    extra={
                        "event": "job_retry_scheduled",
                        "error_code": "asr_worker_unavailable",
                        "retry_after_seconds": self.asr_retry_seconds,
                    },
                )
                defer_recording_job(
                    self.repository.database,
                    job_id=job.job_id,
                    error_code="asr_worker_unavailable",
                    error_detail="no ready ASR worker is registered",
                    retry_after_seconds=self.asr_retry_seconds,
                    trace_events=trace.events,
                    lease_owner=self.worker_id,
                )
                return
            worker_url = worker.url

        logger.info("job_started", extra={"event": "job_started", "attempt": attempt})
        trace.start("asr_transcription")
        try:
            with materialize_recording_audio(recording, self.storage) as audio_file:
                # Only an explicit choice travels to the recogniser. "auto"
                # and "mixed" pass nothing, so code-switched speech still
                # reaches an unpinned decoder.
                declared = recording.audio_language
                transcript = self.asr_client.transcribe(
                    worker_url=worker_url,
                    audio_path=audio_file,
                    recording_id=recording.recording_id,
                    content_type=recording.content_type,
                    **(
                        {"declared_language": declared}
                        if declared in {"ru", "kk"}
                        and hasattr(self.asr_client, "requires_registered_worker")
                        and not self.asr_client.requires_registered_worker
                        else {}
                    ),
                )
        except ASRClientError as exc:
            # Deletion removes both the recording and its job atomically. A
            # provider response racing that commit is obsolete work, not a
            # failure to requeue against a row that intentionally no longer
            # exists.
            if self.repository.get_recording(recording.recording_id) is None:
                logger.info(
                    "job_abandoned_deleted",
                    extra={"event": "job_abandoned_deleted", "attempt": attempt},
                )
                return
            outcome = TraceOutcome.DEFERRED if exc.retryable else TraceOutcome.ERROR
            trace.finish(
                "asr_transcription",
                outcome=outcome,
                event_name="stage_deferred" if exc.retryable else "stage_failed",
                attributes={
                    "error_code": "asr_temporarily_unavailable" if exc.retryable else "asr_failed"
                },
            )
            if exc.retryable:
                logger.info(
                    "job_retry_scheduled",
                    extra={
                        "event": "job_retry_scheduled",
                        "error_code": "asr_temporarily_unavailable",
                        "retry_after_seconds": self.asr_retry_seconds,
                    },
                )
                defer_recording_job(
                    self.repository.database,
                    job_id=job.job_id,
                    error_code="asr_temporarily_unavailable",
                    error_detail=(
                        "ASR worker temporarily unavailable; inspect privacy-safe trace codes"
                    ),
                    retry_after_seconds=self.asr_retry_seconds,
                    trace_events=trace.events,
                    lease_owner=self.worker_id,
                )
            else:
                logger.error(
                    "job_failed",
                    exc_info=exc,
                    extra={
                        "event": "job_failed",
                        "error_code": "asr_failed",
                    },
                )
                capture_exception(
                    exc,
                    tags={"error_code": "asr_failed"},
                    extra={
                        "job_id": job.job_id,
                        "recording_id": recording.recording_id,
                        "attempt": attempt,
                    },
                )
                fail_recording_job(
                    self.repository.database,
                    job_id=job.job_id,
                    error_code="asr_failed",
                    error_detail="ASR transcription failed; inspect privacy-safe trace codes",
                    trace_events=trace.events,
                    lease_owner=self.worker_id,
                )
            return

        trace.finish(
            "asr_transcription",
            outcome=TraceOutcome.SUCCESS,
            attributes={
                "segment_count": len(transcript.segments),
                "duration_seconds": transcript.duration_seconds,
            },
        )

        # Recognition takes seconds and extraction most of a minute. Publishing
        # the text now lets the speaker read their own words while people,
        # events and stories are still being found. A failure here costs only
        # that early view, so it must never stop the recording being processed.
        try:
            self.repository.save_transcript_preview(
                job.job_id, transcript.full_text, lease_owner=self.worker_id
            )
        except LeaseOwnershipLost:
            raise
        except Exception:
            logger.warning("transcript preview not saved job_id=%s", job.job_id)

        status_by_stage = {
            "cleaning": JobStatus.CLEANING,
            "extracting": JobStatus.EXTRACTING,
            "resolving": JobStatus.RESOLVING,
            "planning_long_form": JobStatus.CLEANING,
            "merging_windows": JobStatus.EXTRACTING,
            "global_validation": JobStatus.EXTRACTING,
            "resolving_entities": JobStatus.RESOLVING,
        }
        active_pipeline_stage: str | None = None

        def report_stage(stage: str) -> None:
            nonlocal active_pipeline_stage
            if active_pipeline_stage is not None:
                trace.finish(active_pipeline_stage, outcome=TraceOutcome.SUCCESS)
            active_pipeline_stage = stage
            trace.start(stage)
            status = status_by_stage.get(stage)
            if status is None and stage.startswith("window_"):
                status = JobStatus.CLEANING if stage.endswith("_cleaning") else JobStatus.EXTRACTING
            if status is not None:
                self.repository.update_job_stage(
                    job.job_id, status, stage, lease_owner=self.worker_id
                )

        try:
            resolution_context = self.archive_repository.build_resolution_context(
                family_id=recording.family_id,
                speaker_id=recording.speaker_id,
            )
            trace.instant(
                stage="archive_context",
                event_name="context_loaded",
                attributes={"known_person_count": len(resolution_context.profiles)},
            )
            result = self.pipeline.process(
                PipelineRequest(
                    transcript=transcript,
                    speaker_id=recording.speaker_id,
                    speaker_name=recording.speaker_name,
                    known_people=[profile.person for profile in resolution_context.profiles],
                    # Reconstructed from the durable row: the upload request is
                    # long gone by the time the worker runs. NULL means a
                    # pre-migration recording, which reads as the defaults.
                    requested_audio_language=AudioLanguage(recording.audio_language or "auto"),
                    requested_output_language=OutputLanguage(
                        recording.output_language or "same_as_transcript"
                    ),
                ),
                stage_callback=report_stage,
                resolution_context=resolution_context,
            )
            if active_pipeline_stage is not None:
                trace.finish(active_pipeline_stage, outcome=TraceOutcome.SUCCESS)
                active_pipeline_stage = None

            result = result.model_copy(
                update={
                    "processing": {
                        **result.processing,
                        "trace_id": trace.trace_id,
                    }
                }
            )
            self.repository.update_job_stage(
                job.job_id,
                JobStatus.RESOLVING,
                "persisting_archive",
            )
            trace.start("archive_persistence")
            with self.repository.database.session_factory.begin() as session:
                self.conflict_resolution.persist_pipeline_result(
                    session,
                    recording=recording,
                    result=result,
                )
                persist_generic_claims(
                    session,
                    recording=recording,
                    result=result,
                )
                trace.finish(
                    "archive_persistence",
                    outcome=TraceOutcome.INFO,
                    event_name="transaction_prepared",
                    attributes=_pipeline_trace_attributes(result),
                )
                finalize_recording_job(
                    session,
                    job_id=job.job_id,
                    result=result,
                    trace_events=trace.events,
                    lease_owner=self.worker_id,
                )
                logger.info(
                    "job_completed",
                    extra={
                        "event": "job_completed",
                        "attempt": attempt,
                    },
                )
        except Exception as exc:
            # Final archive persistence is transactional with job finalization.
            # If deletion won the race, the job/recording row is gone and that
            # transaction has rolled back; do not recreate state or crash the
            # supervisor by trying to fail a deliberately deleted job.
            if self.repository.get_recording(recording.recording_id) is None:
                logger.info(
                    "job_abandoned_deleted",
                    extra={"event": "job_abandoned_deleted", "attempt": attempt},
                )
                return
            logger.error(
                "job_failed",
                exc_info=exc,
                extra={
                    "event": "job_failed",
                    "error_code": "pipeline_failed",
                },
            )
            capture_exception(
                exc,
                tags={"error_code": "pipeline_failed"},
                extra={
                    "job_id": job.job_id,
                    "recording_id": recording.recording_id,
                    "attempt": attempt,
                },
            )
            trace.fail_active_stages(error_code="pipeline_failed")
            trace.instant(
                stage="job",
                event_name="job_failed",
                outcome=TraceOutcome.ERROR,
                attributes={"error_code": "pipeline_failed"},
            )
            fail_recording_job(
                self.repository.database,
                job_id=job.job_id,
                error_code="pipeline_failed",
                error_detail="pipeline processing failed; inspect privacy-safe trace codes",
                trace_events=trace.events,
                lease_owner=self.worker_id,
            )

    def wait_until_idle(self, timeout_seconds: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if not self.process_once():
                return True
        return False


def _pipeline_trace_attributes(
    result: PipelineResult,
) -> dict[str, str | int | float | bool | None]:
    attributes: dict[str, str | int | float | bool | None] = {
        "pipeline_seconds": result.processing.get("total_seconds"),
        "people_count": len(result.extraction.people_mentions),
        "relationship_count": len(result.extraction.relationship_claims),
        "resolution_count": len(result.resolutions),
        "question_count": len(result.extraction.unresolved_questions),
    }
    for usage_name in ("cleaner_usage", "extractor_usage"):
        usage = result.processing.get(usage_name)
        if not isinstance(usage, dict):
            continue
        prefix = usage_name.removesuffix("_usage")
        for source_key, target_key in (
            ("prompt_tokens", "input_units"),
            ("completion_tokens", "output_units"),
            ("total_tokens", "total_units"),
        ):
            value = usage.get(source_key)
            if isinstance(value, (int, float)):
                attributes[f"{prefix}_{target_key}"] = value
    return attributes
