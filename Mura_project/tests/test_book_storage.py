"""Tests for book storage models and repositories."""

from __future__ import annotations

from datetime import timedelta

import pytest

from mura.domain.book_models import (
    BookJobStatus,
    BookLanguage,
    BookStage,
    BookStatus,
    ChapterStatus,
    ExportFormat,
    ExportStatus,
)
from mura.jobs import JobStatus
from mura.leases import LeaseOwnershipLost
from mura.storage.book import (
    BookChapterRepository,
    BookContinuityRepository,
    BookExportRepository,
    BookJobRepository,
    BookPlanRepository,
    BookRepository,
    BookSourceSnapshotRepository,
    get_eligible_recordings,
)
from mura.storage.database import (
    Database,
    PipelineResultRow,
    ProcessingJobRow,
    RecordingRepository,
    utcnow,
)
from mura.storage.identity import FamilyRow, UserRow


@pytest.fixture
def db() -> Database:
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    return database


@pytest.fixture
def family_and_user(db: Database) -> tuple[str, str]:
    now = utcnow()
    fid = "family_book_test_1"
    uid = "user_book_test_1"
    with db.session_factory.begin() as session:
        session.add(FamilyRow(family_id=fid, name="Test Family", created_at=now, updated_at=now))
        session.add(
            UserRow(
                user_id=uid,
                auth_issuer="https://auth.mura.test",
                auth_subject="sub_test_user_1",
                email="user@test.com",
                created_at=now,
                updated_at=now,
            )
        )
    return fid, uid


def test_book_crud_and_family_isolation(db: Database, family_and_user: tuple[str, str]) -> None:
    fid, uid = family_and_user
    other_fid = "family_other"
    now = utcnow()
    with db.session_factory.begin() as session:
        session.add(
            FamilyRow(
                family_id=other_fid,
                name="Other Family",
                created_at=now,
                updated_at=now,
            )
        )

    repo = BookRepository(db)
    book = repo.create_book(
        family_id=fid,
        created_by_user_id=uid,
        title="История семьи",
        subtitle="Летопись поколений",
        output_language=BookLanguage.RU.value,
        target_word_count=25000,
    )

    assert book.book_id.startswith("book_")
    assert book.status == BookStatus.QUEUED.value
    assert book.stage == BookStage.PREPARING_SOURCES.value

    # Authorized read
    found = repo.get_book(family_id=fid, book_id=book.book_id)
    assert found is not None
    assert found.title == "История семьи"

    # Cross-family isolation (BOLA protection)
    assert repo.get_book(family_id=other_fid, book_id=book.book_id) is None

    # List books
    items, total = repo.list_books(family_id=fid)
    assert total == 1
    assert len(items) == 1
    assert items[0].book_id == book.book_id

    # Update stage & cancellation
    repo.update_stage(
        book.book_id,
        stage=BookStage.WRITING_CHAPTER.value,
        current_chapter_number=1,
        chapters_total=10,
        chapters_approved=0,
    )
    reloaded = repo.get_book(family_id=fid, book_id=book.book_id)
    assert reloaded is not None
    assert reloaded.stage == BookStage.WRITING_CHAPTER.value
    assert reloaded.current_chapter_number == 1

    assert not repo.is_cancel_requested(book.book_id)
    cancelled = repo.request_cancel(family_id=fid, book_id=book.book_id)
    assert cancelled is True
    assert repo.is_cancel_requested(book.book_id)


