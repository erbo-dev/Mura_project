"""Tests for deterministic chapter gates and review (Layer 7)."""
# ruff: noqa: RUF001, E501

from __future__ import annotations

from unittest.mock import MagicMock

from mura.book.chapter_gates import run_chapter_gates
from mura.book.reviewer import review_chapter
from mura.book.snapshot import compile_source_snapshot
from mura.deepseek.client import DeepSeekUsage
from mura.domain.book_models import (
    BookLanguage,
    ChapterDraft,
    ChapterPlan,
    GateCode,
    ReviewStatus,
)
from mura.storage.archive_read import GroundingBundle

FAMILY_A = "fam_gates_test"


def _sample_snapshot():
    bundle = GroundingBundle(
        family_id=FAMILY_A,
        recordings=[
            {
                "recording_id": "rec_001",
                "family_id": FAMILY_A,
                "speaker_name": "Айгүл",
                "speaker_id": "spk_1",
                "detected_language": "kk",
            }
        ],
        pipeline_payloads={
            "rec_001": {
                "extraction": {
                    "languages": ["kk"],
                    "evidence_spans": [
                        {
                            "evidence_id": "ev_001",
                            "text": "Атам 1941 жылы майданға аттанды, үйде тек көне домбыра қалған еді.",
                        },
                        {
                            "evidence_id": "ev_002",
                            "text": "1945 жылғы жеңіс күні бәріміз көшеге шығып қуандық.",
                        },
                    ],
                }
            }
        },
        people=[
            {
                "person_id": "per_kanat",
                "family_id": FAMILY_A,
                "canonical_name": "Қанат",
                "normalized_name": "канат",
                "aliases": ["Қанат-ата"],
                "verified_aliases": [],
                "category": "core",
                "source_recording_ids": ["rec_001"],
            }
        ],
        stories=[],
        events=[],
        claims=[],
        corrections=[
            {
                "correction_id": "cor_city",
                "family_id": FAMILY_A,
                "recording_id": "rec_001",
                "kind": "city",
                "original_value": "Сталинград",
                "corrected_value": "Ленинград",
                "explanation": "Госпиталь был в Ленинграде.",
            }
        ],
    )
    return compile_source_snapshot(bundle).snapshot


def _sample_plan() -> ChapterPlan:
    return ChapterPlan(
        chapter_number=1,
        title="Соғыс жылдары",
        purpose="Рассказать об отъезде на фронт",
        synopsis="1941 жылғы соғыс басталған кез.",
        target_word_count=800,
        time_range="1941",
        person_ids=["per_kanat"],
        evidence_refs=["ev_001"],
    )


