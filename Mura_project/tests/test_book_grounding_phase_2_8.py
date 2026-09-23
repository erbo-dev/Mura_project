"""Adversarial Phase 2.8 grounding tests.

These tests attack provenance and prose boundaries directly. Writer-declared
metadata is intentionally omitted or falsified where that is the bypass under
test.
"""
# ruff: noqa: RUF001, E501

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from apps.api.books import resolve_book_source_ids
from apps.api.errors import BOOK_SOURCE_LIMIT_EXCEEDED
from mura.book.chapter_gates import run_chapter_gates
from mura.book.snapshot import compile_source_snapshot
from mura.book.snapshot_validation import (
    SnapshotClosureError,
    SnapshotSizeError,
    validate_snapshot_closure,
)
from mura.domain.book_models import (
    BookLanguage,
    BookSourceSnapshot,
    ChapterDraft,
    ChapterPlan,
    GateCode,
    SnapshotClaim,
    SnapshotConflict,
    SnapshotCorrection,
    SnapshotEvidence,
    SnapshotManifest,
    SnapshotPerson,
    SnapshotRelationship,
)
from mura.storage.archive_read import GroundingBundle
from mura.storage.database import Database

NOW = datetime(2026, 9, 23, tzinfo=UTC)
FAMILY = "fam_phase_28"


def _plan(
    *,
    claim_ids: list[str] | None = None,
    evidence_refs: list[str] | None = None,
) -> ChapterPlan:
    return ChapterPlan(
        chapter_number=1,
        title="Chapter",
        purpose="Grounded family history",
        synopsis="Selected-source chapter",
        target_word_count=100,
        person_ids=["per_alikhan", "per_aigul"],
        claim_ids=claim_ids or [],
        source_recording_ids=["rec_a"],
        evidence_refs=evidence_refs or [],
    )


def _person(person_id: str, name: str) -> SnapshotPerson:
    return SnapshotPerson(
        person_id=person_id,
        display_name=name,
        category="core",
        source_recording_ids=["rec_a"],
        attribute_sources={
            "display_name": ["rec_a"],
            "category": ["rec_a"],
        },
    )


def _gate_snapshot(*, with_conflict: bool = False) -> BookSourceSnapshot:
    claims = [
        SnapshotClaim(
            claim_id="cl_a",
            recording_id="rec_a",
            object_type="description",
            predicate="birth_year",
            evidence_class="A_EXPLICIT",
            evidence_ids=["ev_1"],
            summary="Алихан жил в Семее в 1925 году.",
        )
    ]
    conflicts: list[SnapshotConflict] = []
    if with_conflict:
        claims.append(
            SnapshotClaim(
                claim_id="cl_b",
                recording_id="rec_a",
                object_type="description",
                predicate="birth_year",
                evidence_class="A_EXPLICIT",
                evidence_ids=["ev_1"],
                summary="Другая выбранная запись утверждает иную версию.",
            )
        )
        conflicts.append(
            SnapshotConflict(
                conflict_id="conf_birth",
                conflict_type="attribute",
                status="open",
                claim_ids=["cl_a", "cl_b"],
                recording_ids=["rec_a"],
                rationale="selected sources disagree",
            )
        )

    return BookSourceSnapshot(
        compiler_version="phase-2.8-test",
        family_id=FAMILY,
        manifest=SnapshotManifest(
            source_recording_ids=["rec_a"],
            source_claim_ids=[claim.claim_id for claim in claims],
            source_person_ids=["per_alikhan", "per_aigul"],
            source_evidence_ids=["ev_1"],
            created_at=NOW,
            conflict_count=len(conflicts),
            correction_count=1,
        ),
        people=[
            _person("per_alikhan", "Алихан"),
            _person("per_aigul", "Айгуль"),
        ],
        claims=claims,
        corrections=[
            SnapshotCorrection(
                correction_id="cor_year",
                recording_id="rec_a",
                kind="speaker_self_correction",
                original_value="1924",
                corrected_value="1925",
                explanation="Говорящий сразу исправил год.",
                confidence="high",
            )
        ],
        conflicts=conflicts,
        evidence=[
            SnapshotEvidence(
                evidence_id="ev_1",
                recording_id="rec_a",
                speaker_name="Narrator",
                text="Алихан жил в Семее в 1925 году.",
            )
        ],
        allowed_years=[1925],
        known_places=["Семей"],
    )


def _gate(text: str, *, snapshot: BookSourceSnapshot | None = None, plan: ChapterPlan | None = None, evidence_usage: list[str] | None = None):
    return run_chapter_gates(
        ChapterDraft(
            chapter_number=1,
            title="Chapter",
            text=text,
            evidence_usage=evidence_usage or [],
            relationship_assertions=[],
            person_ids_used=[],
        ),
        plan or _plan(),
        snapshot or _gate_snapshot(),
        BookLanguage.RU,
        min_chapter_words=1,
        max_chapter_words=10_000,
    )


