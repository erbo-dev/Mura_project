from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from mura.book.relationship_semantics import relationship_semantics_match
from mura.book.snapshot_validation import SnapshotClosureError, validate_snapshot_closure
from mura.book.truth_eligibility import is_book_truth_eligible
from mura.domain.book_models import (
    SNAPSHOT_SCHEMA_VERSION,
    BookSourceSnapshot,
    SnapshotClaim,
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
            display_name="А",
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
