"""Family Book chapter writer and repair module.

Orchestrates drafting and repairing individual chapters under strict grounding rules:
- Verbatim evidence quotes only.
- Forbidden original values from self-corrections are strictly excluded.
- Preserved uncertainties and conflicts survive in prose.
- Respects continuity state from previous chapters.
"""

from __future__ import annotations

from typing import Any

from mura.book.prompts import (
    BOOK_CHAPTER_REPAIR_PROMPT_V1,
    BOOK_CHAPTER_REPAIR_PROMPT_VERSION,
    BOOK_WRITER_PROMPT_V1,
    BOOK_WRITER_PROMPT_VERSION,
)
from mura.domain.book_models import (
    BookLanguage,
    BookSourceSnapshot,
    ChapterDraft,
    ChapterPlan,
    ContinuityState,
)


def build_writer_payload(
    chapter_plan: ChapterPlan,
    snapshot: BookSourceSnapshot,
    continuity: ContinuityState | None,
    output_language: BookLanguage,
) -> dict[str, Any]:
    """Assemble the grounded context and constraints for writing one chapter."""
    relevant_people = [
        {
            "person_id": p.person_id,
            "display_name": p.display_name,
            "aliases": p.aliases,
            "category": p.category,
            "relation_to_speaker": p.relation_to_speaker,
            "professions": p.professions,
            "locations": p.locations,
        }
        for p in snapshot.people
        if p.person_id in chapter_plan.person_ids
    ]

    evidence_map = {e.evidence_id: e for e in snapshot.evidence}
    relevant_evidence = [
        {
            "evidence_id": eid,
            "speaker_name": evidence_map[eid].speaker_name,
            "text": evidence_map[eid].text,
        }
        for eid in chapter_plan.evidence_refs
        if eid in evidence_map
    ]

    # If no specific evidence refs were planned, include evidence from source recordings
    if not relevant_evidence and chapter_plan.source_recording_ids:
        rec_ids = set(chapter_plan.source_recording_ids)
        relevant_evidence = [
            {
                "evidence_id": e.evidence_id,
                "speaker_name": e.speaker_name,
                "text": e.text,
            }
            for e in snapshot.evidence
            if e.recording_id in rec_ids
        ][:10]

    relevant_stories = [
        {
            "story_id": s.story_id,
            "title": s.title,
            "summary": s.summary,
            "speaker_name": s.speaker_name,
        }
        for s in snapshot.stories
        if s.story_id in chapter_plan.source_story_ids
    ]

    # Corrections are global constraints: original_value is forbidden
    corrections_payload = [
        {
            "correction_id": cor.correction_id,
            "original_forbidden_value": cor.original_value,
            "corrected_value": cor.corrected_value,
            "explanation": cor.explanation,
        }
        for cor in snapshot.corrections
    ]

    return {
        "chapter_number": chapter_plan.chapter_number,
        "title": chapter_plan.title,
        "purpose": chapter_plan.purpose,
        "synopsis": chapter_plan.synopsis,
        "target_word_count": chapter_plan.target_word_count,
        "time_range": chapter_plan.time_range,
        "output_language": output_language.value,
        "allowed_years": snapshot.allowed_years,
        "material_anchor_refs": chapter_plan.material_anchor_refs,
        "continuity": continuity.model_dump() if continuity else None,
        "people": relevant_people,
        "evidence_quotes": relevant_evidence,
        "stories": relevant_stories,
        "forbidden_corrections": corrections_payload,
        "uncertainties": chapter_plan.uncertainties,
        "forbidden_inventions": chapter_plan.forbidden_inventions,
    }


def write_chapter(
    client: Any,
    chapter_plan: ChapterPlan,
    snapshot: BookSourceSnapshot,
    continuity: ContinuityState | None,
    output_language: BookLanguage,
    *,
    temperature: float = 0.75,
    max_tokens: int = 6000,
) -> tuple[ChapterDraft, dict[str, Any]]:
    """Draft one chapter of the family book using LLM assistance under strict rules."""
    payload = build_writer_payload(chapter_plan, snapshot, continuity, output_language)

    raw_json, usage = client.request_json(
        system_prompt=BOOK_WRITER_PROMPT_V1,
        payload=payload,
        max_tokens=max_tokens,
        operation="book_write",
        temperature=temperature,
    )

    draft = ChapterDraft.model_validate(raw_json)
    telemetry = {
        "operation": "book_write",
        "prompt_version": BOOK_WRITER_PROMPT_VERSION,
        "model": getattr(usage, "model", "deepseek-chat"),
        "total_tokens": getattr(usage, "total_tokens", 0),
        "prompt_tokens": getattr(usage, "prompt_tokens", 0),
        "completion_tokens": getattr(usage, "completion_tokens", 0),
        "request_seconds": getattr(usage, "request_seconds", 0.0),
    }
    return draft, telemetry


def repair_chapter(
    client: Any,
    *,
    previous_draft: ChapterDraft,
    feedback: str,
    chapter_plan: ChapterPlan,
    snapshot: BookSourceSnapshot,
    continuity: ContinuityState | None,
    output_language: BookLanguage,
    temperature: float = 0.5,
    max_tokens: int = 6000,
) -> tuple[ChapterDraft, dict[str, Any]]:
    """Repair a chapter draft that failed deterministic validation gates or review."""
    base_payload = build_writer_payload(chapter_plan, snapshot, continuity, output_language)
    repair_payload = {
        **base_payload,
        "previous_draft_text": previous_draft.text,
        "issues_to_fix": feedback,
    }

    raw_json, usage = client.request_json(
        system_prompt=BOOK_CHAPTER_REPAIR_PROMPT_V1,
        payload=repair_payload,
        max_tokens=max_tokens,
        operation="book_repair",
        temperature=temperature,
    )

    repaired_draft = ChapterDraft.model_validate(raw_json)
    telemetry = {
        "operation": "book_repair",
        "prompt_version": BOOK_CHAPTER_REPAIR_PROMPT_VERSION,
        "model": getattr(usage, "model", "deepseek-chat"),
        "total_tokens": getattr(usage, "total_tokens", 0),
        "prompt_tokens": getattr(usage, "prompt_tokens", 0),
        "completion_tokens": getattr(usage, "completion_tokens", 0),
        "request_seconds": getattr(usage, "request_seconds", 0.0),
    }
    return repaired_draft, telemetry