def test_person_attributes_from_excluded_recording_are_not_projected() -> None:
    bundle = GroundingBundle(
        family_id=FAMILY,
        recordings=[{"recording_id": "rec_a", "family_id": FAMILY, "speaker_name": "N"}],
        pipeline_payloads={
            "rec_a": {
                "extraction": {
                    "evidence_spans": [
                        {"evidence_id": "ev_a", "text": "Алихан — мой дед."}
                    ]
                }
            }
        },
        people=[
            {
                "person_id": "per_alikhan",
                "family_id": FAMILY,
                "canonical_name": "Алихан",
                "normalized_name": "алихан",
                "aliases": [],
                "verified_aliases": [],
                "category": "core",
                "professions": ["врач"],
                "locations": ["Караганда"],
                "source_recording_ids": ["rec_a", "rec_c"],
                "attribute_sources": {
                    "display_name": ["rec_a"],
                    "category": ["rec_a"],
                    "profession:врач": ["rec_c"],
                    "location:Караганда": ["rec_c"],
                },
            }
        ],
    )

    snapshot = compile_source_snapshot(
        bundle,
        recording_ids=["rec_a"],
        created_at=NOW,
    ).snapshot

    assert [person.display_name for person in snapshot.people] == ["Алихан"]
    assert snapshot.people[0].professions == []
    assert snapshot.people[0].locations == []
    assert "Караганда" not in snapshot.known_places


def test_invented_relationship_is_blocked_even_with_empty_writer_metadata() -> None:
    report = _gate("Мурат был братом Алихана.")
    assert GateCode.RELATIONSHIP in {issue.code for issue in report.blockers}


@pytest.mark.parametrize(
    "text",
    [
        "Алиханның ағасы Мұрат еді.",
        "Его сестра Айгуль приехала домой.",
        "Айгуль приходилась ему тётей.",
    ],
)
def test_ru_kz_and_coreference_relationship_frames_fail_closed(text: str) -> None:
    report = _gate(text)
    assert GateCode.RELATIONSHIP in {issue.code for issue in report.blockers}


def test_invented_person_at_sentence_start_is_blocked() -> None:
    report = _gate("Мурат приехал домой.")
    assert GateCode.NAMED_PERSON in {issue.code for issue in report.blockers}


@pytest.mark.parametrize(
    "text",
    [
        "Это случилось в 1924 году.",
        "Это случилось в 24-м году.",
        "Это случилось в двадцать четвёртом году.",
    ],
)
def test_rejected_correction_year_paraphrases_are_blocked(text: str) -> None:
    report = _gate(text)
    assert GateCode.CORRECTION in {issue.code for issue in report.blockers}


def test_fabricated_dash_dialogue_is_blocked() -> None:
    report = _gate("— Я обязательно вернусь, — сказал он.")
    assert GateCode.QUOTE in {issue.code for issue in report.blockers}


def test_invented_location_is_blocked() -> None:
    report = _gate("Он жил в Алматы.")
    assert GateCode.LOCATION in {issue.code for issue in report.blockers}


def test_evidence_usage_self_report_is_not_proof() -> None:
    report = _gate(
        "Мурат приехал домой.",
        plan=_plan(evidence_refs=["ev_1"]),
        evidence_usage=["ev_1"],
    )
    codes = {issue.code for issue in report.blockers}
    assert GateCode.NAMED_PERSON in codes
    assert GateCode.EVIDENCE_COVERAGE in codes


def test_unresolved_selected_conflict_cannot_be_stated_as_certain() -> None:
    snapshot = _gate_snapshot(with_conflict=True)
    plan = _plan(claim_ids=["cl_a", "cl_b"])
    report = _gate("Алихан родился в 1925 году.", snapshot=snapshot, plan=plan)
    assert GateCode.CONFLICT in {issue.code for issue in report.blockers}


def test_unresolved_selected_conflict_allows_explicit_disagreement_language() -> None:
    snapshot = _gate_snapshot(with_conflict=True)
    plan = _plan(claim_ids=["cl_a", "cl_b"])
    report = _gate(
        "По одной версии Алихан родился в 1925 году, но источники расходятся.",
        snapshot=snapshot,
        plan=plan,
    )
    assert GateCode.CONFLICT not in {issue.code for issue in report.blockers}


