"""Privacy and data-portability service for one family archive.

The export is explicit rather than a raw ORM dump: private family content is
included, but provider credentials, auth issuer/subject, local filesystem paths,
tokens, and service configuration are not.

Binary audio/PDF/EPUB objects are represented in object_manifest. Packaging
large private objects belongs in a separate background export job.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select

from mura.storage.archive import (
    ArchiveClaimRow,
    ArchiveConflictRow,
    ArchiveCorrectionRow,
    ArchivePersonRow,
    FamilyGraphEdgeRow,
)
from mura.storage.book import (
    BookChapterRow,
    BookContinuityStateRow,
    BookExportRow,
    BookPlanRow,
    BookRow,
    BookSourceSnapshotRow,
)
from mura.storage.conflict_resolution import ArchiveConflictDecisionRow
from mura.storage.database import Database, PipelineResultRow, RecordingRow, utcnow
from mura.storage.identity import FamilyMembershipRow, FamilyRow, UserRow

EXPORT_SCHEMA_VERSION = "mura-family-export-v2"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _evidence_from_pipeline(
    pipeline_payloads: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for recording_id, payload in sorted(pipeline_payloads.items()):
        extraction = payload.get("extraction")
        if not isinstance(extraction, dict):
            continue
        spans = extraction.get("evidence_spans")
        if not isinstance(spans, list):
            continue
        for span in spans:
            if not isinstance(span, dict):
                continue
            evidence_id = span.get("evidence_id")
            if not isinstance(evidence_id, str) or not evidence_id:
                continue
            evidence.append(
                {
                    "evidence_id": evidence_id,
                    "recording_id": recording_id,
                    "text": span.get("text") if isinstance(span.get("text"), str) else None,
                    "start_ms": span.get("start_ms"),
                    "end_ms": span.get("end_ms"),
                    "source_segment_ids": span.get("source_segment_ids", []),
                }
            )
    return evidence


def export_family_data(database: Database, *, family_id: str) -> dict[str, Any] | None:
    """Return a family-scoped portable JSON snapshot with no secret configuration."""

    with database.session_factory() as session:
        family = session.scalar(select(FamilyRow).where(FamilyRow.family_id == family_id))
        if family is None:
            return None

        members = list(
            session.execute(
                select(FamilyMembershipRow, UserRow)
                .outerjoin(UserRow, UserRow.user_id == FamilyMembershipRow.user_id)
                .where(FamilyMembershipRow.family_id == family_id)
                .order_by(FamilyMembershipRow.created_at, FamilyMembershipRow.membership_id)
            ).all()
        )

        recordings = list(
            session.scalars(
                select(RecordingRow)
                .where(RecordingRow.family_id == family_id)
                .order_by(RecordingRow.created_at, RecordingRow.recording_id)
            ).all()
        )
        recording_ids = [row.recording_id for row in recordings]
        pipeline_rows = (
            list(
                session.scalars(
                    select(PipelineResultRow).where(
                        PipelineResultRow.recording_id.in_(recording_ids)
                    )
                ).all()
            )
            if recording_ids
            else []
        )
        pipeline_by_recording = {
            row.recording_id: row.payload if isinstance(row.payload, dict) else {}
            for row in pipeline_rows
        }

        people = list(
            session.scalars(
                select(ArchivePersonRow)
                .where(ArchivePersonRow.family_id == family_id)
                .order_by(ArchivePersonRow.person_id)
            ).all()
        )
        relationships = list(
            session.scalars(
                select(FamilyGraphEdgeRow)
                .where(FamilyGraphEdgeRow.family_id == family_id)
                .order_by(FamilyGraphEdgeRow.edge_id)
            ).all()
        )
        claims = list(
            session.scalars(
                select(ArchiveClaimRow)
                .where(ArchiveClaimRow.family_id == family_id)
                .order_by(ArchiveClaimRow.created_at, ArchiveClaimRow.claim_id)
            ).all()
        )
        corrections = list(
            session.scalars(
                select(ArchiveCorrectionRow)
                .where(ArchiveCorrectionRow.family_id == family_id)
                .order_by(ArchiveCorrectionRow.created_at, ArchiveCorrectionRow.correction_id)
            ).all()
        )
        conflicts = list(
            session.scalars(
                select(ArchiveConflictRow)
                .where(ArchiveConflictRow.family_id == family_id)
                .order_by(ArchiveConflictRow.created_at, ArchiveConflictRow.conflict_id)
            ).all()
        )
        decisions = list(
            session.scalars(
                select(ArchiveConflictDecisionRow)
                .where(ArchiveConflictDecisionRow.family_id == family_id)
                .order_by(
                    ArchiveConflictDecisionRow.created_at,
                    ArchiveConflictDecisionRow.decision_id,
                )
            ).all()
        )

        books = list(
            session.scalars(
                select(BookRow)
                .where(BookRow.family_id == family_id)
                .order_by(BookRow.created_at, BookRow.book_id)
            ).all()
        )
        book_ids = [row.book_id for row in books]

        snapshots = (
            list(
                session.scalars(
                    select(BookSourceSnapshotRow).where(BookSourceSnapshotRow.book_id.in_(book_ids))
                ).all()
            )
            if book_ids
            else []
        )
        plans = (
            list(
                session.scalars(select(BookPlanRow).where(BookPlanRow.book_id.in_(book_ids))).all()
            )
            if book_ids
            else []
        )
        chapters = (
            list(
                session.scalars(
                    select(BookChapterRow)
                    .where(BookChapterRow.book_id.in_(book_ids))
                    .order_by(BookChapterRow.book_id, BookChapterRow.chapter_number)
                ).all()
            )
            if book_ids else []
        )
        continuity = (
            list(
                session.scalars(
                    select(BookContinuityStateRow)
                    .where(BookContinuityStateRow.book_id.in_(book_ids))
                    .order_by(
                        BookContinuityStateRow.book_id,
                        BookContinuityStateRow.after_chapter_number,
                    )
                ).all()
            )
            if book_ids else []
        )
        exports = (
            list(
                session.scalars(
                    select(BookExportRow)
                    .where(BookExportRow.book_id.in_(book_ids))
                    .order_by(BookExportRow.book_id, BookExportRow.format)
                ).all()
            )
            if book_ids else []
        )

        snapshot_by_book = {row.book_id: row for row in snapshots}
        plan_by_book = {row.book_id: row for row in plans}
        chapters_by_book: dict[str, list[BookChapterRow]] = defaultdict(list)
        continuity_by_book: dict[str, list[BookContinuityStateRow]] = defaultdict(list)
        exports_by_book: dict[str, list[BookExportRow]] = defaultdict(list)
        for chapter_row in chapters:
            chapters_by_book[chapter_row.book_id].append(chapter_row)
        for continuity_row in continuity:
            continuity_by_book[continuity_row.book_id].append(continuity_row)
        for export_row in exports:
            exports_by_book[export_row.book_id].append(export_row)

        object_manifest: list[dict[str, Any]] = []
        for recording in recordings:
            if recording.storage_key:
                object_manifest.append(
                    {
                        "resource_kind": "recording_audio",
                        "recording_id": recording.recording_id,
                        "storage_backend": recording.storage_backend,
                        "storage_key": recording.storage_key,
                        "sha256": recording.audio_sha256,
                        "size_bytes": recording.audio_size_bytes,
                        "content_type": recording.audio_mime_type or recording.content_type,
                    }
                )
        for export in exports:
            if export.storage_key:
                object_manifest.append(
                    {
                        "resource_kind": f"book_{export.format}",
                        "book_id": export.book_id,
                        "export_id": export.export_id,
                        "storage_backend": export.storage_backend,
                        "storage_key": export.storage_key,
                        "sha256": export.sha256,
                        "size_bytes": export.size_bytes,
                        "content_type": export.content_type,
                    }
                )

        claims_payload = [
            {
                "claim_id": row.claim_id,
                "recording_id": row.recording_id,
                "object_type": row.object_type,
                "source_object_id": row.source_object_id,
                "predicate": row.predicate,
                "subject_person_id": row.subject_person_id,
                "object_person_id": row.object_person_id,
                "payload": row.payload,
                "evidence_ids": row.evidence_ids,
                "evidence_class": row.evidence_class,
                "verification_status": row.verification_status,
                "assertion_mode": row.assertion_mode,
                "status": row.status,
                "derived_from_claim_ids": row.derived_from_claim_ids,
                "created_at": _iso(row.created_at),
            }
            for row in claims
        ]

        return {
            "schema_version": EXPORT_SCHEMA_VERSION,
            "exported_at": utcnow().isoformat(),
            "family": {
                "family_id": family.family_id,
                "name": family.name,
                "created_at": _iso(family.created_at),
                "updated_at": _iso(family.updated_at),
            },
            "members": [
                {
                    "user_id": membership.user_id,
                    "role": membership.role,
                    "display_name": user.display_name if user is not None else None,
                    "joined_at": _iso(membership.created_at),
                }
                for membership, user in members
            ],
            "recordings": [
                {
                    "recording_id": row.recording_id,
                    "speaker_name": row.speaker_name,
                    "speaker_id": row.speaker_id,
                    "original_filename": row.original_filename,
                    "content_type": row.content_type,
                    "audio_language": row.audio_language,
                    "output_language": row.output_language,
                    "transcript_preview": row.transcript_preview,
                    "created_at": _iso(row.created_at),
                    "pipeline_result": pipeline_by_recording.get(row.recording_id),
                }
                for row in recordings
            ],
            "evidence": _evidence_from_pipeline(pipeline_by_recording),
            "people": [
                {
                    "person_id": row.person_id,
                    "canonical_name": row.canonical_name,
                    "normalized_name": row.normalized_name,
                    "aliases": row.aliases,
                    "verified_aliases": row.verified_aliases,
                    "category": row.category,
                    "relations_to_speakers": row.relations_to_speakers,
                    "source_recording_ids": row.source_recording_ids,
                    "created_at": _iso(row.created_at),
                    "updated_at": _iso(row.updated_at),
                }
                for row in people
            ],
            "relationships": [
                {
                    "edge_id": row.edge_id,
                    "relationship_type": row.relationship_type,
                    "subject_person_id": row.subject_person_id,
                    "subject_role": row.subject_role,
                    "object_person_id": row.object_person_id,
                    "object_role": row.object_role,
                    "source_claim_ids": row.source_claim_ids,
                    "created_at": _iso(row.created_at),
                    "updated_at": _iso(row.updated_at),
                }
                for row in relationships
            ],
            "claims": claims_payload,
            "stories": [
                {
                    **row,
                    "story_id": row["claim_id"],
                    "title": (
                        row["payload"].get("title")
                        if isinstance(row["payload"], dict)
                        else None
                    ),
                    "summary": (
                        row["payload"].get("summary") if isinstance(row["payload"], dict) else None
                    ),
                }
                for row in claims_payload
                if row["object_type"] == "story"
            ],
            "events": [
                {
                    **row,
                    "event_id": row["source_object_id"] or row["claim_id"],
                    "title": (
                        row["payload"].get("title")
                        if isinstance(row["payload"], dict)
                        else None
                    ),
                }
                for row in claims_payload
                if row["object_type"] == "event"
            ],
            "corrections": [
                {
                    "correction_id": row.correction_id,
                    "recording_id": row.recording_id,
                    "kind": row.kind,
                    "subject": row.subject,
                    "original_value": row.original_value,
                    "corrected_value": row.corrected_value,
                    "source_segment_ids": row.source_segment_ids,
                    "explanation": row.explanation,
                    "confidence": row.confidence,
                    "created_at": _iso(row.created_at),
                }
                for row in corrections
            ],
            "conflicts": [
                {
                    "conflict_id": row.conflict_id,
                    "conflict_type": row.conflict_type,
                    "status": row.status,
                    "detected_by": row.detected_by,
                    "claim_ids": row.claim_ids,
                    "preferred_claim_id": row.preferred_claim_id,
                    "rationale": row.rationale,
                    "resolution_note": row.resolution_note,
                    "created_at": _iso(row.created_at),
                    "updated_at": _iso(row.updated_at),
                }
                for row in conflicts
            ],
            "conflict_decisions": [
                {
                    "decision_id": row.decision_id,
                    "conflict_id": row.conflict_id,
                    "action": row.action,
                    "previous_status": row.previous_status,
                    "resulting_status": row.resulting_status,
                    "preferred_claim_id": row.preferred_claim_id,
                    "reviewer_reference": row.reviewer_reference,
                    "note": row.note,
                    "metadata": row.metadata_payload,
                    "created_at": _iso(row.created_at),
                }
                for row in decisions
            ],
            "books": [
                {
                    "book_id": book.book_id,
                    "created_by_user_id": book.created_by_user_id,
                    "title": book.title,
                    "subtitle": book.subtitle,
                    "status": book.status,
                    "stage": book.stage,
                    "output_language": book.output_language,
                    "target_word_count": book.target_word_count,
                    "source_snapshot_version": book.source_snapshot_version,
                    "chapters_total": book.chapters_total,
                    "chapters_approved": book.chapters_approved,
                    "word_count": book.word_count,
                    "supersedes_book_id": book.supersedes_book_id,
                    "created_at": _iso(book.created_at),
                    "updated_at": _iso(book.updated_at),
                    "completed_at": _iso(book.completed_at),
                    "source_snapshot": (
                        {
                            "snapshot_id": snapshot_by_book[book.book_id].snapshot_id,
                            "compiler_version": snapshot_by_book[book.book_id].compiler_version,
                            "content_hash": snapshot_by_book[book.book_id].content_hash,
                            "manifest": snapshot_by_book[book.book_id].manifest,
                            "payload": snapshot_by_book[book.book_id].payload,
                            "created_at": _iso(snapshot_by_book[book.book_id].created_at),
                        }
                        if book.book_id in snapshot_by_book
                        else None
                    ),
                    "plan": (
                        {
                            "plan_id": plan_by_book[book.book_id].plan_id,
                            "plan_version": plan_by_book[book.book_id].plan_version,
                            "blueprint": plan_by_book[book.book_id].blueprint,
                            "validation_report": plan_by_book[book.book_id].validation_report,
                            "central_theme": plan_by_book[book.book_id].central_theme,
                            "narrative_voice": plan_by_book[book.book_id].narrative_voice,
                            "material_anchor": plan_by_book[book.book_id].material_anchor,
                            "created_at": _iso(plan_by_book[book.book_id].created_at),
                            "updated_at": _iso(plan_by_book[book.book_id].updated_at),
                        }
                        if book.book_id in plan_by_book
                        else None
                    ),
                    "chapters": [
                        {
                            "chapter_id": row.chapter_id,
                            "chapter_number": row.chapter_number,
                            "status": row.status,
                            "title": row.title,
                            "plan": row.plan,
                            "draft_text": row.draft_text,
                            "draft_payload": row.draft_payload,
                            "final_text": row.final_text,
                            "word_count": row.word_count,
                            "review": row.review,
                            "gate_report": row.gate_report,
                            "person_ids": row.person_ids,
                            "place_names": row.place_names,
                            "claim_ids": row.claim_ids,
                            "source_recording_ids": row.source_recording_ids,
                            "source_story_ids": row.source_story_ids,
                            "evidence_refs": row.evidence_refs,
                            "repair_attempts": row.repair_attempts,
                            "approved_at": _iso(row.approved_at),
                            "created_at": _iso(row.created_at),
                            "updated_at": _iso(row.updated_at),
                        }
                        for row in chapters_by_book[book.book_id]
                    ],
                    "continuity": [
                        {
                            "continuity_id": row.continuity_id,
                            "after_chapter_number": row.after_chapter_number,
                            "state": row.state,
                            "created_at": _iso(row.created_at),
                            "updated_at": _iso(row.updated_at),
                        }
                        for row in continuity_by_book[book.book_id]
                    ],
                    "exports": [
                        {
                            "export_id": row.export_id,
                            "format": row.format,
                            "status": row.status,
                            "storage_backend": row.storage_backend,
                            "storage_key": row.storage_key,
                            "size_bytes": row.size_bytes,
                            "sha256": row.sha256,
                            "content_type": row.content_type,
                            "chapter_count": row.chapter_count,
                            "word_count": row.word_count,
                            "engine": row.engine,
                            "created_at": _iso(row.created_at),
                            "updated_at": _iso(row.updated_at),
                        }
                        for row in exports_by_book[book.book_id]
                    ],
                }
                for book in books
            ],
            "object_manifest": object_manifest,
            "binary_objects_included": False,
        }
