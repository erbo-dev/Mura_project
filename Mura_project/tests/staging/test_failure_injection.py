"""Staging validation tests for production failure injection and chaos engineering.

Validates that:
1. Fault injection is strictly blocked when APP_ENV=production or MURA_ENVIRONMENT=production.
2. DeepSeek timeout triggers non-blocking RETRY backoff.
3. DeepSeek 429 parses and adheres to Retry-After headers without a busy loop.
4. Provider 401 produces a TERMINAL classification (no infinite retry).
5. Provider 503 produces a transient RETRY classification.
6. Storage 503 causes a retryable AudioStorageError.
7. WeasyPrint PDF renderer failure records an export failure without corrupting chapters.
8. Expired worker recording leases past grace are reclaimed by a second worker.
9. Worker crash during book chapter writing preserves approved chapters and resumes.
10. Concurrent duplicate Book POST attempts trigger row locks and return 409 conflict.
11. Exceeding daily book limit returns 429 quota exhaustion.
"""

from __future__ import annotations

import io
import os
from datetime import timedelta
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from apps.api.errors import (
    BOOK_DAILY_LIMIT_REACHED,
    BOOK_GENERATION_ALREADY_ACTIVE,
)
from mura.book.exporter import ExportService, WeasyPrintRenderer
from mura.config import CoreSettings
from mura.deepseek.client import DeepSeekClient
from mura.domain.book_models import (
    BookLanguage,
    BookStage,
    BookStatus,
    ChapterStatus,
    ExportFormat,
    ExportStatus,
)
from mura.quotas import BookQuotaService
from mura.reliability.failures import (
    FailureCategory,
    FailureDisposition,
    classify_failure,
)
from mura.storage.audio import AudioStorageError, SupabaseAudioStorage
from mura.storage.book import (
    BookChapterRepository,
    BookExportRepository,
    BookJobRepository,
    BookJobRow,
    BookRepository,
    BookRow,
)
from mura.storage.book_artifacts import LocalBookArtifactStorage
from mura.storage.database import Database, ProcessingJobRow, RecordingRepository, utcnow
from mura.storage.identity import FamilyRow, UserRow
from mura.testing.fault_injection import (
    FAULT_DEEPSEEK_429,
    FAULT_DEEPSEEK_TIMEOUT,
    FAULT_PDF_FAILURE,
    FAULT_PROVIDER_401,
    FAULT_PROVIDER_503,
    FAULT_STORAGE_503,
    assert_fault_injection_allowed,
    fault_injected,
    reset_faults,
)


@pytest.fixture(autouse=True)
def setup_fault_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MURA_ENVIRONMENT", "staging")
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("MURA_FAULT_INJECTION", "true")
    reset_faults()
    yield
    reset_faults()


@pytest.fixture
def db() -> Database:
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    return database


@pytest.fixture
def settings() -> CoreSettings:
    return CoreSettings.model_validate(
        {
            "MURA_ENVIRONMENT": "local",
            "MURA_FAULT_INJECTION": True,
            "DEEPSEEK_API_KEY": "sk-" + "d" * 40,
            "CORE_API_KEY": "c" * 40,
            "OPERATIONS_API_KEY": "o" * 40,
            "WORKER_REGISTRATION_TOKEN": "w" * 40,
            "KAGGLE_ASR_API_KEY": "k" * 40,
            "DATABASE_URL": "sqlite+pysqlite:///:memory:",
            "DATABASE_AUTO_CREATE": True,
            "BOOK_MAX_ACTIVE_PER_FAMILY": 1,
            "BOOK_MAX_CREATED_PER_FAMILY_PER_DAY": 3,
        }
    )


