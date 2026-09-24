"""Book generation job worker and orchestration.

Orchestrates the end-to-end Family Book generation pipeline:
1. Snapshot Compilation (Grounding Compiler)
2. Planning & Blueprint Generation (LLM + Validation & Repair)
3. Chapter Iteration (Drafting -> Deterministic Gates & Review -> Optional Repair -> Continuity)
4. Document Export (PDF & EPUB 3)
5. Completion and correlation
"""

from __future__ import annotations

import logging
import threading
from datetime import timedelta
from typing import Any

from mura.book.blueprint_validation import BlueprintLimits
from mura.book.continuity import (
    create_initial_continuity_state,
    update_continuity,
)
from mura.book.exporter import (
    BookExportCancelled,
    ExportEngineUnavailable,
    ExportService,
    PDFRenderer,
)
from mura.book.planner import plan_book
from mura.book.prompts import (
    BOOK_PLANNER_PROMPT_VERSION,
)
from mura.book.reviewer import review_chapter
from mura.book.snapshot_validation import validate_snapshot_closure
from mura.book.writer import repair_chapter, write_chapter
from mura.domain.book_models import (
    BookBlueprint,
    BookLanguage,
    BookSourceSnapshot,
    BookStage,
    BookStatus,
    ChapterDraft,
    ChapterPlan,
    ChapterStatus,
    ContinuityState,
    ExportFormat,
    ExportStatus,
    ReviewStatus,
)
from mura.leases import LeaseHeartbeat, LeaseOwnershipLost, new_worker_id
from mura.logging import BookChapterContextManager, WorkerBookJobContextManager
from mura.reliability.failures import calculate_retry_delay, classify_failure
from mura.sentry import capture_exception
from mura.storage.ai_usage import AIUsageLedger
from mura.storage.book import (
    BookChapterRepository,
    BookContinuityRepository,
    BookExportRepository,
    BookJobRepository,
    BookJobRow,
    BookPlanRepository,
    BookRepository,
    BookRow,
    BookSourceSnapshotRepository,
)
from mura.storage.book_artifacts import BookArtifactStorage
from mura.storage.database import Database, utcnow

logger = logging.getLogger(__name__)


def _require_repair_telemetry(telemetry: dict[str, Any]) -> tuple[str, str]:
    """Validate repair metadata before it can be persisted as durable Book state."""
    prompt_version = telemetry.get("prompt_version")
    model = telemetry.get("model")
    if not isinstance(prompt_version, str) or not prompt_version:
        raise RuntimeError("book repair telemetry is missing prompt_version")
    if not isinstance(model, str) or not model:
        raise RuntimeError("book repair telemetry is missing model")
    return prompt_version, model


