from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mura.book.exporter import BookExportCancelled, ExportService
from mura.domain.book_models import BookLanguage, BookStage, ExportFormat, ExportStatus
from mura.leases import LeaseOwnershipLost
from mura.storage.book import (
    BookChapterRepository,
    BookExportRepository,
    BookJobRepository,
    BookRepository,
)
from mura.storage.book_artifacts import LocalBookArtifactStorage
from mura.storage.cleanup import StorageCleanupJobRow, StorageCleanupRepository
from mura.storage.database import Database, utcnow
from mura.storage.identity import FamilyRow, UserRow


def _seed(tmp_path: Path):
    db = Database("sqlite+pysqlite:///:memory:")
    db.create_schema()
    now = utcnow()
    with db.session_factory.begin() as session:
        session.add(
            UserRow(
                user_id="user_book_delete",
                auth_issuer="issuer",
                auth_subject="subject",
                email=None,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            FamilyRow(
                family_id="fam_book_delete",
                name="Family",
                created_by_user_id="user_book_delete",
                created_at=now,
                updated_at=now,
            )
        )
    repo = BookRepository(db)
    book = repo.create_book(
        family_id="fam_book_delete",
        created_by_user_id="user_book_delete",
        title="Delete me",
        output_language=BookLanguage.RU.value,
        target_word_count=20000,
    )
    return db, repo, book, LocalBookArtifactStorage(tmp_path / "books")


def test_completed_book_delete_queues_exact_pdf_and_epub_cleanup(tmp_path: Path) -> None:
    db, repo, book, storage = _seed(tmp_path)
    exports = BookExportRepository(db)
    pdf_key = storage.store(
        family_id=book.family_id,
        book_id=book.book_id,
        export_format=ExportFormat.PDF,
        data=b"pdf",
    )
    epub_key = storage.store(
        family_id=book.family_id,
        book_id=book.book_id,
        export_format=ExportFormat.EPUB,
        data=b"epub",
    )
    exports.save_export(
        book_id=book.book_id,
        format=ExportFormat.PDF.value,
        status=ExportStatus.READY.value,
        storage_key=pdf_key,
        storage_backend="local",
    )
    exports.save_export(
        book_id=book.book_id,
        format=ExportFormat.EPUB.value,
        status=ExportStatus.READY.value,
        storage_key=epub_key,
        storage_backend="local",
    )

    cleanup_ids = repo.delete_family_book(
        family_id=book.family_id,
        book_id=book.book_id,
        default_storage_backend="local",
    )
    assert cleanup_ids is not None
    assert repo.get_book(family_id=book.family_id, book_id=book.book_id) is None

    with db.session_factory() as session:
        rows = list(session.query(StorageCleanupJobRow).all())
        assert len(rows) == 2
        assert {row.storage_key for row in rows} == {pdf_key, epub_key}


def test_book_without_export_metadata_still_queues_deterministic_keys(tmp_path: Path) -> None:
    db, repo, book, _storage = _seed(tmp_path)

    cleanup_ids = repo.delete_family_book(
        family_id=book.family_id,
        book_id=book.book_id,
        default_storage_backend="local",
    )
    assert cleanup_ids is not None

    with db.session_factory() as session:
        rows = list(session.query(StorageCleanupJobRow).all())
        assert len(rows) == 2
        assert {row.resource_type for row in rows} == {"book_pdf", "book_epub"}
        assert {row.storage_key.rsplit("/", 1)[-1] for row in rows} == {
            "book.pdf",
            "book.epub",
        }


def test_book_delete_rolls_back_if_cleanup_enqueue_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, repo, book, _storage = _seed(tmp_path)
    cleanup_repo = StorageCleanupRepository(db)
    monkeypatch.setattr(
        cleanup_repo,
        "enqueue_many",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("enqueue failed")),
    )

    with pytest.raises(RuntimeError, match="enqueue failed"):
        repo.delete_family_book(
            family_id=book.family_id,
            book_id=book.book_id,
            cleanup_repository=cleanup_repo,
        )

    assert repo.get_book(family_id=book.family_id, book_id=book.book_id) is not None
    with db.session_factory() as session:
        assert session.query(StorageCleanupJobRow).count() == 0


