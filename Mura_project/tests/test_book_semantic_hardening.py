from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from mura.book.chapter_gates import run_chapter_gates
from mura.book.prose_grounding import evidence_refs_used_by_prose
from mura.book.relationship_semantics import relationship_semantics_match
from mura.book.snapshot_validation import SnapshotClosureError, validate_snapshot_closure
from mura.book.truth_eligibility import (
    is_book_truth_eligible,
    is_book_uncertainty_context_eligible,
)
from mura.domain.book_models import (
    SNAPSHOT_SCHEMA_VERSION,
    BookLanguage,
    BookSourceSnapshot,
    ChapterDraft,
    ChapterPlan,
    GateCode,
    SnapshotClaim,
    SnapshotCorrection,
    SnapshotEvidence,
    SnapshotManifest,
    SnapshotPerson,
    SnapshotRelationship,
)


def _rel_match(
    left_subject: str,
    left_subject_role: str,
    left_object: str,
    left_object_role: str,
    right_subject: str,
    right_subject_role: str,
    right_object: str,
    right_object_role: str,
    *,
    relationship_type: str = "parent_child",
) -> bool:
    return relationship_semantics_match(
        left_type=relationship_type,
        left_subject_person_id=left_subject,
        left_subject_role=left_subject_role,
        left_object_person_id=left_object,
        left_object_role=left_object_role,
        right_type=relationship_type,
        right_subject_person_id=right_subject,
        right_subject_role=right_subject_role,
        right_object_person_id=right_object,
        right_object_role=right_object_role,
    )


def test_parent_child_identical_direction_passes() -> None:
    assert _rel_match("a", "parent", "b", "child", "a", "parent", "b", "child")


def test_parent_child_representation_swap_with_roles_preserved_passes() -> None:
    assert _rel_match("a", "parent", "b", "child", "b", "child", "a", "parent")


def test_parent_child_semantic_reversal_fails() -> None:
    assert not _rel_match("a", "parent", "b", "child", "b", "parent", "a", "child")


@pytest.mark.parametrize("relationship_type,role", [("sibling", "sibling"), ("spouse", "spouse")])
def test_symmetric_relationship_endpoint_swap_passes(
    relationship_type: str,
    role: str,
) -> None:
    assert _rel_match(
        "a",
        role,
        "b",
        role,
        "b",
        role,
        "a",
        role,
        relationship_type=relationship_type,
    )


def test_sibling_older_younger_roles_remain_attached_to_people() -> None:
    assert _rel_match(
        "a",
        "older_sibling",
        "b",
        "younger_sibling",
        "b",
        "younger_sibling",
        "a",
        "older_sibling",
        relationship_type="sibling",
    )
    assert not _rel_match(
        "a",
        "older_sibling",
        "b",
        "younger_sibling",
        "b",
        "older_sibling",
        "a",
        "younger_sibling",
        relationship_type="sibling",
    )


@dataclass
class _Claim:
    recording_id: str = "rec_a"
    object_type: str = "description"
    predicate: str = "description"
    subject_person_id: str | None = "p_a"
    object_person_id: str | None = None
    evidence_ids: list[str] = field(default_factory=lambda: ["ev_a"])
    evidence_class: str = "A_explicit"
    verification_status: str = "unreviewed"
    assertion_mode: str | None = "explicit"
    status: str = "active"
    payload: dict[str, Any] = field(default_factory=dict)


@pytest.mark.parametrize(
    "changes",
    [
        {"recording_id": "rec_c"},
        {"status": "rejected"},
        {"status": "disputed"},
        {"verification_status": "rejected"},
        {"assertion_mode": "inferred"},
        {"assertion_mode": "uncertain"},
        {"evidence_ids": []},
        {"evidence_class": "E_inferred"},
        {"evidence_class": "U_uncertain"},
        {"evidence_class": "D_context_resolved"},
    ],
)
def test_book_truth_eligibility_rejects_nontruth_states(changes: dict[str, Any]) -> None:
    claim = _Claim()
    for key, value in changes.items():
        setattr(claim, key, value)
    assert not is_book_truth_eligible(
        claim,
        selected_recording_ids={"rec_a"},
    )


def test_book_truth_eligibility_allows_fully_selected_disputed_claim_for_conflict() -> None:
    claim = _Claim(status="disputed")
    assert is_book_truth_eligible(
        claim,
        selected_recording_ids={"rec_a"},
        allow_disputed=True,
    )