def test_conflict_with_excluded_claim_is_not_imported() -> None:
    bundle = GroundingBundle(
        family_id=FAMILY,
        recordings=[{"recording_id": "rec_a", "family_id": FAMILY, "speaker_name": "N"}],
        pipeline_payloads={
            "rec_a": {
                "extraction": {
                    "evidence_spans": [
                        {"evidence_id": "ev_a", "text": "Selected fact."}
                    ]
                }
            }
        },
        claims=[
            {
                "claim_id": "cl_a",
                "family_id": FAMILY,
                "recording_id": "rec_a",
                "object_type": "description",
                "source_object_id": "desc_a",
                "predicate": "description",
                "payload": {"description": "Selected fact."},
                "evidence_ids": ["ev_a"],
                "evidence_class": "A_EXPLICIT",
            }
        ],
        conflicts=[
            {
                "conflict_id": "conf_mixed",
                "family_id": FAMILY,
                "conflict_type": "attribute",
                "status": "open",
                "claim_ids": ["cl_a", "cl_c"],
                "rationale": "C says an excluded contrary fact.",
            }
        ],
    )

    snapshot = compile_source_snapshot(
        bundle,
        recording_ids=["rec_a"],
        created_at=NOW,
    ).snapshot
    assert snapshot.conflicts == []


def test_required_evidence_over_cap_fails_instead_of_dangling() -> None:
    evidence = [
        {"evidence_id": f"ev_{idx}", "text": f"Evidence text {idx}"}
        for idx in range(3)
    ]
    claims = [
        {
            "claim_id": f"cl_{idx}",
            "family_id": FAMILY,
            "recording_id": "rec_a",
            "object_type": "description",
            "source_object_id": f"desc_{idx}",
            "predicate": "description",
            "payload": {"description": f"Fact {idx}"},
            "evidence_ids": [f"ev_{idx}"],
            "evidence_class": "A_EXPLICIT",
        }
        for idx in range(3)
    ]
    bundle = GroundingBundle(
        family_id=FAMILY,
        recordings=[{"recording_id": "rec_a", "family_id": FAMILY, "speaker_name": "N"}],
        pipeline_payloads={"rec_a": {"extraction": {"evidence_spans": evidence}}},
        claims=claims,
    )

    with pytest.raises(SnapshotSizeError):
        compile_source_snapshot(
            bundle,
            recording_ids=["rec_a"],
            max_evidence_quotes=2,
            created_at=NOW,
        )


def test_snapshot_validator_rejects_relationship_with_dangling_support() -> None:
    snapshot = BookSourceSnapshot(
        compiler_version="phase-2.8-test",
        family_id=FAMILY,
        manifest=SnapshotManifest(
            source_recording_ids=["rec_a"],
            source_person_ids=["per_alikhan", "per_aigul"],
            created_at=NOW,
        ),
        people=[
            _person("per_alikhan", "Алихан"),
            _person("per_aigul", "Айгуль"),
        ],
        relationships=[
            SnapshotRelationship(
                edge_id="edge_bad",
                relationship_type="sibling",
                subject_person_id="per_alikhan",
                subject_role="sibling",
                object_person_id="per_aigul",
                object_role="sibling",
                source_claim_ids=["cl_missing"],
            )
        ],
    )

    with pytest.raises(SnapshotClosureError):
        validate_snapshot_closure(
            snapshot,
            expected_recording_ids=["rec_a"],
        )



def test_more_than_100_explicit_sources_fails_instead_of_truncating() -> None:
    db = Database("sqlite+pysqlite:///:memory:")
    db.create_schema()
    requested = [f"rec_{idx:03d}" for idx in range(101)]

    with db.session_factory() as session:
        with pytest.raises(HTTPException) as caught:
            resolve_book_source_ids(
                session,
                family_id=FAMILY,
                requested_recording_ids=requested,
            )

    assert caught.value.status_code == 422
    assert caught.value.detail == BOOK_SOURCE_LIMIT_EXCEEDED



def test_snapshot_manifest_counts_must_match_payload() -> None:
    snapshot = _gate_snapshot(with_conflict=True)
    snapshot.manifest.conflict_count = 0

    with pytest.raises(SnapshotClosureError):
        validate_snapshot_closure(
            snapshot,
            expected_recording_ids=["rec_a"],
        )


def test_snapshot_size_budget_fails_explicitly_without_truncating_truth() -> None:
    bundle = GroundingBundle(
        family_id=FAMILY,
        recordings=[
            {"recording_id": "rec_a", "family_id": FAMILY, "speaker_name": "N"}
        ],
        pipeline_payloads={
            "rec_a": {
                "extraction": {
                    "evidence_spans": [
                        {
                            "evidence_id": "ev_big",
                            "text": "Алихан жил в Семее. " * 50,
                        }
                    ]
                }
            }
        },
        claims=[
            {
                "claim_id": "cl_big",
                "family_id": FAMILY,
                "recording_id": "rec_a",
                "object_type": "description",
                "source_object_id": "desc_big",
                "predicate": "description",
                "payload": {"description": "Алихан жил в Семее."},
                "evidence_ids": ["ev_big"],
                "evidence_class": "A_EXPLICIT",
            }
        ],
    )

    with pytest.raises(SnapshotSizeError):
        compile_source_snapshot(
            bundle,
            recording_ids=["rec_a"],
            max_snapshot_bytes=128,
            created_at=NOW,
        )
