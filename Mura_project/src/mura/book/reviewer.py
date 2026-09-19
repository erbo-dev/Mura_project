"""Family Book chapter reviewer.

Combines 8 deterministic verification gates with LLM-assisted cultural nuance review.
Crucial invariant: The deterministic verdict always outranks the model's opinion.
"""

from __future__ import annotations

import logging
from typing import Any

from mura.book.chapter_gates import run_chapter_gates
from mura.book.prompts import (
    BOOK_REVIEWER_PROMPT_V1,
    BOOK_REVIEWER_PROMPT_VERSION,
)
from mura.domain.book_models import (
    BookLanguage,
    BookSourceSnapshot,
    ChapterDraft,
    ChapterPlan,
    GateReport,
    IssueSeverity,
    ReviewIssue,
    ReviewResult,
    ReviewStatus,
)

logger = logging.getLogger(__name__)


def review_chapter(
    client: Any,
    draft: ChapterDraft,
    chapter_plan: ChapterPlan,
    snapshot: BookSourceSnapshot,
    output_language: BookLanguage,
    *,
    temperature: float = 0.1,
    max_tokens: int = 3000,
    min_chapter_words: int = 700,
    max_chapter_words: int = 3500,
) -> tuple[ReviewResult, GateReport, dict[str, Any]]:
    """Conduct fact grounding, deterministic gate checks, and cultural review on a draft."""
    # 1. Run deterministic code-level chapter gates first
    gate_report = run_chapter_gates(
        draft,
        chapter_plan,
        snapshot,
        output_language,
        min_chapter_words=min_chapter_words,
        max_chapter_words=max_chapter_words,
    )

    # 2. LLM-assisted nuance review
    payload = {
        "chapter_number": draft.chapter_number,
        "title": draft.title,
        "chapter_plan_synopsis": chapter_plan.synopsis,
        "chapter_text": draft.text,
        "evidence_usage": draft.evidence_usage,
        "person_ids_used": draft.person_ids_used,
        "allowed_years": snapshot.allowed_years,
        "known_people": [p.display_name for p in snapshot.people],
        "output_language": output_language.value,
        "deterministic_blockers": [b.detail for b in gate_report.blockers],
        "deterministic_warnings": [w.detail for w in gate_report.warnings],
    }

    try:
        raw_json, usage = client.request_json(
            system_prompt=BOOK_REVIEWER_PROMPT_V1,
            payload=payload,
            max_tokens=max_tokens,
            operation="book_review",
            temperature=temperature,
        )
        review_result = ReviewResult.model_validate(raw_json)
        telemetry = {
            "operation": "book_review",
            "prompt_version": BOOK_REVIEWER_PROMPT_VERSION,
            "model": getattr(usage, "model", "deepseek-chat"),
            "total_tokens": getattr(usage, "total_tokens", 0),
            "prompt_tokens": getattr(usage, "prompt_tokens", 0),
            "completion_tokens": getattr(usage, "completion_tokens", 0),
            "request_seconds": getattr(usage, "request_seconds", 0.0),
        }
    except Exception as exc:
        logger.warning("Reviewer LLM call failed; using deterministic gate verdict: %s", exc)
        review_result = ReviewResult(
            status=ReviewStatus.APPROVED if gate_report.passed else ReviewStatus.REPAIR_REQUIRED,
            issues=[],
            grounding_score=gate_report.grounding_coverage,
            coverage_note="Deterministic gate evaluation (LLM review unavailable)",
        )
        telemetry = {
            "operation": "book_review_fallback",
            "prompt_version": BOOK_REVIEWER_PROMPT_VERSION,
            "error": str(exc),
        }

    # 3. Deterministic verdict override: if deterministic gates failed, chapter CANNOT be approved!
    if not gate_report.passed:
        review_result = review_result.model_copy(update={"status": ReviewStatus.REPAIR_REQUIRED})
        # Merge gate blockers as ReviewIssues
        merged_issues = list(review_result.issues)
        for b in gate_report.blockers:
            merged_issues.append(
                ReviewIssue(
                    issue_type=b.issue_type,
                    severity=IssueSeverity.BLOCKER,
                    location=b.location,
                    detail=b.detail,
                )
            )
        review_result = review_result.model_copy(update={"issues": merged_issues})

    return review_result, gate_report, telemetry