def test_relationship_truth_eligibility_requires_current_resolved_roles() -> None:
    claim = _Claim(
        object_type="relationship",
        predicate="parent_child",
        object_person_id="p_b",
        payload={
            "relationship_type": "parent_child",
            "subject_role": "parent",
            "object_role": "child",
            "relationship_state": "current",
        },
    )
    assert is_book_truth_eligible(claim, selected_recording_ids={"rec_a"})

    claim.payload = {**claim.payload, "relationship_state": "negated"}
    assert not is_book_truth_eligible(claim, selected_recording_ids={"rec_a"})

    claim.payload = {
        "relationship_type": "parent_child",
        "subject_role": "parent",
        "object_role": "parent",
        "relationship_state": "current",
    }
    assert not is_book_truth_eligible(claim, selected_recording_ids={"rec_a"})


def _snapshot_relationship(
    *,
    claim_subject: str,
    claim_subject_role: str,
    claim_object: str,
    claim_object_role: str,
    edge_subject: str,
    edge_subject_role: str,
    edge_object: str,
    edge_object_role: str,
) -> BookSourceSnapshot:
    now = datetime(2026, 9, 23, tzinfo=UTC)
    people = [
        SnapshotPerson(
            person_id="p_a",
            display_name="А",  # noqa: RUF001
            source_recording_ids=["rec_a"],
            attribute_sources={"display_name": ["rec_a"]},
        ),
        SnapshotPerson(
            person_id="p_b",
            display_name="Б",
            source_recording_ids=["rec_a"],
            attribute_sources={"display_name": ["rec_a"]},
        ),
    ]
    claim = SnapshotClaim(
        claim_id="cl_rel",
        recording_id="rec_a",
        object_type="relationship",
        predicate="parent_child",
        subject_person_id=claim_subject,
        subject_role=claim_subject_role,
        object_person_id=claim_object,
        object_role=claim_object_role,
        evidence_class="A_explicit",
        assertion_mode="explicit",
        verification_status="unreviewed",
        archive_status="active",
        evidence_ids=["ev_a"],
    )
    evidence = SnapshotEvidence(
        evidence_id="ev_a",
        recording_id="rec_a",
        speaker_name="Narrator",
        text="Relationship evidence",
    )
    return BookSourceSnapshot(
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        compiler_version="test",
        family_id="fam_a",
        manifest=SnapshotManifest(
            source_recording_ids=["rec_a"],
            source_claim_ids=["cl_rel"],
            source_person_ids=["p_a", "p_b"],
            source_evidence_ids=["ev_a"],
            created_at=now,
        ),
        people=people,
        claims=[claim],
        relationships=[
            SnapshotRelationship(
                edge_id="edge_rel",
                relationship_type="parent_child",
                subject_person_id=edge_subject,
                subject_role=edge_subject_role,
                object_person_id=edge_object,
                object_role=edge_object_role,
                source_claim_ids=["cl_rel"],
            )
        ],
        evidence=[evidence],
    )


def test_snapshot_closure_accepts_equivalent_parent_child_representation() -> None:
    snapshot = _snapshot_relationship(
        claim_subject="p_a",
        claim_subject_role="parent",
        claim_object="p_b",
        claim_object_role="child",
        edge_subject="p_b",
        edge_subject_role="child",
        edge_object="p_a",
        edge_object_role="parent",
    )
    validate_snapshot_closure(snapshot, expected_recording_ids=["rec_a"])


def test_snapshot_closure_rejects_reversed_parent_child_semantics() -> None:
    snapshot = _snapshot_relationship(
        claim_subject="p_a",
        claim_subject_role="parent",
        claim_object="p_b",
        claim_object_role="child",
        edge_subject="p_b",
        edge_subject_role="parent",
        edge_object="p_a",
        edge_object_role="child",
    )
    with pytest.raises(SnapshotClosureError):
        validate_snapshot_closure(snapshot, expected_recording_ids=["rec_a"])


