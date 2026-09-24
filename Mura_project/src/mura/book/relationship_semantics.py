"""Canonical semantic comparison for persisted family relationships.

Endpoint order is representation detail for symmetric relationships and for
parent/child claims that carry explicit roles. Roles remain attached to the
person they describe, so reversing a directional meaning never becomes support.
"""

from __future__ import annotations

from dataclasses import dataclass

from mura.domain.models import RelationshipRole, RelationshipType

_ROLE_ALIASES = {
    "parent": RelationshipRole.PARENT.value,
    "father": RelationshipRole.PARENT.value,
    "mother": RelationshipRole.PARENT.value,
    "отец": RelationshipRole.PARENT.value,
    "мать": RelationshipRole.PARENT.value,
    "әке": RelationshipRole.PARENT.value,
    "ана": RelationshipRole.PARENT.value,
    "child": RelationshipRole.CHILD.value,
    "son": RelationshipRole.CHILD.value,
    "daughter": RelationshipRole.CHILD.value,
    "сын": RelationshipRole.CHILD.value,
    "дочь": RelationshipRole.CHILD.value,
    "ұл": RelationshipRole.CHILD.value,
    "қыз": RelationshipRole.CHILD.value,
    "spouse": RelationshipRole.SPOUSE.value,
    "husband": RelationshipRole.SPOUSE.value,
    "wife": RelationshipRole.SPOUSE.value,
    "муж": RelationshipRole.SPOUSE.value,
    "жена": RelationshipRole.SPOUSE.value,
    "күйеу": RelationshipRole.SPOUSE.value,
    "әйел": RelationshipRole.SPOUSE.value,
    "sibling": RelationshipRole.SIBLING.value,
    "brother": RelationshipRole.SIBLING.value,
    "sister": RelationshipRole.SIBLING.value,
    "брат": RelationshipRole.SIBLING.value,
    "сестра": RelationshipRole.SIBLING.value,
    "older_sibling": RelationshipRole.OLDER_SIBLING.value,
    "older brother": RelationshipRole.OLDER_SIBLING.value,
    "older sister": RelationshipRole.OLDER_SIBLING.value,
    "younger_sibling": RelationshipRole.YOUNGER_SIBLING.value,
    "younger brother": RelationshipRole.YOUNGER_SIBLING.value,
    "younger sister": RelationshipRole.YOUNGER_SIBLING.value,
}


@dataclass(frozen=True)
class CanonicalRelationship:
    relationship_type: str
    first_person_id: str
    first_role: str
    second_person_id: str
    second_role: str


def normalize_relationship_role(value: str | None) -> str:
    raw = str(value or "").strip().casefold().replace("-", "_")
    return _ROLE_ALIASES.get(raw, raw)


def canonical_relationship(
    *,
    relationship_type: str,
    subject_person_id: str | None,
    subject_role: str | None,
    object_person_id: str | None,
    object_role: str | None,
) -> CanonicalRelationship | None:
    """Return semantic canonical form, or None for an invalid role shape."""

    rtype = str(relationship_type or "").strip().casefold()
    subject_id = str(subject_person_id or "").strip()
    object_id = str(object_person_id or "").strip()
    if not rtype or not subject_id or not object_id or subject_id == object_id:
        return None

    subject_role_norm = normalize_relationship_role(subject_role)
    object_role_norm = normalize_relationship_role(object_role)

    if rtype == RelationshipType.PARENT_CHILD.value:
        if (
            subject_role_norm == RelationshipRole.PARENT.value
            and object_role_norm == RelationshipRole.CHILD.value
        ):
            parent_id, child_id = subject_id, object_id
        elif (
            subject_role_norm == RelationshipRole.CHILD.value
            and object_role_norm == RelationshipRole.PARENT.value
        ):
            parent_id, child_id = object_id, subject_id
        else:
            return None
        return CanonicalRelationship(
            relationship_type=rtype,
            first_person_id=parent_id,
            first_role=RelationshipRole.PARENT.value,
            second_person_id=child_id,
            second_role=RelationshipRole.CHILD.value,
        )

    if rtype == RelationshipType.SPOUSE.value:
        if {
            subject_role_norm,
            object_role_norm,
        } != {RelationshipRole.SPOUSE.value}:
            return None
        pairs = sorted(
            (
                (subject_id, RelationshipRole.SPOUSE.value),
                (object_id, RelationshipRole.SPOUSE.value),
            )
        )
        return CanonicalRelationship(rtype, pairs[0][0], pairs[0][1], pairs[1][0], pairs[1][1])

    if rtype == RelationshipType.SIBLING.value:
        allowed = {
            RelationshipRole.SIBLING.value,
            RelationshipRole.OLDER_SIBLING.value,
            RelationshipRole.YOUNGER_SIBLING.value,
        }
        if subject_role_norm not in allowed or object_role_norm not in allowed:
            return None
        role_pair = {subject_role_norm, object_role_norm}
        if role_pair not in (
            {RelationshipRole.SIBLING.value},
            {
                RelationshipRole.OLDER_SIBLING.value,
                RelationshipRole.YOUNGER_SIBLING.value,
            },
        ):
            return None
        pairs = sorted(
            (
                (subject_id, subject_role_norm),
                (object_id, object_role_norm),
            )
        )
        return CanonicalRelationship(rtype, pairs[0][0], pairs[0][1], pairs[1][0], pairs[1][1])

    return CanonicalRelationship(
        rtype,
        subject_id,
        subject_role_norm,
        object_id,
        object_role_norm,
    )


def relationship_semantics_match(
    *,
    left_type: str,
    left_subject_person_id: str | None,
    left_subject_role: str | None,
    left_object_person_id: str | None,
    left_object_role: str | None,
    right_type: str,
    right_subject_person_id: str | None,
    right_subject_role: str | None,
    right_object_person_id: str | None,
    right_object_role: str | None,
) -> bool:
    left = canonical_relationship(
        relationship_type=left_type,
        subject_person_id=left_subject_person_id,
        subject_role=left_subject_role,
        object_person_id=left_object_person_id,
        object_role=left_object_role,
    )
    right = canonical_relationship(
        relationship_type=right_type,
        subject_person_id=right_subject_person_id,
        subject_role=right_subject_role,
        object_person_id=right_object_person_id,
        object_role=right_object_role,
    )
    return left is not None and left == right
