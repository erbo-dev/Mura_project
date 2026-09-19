"""Privacy and data lifecycle service.

Exports structured family archive data including family details, members,
recordings with pipeline results, entities, relationships, stories, and book metadata.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from mura.storage.archive import ArchiveClaimRow, ArchivePersonRow, FamilyGraphEdgeRow
from mura.storage.book import BookChapterRow, BookExportRow, BookRow
from mura.storage.database import (
    Database,
    PipelineResultRow,
    RecordingRow,
    utcnow,
)
from mura.storage.identity import FamilyMembershipRow, FamilyRow, UserRow


def export_family_data(database: Database, *, family_id: str) -> dict[str, Any] | None:
    with database.session_factory() as session:
        family = session.scalar(select(FamilyRow).where(FamilyRow.family_id == family_id))
        if family is None:
            return None

        family_info = {
            "family_id": family.family_id,
            "name": family.name,
            "created_at": family.created_at.isoformat() if family.created_at else None,
            "updated_at": family.updated_at.isoformat() if family.updated_at else None,
        }

        members_stmt = (
            select(FamilyMembershipRow, UserRow)
            .outerjoin(UserRow, UserRow.user_id == FamilyMembershipRow.user_id)
            .where(FamilyMembershipRow.family_id == family_id)
        )
        members_list = []
        for membership, user in session.execute(members_stmt):
            members_list.append({
                "user_id": membership.user_id,
                "role": membership.role,
                "display_name": user.display_name if user else None,
                "created_at": membership.created_at.isoformat() if membership.created_at else None,
            })

        rec_stmt = (
            select(RecordingRow, PipelineResultRow)
            .outerjoin(PipelineResultRow, PipelineResultRow.recording_id == RecordingRow.recording_id)
            .where(RecordingRow.family_id == family_id)
            .order_by(RecordingRow.created_at.asc())
        )
        recordings_list = []
        for recording, pipeline_res in session.execute(rec_stmt):
            recordings_list.append({
                "recording_id": recording.recording_id,
                "speaker_name": recording.speaker_name,
                "speaker_id": recording.speaker_id,
                "original_filename": recording.original_filename,
                "audio_language": recording.audio_language,
                "output_language": recording.output_language,
                "created_at": recording.created_at.isoformat() if recording.created_at else None,
                "pipeline_result": pipeline_res.payload if pipeline_res else None,
            })

        people_rows = list(
            session.scalars(
                select(ArchivePersonRow).where(ArchivePersonRow.family_id == family_id)
            ).all()
        )
        people_list = [
            {
                "person_id": p.person_id,
                "canonical_name": p.canonical_name,
                "category": p.category,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in people_rows
        ]

        edge_rows = list(
            session.scalars(
                select(FamilyGraphEdgeRow).where(FamilyGraphEdgeRow.family_id == family_id)
            ).all()
        )
        relationships_list = [
            {
                "edge_id": e.edge_id,
                "relationship_type": e.relationship_type,
                "subject_person_id": e.subject_person_id,
                "subject_role": e.subject_role,
                "object_person_id": e.object_person_id,
                "object_role": e.object_role,
                "source_claim_ids": e.source_claim_ids,
            }
            for e in edge_rows
        ]

        claim_rows = list(
            session.scalars(
                select(ArchiveClaimRow).where(
                    ArchiveClaimRow.family_id == family_id,
                    ArchiveClaimRow.object_type == "story",
                )
            ).all()
        )
        stories_list = []
        for c in claim_rows:
            p = c.payload if isinstance(c.payload, dict) else {}
            stories_list.append({
                "story_id": c.claim_id,
                "recording_id": c.recording_id,
                "title": p.get("title"),
                "summary": p.get("summary"),
                "created_at": c.created_at.isoformat() if c.created_at else None,
            })

        book_rows = list(
            session.scalars(
                select(BookRow).where(BookRow.family_id == family_id)
            ).all()
        )
        books_list = []
        for b in book_rows:
            chapters = list(
                session.scalars(
                    select(BookChapterRow)
                    .where(BookChapterRow.book_id == b.book_id)
                    .order_by(BookChapterRow.chapter_number.asc())
                ).all()
            )
            exports = list(
                session.scalars(
                    select(BookExportRow).where(BookExportRow.book_id == b.book_id)
                ).all()
            )
            books_list.append({
                "book_id": b.book_id,
                "title": b.title,
                "subtitle": b.subtitle,
                "status": b.status,
                "stage": b.stage,
                "target_word_count": b.target_word_count,
                "output_language": b.output_language,
                "created_at": b.created_at.isoformat() if b.created_at else None,
                "completed_at": b.completed_at.isoformat() if b.completed_at else None,
                "chapters": [
                    {
                        "chapter_number": ch.chapter_number,
                        "title": ch.title,
                        "word_count": ch.word_count,
                        "status": ch.status,
                    }
                    for ch in chapters
                ],
                "exports": [
                    {
                        "export_id": exp.export_id,
                        "export_format": exp.export_format,
                        "status": exp.status,
                        "storage_backend": exp.storage_backend,
                        "file_size_bytes": exp.file_size_bytes,
                        "created_at": exp.created_at.isoformat() if exp.created_at else None,
                    }
                    for exp in exports
                ],
            })

        return {
            "exported_at": utcnow().isoformat(),
            "family": family_info,
            "members": members_list,
            "recordings": recordings_list,
            "people": people_list,
            "relationships": relationships_list,
            "stories": stories_list,
            "books": books_list,
        }