def _prose_snapshot(
    *,
    evidence_text: str = "Алихан работал врачом.",
    correction: SnapshotCorrection | None = None,
    include_relationship: bool = False,
) -> BookSourceSnapshot:
    now = datetime(2026, 9, 23, tzinfo=UTC)
    people = [
        SnapshotPerson(
            person_id="p_alikhan",
            display_name="Алихан",
            source_recording_ids=["rec_a"],
            attribute_sources={"display_name": ["rec_a"]},
        ),
        SnapshotPerson(
            person_id="p_aigul",
            display_name="Айгуль",
            aliases=["Айгүл"],
            source_recording_ids=["rec_a"],
            attribute_sources={
                "display_name": ["rec_a"],
                "alias:Айгүл": ["rec_a"],
            },
        ),
        SnapshotPerson(
            person_id="p_murat",
            display_name="Мұрат",
            aliases=["Мурат"],
            source_recording_ids=["rec_a"],
            attribute_sources={
                "display_name": ["rec_a"],
                "alias:Мурат": ["rec_a"],
            },
        ),
    ]
    relationships = []
    claims = [
        SnapshotClaim(
            claim_id="cl_fact",
            recording_id="rec_a",
            object_type="description",
            predicate="description",
            evidence_class="A_explicit",
            assertion_mode="explicit",
            verification_status="unreviewed",
            archive_status="active",
            evidence_ids=["ev_a"],
            summary=evidence_text,
        )
    ]
    if include_relationship:
        claims.append(
            SnapshotClaim(
                claim_id="cl_sibling",
                recording_id="rec_a",
                object_type="relationship",
                predicate="sibling",
                subject_person_id="p_aigul",
                subject_role="sibling",
                object_person_id="p_murat",
                object_role="sibling",
                evidence_class="A_explicit",
                assertion_mode="explicit",
                verification_status="unreviewed",
                archive_status="active",
                evidence_ids=["ev_a"],
            )
        )
        relationships.append(
            SnapshotRelationship(
                edge_id="edge_sibling",
                relationship_type="sibling",
                subject_person_id="p_aigul",
                subject_role="sibling",
                object_person_id="p_murat",
                object_role="sibling",
                source_claim_ids=["cl_sibling"],
            )
        )

    corrections = [correction] if correction is not None else []
    return BookSourceSnapshot(
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        compiler_version="test",
        family_id="fam_a",
        manifest=SnapshotManifest(
            source_recording_ids=["rec_a"],
            source_claim_ids=[claim.claim_id for claim in claims],
            source_person_ids=[person.person_id for person in people],
            source_evidence_ids=["ev_a"],
            correction_count=len(corrections),
            created_at=now,
        ),
        people=people,
        claims=claims,
        relationships=relationships,
        corrections=corrections,
        evidence=[
            SnapshotEvidence(
                evidence_id="ev_a",
                recording_id="rec_a",
                speaker_name="Narrator",
                text=evidence_text,
            )
        ],
        known_places=["Семей"],
        allowed_years=[1945],
    )


def _gate_text(text: str, snapshot: BookSourceSnapshot) -> set[GateCode]:
    report = run_chapter_gates(
        ChapterDraft(chapter_number=1, title="One", text=text),
        ChapterPlan(
            chapter_number=1,
            title="One",
            target_word_count=100,
            person_ids=[person.person_id for person in snapshot.people],
            claim_ids=[claim.claim_id for claim in snapshot.claims],
            source_recording_ids=["rec_a"],
            evidence_refs=["ev_a"],
        ),
        snapshot,
        BookLanguage.RU,
        min_chapter_words=1,
        max_chapter_words=1_000,
    )
    return {issue.code for issue in report.blockers}


@pytest.mark.parametrize(
    "text",
    [
        "Рустам улыбнулся.",
        "Рустам открыл дверь.",
        "Рустам посмотрел на фотографию.",
        "Рустам воевал.",
        "Рустам вспомнил детство.",
        "Рустам поднял чемодан.",
        "Рустам долго молчал.",
    ],
)
def test_unknown_sentence_start_person_with_arbitrary_verb_fails(text: str) -> None:
    snapshot = _prose_snapshot()
    assert GateCode.NAMED_PERSON in _gate_text(text, snapshot)


def test_aunt_is_not_authorized_by_sibling_edge_ru() -> None:
    snapshot = _prose_snapshot(
        evidence_text="Айгуль и Мурат были родственниками.",
        include_relationship=True,
    )
    assert GateCode.RELATIONSHIP in _gate_text(
        "Айгуль была тётей Мурата.",
        snapshot,
    )


