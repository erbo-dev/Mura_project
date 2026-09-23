"""Family Book planner.

Coordinates the LLM-assisted generation of a structured book blueprint from an
immutable source snapshot, followed by deterministic validation and arithmetic repair.
"""

from __future__ import annotations

from typing import Any

from mura.book.blueprint_validation import (
    BlueprintLimits,
    repair_blueprint_arithmetic,
    validate_blueprint,
)
from mura.book.prompts import (
    BOOK_PLANNER_PROMPT_V1,
    BOOK_PLANNER_PROMPT_VERSION,
)
from mura.domain.book_models import (
    BlueprintValidationReport,
    BookBlueprint,
    BookLanguage,
    BookSourceSnapshot,
    NarrativeVoice,
)


def build_planner_payload(
    snapshot: BookSourceSnapshot,
    *,
    output_language: BookLanguage,
    target_total_words: int,
    central_theme: str | None = None,
    narrative_voice: NarrativeVoice = NarrativeVoice.THIRD_PERSON,
) -> dict[str, Any]:
    """Build privacy-safe structured input payload for the book planner prompt."""
    return {
        "output_language": output_language.value,
        "target_total_words": target_total_words,
        "central_theme": central_theme or "Family Chronicle",
        "narrative_voice": narrative_voice.value,
        "allowed_years": snapshot.allowed_years,
        "material_anchor_candidates": snapshot.material_anchor_candidates,
        "known_places": snapshot.known_places,
        "people": [
            {
                "person_id": p.person_id,
                "display_name": p.display_name,
                "aliases": p.aliases,
                "category": p.category,
                "relation_to_speaker": p.relation_to_speaker,
                "birth_date": p.birth_date.model_dump() if p.birth_date else None,
                "death_date": p.death_date.model_dump() if p.death_date else None,
                "professions": p.professions,
                "locations": p.locations,
            }
            for p in snapshot.people
        ],
        "relationships": [
            {
                "edge_id": r.edge_id,
                "relationship_type": r.relationship_type,
                "subject_person_id": r.subject_person_id,
                "subject_role": r.subject_role,
                "object_person_id": r.object_person_id,
                "object_role": r.object_role,
                "source_claim_ids": r.source_claim_ids,
            }
            for r in snapshot.relationships
        ],
        "stories": [
            {
                "story_id": s.story_id,
                "title": s.title,
                "summary": s.summary,
                "recording_id": s.recording_id,
                "speaker_name": s.speaker_name,
                "person_ids": s.person_ids,
                "evidence_quote_ids": s.evidence_quote_ids,
            }
            for s in snapshot.stories
        ],
        "events": [
            {
                "event_id": e.event_id,
                "title": e.title,
                "event_type": e.event_type,
                "description": e.description,
                "location": e.location,
                "date": e.date.model_dump() if e.date else None,
                "participant_person_ids": e.participant_person_ids,
                "evidence_quote_ids": e.evidence_quote_ids,
            }
            for e in snapshot.events
        ],
        "claims": [
            {
                "claim_id": c.claim_id,
                "recording_id": c.recording_id,
                "object_type": c.object_type,
                "predicate": c.predicate,
                "subject_person_id": c.subject_person_id,
                "object_person_id": c.object_person_id,
                "evidence_class": c.evidence_class,
                "assertion_mode": c.assertion_mode,
                "archive_status": c.archive_status,
                "summary": c.summary,
            }
            for c in snapshot.claims
        ],
        "corrections": [
            {
                "correction_id": cor.correction_id,
                "kind": cor.kind,
                "subject": cor.subject,
                "original_value": cor.original_value,
                "corrected_value": cor.corrected_value,
                "explanation": cor.explanation,
            }
            for cor in snapshot.corrections
        ],
        "uncertainties": [
            {
                "uncertainty_id": u.uncertainty_id,
                "kind": u.kind,
                "text": u.text,
            }
            for u in snapshot.uncertainties
        ],
        "conflicts": [
            {
                "conflict_id": conf.conflict_id,
                "conflict_type": conf.conflict_type,
                "status": conf.status,
                "claim_ids": conf.claim_ids,
                "rationale": conf.rationale,
            }
            for conf in snapshot.conflicts
        ],
    }


def plan_book(
    client: Any,
    snapshot: BookSourceSnapshot,
    *,
    output_language: BookLanguage = BookLanguage.RU,
    target_total_words: int = 24000,
    central_theme: str | None = None,
    narrative_voice: NarrativeVoice = NarrativeVoice.THIRD_PERSON,
    temperature: float = 0.4,
    limits: BlueprintLimits | None = None,
    max_tokens: int = 4000,
) -> tuple[BookBlueprint, BlueprintValidationReport, dict[str, Any]]:
    """Generate and validate a structured BookBlueprint."""
    payload = build_planner_payload(
        snapshot,
        output_language=output_language,
        target_total_words=target_total_words,
        central_theme=central_theme,
        narrative_voice=narrative_voice,
    )

    raw_json, usage = client.request_json(
        system_prompt=BOOK_PLANNER_PROMPT_V1,
        payload=payload,
        max_tokens=max_tokens,
        operation="book_plan",
        temperature=temperature,
    )

    blueprint = BookBlueprint.model_validate(raw_json)
    report = validate_blueprint(blueprint, snapshot, limits)

    if not report.valid:
        # Attempt deterministic repair of arithmetic and ungrounded anchors
        repaired = repair_blueprint_arithmetic(blueprint, limits=limits, snapshot=snapshot)
        repaired_report = validate_blueprint(repaired, snapshot, limits)
        if repaired_report.valid:
            repaired_report.repaired = True
            blueprint = repaired
            report = repaired_report

    telemetry: dict[str, Any] = {
        "operation": "book_plan",
        "prompt_version": BOOK_PLANNER_PROMPT_VERSION,
        "model": getattr(usage, "model", "deepseek-chat"),
        "total_tokens": getattr(usage, "total_tokens", 0),
        "prompt_tokens": getattr(usage, "prompt_tokens", 0),
        "completion_tokens": getattr(usage, "completion_tokens", 0),
        "cached_tokens": getattr(usage, "prompt_cache_hit_tokens", 0),
        "request_seconds": getattr(usage, "request_seconds", 0.0),
    }

    return blueprint, report, telemetry