def test_cancelled_book_cannot_cross_artifact_durability_boundary(tmp_path: Path) -> None:
    db, repo, book, storage = _seed(tmp_path)
    chapters = BookChapterRepository(db)
    chapters.create_chapter_stubs(
        book_id=book.book_id,
        chapter_plans=[
            {
                "chapter_number": 1,
                "title": "One",
                "target_word_count": 100,
            }
        ],
    )
    chapters.approve_chapter(
        book_id=book.book_id,
        chapter_number=1,
        final_text="grounded text",
        word_count=2,
    )
    repo.request_cancel(family_id=book.family_id, book_id=book.book_id)

    renderer = MagicMock()
    renderer.render_pdf.return_value = b"pdf"
    service = ExportService(
        book_repo=repo,
        chapter_repo=chapters,
        export_repo=BookExportRepository(db),
        artifact_storage=storage,
        pdf_renderer=renderer,
    )

    with pytest.raises(BookExportCancelled):
        service.export_book(
            family_id=book.family_id,
            book_id=book.book_id,
            export_format=ExportFormat.PDF,
        )

    assert list((tmp_path / "books").rglob("*.pdf")) == []


def test_stale_book_worker_cannot_mutate_or_publish_after_reclaim(tmp_path: Path) -> None:
    db, repo, book, storage = _seed(tmp_path)
    chapters = BookChapterRepository(db)
    chapters.create_chapter_stubs(
        book_id=book.book_id,
        chapter_plans=[
            {
                "chapter_number": 1,
                "title": "One",
                "target_word_count": 100,
            }
        ],
    )
    chapters.approve_chapter(
        book_id=book.book_id,
        chapter_number=1,
        final_text="grounded text",
        word_count=2,
    )

    jobs = BookJobRepository(db)
    job = jobs.create_job(book_id=book.book_id, family_id=book.family_id)
    first = jobs.claim_next_job(
        lease_owner="worker_old",
        lease_seconds=60,
        now=utcnow(),
    )
    assert first is not None

    # Simulate a stalled worker whose lease expires and is legitimately
    # reclaimed by another process.
    with db.session_factory.begin() as session:
        row = session.get(type(first), job.job_id)
        assert row is not None
        row.lease_expires_at = utcnow() - timedelta(seconds=1)

    reclaimed = jobs.claim_next_job(
        lease_owner="worker_new",
        lease_seconds=60,
        now=utcnow(),
    )
    assert reclaimed is not None
    assert reclaimed.lease_owner == "worker_new"

    before = repo.get_book_unscoped(book.book_id)
    assert before is not None
    with pytest.raises(LeaseOwnershipLost):
        repo.update_stage(
            book.book_id,
            stage=BookStage.PLANNING.value,
            job_id=job.job_id,
            lease_owner="worker_old",
        )
    after = repo.get_book_unscoped(book.book_id)
    assert after is not None
    assert after.stage == before.stage

    # Chapter publication is fenced by the same current job lease.
    with pytest.raises(LeaseOwnershipLost):
        chapters.approve_chapter(
            book_id=book.book_id,
            chapter_number=1,
            final_text="stale overwrite",
            word_count=2,
            job_id=job.job_id,
            lease_owner="worker_old",
        )
    current = chapters.get_chapter(book_id=book.book_id, chapter_number=1)
    assert current is not None
    assert current.final_text == "grounded text"

    renderer = MagicMock()
    renderer.render_pdf.return_value = b"pdf"
    service = ExportService(
        book_repo=repo,
        chapter_repo=chapters,
        export_repo=BookExportRepository(db),
        artifact_storage=storage,
        pdf_renderer=renderer,
    )
    with pytest.raises(LeaseOwnershipLost):
        service.export_book(
            family_id=book.family_id,
            book_id=book.book_id,
            export_format=ExportFormat.PDF,
            job_id=job.job_id,
            lease_owner="worker_old",
        )

    assert list((tmp_path / "books").rglob("*.pdf")) == []