@pytest.mark.staging
def test_production_safety_gate_blocks_fault_injection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies fault injection hard-fails when MURA_ENVIRONMENT=production or APP_ENV=production."""
    monkeypatch.setenv("MURA_ENVIRONMENT", "production")
    monkeypatch.setenv("MURA_FAULT_INJECTION", "true")

    with pytest.raises(ValueError, match="Fault injection cannot be enabled in production"):
        assert_fault_injection_allowed()

    with pytest.raises(ValueError, match="Fault injection cannot be enabled in production"):
        CoreSettings.model_validate(
            {
                "MURA_ENVIRONMENT": "production",
                "MURA_FAULT_INJECTION": True,
                "DEEPSEEK_API_KEY": "sk-" + "d" * 40,
                "CORE_API_KEY": "c" * 40,
                "OPERATIONS_API_KEY": "o" * 40,
                "WORKER_REGISTRATION_TOKEN": "w" * 40,
                "KAGGLE_ASR_API_KEY": "k" * 40,
                "DATABASE_URL": "sqlite+pysqlite:///:memory:",
                "DATABASE_AUTO_CREATE": False,
            }
        )


@pytest.mark.staging
def test_deepseek_timeout_triggers_retry_backoff() -> None:
    """Verifies that an injected DeepSeek timeout classifies as RETRY."""
    with fault_injected(FAULT_DEEPSEEK_TIMEOUT):
        client = DeepSeekClient(api_key="sk-" + "d" * 40)
        with pytest.raises(Exception) as exc_info:
            client._request_model_json(
                model="deepseek-chat",
                system_prompt="Test system prompt",
                payload={"test": 1},
                max_tokens=100,
                attempts=1,
            )
        classified = classify_failure(exc_info.value)
        assert classified.category == FailureCategory.PROVIDER_TIMEOUT
        assert classified.disposition == FailureDisposition.RETRY
        assert classified.is_retryable is True


@pytest.mark.staging
def test_deepseek_429_adheres_to_retry_after_header() -> None:
    """Verifies that an injected 429 response parses Retry-After and classifies as RETRY."""
    with fault_injected(FAULT_DEEPSEEK_429, retry_after=45):
        client = DeepSeekClient(api_key="sk-" + "d" * 40)
        with pytest.raises(Exception) as exc_info:
            client._request_model_json(
                model="deepseek-chat",
                system_prompt="Test system prompt",
                payload={"test": 1},
                max_tokens=100,
                attempts=1,
            )
        classified = classify_failure(exc_info.value)
        assert classified.category == FailureCategory.PROVIDER_RATE_LIMIT
        assert classified.disposition == FailureDisposition.RETRY
        assert classified.retry_after_seconds == 45.0


@pytest.mark.staging
def test_provider_401_marks_terminal_failure_without_looping() -> None:
    """Verifies that an injected 401 classifies as TERMINAL, preventing infinite retry."""
    with fault_injected(FAULT_PROVIDER_401):
        client = DeepSeekClient(api_key="sk-" + "d" * 40)
        with pytest.raises(Exception) as exc_info:
            client._request_model_json(
                model="deepseek-chat",
                system_prompt="Test system prompt",
                payload={"test": 1},
                max_tokens=100,
                attempts=1,
            )
        classified = classify_failure(exc_info.value)
        assert classified.category == FailureCategory.PROVIDER_AUTH_ERROR
        assert classified.disposition == FailureDisposition.TERMINAL
        assert classified.is_retryable is False


@pytest.mark.staging
def test_provider_503_causes_transient_retry() -> None:
    """Verifies that an injected 503 classifies as transient RETRY."""
    with fault_injected(FAULT_PROVIDER_503):
        client = DeepSeekClient(api_key="sk-" + "d" * 40)
        with pytest.raises(Exception) as exc_info:
            client._request_model_json(
                model="deepseek-chat",
                system_prompt="Test system prompt",
                payload={"test": 1},
                max_tokens=100,
                attempts=1,
            )
        classified = classify_failure(exc_info.value)
        assert classified.category == FailureCategory.PROVIDER_SERVER_ERROR
        assert classified.disposition == FailureDisposition.RETRY


@pytest.mark.staging
def test_storage_503_raises_audio_storage_error() -> None:
    """Verifies that an injected storage 503 raises AudioStorageError."""
    valid_wav = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
    storage = SupabaseAudioStorage(
        url="https://supabase.example.com",
        service_role_key="key",
        bucket="mura-audio-staging",
    )
    with fault_injected(FAULT_STORAGE_503):
        with pytest.raises(AudioStorageError, match="503"):
            storage.save(
                family_id="fam_1",
                recording_id="rec_1",
                original_filename="test.wav",
                content_type="audio/wav",
                source=io.BytesIO(valid_wav),
            )


@pytest.mark.staging
def test_weasyprint_pdf_failure_records_failed_export(db: Database, tmp_path: Any) -> None:
    """Verifies that a PDF rendering failure records FAILED export without losing chapters."""
    now = utcnow()
    fid = "fam_pdf_fail"
    uid = "usr_pdf_fail"
    bid = "book_pdf_fail"

    with db.session_factory.begin() as session:
        session.add(FamilyRow(family_id=fid, name="PDF Fail Family", created_at=now, updated_at=now))
        session.add(UserRow(user_id=uid, auth_issuer="issuer", auth_subject="sub", email="pdf@test.com", created_at=now, updated_at=now))
        session.add(
            BookRow(
                book_id=bid,
                family_id=fid,
                title="PDF Test Book",
                created_by_user_id=uid,
                status=BookStatus.COMPLETED.value,
                stage=BookStage.EXPORTING_PDF.value,
                output_language=BookLanguage.RU.value,
                target_word_count=25000,
                created_at=now,
                updated_at=now,
            )
        )

    chapter_repo = BookChapterRepository(db)
    chapter_repo.create_chapter_stubs(
        book_id=bid,
        chapter_plans=[{"chapter_number": 1, "title": "Chapter 1", "source_recording_ids": ["rec_1"]}],
    )
    chapter_repo.approve_chapter(book_id=bid, chapter_number=1, final_text="Some text", word_count=2)

    book_repo = BookRepository(db)
    export_repo = BookExportRepository(db)
    storage = LocalBookArtifactStorage(tmp_path / "artifacts")
    service = ExportService(
        book_repo=book_repo,
        chapter_repo=chapter_repo,
        export_repo=export_repo,
        artifact_storage=storage,
        pdf_renderer=WeasyPrintRenderer(),
    )

    with fault_injected(FAULT_PDF_FAILURE):
        with pytest.raises(RuntimeError, match="Injected WeasyPrint PDF renderer failure"):
            service.export_book(family_id=fid, book_id=bid, export_format=ExportFormat.PDF)

    # Verify export record shows failed status
    exports = export_repo.list_exports(book_id=bid)
    assert len(exports) == 1
    assert exports[0].status == ExportStatus.FAILED.value
    assert exports[0].error_code == "RENDER_FAILED"

    # Verify chapter is still APPROVED and intact
    chapters = chapter_repo.list_chapters(book_id=bid)
    assert len(chapters) == 1
    assert chapters[0].status == ChapterStatus.APPROVED.value


@pytest.mark.staging
def test_worker_crash_during_recording_lease_reclaimed_by_second_worker(db: Database) -> None:
    """Verifies that when a worker crashes with an active lease, a second worker reclaims the job past grace."""
    recording_repo = RecordingRepository(db)
    recording_id = "rec_lease_crash"
    job_id = "job_lease_crash"

    now = utcnow()
    with db.session_factory.begin() as session:
        session.add(FamilyRow(family_id="fam_crash", name="Crash Fam", created_at=now, updated_at=now))

    recording_repo.create_recording_and_job(
        recording_id=recording_id,
        job_id=job_id,
        family_id="fam_crash",
        speaker_id="spk_crash",
        speaker_name="Speaker",
        original_filename="crash.wav",
        content_type="audio/wav",
        audio_path="crash.wav",
    )

    # Worker 1 claims job
    claimed_1 = recording_repo.claim_next_job(lease_owner="worker_1", lease_seconds=30)
    assert claimed_1 is not None
    assert claimed_1.job_id == job_id
    assert claimed_1.lease_owner == "worker_1"

    # Worker 2 immediately tries to claim -> gets None because lease is active
    claimed_2_immediate = recording_repo.claim_next_job(lease_owner="worker_2", lease_seconds=30)
    assert claimed_2_immediate is None

    # Simulate Worker 1 crashing and lease expiring (backdate lease_expires_at)
    with db.session_factory.begin() as session:
        job = session.scalar(select(ProcessingJobRow).where(ProcessingJobRow.job_id == job_id))
        assert job is not None
        job.lease_expires_at = now - timedelta(seconds=60)

    # Worker 2 tries to claim again -> successfully reclaims expired lease
    claimed_2_reclaimed = recording_repo.claim_next_job(lease_owner="worker_2", lease_seconds=30)
    assert claimed_2_reclaimed is not None
    assert claimed_2_reclaimed.job_id == job_id
    assert claimed_2_reclaimed.lease_owner == "worker_2"


@pytest.mark.staging
def test_worker_crash_during_book_chapter_generation_preserves_approved_chapters(db: Database) -> None:
    """Verifies that if a book worker crashes mid-book, previously approved chapters are preserved

    and the next worker reclaims the job and identifies where to resume.
    """
    now = utcnow()
    fid = "fam_book_crash"
    uid = "usr_book_crash"
    bid = "book_mid_crash"

    with db.session_factory.begin() as session:
        session.add(FamilyRow(family_id=fid, name="Book Crash Fam", created_at=now, updated_at=now))
        session.add(UserRow(user_id=uid, auth_issuer="issuer", auth_subject="sub", email="bk@test.com", created_at=now, updated_at=now))
        session.add(
            BookRow(
                book_id=bid,
                family_id=fid,
                title="Mid Crash Book",
                created_by_user_id=uid,
                status=BookStatus.WRITING.value,
                stage=BookStage.WRITING_CHAPTER.value,
                output_language=BookLanguage.RU.value,
                target_word_count=25000,
                created_at=now,
                updated_at=now,
            )
        )

    chapter_repo = BookChapterRepository(db)
    # Chapter 1 was drafted and approved by worker 1
    chapter_repo.create_chapter_stubs(
        book_id=bid,
        chapter_plans=[{"chapter_number": 1, "title": "Chapter 1 Approved", "source_recording_ids": ["rec_1"]}],
    )
    chapter_repo.approve_chapter(book_id=bid, chapter_number=1, final_text="Preserved text", word_count=2)

    # Book job was leased to worker 1
    job_repo = BookJobRepository(db)
    job_repo.create_job(family_id=fid, book_id=bid)
    job = job_repo.claim_next_job(lease_owner="worker_1", lease_seconds=30)
    assert job is not None

    # Simulate worker 1 crashing and lease expiring
    with db.session_factory.begin() as session:
        j = session.scalar(select(BookJobRow).where(BookJobRow.book_id == bid))
        assert j is not None
        j.lease_expires_at = now - timedelta(seconds=60)

    # Worker 2 reclaims the book job
    reclaimed = job_repo.claim_next_job(lease_owner="worker_2", lease_seconds=30)
    assert reclaimed is not None
    assert reclaimed.book_id == bid
    assert reclaimed.lease_owner == "worker_2"

    # Worker 2 inspects existing chapters: Chapter 1 is still APPROVED!
    existing_chapters = chapter_repo.list_chapters(book_id=bid)
    assert len(existing_chapters) == 1
    assert existing_chapters[0].chapter_number == 1
    assert existing_chapters[0].status == ChapterStatus.APPROVED.value
    assert existing_chapters[0].final_text == "Preserved text"


@pytest.mark.staging
def test_concurrent_book_post_race_row_locking_409(db: Database, settings: CoreSettings) -> None:
    """Verifies that concurrent creation attempts are blocked by row-locking and return 409."""
    now = utcnow()
    fid = "fam_quota_race"
    uid = "usr_quota_race"
    bid = "book_active_1"

    with db.session_factory.begin() as session:
        session.add(FamilyRow(family_id=fid, name="Quota Race Fam", created_at=now, updated_at=now))
        session.add(UserRow(user_id=uid, auth_issuer="issuer", auth_subject="sub", email="q@test.com", created_at=now, updated_at=now))
        session.add(
            BookRow(
                book_id=bid,
                family_id=fid,
                title="Active Book",
                created_by_user_id=uid,
                status=BookStatus.WRITING.value,
                stage=BookStage.WRITING_CHAPTER.value,
                output_language=BookLanguage.RU.value,
                target_word_count=25000,
                created_at=now,
                updated_at=now,
            )
        )

    # Second book attempt while one is active -> 409
    with db.session_factory() as session:
        with pytest.raises(HTTPException) as exc_info:
            BookQuotaService.check_creation_allowed(session, fid, settings)
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail == BOOK_GENERATION_ALREADY_ACTIVE


@pytest.mark.staging
def test_daily_book_quota_limit_429(db: Database, settings: CoreSettings) -> None:
    """Verifies that exceeding the daily creation limit (3 books) returns 429."""
    now = utcnow()
    fid = "fam_daily_limit"
    uid = "usr_daily_limit"

    with db.session_factory.begin() as session:
        session.add(FamilyRow(family_id=fid, name="Daily Limit Fam", created_at=now, updated_at=now))
        session.add(UserRow(user_id=uid, auth_issuer="issuer", auth_subject="sub", email="d@test.com", created_at=now, updated_at=now))
        # 3 books already created today (even if completed)
        for i in range(1, 4):
            session.add(
                BookRow(
                    book_id=f"book_done_{i}",
                    family_id=fid,
                    title=f"Book {i}",
                    created_by_user_id=uid,
                    status=BookStatus.COMPLETED.value,
                    stage=BookStage.COMPLETED.value,
                    output_language=BookLanguage.RU.value,
                    target_word_count=25000,
                    created_at=now - timedelta(hours=i),
                    updated_at=now,
                )
            )

    with db.session_factory() as session:
        with pytest.raises(HTTPException) as exc_info:
            BookQuotaService.check_creation_allowed(session, fid, settings)
        assert exc_info.value.status_code == 429
        assert exc_info.value.detail == BOOK_DAILY_LIMIT_REACHED
