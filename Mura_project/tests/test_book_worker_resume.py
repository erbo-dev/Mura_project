"""Tests for BookJobWorker lifecycle, cancellation, and resume capability (Layer 5)."""
# ruff: noqa: RUF001, E501

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from mura.book.blueprint_validation import BlueprintLimits
from mura.book.snapshot import compile_source_snapshot
from mura.deepseek.client import DeepSeekUsage
from mura.domain.book_models import (
    BookJobStatus,
    BookLanguage,
    BookStage,
    BookStatus,
    ChapterStatus,
    CONTINUITY_SCHEMA_VERSION,
)
from mura.orchestration.books import BookJobWorker
from mura.storage.archive_read import GroundingBundle
from mura.storage.book import (
    BookChapterRepository,
    BookContinuityRepository,
    BookExportRepository,
    BookJobRepository,
    BookPlanRepository,
    BookRepository,
    BookSourceSnapshotRepository,
)
from mura.storage.book_artifacts import LocalBookArtifactStorage
from mura.storage.database import Database, utcnow
from mura.storage.identity import FamilyRow, UserRow

FAMILY_TEST = "fam_worker_test"
USER_TEST = "user_worker_test"


@pytest.fixture
def db() -> Database:
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    return database


@pytest.fixture
def family_and_user(db: Database) -> tuple[str, str]:
    now = utcnow()
    with db.session_factory.begin() as session:
        session.add(FamilyRow(family_id=FAMILY_TEST, name="Worker Family", created_at=now, updated_at=now))
        session.add(
            UserRow(
                user_id=USER_TEST,
                auth_issuer="https://auth.mura.test",
                auth_subject="sub_test_worker_user",
                email="worker@test.com",
                created_at=now,
                updated_at=now,
            )
        )
    return FAMILY_TEST, USER_TEST


class FakePDFRenderer:
    def render_pdf(self, html_content: str) -> bytes:
        return b"%PDF-1.4 Fake PDF for Worker Test"


def _fake_blueprint_dict() -> dict[str, Any]:
    return {
        "book_title": "Хроника семьи",
        "subtitle": "Память",
        "central_theme": "Семейные узы",
        "narrative_voice": "third_person",
        "output_language": "ru",
        "target_total_words": 1000,
        "material_anchor": "домбра",
        "epigraph": None,
        "chapters": [
            {
                "chapter_number": 1,
                "title": "Глава 1. Истоки",
                "purpose": "Начало",
                "synopsis": "1945 год.",
                "target_word_count": 500,
                "time_range": "1945",
                "person_ids": ["per_1"],
                "place_names": [],
                "claim_ids": ["cl_1"],
                "source_recording_ids": ["rec_1"],
                "source_story_ids": ["story_1"],
                "evidence_refs": ["ev_1"],
                "material_anchor_refs": ["домбра"],
                "continuity_in": None,
                "continuity_out": "Переход к следующей главе",
            },
            {
                "chapter_number": 2,
                "title": "Глава 2. Новая жизнь",
                "purpose": "Продолжение",
                "synopsis": "1945 год.",
                "target_word_count": 500,
                "time_range": "1945",
                "person_ids": ["per_1"],
                "place_names": [],
                "claim_ids": ["cl_1"],
                "source_recording_ids": ["rec_1"],
                "source_story_ids": ["story_1"],
                "evidence_refs": ["ev_1"],
                "material_anchor_refs": [],
                "continuity_in": "Связь с главой 1",
                "continuity_out": "Завершение",
            },
        ],
    }


def _fake_draft_dict(ch_num: int) -> dict[str, Any]:
    return {
        "chapter_number": ch_num,
        "title": f"Глава {ch_num}",
        "text": f"Текст главы {ch_num}. В 1945 году старая домбра стояла у окна. «Это память», — сказал дедушка.",
        "evidence_usage": ["ev_1"],
        "person_ids_used": ["per_1"],
        "uncertainty_notes": [],
        "conflict_notes": [],
    }


