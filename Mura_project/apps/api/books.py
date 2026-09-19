"""Family-scoped Book API.

10 endpoints providing end-to-end Family Book generation, source eligibility,
truthful progress reporting, lazy chapter reading, artifact streaming, and
re-generation without destructive overwrites.

All endpoints are family-scoped. A non-existent book or a book belonging to a
different family returns 404 (BOLA defense).
"""

from __future__ import annotations

import io
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Protocol, cast

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from sqlalchemy.orm import Session

from apps.api.errors import FAMILY_NOT_FOUND
from mura.book.snapshot import compile_source_snapshot
from mura.quotas import BookQuotaService
from mura.domain.book_models import (
    TERMINAL_BOOK_JOB_STATUSES,
    BookAccepted,
    BookChapterPageView,
    BookChapterSummaryView,
    BookChapterView,
    BookCreateRequest,
    BookDetailView,
    BookListPageView,
    BookProgressView,
    BookRegenerateRequest,
    BookSourceOptionView,
    BookStage,
    BookStatus,
    BookSummaryView,
    ChapterStatus,
    ExportFormat,
    ExportStatus,
)
from mura.identity.context import AuthorizedFamilyContext
from mura.storage.book import (
    BookChapterRepository,
    BookCreationRepository,
    BookExportRepository,
    BookJobRepository,
    BookPlanRepository,
    BookRepository,
    BookSourceSnapshotRepository,
    get_eligible_recordings,
)
from mura.storage.book_artifacts import BookArtifactStorage, LocalBookArtifactStorage
from mura.storage.book_artifacts import BookArtifactStorage, build_book_artifact_storage
from mura.storage.database import Database, PipelineResultRow


class RuntimeWithDatabaseAndSettings(Protocol):
    database: Database
    settings: Any


def _not_found(detail: str = FAMILY_NOT_FOUND) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _get_artifact_storage(runtime: object) -> BookArtifactStorage:
    typed = cast(RuntimeWithDatabaseAndSettings, runtime)
    storage_dir = getattr(typed.settings, "book_storage_dir", Path(".mura/books"))
    return LocalBookArtifactStorage(storage_dir)
    return build_book_artifact_storage(typed.settings)


def resolve_book_source_ids(
    session: Session,
    *,
    family_id: str,
    requested_recording_ids: list[str] | None,
) -> list[str]:
    """Validate and resolve requested recording IDs for a family book.

    If requested_recording_ids is None, returns all eligible recordings in the family.
    If requested_recording_ids is provided, ensures every ID exists and is eligible;
    otherwise raises HTTP 400 without leaking cross-family data.
    """
    if requested_recording_ids is not None and len(requested_recording_ids) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one recording must be selected for book generation.",
        )

    eligible_recordings = get_eligible_recordings(session, family_id=family_id)
    if not eligible_recordings:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Archive has no eligible processed recordings for book generation.",
        )

    eligible_id_set = {rec.recording_id for rec in eligible_recordings}

    if requested_recording_ids is None:
        return [rec.recording_id for rec in eligible_recordings]

    for rec_id in requested_recording_ids:
        if rec_id not in eligible_id_set:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="One or more requested recordings are not eligible or do not exist in the family archive.",
            )

    seen: set[str] = set()
    deduped: list[str] = []
    for rid in requested_recording_ids:
        if rid not in seen:
            seen.add(rid)
            deduped.append(rid)
    return deduped


def _build_progress_view(book: Any, chapter_repo: BookChapterRepository) -> BookProgressView:
    current_title = None
    if book.current_chapter_number is not None:
        ch = chapter_repo.get_chapter(
            book_id=book.book_id, chapter_number=book.current_chapter_number
        )
        if ch:
            current_title = ch.title

    return BookProgressView(
        stage=BookStage(book.stage),
        chapters_total=book.chapters_total or 0,
        chapters_approved=book.chapters_approved or 0,
        current_chapter_number=book.current_chapter_number,
        current_chapter_title=current_title,
    )