class BookJobWorker:
    def __init__(
        self,
        *,
        db: Database,
        deepseek_client: Any,
        artifact_storage: BookArtifactStorage,
        pdf_renderer: PDFRenderer | None = None,
        ai_ledger: AIUsageLedger | None = None,
        poll_interval_seconds: float = 1.0,
        lease_seconds: float = 300.0,
        heartbeat_seconds: float = 60.0,
        retry_base_seconds: float = 5.0,
        retry_max_seconds: float = 300.0,
        max_repair_attempts: int = 2,
        worker_id: str | None = None,
        book_repo: BookRepository | None = None,
        job_repo: BookJobRepository | None = None,
        snapshot_repo: BookSourceSnapshotRepository | None = None,
        plan_repo: BookPlanRepository | None = None,
        chapter_repo: BookChapterRepository | None = None,
        continuity_repo: BookContinuityRepository | None = None,
        export_repo: BookExportRepository | None = None,
        export_service: ExportService | None = None,
        blueprint_limits: BlueprintLimits | None = None,
    ) -> None:
        self.db = db
        self.deepseek_client = deepseek_client
        self.artifact_storage = artifact_storage
        self.pdf_renderer = pdf_renderer
        self.ai_ledger = ai_ledger or AIUsageLedger(db)
        self.poll_interval_seconds = poll_interval_seconds
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.max_repair_attempts = max_repair_attempts
        self.worker_id = worker_id or new_worker_id()
        self.blueprint_limits = blueprint_limits

        self.book_repo = book_repo or BookRepository(db)
        self.job_repo = job_repo or BookJobRepository(db)
        self.snapshot_repo = snapshot_repo or BookSourceSnapshotRepository(db)
        self.plan_repo = plan_repo or BookPlanRepository(db)
        self.chapter_repo = chapter_repo or BookChapterRepository(db)
        self.continuity_repo = continuity_repo or BookContinuityRepository(db)
        self.export_repo = export_repo or BookExportRepository(db)
        self.export_service = export_service or ExportService(
            book_repo=self.book_repo,
            chapter_repo=self.chapter_repo,
            export_repo=self.export_repo,
            artifact_storage=self.artifact_storage,
            pdf_renderer=self.pdf_renderer,
        )

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            name="mura-book-worker",
            daemon=True,
        )
        self._thread.start()

    def request_stop(self) -> None:
        self._stop_event.set()

    def run_forever(self) -> None:
        self._run()

    def stop(self, timeout_seconds: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout_seconds)

    def process_once(self) -> bool:
        job = self.job_repo.claim_next_job(
            lease_owner=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if job is None:
            return False

        heartbeat = LeaseHeartbeat(
            job_id=job.job_id,
            renew=lambda: self.job_repo.renew_lease(
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
            logger.warning("lease ownership lost for book job; abandoning attempt")
        return True

    def _run(self) -> None:
        while not self._stop_event.is_set():
            processed = self.process_once()
            if not processed:
                self._stop_event.wait(self.poll_interval_seconds)

    def _check_cancellation(self, job: BookJobRow, book_id: str) -> bool:
        # Deletion removes the Book and its queue row atomically. Missing means
        # deletion already won; detached work must stop without recreating state.
        if self.book_repo.get_book_unscoped(book_id) is None:
            logger.info(
                "book_job_abandoned_deleted",
                extra={"event": "book_job_abandoned_deleted", "book_id": book_id},
            )
            return True
        if self.book_repo.is_cancel_requested(book_id):
            self.job_repo.cancel_book_and_job(
                job.job_id,
                book_id=book_id,
                lease_owner=self.worker_id,
            )
            logger.info(
                "book_cancelled",
                extra={
                    "event": "book_cancelled",
                    "book_id": book_id,
                    "job_id": job.job_id,
                },
            )
            return True
        return False

    def _process_job(self, job: BookJobRow) -> None:
        book = self.book_repo.get_book_unscoped(job.book_id)
        if book is None:
            self.job_repo.fail_job(
                job.job_id,
                error_code="book_missing",
                error_detail=f"book {job.book_id} not found",
                lease_owner=self.worker_id,
            )
            return

        attempt = job.attempts
        with WorkerBookJobContextManager(
            job_id=job.job_id,
            book_id=book.book_id,
            family_id=book.family_id,
            attempt=attempt,
            worker_id=self.worker_id,
        ):
            try:
                self._execute_job_attempt(job, book, attempt)
            except BookExportCancelled:
                # Export publication discovered cancellation/deletion after a
                # potentially long render. Convert an explicit cancellation to
                # CANCELLED; deletion is already represented by the missing
                # Book row and must simply abandon detached work.
                if self._check_cancellation(job, book.book_id):
                    return
                raise
            except LeaseOwnershipLost as lease_exc:
                logger.warning(
                    "book_job_lease_lost",
                    extra={
                        "event": "book_job_lease_lost",
                        "book_id": book.book_id,
                        "job_id": job.job_id,
                        "error": str(lease_exc),
                    },
                )
                return
            except Exception as exc:
                if self.book_repo.get_book_unscoped(book.book_id) is None:
                    logger.info(
                        "book_job_abandoned_deleted",
                        extra={
                            "event": "book_job_abandoned_deleted",
                            "book_id": book.book_id,
                            "job_id": job.job_id,
                        },
                    )
                    return
                classified = classify_failure(exc)
                capture_exception(
                    exc,
                    extra={
                        "job_id": job.job_id,
                        "book_id": book.book_id,
                        "family_id": book.family_id,
                        "attempt": attempt,
                        "failure_category": classified.category.value,
                        "disposition": classified.disposition.value,
                    },
                )

                if classified.is_retryable and attempt < job.max_attempts:
                    delay = calculate_retry_delay(
                        attempt,
                        base_seconds=self.retry_base_seconds,
                        max_seconds=self.retry_max_seconds,
                        retry_after=classified.retry_after_seconds,
                    )
                    next_attempt = utcnow() + timedelta(seconds=delay)
                    logger.warning(
                        "book_job_deferred",
                        extra={
                            "event": "book_job_deferred",
                            "book_id": book.book_id,
                            "job_id": job.job_id,
                            "attempt": attempt,
                            "max_attempts": job.max_attempts,
                            "delay_seconds": delay,
                            "error_code": classified.error_code,
                        },
                    )
                    try:
                        self.job_repo.defer_job(
                            job.job_id,
                            next_attempt_at=next_attempt,
                            error_code=classified.error_code,
                            error_detail=classified.error_detail,
                            lease_owner=self.worker_id,
                        )
                    except Exception as defer_exc:
                        logger.warning("failed to defer book job: %s", defer_exc)
                    return

                logger.error(
                    "book_job_failed",
                    extra={
                        "event": "book_job_failed",
                        "book_id": book.book_id,
                        "job_id": job.job_id,
                        "attempt": attempt,
                        "max_attempts": job.max_attempts,
                        "category": classified.category.value,
                        "error": classified.error_detail,
                    },
                    exc_info=True,
                )
                try:
                    self.job_repo.fail_job(
                        job.job_id,
                        error_code=classified.error_code,
                        error_detail=classified.error_detail,
                        lease_owner=self.worker_id,
                    )
                    self.book_repo.fail_book(
                        book.book_id,
                        error_code=classified.error_code,
                        error_detail=classified.error_detail,
                    )
                except Exception as cleanup_exc:
                    logger.warning("failed to set failed status on book job: %s", cleanup_exc)

    def _execute_job_attempt(
        self,
        job: BookJobRow,
        book: BookRow,
        attempt: int,
    ) -> None:
        logger.info(
            "book_job_claimed",
            extra={
                "event": "book_job_claimed",
                "job_id": job.job_id,
                "book_id": book.book_id,
                "family_id": book.family_id,
                "attempt": attempt,
            },
        )

        if self._check_cancellation(job, book.book_id):
            return

        # 1. PREPARING_SOURCES (immutable snapshot verification)
        snapshot_row = self.snapshot_repo.get_snapshot(book.book_id)
        if snapshot_row is None:
            # A queued book must already own the exact source snapshot compiled
            # by the API. Recompiling here from the live archive would allow
            # later or unrequested recordings to enter an existing book.
            raise RuntimeError(f"source snapshot missing for book {book.book_id}")
        if snapshot_row.family_id != book.family_id:
            raise RuntimeError(f"source snapshot family mismatch for book {book.book_id}")
        snapshot = BookSourceSnapshot.model_validate(snapshot_row.payload)
        if snapshot.family_id != book.family_id:
            raise RuntimeError(f"source snapshot payload family mismatch for book {book.book_id}")
        # Defense in depth for queued books created before Phase 2.8 or rows
        # corrupted after persistence. Reclaiming work never weakens closure.
        validate_snapshot_closure(
            snapshot,
            expected_recording_ids=snapshot.manifest.source_recording_ids,
        )

        if self._check_cancellation(job, book.book_id):
            return

        # 2. PLANNING
        plan_row = self.plan_repo.get_plan(book.book_id)
        if plan_row is None:
            self.book_repo.update_stage(
                book.book_id,
                stage=BookStage.PLANNING.value,
                status=BookStatus.PLANNING.value,
                job_id=job.job_id,
                lease_owner=self.worker_id,
            )
            self.job_repo.update_job_stage(
                job.job_id,
                stage=BookStage.PLANNING.value,
                lease_owner=self.worker_id,
            )
            out_lang = (
                BookLanguage(book.output_language)
                if book.output_language in BookLanguage._value2member_map_
                else BookLanguage.RU
            )
            blueprint, report, _ = plan_book(
                self.deepseek_client,
                snapshot,
                output_language=out_lang,
                target_total_words=book.target_word_count,
                limits=self.blueprint_limits,
            )
            if not report.valid:
                raise ValueError(f"Blueprint validation failed after repair: {report.issues}")

            self.plan_repo.save_plan(
                book_id=book.book_id,
                blueprint=blueprint.model_dump(mode="json"),
                validation_report=report.model_dump(mode="json"),
                central_theme=blueprint.central_theme,
                narrative_voice=(
                    blueprint.narrative_voice.value
                    if hasattr(blueprint.narrative_voice, "value")
                    else str(blueprint.narrative_voice)
                ),
                material_anchor=blueprint.material_anchor,
                chapter_count=len(blueprint.chapters),
                target_total_words=blueprint.target_total_words,
                planner_prompt_version=BOOK_PLANNER_PROMPT_VERSION,
                planner_model=(
                    str(self.deepseek_client.model)
                    if hasattr(self.deepseek_client, "model")
                    and isinstance(self.deepseek_client.model, str)
                    else "deepseek-chat"
                ),
                job_id=job.job_id,
                lease_owner=self.worker_id,
            )
            chapter_plans = [ch.model_dump(mode="json") for ch in blueprint.chapters]
            self.chapter_repo.create_chapter_stubs(
                book_id=book.book_id,
                chapter_plans=chapter_plans,
                job_id=job.job_id,
                lease_owner=self.worker_id,
            )
            self.book_repo.update_stage(
                book.book_id,
                stage=BookStage.PLANNING.value,
                chapters_total=len(blueprint.chapters),
                job_id=job.job_id,
                lease_owner=self.worker_id,
            )
        else:
            blueprint = BookBlueprint.model_validate(plan_row.blueprint)

        if self._check_cancellation(job, book.book_id):
            return

        # 3. CHAPTERS ITERATION
        raw_chapters = self.chapter_repo.list_chapters(book_id=book.book_id)
        sorted_chapters = sorted(raw_chapters, key=lambda c: c.chapter_number)

        for chapter in sorted_chapters:
            if chapter.status == ChapterStatus.APPROVED.value:
                continue

            if self._check_cancellation(job, book.book_id):
                return

            with BookChapterContextManager(chapter.chapter_number):
                chapter_plan = ChapterPlan.model_validate(chapter.plan)

                # Resolve previous continuity
                if chapter.chapter_number == 1:
                    continuity_row = self.continuity_repo.get_continuity_after(book.book_id, 0)
                    if continuity_row is None:
                        continuity = create_initial_continuity_state(blueprint)
                        self.continuity_repo.save_continuity(
                            book_id=book.book_id,
                            after_chapter_number=0,
                            state=continuity.model_dump(mode="json"),
                            prompt_version="initial",
                            model="rule_based",
                            job_id=job.job_id,
                            lease_owner=self.worker_id,
                        )
                    else:
                        continuity = ContinuityState.model_validate(continuity_row.state)
                else:
                    prev_cont_row = self.continuity_repo.get_continuity_after(
                        book.book_id, chapter.chapter_number - 1
                    )
                    if prev_cont_row is None:
                        continuity = create_initial_continuity_state(blueprint)
                    else:
                        continuity = ContinuityState.model_validate(prev_cont_row.state)

                out_lang = (
                    BookLanguage(book.output_language)
                    if book.output_language in BookLanguage._value2member_map_
                    else BookLanguage.RU
                )

                # Drafting
                self.book_repo.update_stage(
                    book.book_id,
                    stage=BookStage.WRITING_CHAPTER.value,
                    status=BookStatus.WRITING.value,
                    current_chapter_number=chapter.chapter_number,
                    job_id=job.job_id,
                    lease_owner=self.worker_id,
                )
                self.job_repo.update_job_stage(
                    job.job_id,
                    stage=BookStage.WRITING_CHAPTER.value,
                    lease_owner=self.worker_id,
                )

                if not chapter.draft_text:
                    draft, write_telemetry = write_chapter(
                        self.deepseek_client,
                        chapter_plan,
                        snapshot,
                        continuity,
                        out_lang,
                    )
                    self.chapter_repo.update_chapter_draft(
                        book_id=book.book_id,
                        chapter_number=chapter.chapter_number,
                        draft_text=draft.text,
                        draft_payload=draft.model_dump(mode="json"),
                        word_count=len(draft.text.split()),
                        writer_prompt_version=write_telemetry.get("prompt_version", "v1"),
                        writer_model=write_telemetry.get("model", "deepseek-chat"),
                        repair_attempts=0,
                        job_id=job.job_id,
                        lease_owner=self.worker_id,
                    )
                else:
                    if chapter.draft_payload:
                        draft = ChapterDraft.model_validate(chapter.draft_payload)
                        if draft.text != chapter.draft_text:
                            raise RuntimeError(
                                f"stored draft payload/text mismatch for book {book.book_id} "
                                f"chapter {chapter.chapter_number}"
                            )
                    else:
                        # Legacy pre-Phase-2.8 row. Truth-critical gates now
                        # derive assertions from prose, so missing writer
                        # self-report metadata cannot weaken verification.
                        draft = ChapterDraft(
                            chapter_number=chapter.chapter_number,
                            title=chapter.title or chapter_plan.title,
                            text=chapter.draft_text,
                        )

                if self._check_cancellation(job, book.book_id):
                    return

                # Reviewing
                self.book_repo.update_stage(
                    book.book_id,
                    stage=BookStage.REVIEWING_CHAPTER.value,
                    current_chapter_number=chapter.chapter_number,
                    job_id=job.job_id,
                    lease_owner=self.worker_id,
                )
                self.job_repo.update_job_stage(
                    job.job_id,
                    stage=BookStage.REVIEWING_CHAPTER.value,
                    lease_owner=self.worker_id,
                )
                min_cw = self.blueprint_limits.min_chapter_words if self.blueprint_limits else 700
                max_cw = self.blueprint_limits.max_chapter_words if self.blueprint_limits else 3500
                review_result, gate_report, review_telemetry = review_chapter(
                    self.deepseek_client,
                    draft,
                    chapter_plan,
                    snapshot,
                    out_lang,
                    min_chapter_words=min_cw,
                    max_chapter_words=max_cw,
                )
                self.chapter_repo.update_chapter_review(
                    book_id=book.book_id,
                    chapter_number=chapter.chapter_number,
                    status=ChapterStatus.REVIEWING.value,
                    review=review_result.model_dump(mode="json"),
                    gate_report=gate_report.model_dump(mode="json"),
                    reviewer_prompt_version=review_telemetry.get("prompt_version"),
                    reviewer_model=review_telemetry.get("model"),
                    job_id=job.job_id,
                    lease_owner=self.worker_id,
                )

                # Repairing if needed
                repair_count = 0
                while (
                    not gate_report.passed or review_result.status != ReviewStatus.APPROVED
                ) and repair_count < self.max_repair_attempts:
                    if self._check_cancellation(job, book.book_id):
                        return

                    repair_count += 1
                    self.book_repo.update_stage(
                        book.book_id,
                        stage=BookStage.REPAIRING_CHAPTER.value,
                        current_chapter_number=chapter.chapter_number,
                        job_id=job.job_id,
                        lease_owner=self.worker_id,
                    )
                    self.job_repo.update_job_stage(
                        job.job_id,
                        stage=BookStage.REPAIRING_CHAPTER.value,
                        lease_owner=self.worker_id,
                    )
                    feedback_items = [b.detail for b in gate_report.blockers] + [
                        iss.detail
                        for iss in review_result.issues
                        if iss.severity in ("blocker", "major")
                    ]
                    feedback = (
                        "\n".join(feedback_items)
                        or "Ensure all names, places, and years are strictly grounded."
                    )
                    draft, rep_telemetry = repair_chapter(
                        self.deepseek_client,
                        previous_draft=draft,
                        feedback=feedback,
                        chapter_plan=chapter_plan,
                        snapshot=snapshot,
                        continuity=continuity,
                        output_language=out_lang,
                    )
                    repair_prompt_version, repair_model = _require_repair_telemetry(rep_telemetry)

                    self.chapter_repo.update_chapter_draft(
                        book_id=book.book_id,
                        chapter_number=chapter.chapter_number,
                        draft_text=draft.text,
                        draft_payload=draft.model_dump(mode="json"),
                        word_count=len(draft.text.split()),
                        writer_prompt_version=repair_prompt_version,
                        writer_model=repair_model,
                        repair_attempts=repair_count,
                        job_id=job.job_id,
                        lease_owner=self.worker_id,
                    )

                    # Re-review
                    review_result, gate_report, review_telemetry = review_chapter(
                        self.deepseek_client,
                        draft,
                        chapter_plan,
                        snapshot,
                        out_lang,
                        min_chapter_words=min_cw,
                        max_chapter_words=max_cw,
                    )
                    self.chapter_repo.update_chapter_review(
                        book_id=book.book_id,
                        chapter_number=chapter.chapter_number,
                        status=ChapterStatus.REVIEWING.value,
                        review=review_result.model_dump(mode="json"),
                        gate_report=gate_report.model_dump(mode="json"),
                        reviewer_prompt_version=review_telemetry.get("prompt_version"),
                        reviewer_model=review_telemetry.get("model"),
                        job_id=job.job_id,
                        lease_owner=self.worker_id,
                    )

                if not gate_report.passed:
                    self.chapter_repo.fail_chapter(
                        book_id=book.book_id,
                        chapter_number=chapter.chapter_number,
                        error_code="GATE_FAILED",
                        gate_report=gate_report.model_dump(mode="json"),
                        review=review_result.model_dump(mode="json"),
                        job_id=job.job_id,
                        lease_owner=self.worker_id,
                    )
                    raise RuntimeError(
                        f"Chapter {chapter.chapter_number} failed validation "
                        f"gates after {repair_count} repairs"
                    )

                # Chapter passed! Approve chapter
                self.chapter_repo.approve_chapter(
                    book_id=book.book_id,
                    chapter_number=chapter.chapter_number,
                    final_text=draft.text,
                    word_count=len(draft.text.split()),
                    review=review_result.model_dump(mode="json"),
                    gate_report=gate_report.model_dump(mode="json"),
                    job_id=job.job_id,
                    lease_owner=self.worker_id,
                )

                # Update continuity
                self.book_repo.update_stage(
                    book.book_id,
                    stage=BookStage.UPDATING_CONTINUITY.value,
                    current_chapter_number=chapter.chapter_number,
                    job_id=job.job_id,
                    lease_owner=self.worker_id,
                )
                self.job_repo.update_job_stage(
                    job.job_id,
                    stage=BookStage.UPDATING_CONTINUITY.value,
                    lease_owner=self.worker_id,
                )
                new_continuity, cont_telemetry = update_continuity(
                    self.deepseek_client,
                    previous_state=continuity,
                    draft=draft,
                    chapter_plan=chapter_plan,
                    blueprint=blueprint,
                )
                self.continuity_repo.save_continuity(
                    book_id=book.book_id,
                    after_chapter_number=chapter.chapter_number,
                    state=new_continuity.model_dump(mode="json"),
                    prompt_version=cont_telemetry.get("prompt_version", "v1"),
                    model=cont_telemetry.get("model", "deepseek-chat"),
                    job_id=job.job_id,
                    lease_owner=self.worker_id,
                )

                # Update book approved chapters and words
                all_chapters = self.chapter_repo.list_chapters(book_id=book.book_id)
                approved_chs = [
                    ch for ch in all_chapters if ch.status == ChapterStatus.APPROVED.value
                ]
                total_words = sum(ch.word_count for ch in approved_chs)
                self.book_repo.update_stage(
                    book.book_id,
                    stage=BookStage.WRITING_CHAPTER.value,
                    chapters_approved=len(approved_chs),
                    word_count=total_words,
                    job_id=job.job_id,
                    lease_owner=self.worker_id,
                )

        if self._check_cancellation(job, book.book_id):
            return

        # 4. EXPORTING_PDF
        self.book_repo.update_stage(
            book.book_id,
            stage=BookStage.EXPORTING_PDF.value,
            status=BookStatus.EXPORTING.value,
            job_id=job.job_id,
            lease_owner=self.worker_id,
        )
        self.job_repo.update_job_stage(
            job.job_id,
            stage=BookStage.EXPORTING_PDF.value,
            lease_owner=self.worker_id,
        )
        try:
            self.export_service.export_book(
                family_id=book.family_id,
                book_id=book.book_id,
                export_format=ExportFormat.PDF,
                job_id=job.job_id,
                lease_owner=self.worker_id,
            )
        except ExportEngineUnavailable as exc:
            logger.warning("PDF renderer unavailable, skipping PDF export: %s", exc)

        if self._check_cancellation(job, book.book_id):
            return

        # 5. EXPORTING_EPUB
        self.book_repo.update_stage(
            book.book_id,
            stage=BookStage.EXPORTING_EPUB.value,
            status=BookStatus.EXPORTING.value,
            job_id=job.job_id,
            lease_owner=self.worker_id,
        )
        self.job_repo.update_job_stage(
            job.job_id,
            stage=BookStage.EXPORTING_EPUB.value,
            lease_owner=self.worker_id,
        )
        self.export_service.export_book(
            family_id=book.family_id,
            book_id=book.book_id,
            export_format=ExportFormat.EPUB,
            job_id=job.job_id,
            lease_owner=self.worker_id,
        )

        if self._check_cancellation(job, book.book_id):
            return

        # Verify exports
        pdf_exp = self.export_repo.get_export(book_id=book.book_id, format=ExportFormat.PDF.value)
        epub_exp = self.export_repo.get_export(book_id=book.book_id, format=ExportFormat.EPUB.value)
        pdf_ready = (
            (pdf_exp is not None and pdf_exp.status == ExportStatus.READY.value)
            if self.export_service.pdf_renderer is not None
            else True
        )
        if not (epub_exp and epub_exp.status == ExportStatus.READY.value and pdf_ready):
            raise RuntimeError(
                f"Book exports incomplete for book {book.book_id}: "
                f"PDF={getattr(pdf_exp, 'status', None)}, EPUB={getattr(epub_exp, 'status', None)}"
            )

        # 6. COMPLETED
        all_chapters = self.chapter_repo.list_chapters(book_id=book.book_id)
        approved_chs = [ch for ch in all_chapters if ch.status == ChapterStatus.APPROVED.value]
        final_words = sum(ch.word_count for ch in approved_chs)

        self.job_repo.complete_book_and_job(
            job.job_id,
            book_id=book.book_id,
            word_count=final_words,
            lease_owner=self.worker_id,
        )

        logger.info(
            "book_completed",
            extra={
                "event": "book_completed",
                "book_id": book.book_id,
                "job_id": job.job_id,
                "chapters_count": len(approved_chs),
                "word_count": final_words,
            },
        )