def _fake_review_dict() -> dict[str, Any]:
    return {
        "status": "approved",
        "issues": [],
        "grounding_score": 1.0,
        "coverage_note": "Grounded",
    }


def _fake_continuity_dict(after_ch: int) -> dict[str, Any]:
    return {
        "schema_version": CONTINUITY_SCHEMA_VERSION,
        "after_chapter_number": after_ch,
        "current_time_position": "1945",
        "active_people": ["per_1"],
        "resolved_story_threads": ["story_1"],
        "open_story_threads": [],
        "last_scene_summary": "Сцена завершена",
        "material_anchor_state": "домбра в доме",
        "tone": "спокойный",
        "important_terminology": [],
        "facts_already_revealed": [],
    }


def _build_mock_client() -> MagicMock:
    client = MagicMock()
    client.model = "deepseek-chat"
    usage = DeepSeekUsage(
        model="deepseek-chat",
        finish_reason="stop",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        request_seconds=0.1,
    )

    def side_effect(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], DeepSeekUsage]:
        operation = kwargs.get("operation")
        if operation == "book_plan":
            return _fake_blueprint_dict(), usage
        elif operation in ("book_write", "book_repair"):
            ch_num = kwargs.get("payload", {}).get("chapter_number", 1)
            return _fake_draft_dict(ch_num), usage
        elif operation == "book_review":
            return _fake_review_dict(), usage
        elif operation == "book_continuity":
            after_ch = kwargs.get("payload", {}).get("chapter_number", 1)
            return _fake_continuity_dict(after_ch), usage
        return {}, usage

    client.request_json.side_effect = side_effect
    return client


def _prepare_snapshot(db: Database, family_id: str) -> None:
    bundle = GroundingBundle(
        family_id=family_id,
        recordings=[
            {
                "recording_id": "rec_1",
                "family_id": family_id,
                "speaker_name": "Айгүл",
                "speaker_id": "spk_1",
                "detected_language": "ru",
            }
        ],
        pipeline_payloads={
            "rec_1": {
                "extraction": {
                    "languages": ["ru"],
                    "evidence_spans": [
                        {
                            "evidence_id": "ev_1",
                            "text": "В 1945 году старая домбра стояла у окна. «Это память», — сказал дедушка.",
                        }
                    ],
                }
            }
        },
        people=[
            {
                "person_id": "per_1",
                "family_id": family_id,
                "canonical_name": "Канат Дедушка",
                "normalized_name": "канат дедушка",
                "aliases": ["дедушка"],
                "verified_aliases": [],
                "category": "core",
                "source_recording_ids": ["rec_1"],
            }
        ],
        stories=[
            {
                "claim_id": "cl_story_1",
                "source_object_id": "story_1",
                "recording_id": "rec_1",
                "payload": {"title": "История", "summary": "1945 год"},
                "evidence_ids": ["ev_1"],
            }
        ],
        events=[],
        claims=[
            {
                "claim_id": "cl_1",
                "family_id": family_id,
                "recording_id": "rec_1",
                "object_type": "relationship",
                "source_object_id": "rel_1",
                "predicate": "grandfather",
                "evidence_ids": ["ev_1"],
                "evidence_class": "A_EXPLICIT",
            }
        ],
    )
    compiled = compile_source_snapshot(bundle)
    snapshot = compiled.snapshot
    snap_repo = BookSourceSnapshotRepository(db)
    snap_repo.save_snapshot(
        book_id="book_worker_test_1",
        family_id=family_id,
        compiler_version=snapshot.compiler_version,
        content_hash=compiled.content_hash,
        payload=snapshot.model_dump(mode="json"),
        manifest=snapshot.manifest.model_dump(mode="json"),
        source_recording_count=1,
        source_story_count=1,
        source_claim_count=1,
    )


