"""Book export service and document renderers.

Orchestrates the conversion of approved book chapters into PDF and EPUB 3 artifacts,
stores them in private artifact storage, and records export metadata.
"""

from __future__ import annotations

import hashlib
from typing import Protocol

from mura.book.export_document import build_book_html, write_epub3
from mura.domain.book_models import ChapterStatus, ExportFormat, ExportStatus
from mura.storage.book import (
    BookChapterRepository,
    BookExportRepository,
    BookExportRow,
    BookRepository,
)
from mura.storage.book_artifacts import BookArtifactStorage


class ExportEngineUnavailable(RuntimeError):
    """Raised when an export engine (e.g. WeasyPrint) is not installed or available."""


class PDFRenderer(Protocol):
    def render_pdf(self, html_content: str) -> bytes: ...


class WeasyPrintRenderer:
    """Renders HTML to PDF using WeasyPrint."""

    def render_pdf(self, html_content: str) -> bytes:
        try:
            from mura.testing.fault_injection import (
                FAULT_PDF_FAILURE,
                consume_fault,
                is_fault_injection_enabled,
            )

            if is_fault_injection_enabled() and consume_fault(FAULT_PDF_FAILURE):
                raise RuntimeError("Injected WeasyPrint PDF renderer failure")
        except ImportError:
            pass

        try:
            import weasyprint
        except (ImportError, OSError) as exc:
            raise ExportEngineUnavailable(
                f"WeasyPrint is not available on this system: {exc}"
            ) from exc

        try:
            return weasyprint.HTML(string=html_content).write_pdf()
        except Exception as exc:
            raise RuntimeError(f"WeasyPrint PDF rendering failed: {exc}") from exc


class ExportService:
    def __init__(
        self,
        *,
        book_repo: BookRepository,
        chapter_repo: BookChapterRepository,
        export_repo: BookExportRepository,
        artifact_storage: BookArtifactStorage,
        pdf_renderer: PDFRenderer | None = None,
    ) -> None:
        self.book_repo = book_repo
        self.chapter_repo = chapter_repo
        self.export_repo = export_repo
        self.artifact_storage = artifact_storage
        self.pdf_renderer = pdf_renderer or WeasyPrintRenderer()

    def export_book(
        self,
        *,
        family_id: str,
        book_id: str,
        export_format: ExportFormat,
    ) -> BookExportRow:
        book = self.book_repo.get_book(family_id=family_id, book_id=book_id)
        if book is None:
            raise LookupError(f"Book not found: {book_id}")

        raw_chapters = self.chapter_repo.list_chapters(book_id=book_id)
        approved_chapters = [
            ch for ch in raw_chapters if ch.status == ChapterStatus.APPROVED.value
        ]
        if not approved_chapters:
            raise ValueError(f"Book {book_id} has no approved chapters to export")

        approved_chapters.sort(key=lambda ch: ch.chapter_number)

        chapters_data = [
            {
                "chapter_number": ch.chapter_number,
                "title": ch.title
                or (
                    ch.plan.get("title")
                    if isinstance(ch.plan, dict)
                    else f"Глава {ch.chapter_number}"
                ),
                "subtitle": ch.plan.get("subtitle") if isinstance(ch.plan, dict) else None,
                "time_period": ch.plan.get("time_period") if isinstance(ch.plan, dict) else None,
                "text": ch.final_text or ch.draft_text or "",
            }
            for ch in approved_chapters
        ]

        total_words = sum(ch.word_count for ch in approved_chapters)
        chapter_count = len(approved_chapters)

        if export_format == ExportFormat.PDF:
            html_content = build_book_html(
                title=book.title,
                subtitle=book.subtitle,
                language=book.output_language,
                chapters=chapters_data,
            )
            engine = "weasyprint"
            content_type = "application/pdf"
            try:
                data = self.pdf_renderer.render_pdf(html_content)
            except Exception as exc:
                self.export_repo.save_export(
                    book_id=book_id,
                    format=export_format.value,
                    status=ExportStatus.FAILED.value,
                    chapter_count=chapter_count,
                    word_count=total_words,
                    engine=engine,
                    error_code=(
                        "ENGINE_UNAVAILABLE"
                        if isinstance(exc, ExportEngineUnavailable)
                        else "RENDER_FAILED"
                    ),
                )
                raise
        elif export_format == ExportFormat.EPUB:
            engine = "internal_epub3"
            content_type = "application/epub+zip"
            data = write_epub3(
                title=book.title,
                subtitle=book.subtitle,
                language=book.output_language,
                book_id=book.book_id,
                chapters=chapters_data,
            )
        else:
            raise ValueError(f"Unsupported export format: {export_format}")

        sha256 = hashlib.sha256(data).hexdigest()
        size_bytes = len(data)

        storage_key = self.artifact_storage.store(
            family_id=family_id,
            book_id=book_id,
            export_format=export_format,
            data=data,
        )

        backend_name = "local"
        if hasattr(self.artifact_storage, "backend"):
            backend_attr = self.artifact_storage.backend
            backend_name = getattr(backend_attr, "value", str(backend_attr))

        export_row = self.export_repo.save_export(
            book_id=book_id,
            format=export_format.value,
            status=ExportStatus.READY.value,
            storage_key=storage_key,
            storage_backend=backend_name,
            size_bytes=size_bytes,
            sha256=sha256,
            content_type=content_type,
            chapter_count=chapter_count,
            word_count=total_words,
            engine=engine,
        )
        return export_row