def test_book_source_snapshot_and_plan(db: Database, family_and_user: tuple[str, str]) -> None:
    fid, uid = family_and_user
    book_repo = BookRepository(db)
    book = book_repo.create_book(
        family_id=fid,
        created_by_user_id=uid,
        title="Воспоминания",
        output_language=BookLanguage.KK.value,
        target_word_count=20000,
    )

    snap_repo = BookSourceSnapshotRepository(db)
    snap = snap_repo.save_snapshot(
        book_id=book.book_id,
        family_id=fid,
        compiler_version="v1.0",
        content_hash="abc123hash",
        payload={"people": []},
        manifest={"recordings": ["rec_1"]},
        source_recording_count=1,
    )
    assert snap.content_hash == "abc123hash"

    loaded_snap = snap_repo.get_snapshot(book.book_id)
    assert loaded_snap is not None
    assert loaded_snap.content_hash == "abc123hash"

    plan_repo = BookPlanRepository(db)
    plan = plan_repo.save_plan(
        book_id=book.book_id,
        blueprint={"book_title": "Воспоминания", "chapters": []},
        validation_report={"valid": True, "issues": []},
        central_theme="Преемственность поколений",
        chapter_count=10,
        target_total_words=20000,
        planner_prompt_version="v1",
        planner_model="deepseek-chat",
    )
    assert plan.chapter_count == 10

    loaded_plan = plan_repo.get_plan(book.book_id)
    assert loaded_plan is not None
    assert loaded_plan.central_theme == "Преемственность поколений"


def test_book_chapters_workflow(db: Database, family_and_user: tuple[str, str]) -> None:
    fid, uid = family_and_user
    book_repo = BookRepository(db)
    book = book_repo.create_book(
        family_id=fid,
        created_by_user_id=uid,
        title="Книга",
        output_language=BookLanguage.RU.value,
        target_word_count=20000,
    )

    ch_repo = BookChapterRepository(db)
    stubs = ch_repo.create_chapter_stubs(
        book_id=book.book_id,
        chapter_plans=[
            {"chapter_number": 1, "title": "Глава 1", "target_word_count": 2000},
            {"chapter_number": 2, "title": "Глава 2", "target_word_count": 2000},
        ],
    )
    assert len(stubs) == 2

    # First unfinished
    first = ch_repo.get_first_unfinished_chapter(book_id=book.book_id)
    assert first is not None
    assert first.chapter_number == 1
    assert first.status == ChapterStatus.PLANNED.value

    # Update draft
    ch_repo.update_chapter_draft(
        book_id=book.book_id,
        chapter_number=1,
        draft_text="Текст первой главы...",
        word_count=1800,
        writer_prompt_version="v1",
        writer_model="deepseek-chat",
    )
    ch1 = ch_repo.get_chapter(book_id=book.book_id, chapter_number=1)
    assert ch1 is not None
    assert ch1.status == ChapterStatus.REVIEWING.value
    assert ch1.draft_text == "Текст первой главы..."

    # Approve chapter
    ch_repo.approve_chapter(
        book_id=book.book_id,
        chapter_number=1,
        final_text="Отредактированный текст первой главы.",
        word_count=1850,
        review={"status": "approved"},
        gate_report={"passed": True},
    )
    ch1_approved = ch_repo.get_chapter(book_id=book.book_id, chapter_number=1)
    assert ch1_approved is not None
    assert ch1_approved.status == ChapterStatus.APPROVED.value
    assert ch1_approved.final_text == "Отредактированный текст первой главы."

    # Next unfinished is now chapter 2
    next_unf = ch_repo.get_first_unfinished_chapter(book_id=book.book_id)
    assert next_unf is not None
    assert next_unf.chapter_number == 2


def test_book_continuity_and_export(db: Database, family_and_user: tuple[str, str]) -> None:
    fid, uid = family_and_user
    book_repo = BookRepository(db)
    book = book_repo.create_book(
        family_id=fid,
        created_by_user_id=uid,
        title="Книга",
        output_language=BookLanguage.RU.value,
        target_word_count=20000,
    )

    cont_repo = BookContinuityRepository(db)
    cont_repo.save_continuity(
        book_id=book.book_id,
        after_chapter_number=1,
        state={"current_time_position": "1965"},
        prompt_version="v1",
        model="deepseek-chat",
    )
    latest = cont_repo.get_latest_continuity(book_id=book.book_id)
    assert latest is not None
    assert latest.after_chapter_number == 1
    assert latest.state.get("current_time_position") == "1965"

    exp_repo = BookExportRepository(db)
    exp_repo.save_export(
        book_id=book.book_id,
        format=ExportFormat.PDF.value,
        status=ExportStatus.READY.value,
        storage_key="families/f1/books/b1/book.pdf",
        size_bytes=10240,
        sha256="deadbeef",
        content_type="application/pdf",
        chapter_count=10,
        word_count=20000,
    )
    pdf_exp = exp_repo.get_export(book_id=book.book_id, format=ExportFormat.PDF.value)
    assert pdf_exp is not None
    assert pdf_exp.status == ExportStatus.READY.value
    assert pdf_exp.storage_key == "families/f1/books/b1/book.pdf"