def test_book_worker_end_to_end_flow(db: Database, family_and_user: tuple[str, str], tmp_path: Path) -> None:
    fid, uid = family_and_user
    _prepare_snapshot(db, fid)

    book_repo = BookRepository(db)
    job_repo = BookJobRepository(db)
    ch_repo = BookChapterRepository(db)
    exp_repo = BookExportRepository(db)
    artifact_storage = LocalBookArtifactStorage(tmp_path / "artifacts")
    client = _build_mock_client()

    book = book_repo.create_book(
        book_id="book_worker_test_1",
        family_id=fid,
        created_by_user_id=uid,
        title="Хроника семьи",
        output_language=BookLanguage.RU.value,
        target_word_count=1000,
    )
    job = job_repo.create_job(
        book_id=book.book_id,
        family_id=fid,
    )

    worker = BookJobWorker(
        db=db,
        deepseek_client=client,
        artifact_storage=artifact_storage,
        pdf_renderer=FakePDFRenderer(),
        poll_interval_seconds=0.1,
        blueprint_limits=BlueprintLimits(
            min_chapters=1,
            max_chapters=15,
            min_total_words=10,
            max_total_words=60000,
            min_chapter_words=5,
            max_chapter_words=10000,
        ),
    )

    processed = worker.process_once()
    assert processed is True

    # Inspect final book state
    updated_book = book_repo.get_book_unscoped(book.book_id)
    assert updated_book is not None
    assert updated_book.status == BookStatus.COMPLETED.value
    assert updated_book.stage == BookStage.COMPLETED.value
    assert updated_book.chapters_approved == 2
    assert updated_book.word_count > 0

    # Inspect job state
    updated_job = job_repo.get_job(job.job_id)
    assert updated_job is not None
    assert updated_job.status == BookJobStatus.COMPLETED.value
    assert updated_job.stage == BookStage.COMPLETED.value

    # Inspect chapters
    chapters = ch_repo.list_chapters(book_id=book.book_id)
    assert len(chapters) == 2
    for ch in chapters:
        assert ch.status == ChapterStatus.APPROVED.value
        assert ch.final_text is not None

    # Inspect exports
    pdf_export = exp_repo.get_export(book_id=book.book_id, format="pdf")
    epub_export = exp_repo.get_export(book_id=book.book_id, format="epub")
    assert pdf_export is not None
    assert epub_export is not None
    assert artifact_storage.exists(storage_key=pdf_export.storage_key)
    assert artifact_storage.exists(storage_key=epub_export.storage_key)


def test_book_worker_resumes_without_rewriting_approved(
    db: Database, family_and_user: tuple[str, str], tmp_path: Path
) -> None:
    fid, uid = family_and_user
    _prepare_snapshot(db, fid)

    book_repo = BookRepository(db)
    job_repo = BookJobRepository(db)
    ch_repo = BookChapterRepository(db)
    plan_repo = BookPlanRepository(db)
    continuity_repo = BookContinuityRepository(db)
    artifact_storage = LocalBookArtifactStorage(tmp_path / "artifacts")
    client = _build_mock_client()

    book = book_repo.create_book(
        book_id="book_worker_test_1",
        family_id=fid,
        created_by_user_id=uid,
        title="Хроника семьи",
        output_language=BookLanguage.RU.value,
        target_word_count=1000,
    )
    _job = job_repo.create_job(
        book_id=book.book_id,
        family_id=fid,
    )

    blueprint_data = _fake_blueprint_dict()
    plan_repo.save_plan(
        book_id=book.book_id,
        blueprint=blueprint_data,
        validation_report={"valid": True, "errors": [], "warnings": [], "repaired": False},
        chapter_count=2,
        target_total_words=1000,
        planner_prompt_version="v1",
        planner_model="deepseek-chat",
    )
    ch_repo.create_chapter_stubs(book_id=book.book_id, chapter_plans=blueprint_data["chapters"])

    # Pre-approve Chapter 1
    ch_repo.approve_chapter(
        book_id=book.book_id,
        chapter_number=1,
        final_text="Уже готовый текст первой главы.",
        word_count=6,
    )
    continuity_repo.save_continuity(
        book_id=book.book_id,
        after_chapter_number=1,
        state=_fake_continuity_dict(1),
        prompt_version="v1",
        model="deepseek-chat",
    )

    worker = BookJobWorker(
        db=db,
        deepseek_client=client,
        artifact_storage=artifact_storage,
        pdf_renderer=FakePDFRenderer(),
        blueprint_limits=BlueprintLimits(
            min_chapters=1,
            max_chapters=15,
            min_total_words=10,
            max_total_words=60000,
            min_chapter_words=5,
            max_chapter_words=10000,
        ),
    )

    processed = worker.process_once()
    assert processed is True

    # Verify Chapter 1 final text was NOT overwritten
    ch1 = ch_repo.get_chapter(book_id=book.book_id, chapter_number=1)
    assert ch1 is not None
    assert ch1.final_text == "Уже готовый текст первой главы."

    # Verify Chapter 2 was approved
    ch2 = ch_repo.get_chapter(book_id=book.book_id, chapter_number=2)
    assert ch2 is not None
    assert ch2.status == ChapterStatus.APPROVED.value

    # Check client calls: book_write should only have been called ONCE (for Chapter 2)
    write_calls = [
        call for call in client.request_json.call_args_list if call.kwargs.get("operation") == "book_write"
    ]
    assert len(write_calls) == 1