def _build_summary_view(book: Any, chapter_repo: BookChapterRepository) -> BookSummaryView:
    progress = _build_progress_view(book, chapter_repo)
    return BookSummaryView(
        book_id=book.book_id,
        family_id=book.family_id,
        title=book.title,
        subtitle=book.subtitle,
        status=BookStatus(book.status),
        output_language=book.output_language,
        target_word_count=book.target_word_count,
        chapters_total=book.chapters_total or 0,
        chapters_approved=book.chapters_approved or 0,
        word_count=book.word_count or 0,
        source_snapshot_version=book.source_snapshot_version or 1,
        supersedes_book_id=book.supersedes_book_id,
        created_at=book.created_at,
        updated_at=book.updated_at,
        completed_at=book.completed_at,
        error_code=book.error_code,
        progress=progress,
    )


def _build_detail_view(
    book: Any,
    snapshot_repo: BookSourceSnapshotRepository,
    plan_repo: BookPlanRepository,
    chapter_repo: BookChapterRepository,
    export_repo: BookExportRepository,
) -> BookDetailView:
    summary = _build_summary_view(book, chapter_repo)

    snapshot = snapshot_repo.get_snapshot(book.book_id)
    source_recording_count = snapshot.source_recording_count if snapshot else 0

    plan = plan_repo.get_plan(book.book_id)
    central_theme = plan.central_theme if plan else None
    narrative_voice = plan.narrative_voice if plan else None
    material_anchor = plan.material_anchor if plan else None

    exports = export_repo.list_exports(book.book_id)
    available_formats: list[ExportFormat] = []
    for exp in exports:
        if exp.status == ExportStatus.READY.value:
            try:
                available_formats.append(ExportFormat(exp.export_format))
            except ValueError:
                pass

    return BookDetailView(
        **summary.model_dump(),
        central_theme=central_theme,
        narrative_voice=narrative_voice,
        material_anchor=material_anchor,
        source_recording_count=source_recording_count,
        available_formats=available_formats,
    )


