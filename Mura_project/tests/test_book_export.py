"""Tests for book document export, EPUB 3 generation, and export service."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from mura.book.export_document import build_book_html, write_epub3
from mura.book.exporter import ExportEngineUnavailable, ExportService
from mura.domain.book_models import (
    BookLanguage,
    BookStage,
    BookStatus,
    ExportFormat,
    ExportStatus,
)
from mura.storage.book import (
    BookChapterRepository,
    BookExportRepository,
    BookRepository,
)
from mura.storage.book_artifacts import LocalBookArtifactStorage
from mura.storage.database import Database, utcnow
from mura.storage.identity import FamilyRow, UserRow


@pytest.fixture
def db() -> Database:
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    return database


@pytest.fixture
def family_and_user(db: Database) -> tuple[str, str]:
    now = utcnow()
    fid = "family_export_test_1"
    uid = "user_export_test_1"
    with db.session_factory.begin() as session:
        session.add(
            FamilyRow(family_id=fid, name="Export Test Family", created_at=now, updated_at=now)
        )
        session.add(
            UserRow(
                user_id=uid,
                auth_issuer="https://auth.mura.test",
                auth_subject="sub_test_export_user_1",
                email="export@test.com",
                created_at=now,
                updated_at=now,
            )
        )
    return fid, uid


class FakePDFRenderer:
    def __init__(self, output: bytes = b"%PDF-1.4 Fake PDF Content") -> None:
        self.output = output
        self.last_rendered_html: str | None = None

    def render_pdf(self, html_content: str) -> bytes:
        self.last_rendered_html = html_content
        return self.output


class FailingPDFRenderer:
    def render_pdf(self, html_content: str) -> bytes:
        raise ExportEngineUnavailable("WeasyPrint is not installed in test environment")


def test_build_book_html_structure() -> None:
    chapters = [
        {
            "chapter_number": 1,
            "title": "Детство на Иртыше",
            "subtitle": "1945–1955",
            "time_period": "1945-1955",
            "text": "Это было тяжелое послевоенное время.\n\nАул стоял на высоком берегу.",
        },
        {
            "chapter_number": 2,
            "title": "Переезд в Алма-Ату",
            "subtitle": "1956",
            "time_period": "1956",
            "text": "Поезд шел двое суток через степь.",
        },
    ]

    html_out = build_book_html(
        title="Летопись семьи Касымовых",
        subtitle="Воспоминания дедушки",
        language=BookLanguage.RU.value,
        chapters=chapters,
    )

    assert "<!DOCTYPE html>" in html_out
    assert "Летопись семьи Касымовых" in html_out
    assert "Воспоминания дедушки" in html_out
    assert "Детство на Иртыше" in html_out
    assert "Переезд в Алма-Ату" in html_out
    assert "Это было тяжелое послевоенное время." in html_out
    assert "Поезд шел двое суток через степь." in html_out


def test_write_epub3_structure() -> None:
    chapters = [
        {
            "chapter_number": 1,
            "title": "Ауылдағы балалық шақ",
            "subtitle": "1950–1960",
            "time_period": "1950-1960",
            "text": (
                "Біздің ауыл өзеннің бойында орналасқан.\n\n"
                "Көктемде күн жылынғанда дала құлпыратын."
            ),
        }
    ]

    epub_bytes = write_epub3(
        title="Қасымовтар шежіресі",
        subtitle="Естеліктер",
        language=BookLanguage.KK.value,
        book_id="book_epub_123",
        chapters=chapters,
    )

    assert len(epub_bytes) > 0

    # Inspect zip structure
    zf = zipfile.ZipFile(io.BytesIO(epub_bytes), "r")
    namelist = zf.namelist()

    # Rule 1: First entry is mimetype, uncompressed
    assert namelist[0] == "mimetype"
    mimetype_info = zf.getinfo("mimetype")
    assert mimetype_info.compress_type == zipfile.ZIP_STORED
    assert zf.read("mimetype") == b"application/epub+zip"

    # Rule 2: META-INF/container.xml pointing to content.opf
    assert "META-INF/container.xml" in namelist
    container_xml = zf.read("META-INF/container.xml").decode("utf-8")
    assert 'full-path="OEBPS/content.opf"' in container_xml

    # Rule 3: OEBPS files
    assert "OEBPS/content.opf" in namelist
    assert "OEBPS/nav.xhtml" in namelist
    assert "OEBPS/title.xhtml" in namelist
    assert "OEBPS/chapter_1.xhtml" in namelist
    assert "OEBPS/style.css" in namelist

    # Rule 4: Chapter content is present
    ch1_content = zf.read("OEBPS/chapter_1.xhtml").decode("utf-8")
    assert "Ауылдағы балалық шақ" in ch1_content
    assert "Біздің ауыл өзеннің бойында орналасқан." in ch1_content

    # Rule 5: Nav TOC is present
    nav_content = zf.read("OEBPS/nav.xhtml").decode("utf-8")
    assert 'epub:type="toc"' in nav_content
    assert "Ауылдағы балалық шақ" in nav_content


def test_export_service_pdf_and_epub(
    db: Database, family_and_user: tuple[str, str], tmp_path: Path
) -> None:
    fid, uid = family_and_user
    book_repo = BookRepository(db)
    ch_repo = BookChapterRepository(db)
    exp_repo = BookExportRepository(db)
    artifact_storage = LocalBookArtifactStorage(tmp_path / "artifacts")
    fake_renderer = FakePDFRenderer(b"%PDF-1.4 Beautiful Family Book")

    book = book_repo.create_book(
        family_id=fid,
        title="Семейная Книга",
        created_by_user_id=uid,
        output_language=BookLanguage.RU.value,
        target_word_count=5000,
    )
    book_repo.update_stage(
        book.book_id,
        stage=BookStage.EXPORTING_PDF.value,
        status=BookStatus.COMPLETED.value,
        chapters_total=1,
        chapters_approved=1,
    )
    ch_repo.create_chapter_stubs(
        book_id=book.book_id,
        chapter_plans=[
            {
                "chapter_number": 1,
                "title": "Первая глава",
                "source_recording_ids": ["rec_1"],
            }
        ],
    )
    ch_repo.approve_chapter(
        book_id=book.book_id,
        chapter_number=1,
        final_text="Текст первой одобренной главы.",
        word_count=5,
    )

    service = ExportService(
        book_repo=book_repo,
        chapter_repo=ch_repo,
        export_repo=exp_repo,
        artifact_storage=artifact_storage,
        pdf_renderer=fake_renderer,
    )

    # 1. Export PDF
    pdf_row = service.export_book(
        family_id=fid,
        book_id=book.book_id,
        export_format=ExportFormat.PDF,
    )
    assert pdf_row.status == ExportStatus.READY.value
    assert pdf_row.format == "pdf"
    assert pdf_row.size_bytes == len(b"%PDF-1.4 Beautiful Family Book")
    assert pdf_row.sha256 is not None
    assert artifact_storage.exists(storage_key=pdf_row.storage_key)
    retrieved_pdf = artifact_storage.retrieve(storage_key=pdf_row.storage_key)
    assert retrieved_pdf == b"%PDF-1.4 Beautiful Family Book"

    # 2. Export EPUB
    epub_row = service.export_book(
        family_id=fid,
        book_id=book.book_id,
        export_format=ExportFormat.EPUB,
    )
    assert epub_row.status == ExportStatus.READY.value
    assert epub_row.format == "epub"
    assert epub_row.size_bytes > 0
    assert artifact_storage.exists(storage_key=epub_row.storage_key)


def test_export_service_engine_unavailable(
    db: Database, family_and_user: tuple[str, str], tmp_path: Path
) -> None:
    fid, uid = family_and_user
    book_repo = BookRepository(db)
    ch_repo = BookChapterRepository(db)
    exp_repo = BookExportRepository(db)
    artifact_storage = LocalBookArtifactStorage(tmp_path / "artifacts")
    failing_renderer = FailingPDFRenderer()

    book = book_repo.create_book(
        family_id=fid,
        title="Тест Ошибки Движка",
        created_by_user_id=uid,
        output_language=BookLanguage.RU.value,
        target_word_count=5000,
    )
    book_repo.update_stage(
        book.book_id,
        stage=BookStage.EXPORTING_PDF.value,
        status=BookStatus.COMPLETED.value,
        chapters_total=1,
        chapters_approved=1,
    )
    ch_repo.create_chapter_stubs(
        book_id=book.book_id,
        chapter_plans=[
            {
                "chapter_number": 1,
                "title": "Глава 1",
                "source_recording_ids": ["rec_1"],
            }
        ],
    )
    ch_repo.approve_chapter(
        book_id=book.book_id,
        chapter_number=1,
        final_text="Текст главы",
        word_count=2,
    )

    service = ExportService(
        book_repo=book_repo,
        chapter_repo=ch_repo,
        export_repo=exp_repo,
        artifact_storage=artifact_storage,
        pdf_renderer=failing_renderer,
    )

    with pytest.raises(ExportEngineUnavailable):
        service.export_book(
            family_id=fid,
            book_id=book.book_id,
            export_format=ExportFormat.PDF,
        )

    export_row = exp_repo.get_export(book_id=book.book_id, format="pdf")
    assert export_row is not None
    assert export_row.status == ExportStatus.FAILED.value
    assert export_row.error_code == "ENGINE_UNAVAILABLE"


def test_local_artifact_storage_traversal_prevention(tmp_path: Path) -> None:
    storage = LocalBookArtifactStorage(tmp_path / "artifacts")
    with pytest.raises(ValueError, match="Storage path traversal detected"):
        storage.store(
            family_id="../../etc",
            book_id="passwd",
            export_format=ExportFormat.PDF,
            data=b"evil",
        )