def _valid_text(word_count: int = 750) -> str:
    base = (
        "Қанат атамыз 1941 жылы күзде майданға аттанған еді. "
        "Үйде тек анасы мен балалары қалды. "
        "Ауылдағы ақсақалдар бата беріп, аман келуін тіледі. "
        "Сол күндері барлығы ел басына күн туған шақты терең сезінді. "
    )
    words = base.split()
    repeated = (words * ((word_count // len(words)) + 1))[:word_count]
    res = " ".join(repeated)
    if not res.endswith("."):
        res += "."
    return res


def test_gates_all_pass():
    snapshot = _sample_snapshot()
    plan = _sample_plan()
    text = _valid_text(750)

    draft = ChapterDraft(
        chapter_number=1,
        title="Соғыс жылдары",
        text=text,
        evidence_usage=["ev_001"],
        person_ids_used=["per_kanat"],
    )

    report = run_chapter_gates(draft, plan, snapshot, BookLanguage.KK)
    assert report.passed is True
    assert len(report.blockers) == 0


def test_gate_ungrounded_year():
    snapshot = _sample_snapshot()
    plan = _sample_plan()
    # 1999 is ungrounded (allowed_years has only 1941, 1945)
    text = _valid_text(750) + " Бұл жағдай 1999 жылы қайталанды."

    draft = ChapterDraft(
        chapter_number=1,
        title="Соғыс жылдары",
        text=text,
        evidence_usage=["ev_001"],
        person_ids_used=["per_kanat"],
    )

    report = run_chapter_gates(draft, plan, snapshot, BookLanguage.KK)
    assert report.passed is False
    year_blockers = [b for b in report.blockers if b.code == GateCode.YEAR]
    assert len(year_blockers) == 1
    assert "1999" in year_blockers[0].offending


def test_gate_forbidden_correction():
    snapshot = _sample_snapshot()
    plan = _sample_plan()
    # "Сталинград" is the forbidden original_value
    text = _valid_text(750) + " Дедушка лежал в городе Сталинград в госпитале."

    draft = ChapterDraft(
        chapter_number=1,
        title="Соғыс жылдары",
        text=text,
        evidence_usage=["ev_001"],
        person_ids_used=["per_kanat"],
    )

    report = run_chapter_gates(draft, plan, snapshot, BookLanguage.KK)
    assert report.passed is False
    assert GateCode.CORRECTION in [b.code for b in report.blockers]
    assert "Сталинград" in report.blockers[0].offending


def test_gate_ungrounded_quote():
    snapshot = _sample_snapshot()
    plan = _sample_plan()
    # Invented quote not found in evidence spans
    text = _valid_text(750) + " Ол кісі: «Біз ертең міндетті түрде жеңеміз деп айтты»."

    draft = ChapterDraft(
        chapter_number=1,
        title="Соғыс жылдары",
        text=text,
        evidence_usage=["ev_001"],
        person_ids_used=["per_kanat"],
    )

    report = run_chapter_gates(draft, plan, snapshot, BookLanguage.KK)
    assert report.passed is False
    assert GateCode.QUOTE in [b.code for b in report.blockers]


def test_gate_verbatim_quote_passes():
    snapshot = _sample_snapshot()
    plan = _sample_plan()
    # Verbatim quote from ev_001: "Атам 1941 жылы майданға аттанды"
    text = _valid_text(750) + " Сол шақта: «Атам 1941 жылы майданға аттанды» деп еске алды."

    draft = ChapterDraft(
        chapter_number=1,
        title="Соғыс жылдары",
        text=text,
        evidence_usage=["ev_001"],
        person_ids_used=["per_kanat"],
    )

    report = run_chapter_gates(draft, plan, snapshot, BookLanguage.KK)
    # Quote gate should NOT be in blockers
    assert GateCode.QUOTE not in [b.code for b in report.blockers]


def test_gate_wrong_word_count():
    snapshot = _sample_snapshot()
    plan = _sample_plan()
    # 50 words (below min 700)
    text = "Қанат атамыз майданға аттанған еді. Үйде домбыра қалды."

    draft = ChapterDraft(
        chapter_number=1,
        title="Соғыс жылдары",
        text=text,
        evidence_usage=["ev_001"],
        person_ids_used=["per_kanat"],
    )

    report = run_chapter_gates(draft, plan, snapshot, BookLanguage.KK)
    assert report.passed is False
    assert GateCode.WORD_COUNT in [b.code for b in report.blockers]


def test_gate_wrong_language():
    snapshot = _sample_snapshot()
    plan = _sample_plan()
    # English Latin text for KK book
    text = "This is an entirely English chapter text that does not belong in a Kazakh book. " * 50

    draft = ChapterDraft(
        chapter_number=1,
        title="Chapter in English",
        text=text,
        evidence_usage=["ev_001"],
        person_ids_used=["per_kanat"],
    )

    report = run_chapter_gates(draft, plan, snapshot, BookLanguage.KK)
    assert report.passed is False
    assert GateCode.LANGUAGE in [b.code for b in report.blockers]


def test_reviewer_overridden_by_deterministic_blocker():
    snapshot = _sample_snapshot()
    plan = _sample_plan()
    # Text contains forbidden correction "Сталинград"
    text = _valid_text(750) + " Қанат Сталинград қаласында болған."

    draft = ChapterDraft(
        chapter_number=1,
        title="Соғыс жылдары",
        text=text,
        evidence_usage=["ev_001"],
        person_ids_used=["per_kanat"],
    )

    mock_client = MagicMock()
    # Even if the LLM falsely claims APPROVED:
    fake_review = {
        "schema_version": "book-review-v1",
        "status": "approved",
        "issues": [],
        "grounding_score": 0.95,
        "coverage_note": "Looks good",
        "corrected_text": None,
    }
    mock_usage = DeepSeekUsage(
        model="deepseek-chat",
        finish_reason="stop",
        request_seconds=0.5,
    )
    mock_client.request_json.return_value = (fake_review, mock_usage)

    review_res, gate_rep, _ = review_chapter(
        mock_client,
        draft,
        plan,
        snapshot,
        BookLanguage.KK,
    )

    # Deterministic code-level gate MUST override LLM opinion
    assert gate_rep.passed is False
    assert review_res.status == ReviewStatus.REPAIR_REQUIRED
    # Blocker issue is merged
    issue_types = [i.issue_type for i in review_res.issues]
    assert GateCode.CORRECTION.issue_type in issue_types


def test_gate_relationship_assertions_grounded_and_ungrounded():
    from mura.domain.book_models import (
        ChapterRelationshipAssertion,
        SnapshotPerson,
        SnapshotRelationship,
    )

    snapshot = _sample_snapshot()
    snapshot.people.append(
        SnapshotPerson(
            person_id="per_aigul",
            display_name="Айгүл",
            category="core",
        )
    )
    snapshot.relationships.append(
        SnapshotRelationship(
            edge_id="edge_1",
            relationship_type="parent_child",
            subject_person_id="per_kanat",
            subject_role="parent",
            object_person_id="per_aigul",
            object_role="child",
        )
    )
    plan = _sample_plan()
    text = _valid_text(750)

    # 1. Grounded assertion: per_kanat is parent of per_aigul
    grounded_draft = ChapterDraft(
        chapter_number=1,
        title="Соғыс жылдары",
        text=text,
        evidence_usage=["ev_001"],
        person_ids_used=["per_kanat", "per_aigul"],
        relationship_assertions=[
            ChapterRelationshipAssertion(
                subject_person_id="per_kanat",
                relation="әке",
                object_person_id="per_aigul",
            )
        ],
    )
    rep_grounded = run_chapter_gates(grounded_draft, plan, snapshot, BookLanguage.KK)
    assert GateCode.RELATIONSHIP not in [b.code for b in rep_grounded.blockers]

    # 2. Ungrounded assertion: per_aigul is parent of per_kanat (reversed)
    ungrounded_draft = ChapterDraft(
        chapter_number=1,
        title="Соғыс жылдары",
        text=text,
        evidence_usage=["ev_001"],
        person_ids_used=["per_kanat", "per_aigul"],
        relationship_assertions=[
            ChapterRelationshipAssertion(
                subject_person_id="per_aigul",
                relation="әке",
                object_person_id="per_kanat",
            )
        ],
    )
    rep_ungrounded = run_chapter_gates(ungrounded_draft, plan, snapshot, BookLanguage.KK)
    assert rep_ungrounded.passed is False
    assert GateCode.RELATIONSHIP in [b.code for b in rep_ungrounded.blockers]

    # 3. Unknown person assertion
    unknown_draft = ChapterDraft(
        chapter_number=1,
        title="Соғыс жылдары",
        text=text,
        evidence_usage=["ev_001"],
        person_ids_used=["per_kanat"],
        relationship_assertions=[
            ChapterRelationshipAssertion(
                subject_person_id="per_kanat",
                relation="brother",
                object_person_id="per_stranger",
            )
        ],
    )
    rep_unknown = run_chapter_gates(unknown_draft, plan, snapshot, BookLanguage.KK)
    assert rep_unknown.passed is False
    assert GateCode.RELATIONSHIP in [b.code for b in rep_unknown.blockers]


def test_gate_evidence_coverage_ignores_writer_self_report_when_prose_is_grounded():
    snapshot = _sample_snapshot()
    plan = _sample_plan()
    text = _valid_text(750)

    draft = ChapterDraft(
        chapter_number=1,
        title="Соғыс жылдары",
        text=text,
        evidence_usage=[],
        person_ids_used=["per_kanat"],
    )
    report = run_chapter_gates(draft, plan, snapshot, BookLanguage.KK)
    # Phase 2.8 derives evidence usage from prose. Omitting Writer metadata is
    # not a bypass and is not itself a grounding failure.
    assert GateCode.EVIDENCE_COVERAGE not in [b.code for b in report.blockers]


def test_gate_evidence_coverage_partial_is_warning():
    snapshot = _sample_snapshot()
    plan = ChapterPlan(
        chapter_number=1,
        title="Соғыс жылдары",
        purpose="Рассказать об отъезде",
        synopsis="1941 жылғы кез",
        target_word_count=800,
        time_range="1941",
        person_ids=["per_kanat"],
        evidence_refs=["ev_001", "ev_002", "ev_003"],
    )
    text = _valid_text(750)

    draft = ChapterDraft(
        chapter_number=1,
        title="Соғыс жылдары",
        text=text,
        evidence_usage=["ev_001"],
        person_ids_used=["per_kanat"],
    )
    report = run_chapter_gates(draft, plan, snapshot, BookLanguage.KK)
    assert report.passed is True
    assert GateCode.EVIDENCE_COVERAGE not in [b.code for b in report.blockers]
    assert GateCode.EVIDENCE_COVERAGE in [w.code for w in report.warnings]
