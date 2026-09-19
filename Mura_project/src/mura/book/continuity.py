"""Continuity management across book chapters.

Tracks chronological position, active characters, unresolved threads, scene summaries,
and material anchor states across successive approved chapters.
"""

from __future__ import annotations

import logging
from typing import Any

from mura.book.prompts import (
    BOOK_CONTINUITY_PROMPT_V1,
    BOOK_CONTINUITY_PROMPT_VERSION,
)
from mura.domain.book_models import (
    CONTINUITY_SCHEMA_VERSION,
    BookBlueprint,
    ChapterDraft,
    ChapterPlan,
    ContinuityState,
)

logger = logging.getLogger(__name__)


def create_initial_continuity_state(blueprint: BookBlueprint) -> ContinuityState:
    """Create a baseline continuity state before chapter 1 is written."""
    anchor_state = (
        f"Material anchor designated: {blueprint.material_anchor}."
        if blueprint.material_anchor
        else None
    )
    return ContinuityState(
        schema_version=CONTINUITY_SCHEMA_VERSION,
        after_chapter_number=0,
        current_time_position=None,
        active_people=[],
        resolved_story_threads=[],
        open_story_threads=[blueprint.central_theme] if blueprint.central_theme else [],
        last_scene_summary="Beginning of the family chronicle.",
        material_anchor_state=anchor_state,
        tone="reverent, reflective",
        important_terminology=[],
        facts_already_revealed=[],
    )


def update_continuity(
    client: Any,
    *,
    previous_state: ContinuityState,
    draft: ChapterDraft,
    chapter_plan: ChapterPlan,
    blueprint: BookBlueprint,
    temperature: float = 0.2,
    max_tokens: int = 2000,
) -> tuple[ContinuityState, dict[str, Any]]:
    """Update continuity state following the approval of a chapter."""
    payload = {
        "previous_continuity": previous_state.model_dump(),
        "chapter_number": draft.chapter_number,
        "chapter_title": draft.title,
        "chapter_plan_synopsis": chapter_plan.synopsis,
        "chapter_plan_time_range": chapter_plan.time_range,
        "chapter_text_excerpt": draft.text[:1500] if len(draft.text) > 1500 else draft.text,
        "person_ids_used": draft.person_ids_used,
        "material_anchor": blueprint.material_anchor,
    }

    try:
        raw_json, usage = client.request_json(
            system_prompt=BOOK_CONTINUITY_PROMPT_V1,
            payload=payload,
            max_tokens=max_tokens,
            operation="book_continuity",
            temperature=temperature,
        )
        new_state = ContinuityState.model_validate(raw_json)
        # Guarantee chapter number continuity
        new_state = new_state.model_copy(update={"after_chapter_number": draft.chapter_number})
        telemetry = {
            "operation": "book_continuity",
            "prompt_version": BOOK_CONTINUITY_PROMPT_VERSION,
            "model": getattr(usage, "model", "deepseek-chat"),
            "total_tokens": getattr(usage, "total_tokens", 0),
            "prompt_tokens": getattr(usage, "prompt_tokens", 0),
            "completion_tokens": getattr(usage, "completion_tokens", 0),
            "request_seconds": getattr(usage, "request_seconds", 0.0),
        }
        return new_state, telemetry
    except Exception as exc:
        logger.warning(
            "Continuity tracking LLM call failed; applying fallback update: %s",
            exc,
        )
        fallback_state = ContinuityState(
            schema_version=CONTINUITY_SCHEMA_VERSION,
            after_chapter_number=draft.chapter_number,
            current_time_position=chapter_plan.time_range or previous_state.current_time_position,
            active_people=sorted(set(previous_state.active_people + draft.person_ids_used)),
            resolved_story_threads=previous_state.resolved_story_threads,
            open_story_threads=previous_state.open_story_threads,
            last_scene_summary=f"Chapter {draft.chapter_number}: {draft.title}",
            material_anchor_state=previous_state.material_anchor_state,
            tone=previous_state.tone,
            important_terminology=previous_state.important_terminology,
            facts_already_revealed=previous_state.facts_already_revealed,
        )
        return fallback_state, {
            "operation": "book_continuity_fallback",
            "prompt_version": BOOK_CONTINUITY_PROMPT_VERSION,
            "error": str(exc),
        }