def test_book_job_queue_and_lease(db: Database, family_and_user: tuple[str, str]) -> None:
    fid, uid = family_and_user
    book_repo = BookRepository(db)
    book = book_repo.create_book(
        family_id=fid,
        created_by_user_id=uid,
        title="Книга",
        output_language=BookLanguage.RU.value,
        target_word_count=20000,
    )

    job_repo = BookJobRepository(db)
    job = job_repo.create_job(
        book_id=book.book_id,
        family_id=fid,
        stage=BookStage.PREPARING_SOURCES.value,
    )
    assert job.status == BookJobStatus.QUEUED.value

    # Claim job
    claimed = job_repo.claim_next_job(lease_owner="worker_1", lease_seconds=60)
    assert claimed is not None
    assert claimed.job_id == job.job_id
    assert claimed.lease_owner == "worker_1"
    assert claimed.attempts == 1
    assert claimed.status == BookJobStatus.RUNNING.value

    # No second claim while active lease
    assert job_repo.claim_next_job(lease_owner="worker_2", lease_seconds=60) is None

    # Renew lease
    assert job_repo.renew_lease(job.job_id, lease_owner="worker_1", lease_seconds=120) is True
    # Renew by non-owner fails
    assert job_repo.renew_lease(job.job_id, lease_owner="worker_2", lease_seconds=120) is False

    # Defer job with backoff
    job_repo.defer_job(
        job.job_id,
        error_code="rate_limited",
        error_detail="DeepSeek 429",
        retry_after_seconds=300,
        lease_owner="worker_1",
    )
    reloaded_job = job_repo.get_job(job.job_id)
    assert reloaded_job is not None
    assert reloaded_job.status == BookJobStatus.QUEUED.value
    assert reloaded_job.lease_owner is None

    # Reclaim with simulated time in future
    future = utcnow() + timedelta(seconds=350)
    reclaimed = job_repo.claim_next_job(lease_owner="worker_2", lease_seconds=60, now=future)
    assert reclaimed is not None
    assert reclaimed.attempts == 2

    # Guard against stale worker
    with pytest.raises(LeaseOwnershipLost):
        job_repo.complete_job(job.job_id, lease_owner="worker_1")

    # Legitimate worker completes
    job_repo.complete_job(job.job_id, lease_owner="worker_2")
    final_job = job_repo.get_job(job.job_id)
    assert final_job is not None
    assert final_job.status == BookJobStatus.COMPLETED.value


def test_eligible_recordings_query(db: Database, family_and_user: tuple[str, str]) -> None:
    fid, _ = family_and_user
    now = utcnow()
    rec_repo = RecordingRepository(db)

    # 1. Eligible recording: job completed + pipeline result
    rec_repo.create_recording_and_job(
        recording_id="rec_eligible_1",
        job_id="job_1",
        family_id=fid,
        speaker_id="narrator_1",
        speaker_name="Айсұлу",
        original_filename="memory1.wav",
        content_type="audio/wav",
        audio_path="/audio1.wav",
    )
    with db.session_factory.begin() as session:
        job1 = session.get(ProcessingJobRow, "job_1")
        assert job1 is not None
        job1.status = JobStatus.COMPLETED.value
        session.add(
            PipelineResultRow(
                recording_id="rec_eligible_1",
                payload={"extraction": {}},
                created_at=now,
                updated_at=now,
            )
        )

    # 2. Ineligible recording: job queued (no pipeline result)
    rec_repo.create_recording_and_job(
        recording_id="rec_ineligible_2",
        job_id="job_2",
        family_id=fid,
        speaker_id="narrator_2",
        speaker_name="Марат",
        original_filename="memory2.wav",
        content_type="audio/wav",
        audio_path="/audio2.wav",
    )

    with db.session_factory() as session:
        eligible = get_eligible_recordings(session, family_id=fid)
        assert len(eligible) == 1
        assert eligible[0].recording_id == "rec_eligible_1"
