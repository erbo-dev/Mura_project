"""Deterministic provenance-closure checks for immutable Family Book snapshots.

The compiler may omit optional archive context, but it may never persist a
reference whose factual support lives outside the exact selected recording
universe. Broken provenance is a data-integrity error, not something an LLM can
repair.
"""

from __future__ import annotations

from collections.abc import Iterable

from mura.domain.book_models import SNAPSHOT_SCHEMA_VERSION, BookSourceSnapshot


class SnapshotClosureError(ValueError):
    """Snapshot provenance is incomplete, dangling, or outside selected sources."""

    code = "book_source_snapshot_invalid"


class SnapshotSizeError(ValueError):
    """Required truth-supporting material exceeds the configured snapshot budget."""

    code = "book_source_snapshot_too_large"


def _exact_ids(values: Iterable[str]) -> list[str]:
    return sorted(set(values))


def _require_recording_subset(
    values: Iterable[str],
    *,
    selected: set[str],
    label: str,
) -> None:
    invalid = sorted(set(values) - selected)
    if invalid:
        raise SnapshotClosureError(
            f"{label} references recordings outside selected sources: {invalid}"
        )


def validate_snapshot_closure(
    snapshot: BookSourceSnapshot,
    *,
    expected_recording_ids: Iterable[str] | None = None,
) -> None:
    """Fail closed unless every factual reference is provenance-closed."""

    if snapshot.schema_version != SNAPSHOT_SCHEMA_VERSION:
        raise SnapshotClosureError(
            f"snapshot schema {snapshot.schema_version!r} predates strict provenance closure"
        )

    manifest = snapshot.manifest
    selected_ids = _exact_ids(manifest.source_recording_ids)
    selected = set(selected_ids)
    if expected_recording_ids is not None:
        expected = _exact_ids(expected_recording_ids)
        if selected_ids != expected:
            raise SnapshotClosureError(
                "snapshot manifest does not exactly match resolved Book sources"
            )

    evidence_by_id = {item.evidence_id: item for item in snapshot.evidence}
    if len(evidence_by_id) != len(snapshot.evidence):
        raise SnapshotClosureError("snapshot contains duplicate evidence ids")
    claim_by_id = {item.claim_id: item for item in snapshot.claims}
    if len(claim_by_id) != len(snapshot.claims):
        raise SnapshotClosureError("snapshot contains duplicate claim ids")
    people_by_id = {item.person_id: item for item in snapshot.people}
    if len(people_by_id) != len(snapshot.people):
        raise SnapshotClosureError("snapshot contains duplicate person ids")

    if _exact_ids(evidence_by_id) != _exact_ids(manifest.source_evidence_ids):
        raise SnapshotClosureError("manifest evidence ids do not match snapshot evidence")
    if _exact_ids(claim_by_id) != _exact_ids(manifest.source_claim_ids):
        raise SnapshotClosureError("manifest claim ids do not match snapshot claims")
    if _exact_ids(people_by_id) != _exact_ids(manifest.source_person_ids):
        raise SnapshotClosureError("manifest person ids do not match snapshot people")
    if _exact_ids(item.story_id for item in snapshot.stories) != _exact_ids(
        manifest.source_story_ids
    ):
        raise SnapshotClosureError("manifest story ids do not match snapshot stories")
    story_ids = [item.story_id for item in snapshot.stories]
    if len(story_ids) != len(set(story_ids)):
        raise SnapshotClosureError("snapshot contains duplicate story ids")
    event_ids = [item.event_id for item in snapshot.events]
    if len(event_ids) != len(set(event_ids)):
        raise SnapshotClosureError("snapshot contains duplicate event ids")
    relationship_ids = [item.edge_id for item in snapshot.relationships]
    if len(relationship_ids) != len(set(relationship_ids)):
        raise SnapshotClosureError("snapshot contains duplicate relationship ids")
    correction_ids = [item.correction_id for item in snapshot.corrections]
    if len(correction_ids) != len(set(correction_ids)):
        raise SnapshotClosureError("snapshot contains duplicate correction ids")
    conflict_ids = [item.conflict_id for item in snapshot.conflicts]
    if len(conflict_ids) != len(set(conflict_ids)):
        raise SnapshotClosureError("snapshot contains duplicate conflict ids")

    if _exact_ids(story_ids) != _exact_ids(manifest.source_story_ids):
        raise SnapshotClosureError("manifest story ids do not match snapshot stories")
    if _exact_ids(event_ids) != _exact_ids(manifest.source_event_ids):
        raise SnapshotClosureError("manifest event ids do not match snapshot events")
    if manifest.correction_count != len(snapshot.corrections):
        raise SnapshotClosureError("manifest correction count does not match snapshot")
    if manifest.uncertainty_count != len(snapshot.uncertainties):
        raise SnapshotClosureError("manifest uncertainty count does not match snapshot")
    if manifest.conflict_count != len(snapshot.conflicts):
        raise SnapshotClosureError("manifest conflict count does not match snapshot")

    for evidence in snapshot.evidence:
        if evidence.recording_id not in selected:
            raise SnapshotClosureError(
                f"evidence {evidence.evidence_id} is outside selected recordings"
            )
        missing_people = sorted(set(evidence.person_ids) - set(people_by_id))
        if missing_people:
            raise SnapshotClosureError(
                f"evidence {evidence.evidence_id} has dangling people refs: {missing_people}"
            )

    for claim in snapshot.claims:
        if claim.recording_id not in selected:
            raise SnapshotClosureError(
                f"claim {claim.claim_id} is outside selected recordings"
            )
        dangling = sorted(set(claim.evidence_ids) - set(evidence_by_id))
        if dangling:
            raise SnapshotClosureError(
                f"claim {claim.claim_id} has dangling evidence refs: {dangling}"
            )

    for person in snapshot.people:
        _require_recording_subset(
            person.source_recording_ids,
            selected=selected,
            label=f"person {person.person_id}",
        )
        if not person.source_recording_ids:
            raise SnapshotClosureError(
                f"person {person.person_id} has no selected-source provenance"
            )

        attrs = person.attribute_sources
        required_keys: list[str] = ["display_name"]
        if person.category and person.category != "unknown":
            required_keys.append("category")
        if person.relation_to_speaker:
            required_keys.append("relation_to_speaker")
        if person.birth_date is not None:
            required_keys.append("birth_date")
        if person.death_date is not None:
            required_keys.append("death_date")
        if person.descriptions:
            required_keys.append("descriptions")
        for profession in person.professions:
            required_keys.append(f"profession:{profession}")
        for location in person.locations:
            required_keys.append(f"location:{location}")
        for alias in person.aliases:
            required_keys.append(f"alias:{alias}")

        for key in required_keys:
            sources = attrs.get(key, [])
            if not sources:
                raise SnapshotClosureError(
                    f"person {person.person_id} attribute {key!r} has no provenance"
                )
            _require_recording_subset(
                sources,
                selected=selected,
                label=f"person {person.person_id} attribute {key}",
            )

    for relationship in snapshot.relationships:
        if (
            relationship.subject_person_id not in people_by_id
            or relationship.object_person_id not in people_by_id
        ):
            raise SnapshotClosureError(
                f"relationship {relationship.edge_id} has missing person endpoint"
            )
        if not relationship.source_claim_ids:
            raise SnapshotClosureError(
                f"relationship {relationship.edge_id} has no supporting claims"
            )
        for claim_id in relationship.source_claim_ids:
            claim = claim_by_id.get(claim_id)
            if claim is None:
                raise SnapshotClosureError(
                    f"relationship {relationship.edge_id} has dangling claim {claim_id}"
                )
            if claim.object_type != "relationship":
                raise SnapshotClosureError(
                    f"relationship {relationship.edge_id} support {claim_id} is not a relationship claim"
                )
            if claim.predicate != relationship.relationship_type:
                raise SnapshotClosureError(
                    f"relationship {relationship.edge_id} support {claim_id} has different relationship type"
                )
            if {claim.subject_person_id, claim.object_person_id} != {
                relationship.subject_person_id,
                relationship.object_person_id,
            }:
                raise SnapshotClosureError(
                    f"relationship {relationship.edge_id} support {claim_id} has different endpoints"
                )
            if claim.recording_id not in selected:
                raise SnapshotClosureError(
                    f"relationship {relationship.edge_id} support is outside selected sources"
                )

    for story in snapshot.stories:
        if story.recording_id not in selected:
            raise SnapshotClosureError(
                f"story {story.story_id} is outside selected recordings"
            )
        dangling = sorted(set(story.evidence_quote_ids) - set(evidence_by_id))
        if dangling:
            raise SnapshotClosureError(
                f"story {story.story_id} has dangling evidence refs: {dangling}"
            )
        missing_people = sorted(set(story.person_ids) - set(people_by_id))
        if missing_people:
            raise SnapshotClosureError(
                f"story {story.story_id} has dangling people refs: {missing_people}"
            )

    for event in snapshot.events:
        if event.recording_id is None or event.recording_id not in selected:
            raise SnapshotClosureError(
                f"event {event.event_id} is outside selected recordings"
            )
        dangling = sorted(set(event.evidence_quote_ids) - set(evidence_by_id))
        if dangling:
            raise SnapshotClosureError(
                f"event {event.event_id} has dangling evidence refs: {dangling}"
            )
        missing_people = sorted(set(event.participant_person_ids) - set(people_by_id))
        if missing_people:
            raise SnapshotClosureError(
                f"event {event.event_id} has dangling people refs: {missing_people}"
            )

    for correction in snapshot.corrections:
        if correction.recording_id not in selected:
            raise SnapshotClosureError(
                f"correction {correction.correction_id} is outside selected recordings"
            )

    for uncertainty in snapshot.uncertainties:
        if uncertainty.recording_id is not None and uncertainty.recording_id not in selected:
            raise SnapshotClosureError(
                f"uncertainty {uncertainty.uncertainty_id} is outside selected recordings"
            )
        missing_people = sorted(set(uncertainty.person_ids) - set(people_by_id))
        if missing_people:
            raise SnapshotClosureError(
                f"uncertainty {uncertainty.uncertainty_id} has dangling people refs"
            )

    for conflict in snapshot.conflicts:
        if not conflict.claim_ids:
            raise SnapshotClosureError(
                f"conflict {conflict.conflict_id} has no included claims"
            )
        dangling = sorted(set(conflict.claim_ids) - set(claim_by_id))
        if dangling:
            raise SnapshotClosureError(
                f"conflict {conflict.conflict_id} has dangling claim refs: {dangling}"
            )
        if (
            conflict.preferred_claim_id is not None
            and conflict.preferred_claim_id not in conflict.claim_ids
        ):
            raise SnapshotClosureError(
                f"conflict {conflict.conflict_id} preferred claim is not included"
            )
        _require_recording_subset(
            conflict.recording_ids,
            selected=selected,
            label=f"conflict {conflict.conflict_id}",
        )
        referenced_recordings = {
            claim_by_id[claim_id].recording_id for claim_id in conflict.claim_ids
        }
        if set(conflict.recording_ids) != referenced_recordings:
            raise SnapshotClosureError(
                f"conflict {conflict.conflict_id} recording provenance is incomplete"
            )