def test_aunt_is_not_authorized_by_sibling_edge_kk() -> None:
    snapshot = _prose_snapshot(
        evidence_text="Айгүл мен Мұрат туыс еді.",
        include_relationship=True,
    )
    assert GateCode.RELATIONSHIP in _gate_text(
        "Мұраттың тәтесі Айгүл еді.",
        snapshot,
    )


@pytest.mark.parametrize(
    "evidence,draft",
    [
        ("Алихан не был врачом.", "Алихан был врачом."),
        ("Алихан не служил в армии.", "Алихан служил в армии."),
        ("Алихан не переехал в Семей.", "Алихан переехал в Семей."),
        ("Алихан жил в Семее после войны.", "Алихан жил в Семее до войны."),
    ],
)
def test_obvious_semantic_inversion_is_not_factual_support(
    evidence: str,
    draft: str,
) -> None:
    snapshot = _prose_snapshot(evidence_text=evidence)
    assert GateCode.FACTUAL_ASSERTION in _gate_text(draft, snapshot)


def test_positive_profession_paraphrase_remains_supported() -> None:
    snapshot = _prose_snapshot(evidence_text="Алихан работал врачом.")
    assert GateCode.FACTUAL_ASSERTION not in _gate_text(
        "Алихан был врачом.",
        snapshot,
    )


@pytest.mark.parametrize(
    "evidence,draft",
    [
        ("Алихан был старшим братом Айгуль.", "Алихан был младшим братом Айгуль."),
        ("Алихан был отцом Айгуль.", "Алихан был сыном Айгуль."),
    ],
)
def test_evidence_coverage_rejects_role_or_order_inversion(
    evidence: str,
    draft: str,
) -> None:
    snapshot = _prose_snapshot(evidence_text=evidence)
    assert (
        evidence_refs_used_by_prose(
            draft,
            snapshot,
            candidate_ids=["ev_a"],
        )
        == ()
    )


def test_evidence_coverage_does_not_count_name_and_year_only() -> None:
    snapshot = _prose_snapshot(evidence_text="Алихан родился в 1945 году.")
    assert (
        evidence_refs_used_by_prose(
            "Алихан служил в 1945 году.",
            snapshot,
            candidate_ids=["ev_a"],
        )
        == ()
    )


def test_place_correction_hyphen_variant_remains_rejected() -> None:
    snapshot = _prose_snapshot(
        evidence_text="Семья жила в Алматы.",
        correction=SnapshotCorrection(
            correction_id="cor_place",
            recording_id="rec_a",
            kind="speaker_self_correction",
            subject="location",
            original_value="Алма-Ата",
            corrected_value="Алматы",
            confidence="high",
        ),
    )
    assert GateCode.CORRECTION in _gate_text(
        "Семья раньше жила в Алма Ата.",
        snapshot,
    )


def test_small_number_correction_word_digit_variant_remains_rejected() -> None:
    snapshot = _prose_snapshot(
        evidence_text="Ему было шестнадцать лет.",
        correction=SnapshotCorrection(
            correction_id="cor_age",
            recording_id="rec_a",
            kind="speaker_self_correction",
            subject="age",
            original_value="семнадцать",
            corrected_value="шестнадцать",
            confidence="high",
        ),
    )
    assert GateCode.CORRECTION in _gate_text(
        "Ему тогда было 17 лет.",
        snapshot,
    )


def test_open_question_is_uncertainty_context_not_book_truth() -> None:
    claim = _Claim(
        object_type="question",
        predicate="question",
        assertion_mode="uncertain",
        evidence_class="U_uncertain",
    )
    assert not is_book_truth_eligible(claim, selected_recording_ids={"rec_a"})
    assert is_book_uncertainty_context_eligible(
        claim,
        selected_recording_ids={"rec_a"},
    )


def test_german_style_fabricated_quote_is_blocked() -> None:
    snapshot = _prose_snapshot(evidence_text="Он обещал вернуться.")
    assert GateCode.QUOTE in _gate_text(
        "„Я обязательно вернусь домой“",
        snapshot,
    )