def test_book_worker_fails_closed_when_snapshot_is_missing(
    db: Database, family_and_user: tuple[str, str], tmp_path: Path
) -> None:
    fid, uid = family_and_user
    book_repo = BookRepository(db)
    job_repo = BookJobRepository(db)
    snapshot_repo = BookSourceSnapshotRepository(db)
    artifact_storage = LocalBookArtifactStorage(tmp_path / "artifacts")
    client = _build_mock_client()

    book = book_repo.create_book(
        book_id="book_missing_snapshot",
        family_id=fid,
        created_by_user_id=uid,
        title="Must stay frozen",
        output_language=BookLanguage.RU.value,
        target_word_count=1000,
    )
    job = job_repo.create_job(book_id=book.book_id, family_id=fid)

    worker = BookJobWorker(
        db=db,
        deepseek_client=client,
        artifact_storage=artifact_storage,
        pdf_renderer=FakePDFRenderer(),
    )

    assert worker.process_once() is True

    failed_book = book_repo.get_book_unscoped(book.book_id)
    failed_job = job_repo.get_job(job.job_id)
    assert failed_book is not None
    assert failed_book.status == BookStatus.FAILED.value
    assert failed_job is not None
    assert failed_job.status == BookJobStatus.FAILED.value
    assert snapshot_repo.get_snapshot(book.book_id) is None
    assert client.request_json.call_count == 0


def test_book_worker_cancellation(
    db: Database, family_and_user: tuple[str, str], tmp_path: Path
) -> None:
    fid, uid = family_and_user
    _prepare_snapshot(db, fid)

    book_repo = BookRepository(db)
    job_repo = BookJobRepository(db)
    artifact_storage = LocalBookArtifactStorage(tmp_path / "artifacts")
    client = _build_mock_client()

    book = book_repo.create_book(
        book_id="book_worker_test_1",
        family_id=fid,
        created_by_user_id=uid,
        title="Хроника семьи",
        output_language=BookLanguage.RU.value,
        target_word_count=1000,
    )
    job = job_repo.create_job(
        book_id=book.book_id,
        family_id=fid,
    )

    # Request cancellation before worker runs
    cancelled = book_repo.request_cancel(family_id=fid, book_id=book.book_id)
    assert cancelled is True

    worker = BookJobWorker(
        db=db,
        deepseek_client=client,
        artifact_storage=artifact_storage,
        pdf_renderer=FakePDFRenderer(),
    )

    processed = worker.process_once()
    assert processed is True

    updated_book = book_repo.get_book_unscoped(book.book_id)
    assert updated_book is not None
    assert updated_book.status == BookStatus.CANCELLED.value
    assert updated_book.stage == BookStage.CANCELLED.value

    updated_job = job_repo.get_job(job.job_id)
    assert updated_job is not None
    assert updated_job.status == BookJobStatus.CANCELLED.value
    assert updated_job.stage == BookStage.CANCELLED.value

    # LLM should never have been called
    assert client.request_json.call_count == 0