def register_book_routes(
    app: FastAPI,
    *,
    get_runtime_dependency: Callable[..., object],
    read_books_dependency: Callable[..., object],
    create_book_dependency: Callable[..., object],
    delete_book_dependency: Callable[..., object] | None = None,
) -> None:
    @app.post(
        "/v1/families/{family_id}/books",
        response_model=BookAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def create_family_book(
        family_id: str,
        payload: BookCreateRequest,
        context: AuthorizedFamilyContext = Depends(create_book_dependency),
        runtime: object = Depends(get_runtime_dependency),
    ) -> BookAccepted:
        typed = cast(RuntimeWithDatabaseAndSettings, runtime)
        with typed.database.session_factory.begin() as session:
            BookQuotaService.check_creation_allowed(session, family_id, typed.settings)
            resolved_ids = resolve_book_source_ids(
                session,
                family_id=family_id,
                requested_recording_ids=payload.requested_recording_ids,
            )
            if not resolved_ids:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Archive has no eligible processed recordings for book generation.",
                )

        book_repo = BookRepository(typed.database)
        job_repo = BookJobRepository(typed.database)
        compiled = compile_source_snapshot(
            typed.database,
            family_id=family_id,
            recording_ids=resolved_ids,
        )

        creation_repo = BookCreationRepository(typed.database)
        result = creation_repo.create_queued_book(
            family_id=family_id,
            created_by_user_id=context.user_id,
            title=payload.title,
            subtitle=payload.subtitle,
            output_language=payload.output_language.value,
            target_word_count=payload.target_word_count,
            compiled_snapshot=compiled,
        )
        return BookAccepted(
            book_id=result.book.book_id,
            job_id=result.job.job_id,
            status=BookStatus.QUEUED,
        )

    @app.get(
        "/v1/families/{family_id}/books/sources",
        response_model=list[BookSourceOptionView],
        dependencies=[Depends(read_books_dependency)],
    )
    def list_family_book_sources(
        family_id: str,
        runtime: object = Depends(get_runtime_dependency),
    ) -> list[BookSourceOptionView]:
        typed = cast(RuntimeWithDatabaseAndSettings, runtime)
        with typed.database.session_factory() as session:
            eligible = get_eligible_recordings(session, family_id=family_id)
            items: list[BookSourceOptionView] = []
            for rec in eligible:
                pres = session.get(PipelineResultRow, rec.recording_id)
                sc = len(pres.payload.get("stories", [])) if pres and pres.payload else 0
                pc = len(pres.payload.get("extracted_entities", [])) if pres and pres.payload else 0
                items.append(
                    BookSourceOptionView(
                        recording_id=rec.recording_id,
                        speaker_name=rec.speaker_name or "",
                        recorded_at=rec.created_at,
                        story_count=sc,
                        person_count=pc,
                        title=getattr(rec, "title", None) or rec.original_filename,
                    )
                )
            return items

    @app.get(
        "/v1/families/{family_id}/books",
        response_model=BookListPageView,
        dependencies=[Depends(read_books_dependency)],
    )
    def list_family_books(
        family_id: str,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
        runtime: object = Depends(get_runtime_dependency),
    ) -> BookListPageView:
        typed = cast(RuntimeWithDatabaseAndSettings, runtime)
        book_repo = BookRepository(typed.database)
        chapter_repo = BookChapterRepository(typed.database)

        books, total = book_repo.list_books(family_id=family_id, limit=limit, offset=offset)
        items = [_build_summary_view(b, chapter_repo) for b in books]
        return BookListPageView(
            page={"total": total, "limit": limit, "offset": offset},
            items=items,
        )

    @app.get(
        "/v1/families/{family_id}/books/{book_id}",
        response_model=BookDetailView,
        dependencies=[Depends(read_books_dependency)],
    )
    def get_family_book(
        family_id: str,
        book_id: str,
        runtime: object = Depends(get_runtime_dependency),
    ) -> BookDetailView:
        typed = cast(RuntimeWithDatabaseAndSettings, runtime)
        book_repo = BookRepository(typed.database)
        book = book_repo.get_book(family_id=family_id, book_id=book_id)
        if book is None:
            raise _not_found()

        snapshot_repo = BookSourceSnapshotRepository(typed.database)
        plan_repo = BookPlanRepository(typed.database)
        chapter_repo = BookChapterRepository(typed.database)
        export_repo = BookExportRepository(typed.database)

        return _build_detail_view(book, snapshot_repo, plan_repo, chapter_repo, export_repo)

    @app.get(
        "/v1/families/{family_id}/books/{book_id}/status",
        response_model=BookProgressView,
        dependencies=[Depends(read_books_dependency)],
    )
    def get_family_book_status(
        family_id: str,
        book_id: str,
        runtime: object = Depends(get_runtime_dependency),
    ) -> BookProgressView:
        typed = cast(RuntimeWithDatabaseAndSettings, runtime)
        book_repo = BookRepository(typed.database)
        book = book_repo.get_book(family_id=family_id, book_id=book_id)
        if book is None:
            raise _not_found()

        chapter_repo = BookChapterRepository(typed.database)
        return _build_progress_view(book, chapter_repo)

    @app.get(
        "/v1/families/{family_id}/books/{book_id}/chapters",
        response_model=BookChapterPageView,
        dependencies=[Depends(read_books_dependency)],
    )
    def list_family_book_chapters(
        family_id: str,
        book_id: str,
        runtime: object = Depends(get_runtime_dependency),
    ) -> BookChapterPageView:
        typed = cast(RuntimeWithDatabaseAndSettings, runtime)
        book_repo = BookRepository(typed.database)
        book = book_repo.get_book(family_id=family_id, book_id=book_id)
        if book is None:
            raise _not_found()

        chapter_repo = BookChapterRepository(typed.database)
        chapters = chapter_repo.list_chapters(book_id=book_id)
        items = [
            BookChapterSummaryView(
                chapter_number=ch.chapter_number,
                title=ch.title,
                status=ChapterStatus(ch.status),
                word_count=ch.word_count or 0,
                approved=(ch.status == ChapterStatus.APPROVED.value),
            )
            for ch in chapters
        ]
        return BookChapterPageView(
            page={"total": len(items), "limit": max(len(items), 1), "offset": 0},
            items=items,
        )

    @app.get(
        "/v1/families/{family_id}/books/{book_id}/chapters/{chapter_number}",
        response_model=BookChapterView,
        dependencies=[Depends(read_books_dependency)],
    )
    def get_family_book_chapter(
        family_id: str,
        book_id: str,
        chapter_number: int,
        runtime: object = Depends(get_runtime_dependency),
    ) -> BookChapterView:
        typed = cast(RuntimeWithDatabaseAndSettings, runtime)
        book_repo = BookRepository(typed.database)
        book = book_repo.get_book(family_id=family_id, book_id=book_id)
        if book is None:
            raise _not_found()

        chapter_repo = BookChapterRepository(typed.database)
        ch = chapter_repo.get_chapter(book_id=book_id, chapter_number=chapter_number)
        if ch is None or ch.status != ChapterStatus.APPROVED.value:
            raise _not_found("Chapter not found or not yet approved")

        text = ch.final_text or ch.draft_text or ""
        if not text:
            raise _not_found("Chapter not found or not yet approved")

        return BookChapterView(
            chapter_number=ch.chapter_number,
            title=ch.title or f"Chapter {ch.chapter_number}",
            text=text,
            word_count=ch.word_count or len(text.split()),
            approved_at=ch.approved_at,
        )

    @app.get(
        "/v1/families/{family_id}/books/{book_id}/download",
        dependencies=[Depends(read_books_dependency)],
        response_class=StreamingResponse,
    )
    def download_family_book(
        family_id: str,
        book_id: str,
        format: Annotated[str, Query()] = "pdf",
        runtime: object = Depends(get_runtime_dependency),
    ) -> StreamingResponse:
        try:
            fmt = ExportFormat(format.lower())
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="format must be 'pdf' or 'epub'",
            ) from None

        typed = cast(RuntimeWithDatabaseAndSettings, runtime)
        book_repo = BookRepository(typed.database)
        book = book_repo.get_book(family_id=family_id, book_id=book_id)
        if book is None:
            raise _not_found()

        if book.status != BookStatus.COMPLETED.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Book is not yet completed",
            )

        export_repo = BookExportRepository(typed.database)
        exp = export_repo.get_export(book_id=book_id, format=fmt.value)
        if exp is None or exp.status != ExportStatus.READY.value or not exp.storage_key:
            raise _not_found("Export file not ready")

        storage = _get_artifact_storage(runtime)
        try:
            data = storage.retrieve(storage_key=exp.storage_key)
        except FileNotFoundError:
            raise _not_found("Export file missing from storage") from None

        media_type = "application/pdf" if fmt == ExportFormat.PDF else "application/epub+zip"
        ascii_chars = [c for c in book.title if c.isascii() and (c.isalnum() or c in " -_")]
        ascii_title = "".join(ascii_chars).strip() or "book"
        ascii_filename = f"{ascii_title}.{fmt.value}"
        encoded_filename = urllib.parse.quote(f"{book.title}.{fmt.value}")
        content_disposition = (
            f'attachment; filename="{ascii_filename}"; filename*=UTF-8\'\'{encoded_filename}'
        )

        return StreamingResponse(
            io.BytesIO(data),
            media_type=media_type,
            headers={
                "content-disposition": content_disposition,
                "cache-control": "private, no-store",
            },
        )

    @app.post(
        "/v1/families/{family_id}/books/{book_id}/cancel",
        response_model=BookDetailView,
    )
    def cancel_family_book(
        family_id: str,
        book_id: str,
        context: AuthorizedFamilyContext = Depends(create_book_dependency),
        runtime: object = Depends(get_runtime_dependency),
    ) -> BookDetailView:
        typed = cast(RuntimeWithDatabaseAndSettings, runtime)
        book_repo = BookRepository(typed.database)
        book = book_repo.get_book(family_id=family_id, book_id=book_id)
        if book is None:
            raise _not_found()

        book_repo.request_cancel(family_id=family_id, book_id=book_id)

        job_repo = BookJobRepository(typed.database)
        job = job_repo.get_job_for_book(book_id)
        if job and job.status not in TERMINAL_BOOK_JOB_STATUSES:
            if job.status == BookStatus.QUEUED.value and job.lease_owner is None:
                job_repo.cancel_job(job.job_id)
                book_repo.cancel_book(book_id)

        # Refresh
        book = book_repo.get_book(family_id=family_id, book_id=book_id)
        assert book is not None

        snapshot_repo = BookSourceSnapshotRepository(typed.database)
        plan_repo = BookPlanRepository(typed.database)
        chapter_repo = BookChapterRepository(typed.database)
        export_repo = BookExportRepository(typed.database)

        return _build_detail_view(book, snapshot_repo, plan_repo, chapter_repo, export_repo)

    @app.post(
        "/v1/families/{family_id}/books/{book_id}/regenerate",
        response_model=BookAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def regenerate_family_book(
        family_id: str,
        book_id: str,
        payload: BookRegenerateRequest | None = None,
        context: AuthorizedFamilyContext = Depends(create_book_dependency),
        runtime: object = Depends(get_runtime_dependency),
    ) -> BookAccepted:
        typed = cast(RuntimeWithDatabaseAndSettings, runtime)
        book_repo = BookRepository(typed.database)
        book = book_repo.get_book(family_id=family_id, book_id=book_id)
        if book is None:
            raise _not_found()

        snapshot_repo = BookSourceSnapshotRepository(typed.database)
        orig_snapshot = snapshot_repo.get_snapshot(book_id)

        if payload and payload.requested_recording_ids is not None:
            req_ids: list[str] | None = payload.requested_recording_ids
        elif orig_snapshot and orig_snapshot.manifest:
            req_ids = orig_snapshot.manifest.get("source_recording_ids")
        else:
            req_ids = None

        with typed.database.session_factory.begin() as session:
            BookQuotaService.check_creation_allowed(session, family_id, typed.settings)
            resolved_ids = resolve_book_source_ids(
                session,
                family_id=family_id,
                requested_recording_ids=req_ids,
            )

        compiled = compile_source_snapshot(
            typed.database,
            family_id=family_id,
            recording_ids=resolved_ids,
        )

        title = payload.title if payload and payload.title else book.title
        subtitle = payload.subtitle if payload and payload.subtitle is not None else book.subtitle
        output_lang = (
            payload.output_language.value
            if payload and payload.output_language
            else book.output_language
        )
        target_words = (
            payload.target_word_count
            if payload and payload.target_word_count
            else book.target_word_count
        )

        creation_repo = BookCreationRepository(typed.database)
        result = creation_repo.create_queued_book(
            family_id=family_id,
            created_by_user_id=context.user_id,
            title=title,
            subtitle=subtitle,
            output_language=output_lang,
            target_word_count=target_words,
            compiled_snapshot=compiled,
            supersedes_book_id=book.book_id,
            source_snapshot_version=(book.source_snapshot_version or 1) + 1,
        )
        return BookAccepted(
            book_id=result.book.book_id,
            job_id=result.job.job_id,
            status=BookStatus.QUEUED,
        )

    @app.delete(
        "/v1/families/{family_id}/books/{book_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(delete_book_dependency or create_book_dependency)],
    )
    def delete_family_book(
        family_id: str,
        book_id: str,
        runtime: object = Depends(get_runtime_dependency),
    ) -> None:
        typed = cast(RuntimeWithDatabaseAndSettings, runtime)
        book_repo = BookRepository(typed.database)
        book = book_repo.get_book(family_id=family_id, book_id=book_id)
        if book is None:
            raise _not_found()

        export_keys = book_repo.delete_family_book(family_id=family_id, book_id=book_id)
        if export_keys is None:
            raise _not_found()

        artifact_storage = build_book_artifact_storage(typed.settings)
        for key in export_keys:
            try:
                artifact_storage.delete(storage_key=key)
            except Exception:
                pass