def test_compiler_relationship_support_rejects_semantic_parent_reversal() -> None:
    from mura.book.snapshot import compile_source_snapshot
    from mura.storage.archive_read import GroundingBundle

    bundle = GroundingBundle(
        family_id="fam_a",
        recordings=[{"recording_id": "rec_a", "family_id": "fam_a", "speaker_name": "N"}],
        pipeline_payloads={
            "rec_a": {
                "extraction": {
                    "evidence_spans": [
                        {"evidence_id": "ev_a", "text": "А — родитель Б."}  # noqa: RUF001  # noqa: RUF001
                    ]
                }
            }
        },
        people=[
            {
                "person_id": "p_a",
                "canonical_name": "Алия",
                "verified_aliases": [],
                "category": "family_member",
                "source_recording_ids": ["rec_a"],
                "attribute_sources": {
                    "display_name": ["rec_a"],
                    "category": ["rec_a"],
                },
            },
            {
                "person_id": "p_b",
                "canonical_name": "Болат",
                "verified_aliases": [],
                "category": "family_member",
                "source_recording_ids": ["rec_a"],
                "attribute_sources": {
                    "display_name": ["rec_a"],
                    "category": ["rec_a"],
                },
            },
        ],
        claims=[
            {
                "claim_id": "cl_parent",
                "recording_id": "rec_a",
                "object_type": "relationship",
                "predicate": "parent_child",
                "subject_person_id": "p_a",
                "object_person_id": "p_b",
                "payload": {
                    "relationship_type": "parent_child",
                    "subject_role": "parent",
                    "object_role": "child",
                },
                "evidence_ids": ["ev_a"],
                "evidence_class": "A_explicit",
                "assertion_mode": "explicit",
                "verification_status": "unreviewed",
                "archive_status": "active",
            }
        ],
        relationships=[
            {
                "edge_id": "edge_bad",
                "relationship_type": "parent_child",
                "subject_person_id": "p_b",
                "subject_role": "parent",
                "object_person_id": "p_a",
                "object_role": "child",
                "source_claim_ids": ["cl_parent"],
            }
        ],
    )

    snapshot = compile_source_snapshot(
        bundle,
        recording_ids=["rec_a"],
        created_at=datetime(2026, 9, 23, tzinfo=UTC),
    ).snapshot
    assert snapshot.relationships == []


def test_compiler_relationship_support_accepts_equivalent_parent_representation() -> None:
    from mura.book.snapshot import compile_source_snapshot
    from mura.storage.archive_read import GroundingBundle

    bundle = GroundingBundle(
        family_id="fam_a",
        recordings=[{"recording_id": "rec_a", "family_id": "fam_a", "speaker_name": "N"}],
        pipeline_payloads={
            "rec_a": {
                "extraction": {
                    "evidence_spans": [
                        {"evidence_id": "ev_a", "text": "А — родитель Б."}  # noqa: RUF001
                    ]
                }
            }
        },
        people=[
            {
                "person_id": "p_a",
                "canonical_name": "Алия",
                "verified_aliases": [],
                "category": "family_member",
                "source_recording_ids": ["rec_a"],
                "attribute_sources": {
                    "display_name": ["rec_a"],
                    "category": ["rec_a"],
                },
            },
            {
                "person_id": "p_b",
                "canonical_name": "Болат",
                "verified_aliases": [],
                "category": "family_member",
                "source_recording_ids": ["rec_a"],
                "attribute_sources": {
                    "display_name": ["rec_a"],
                    "category": ["rec_a"],
                },
            },
        ],
        claims=[
            {
                "claim_id": "cl_parent",
                "recording_id": "rec_a",
                "object_type": "relationship",
                "predicate": "parent_child",
                "subject_person_id": "p_a",
                "object_person_id": "p_b",
                "payload": {
                    "relationship_type": "parent_child",
                    "subject_role": "parent",
                    "object_role": "child",
                },
                "evidence_ids": ["ev_a"],
                "evidence_class": "A_explicit",
                "assertion_mode": "explicit",
                "verification_status": "unreviewed",
                "archive_status": "active",
            }
        ],
        relationships=[
            {
                "edge_id": "edge_ok",
                "relationship_type": "parent_child",
                "subject_person_id": "p_b",
                "subject_role": "child",
                "object_person_id": "p_a",
                "object_role": "parent",
                "source_claim_ids": ["cl_parent"],
            }
        ],
    )

    snapshot = compile_source_snapshot(
        bundle,
        recording_ids=["rec_a"],
        created_at=datetime(2026, 9, 23, tzinfo=UTC),
    ).snapshot
    assert [relationship.edge_id for relationship in snapshot.relationships] == ["edge_ok"]
