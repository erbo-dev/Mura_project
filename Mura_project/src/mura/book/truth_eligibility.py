"""Central truth-eligibility policy for Family Book source claims.

Existence in archive_claims is not enough to make a claim usable as Book truth.
This module is intentionally pure so archive projection, snapshot validation,
and tests can share the same policy.
"""

from __future__ import annotations

from typing import Any, Protocol

from mura.domain.models import (
    AssertionMode,
    ClaimObjectType,
    EvidenceClass,
    RelationshipState,
    VerificationStatus,
)
from mura.book.relationship_semantics import canonical_relationship


_BOOK_GROUNDED_EVIDENCE_CLASSES = {
    EvidenceClass.A_EXPLICIT.value,
    EvidenceClass.B_MORPHOLOGICALLY_EXPLICIT.value,
    EvidenceClass.C_SPEAKER_ANCHORED.value,
}


class BookClaimLike(Protocol):
    recording_id: str
    object_type: str
    predicate: str
    subject_person_id: str | None
    object_person_id: str | None
    evidence_ids: list[str]
    evidence_class: str
    verification_status: str
    assertion_mode: str | None
    status: str
    payload: dict[str, Any]


def is_book_truth_eligible(
    claim: BookClaimLike,
    *,
    selected_recording_ids: set[str],
    allow_disputed: bool = False,
) -> bool:
    """Return whether a persisted claim may enter the immutable Book truth set."""

    if claim.recording_id not in selected_recording_ids:
        return False

    allowed_statuses = {"active", "accepted"}
    if allow_disputed:
        allowed_statuses.add("disputed")
    if claim.status not in allowed_statuses:
        return False

    if claim.verification_status == VerificationStatus.REJECTED.value:
        return False

    if claim.assertion_mode not in (None, AssertionMode.EXPLICIT.value):
        return False

    if claim.evidence_class not in _BOOK_GROUNDED_EVIDENCE_CLASSES:
        return False

    if not claim.evidence_ids:
        return False

    if claim.object_type == ClaimObjectType.PERSON_MENTION.value:
        return claim.subject_person_id is not None

    if claim.object_type == ClaimObjectType.RELATIONSHIP.value:
        payload = claim.payload if isinstance(claim.payload, dict) else {}
        state = str(payload.get("relationship_state") or RelationshipState.CURRENT.value)
        if state != RelationshipState.CURRENT.value or payload.get("uncertainty") is not None:
            return False
        return (
            canonical_relationship(
                relationship_type=str(payload.get("relationship_type") or claim.predicate or ""),
                subject_person_id=claim.subject_person_id,
                subject_role=str(payload.get("subject_role") or ""),
                object_person_id=claim.object_person_id,
                object_role=str(payload.get("object_role") or ""),
            )
            is not None
        )

    return True
