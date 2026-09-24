from __future__ import annotations

from datetime import UTC, datetime

from mura.book.chapter_gates import run_chapter_gates
from mura.domain.book_models import (
    BookLanguage,
    BookSourceSnapshot,
    ChapterDraft,
    ChapterPlan,
    ChapterRelationshipAssertion,
    GateCode,
    SnapshotManifest,
    SnapshotPerson,
)


def _relationship_report(
    subject_person_id: str | None,
    object_person_id: str | None,
):
    snapshot = BookSourceSnapshot(
        compiler_version="relationship-safety-test",
        family_id="fam_relationship_safety",
        manifest=SnapshotManifest(created_at=datetime(2026, 9, 24, tzinfo=UTC)),
        people=[SnapshotPerson(person_id="p_known", display_name="Қанат")],
    )
    plan = ChapterPlan(
        chapter_number=1,
        title="Естелік",
        target_word_count=800,
    )
    assertion = ChapterRelationshipAssertion.model_construct(
        subject_person_id=subject_person_id,
        relation="parent",
        object_person_id=object_person_id,
        text_span="",
    )
    draft = ChapterDraft.model_construct(
        chapter_number=1,
        title="Естелік",
        text="Бұл отбасы туралы қарапайым естелік еді. " * 120,
        evidence_usage=[],
        person_ids_used=["p_known"],
        relationship_assertions=[assertion],
        uncertainty_notes=[],
        conflict_notes=[],
    )
    return run_chapter_gates(draft, plan, snapshot, BookLanguage.KK)


def _has_relationship_blocker(report) -> bool:
    return GateCode.RELATIONSHIP in [issue.code for issue in report.blockers]


def test_reported_relationship_with_missing_subject_fails_closed() -> None:
    assert _has_relationship_blocker(_relationship_report(None, "p_known"))


def test_reported_relationship_with_missing_object_fails_closed() -> None:
    assert _has_relationship_blocker(_relationship_report("p_known", None))


def test_reported_relationship_with_unknown_subject_fails_closed() -> None:
    assert _has_relationship_blocker(_relationship_report("p_unknown", "p_known"))


def test_reported_relationship_with_unknown_object_fails_closed() -> None:
    assert _has_relationship_blocker(_relationship_report("p_known", "p_unknown"))
