"""Blueprint validation and deterministic arithmetic repair.

The blueprint validator enforces that a structured book blueprint strictly obeys:
1. Chapter count bounds (10 to 15 chapters).
2. Contiguous chapter numbering (1 to N).
3. Target word budgets (overall and per-chapter).
4. Stable ID resolution: every referenced person, recording, story, claim, and evidence ref
   must exist in the immutable source snapshot.
5. Year grounding: every 4-digit year appearing in chapter plans must be present in allowed_years.
6. Grounding presence: every chapter must carry at least one grounded archive reference.
7. Material anchor grounding: any material anchor must be grounded in evidence or set to None.
8. Deterministic arithmetic repair: budget and numbering defects are repaired in code without
   spending an LLM call on arithmetic.
"""

from __future__ import annotations

import re
from typing import Any

from mura.domain.book_models import (
    MAX_CHAPTERS,
    MIN_CHAPTERS,
    BlueprintIssue,
    BlueprintIssueCode,
    BlueprintValidationReport,
    BookBlueprint,
    BookSourceSnapshot,
    IssueSeverity,
)
from mura.domain.models import StrictModel

_YEAR_REGEX = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")


class BlueprintLimits(StrictModel):
    """Limits and word count bounds for book blueprints."""

    min_chapters: int = MIN_CHAPTERS  # 10
    max_chapters: int = MAX_CHAPTERS  # 15
    min_total_words: int = 20000
    max_total_words: int = 30000
    min_chapter_words: int = 700
    max_chapter_words: int = 3500


DEFAULT_BLUEPRINT_LIMITS = BlueprintLimits()


def _extract_years(text: str | None) -> list[int]:
    if not text:
        return []
    return [int(m) for m in _YEAR_REGEX.findall(text)]


def _is_anchor_grounded(anchor: str | None, snapshot: BookSourceSnapshot) -> bool:
    """Verify that an anchor is a normalized substring of candidate or evidence text."""
    if not anchor or not anchor.strip():
        return False
    norm_anchor = anchor.strip().lower()

    # Check against snapshot candidate list
    for cand in snapshot.material_anchor_candidates:
        if cand.strip().lower() == norm_anchor or norm_anchor in cand.strip().lower():
            return True

    # Check directly against evidence text
    for ev in snapshot.evidence:
        if norm_anchor in ev.text.lower():
            return True

    return False


def validate_blueprint(
    blueprint: BookBlueprint,
    snapshot: BookSourceSnapshot,
    limits: BlueprintLimits | None = None,
) -> BlueprintValidationReport:
    """Validate a structured blueprint against snapshot and limits."""
    lim = limits or DEFAULT_BLUEPRINT_LIMITS
    issues: list[BlueprintIssue] = []

    # 1. Chapter count bounds
    num_chapters = len(blueprint.chapters)
    if num_chapters < lim.min_chapters or num_chapters > lim.max_chapters:
        issues.append(
            BlueprintIssue(
                code=BlueprintIssueCode.CHAPTER_COUNT_OUT_OF_RANGE,
                severity=IssueSeverity.BLOCKER,
                detail=(
                    f"Blueprint has {num_chapters} chapters; required between "
                    f"{lim.min_chapters} and {lim.max_chapters}."
                ),
                offending=[str(num_chapters)],
            )
        )

    # 2. Chapter numbering & uniqueness
    chapter_numbers = [ch.chapter_number for ch in blueprint.chapters]
    seen_numbers: set[int] = set()
    dup_numbers: list[str] = []
    for num in chapter_numbers:
        if num in seen_numbers:
            dup_numbers.append(str(num))
        seen_numbers.add(num)

    if dup_numbers:
        issues.append(
            BlueprintIssue(
                code=BlueprintIssueCode.DUPLICATE_CHAPTER_NUMBER,
                severity=IssueSeverity.BLOCKER,
                detail=f"Duplicate chapter numbers found: {', '.join(dup_numbers)}",
                offending=dup_numbers,
            )
        )

    expected_numbers = list(range(1, num_chapters + 1))
    if sorted(chapter_numbers) != expected_numbers and not dup_numbers:
        issues.append(
            BlueprintIssue(
                code=BlueprintIssueCode.CHAPTER_NUMBER_GAP,
                severity=IssueSeverity.BLOCKER,
                detail=(
                    f"Chapter numbers must be contiguous 1..{num_chapters}; found {chapter_numbers}"
                ),
                offending=[str(n) for n in chapter_numbers],
            )
        )

    # 3. Word budgets
    if (
        blueprint.target_total_words < lim.min_total_words
        or blueprint.target_total_words > lim.max_total_words
    ):
        issues.append(
            BlueprintIssue(
                code=BlueprintIssueCode.WORD_BUDGET_OUT_OF_RANGE,
                severity=IssueSeverity.BLOCKER,
                detail=(
                    f"Total target words {blueprint.target_total_words} is outside allowed range "
                    f"[{lim.min_total_words}, {lim.max_total_words}]."
                ),
                offending=[str(blueprint.target_total_words)],
            )
        )

    sum_chapter_words = sum(ch.target_word_count for ch in blueprint.chapters)
    if sum_chapter_words != blueprint.target_total_words:
        issues.append(
            BlueprintIssue(
                code=BlueprintIssueCode.CHAPTER_TOTAL_MISMATCH,
                severity=IssueSeverity.BLOCKER,
                detail=(
                    f"Sum of chapter word counts ({sum_chapter_words}) does not match "
                    f"target_total_words ({blueprint.target_total_words})."
                ),
                offending=[str(sum_chapter_words), str(blueprint.target_total_words)],
            )
        )

    # 4. Material anchor check
    if blueprint.material_anchor:
        if not _is_anchor_grounded(blueprint.material_anchor, snapshot):
            issues.append(
                BlueprintIssue(
                    code=BlueprintIssueCode.UNSUPPORTED_MATERIAL_ANCHOR,
                    severity=IssueSeverity.BLOCKER,
                    detail=(
                        f"Material anchor '{blueprint.material_anchor}' is not grounded "
                        "in source evidence candidates."
                    ),
                    offending=[blueprint.material_anchor],
                )
            )

    # Sets of known IDs from the snapshot
    known_person_ids = {p.person_id for p in snapshot.people}
    known_rec_ids = set(snapshot.manifest.source_recording_ids)
    known_story_ids = set(snapshot.manifest.source_story_ids)
    known_claim_ids = set(snapshot.manifest.source_claim_ids)
    known_evidence_ids = set(snapshot.manifest.source_evidence_ids)
    allowed_years_set = set(snapshot.allowed_years)

    # 5. Per-chapter checks
    for ch in blueprint.chapters:
        ch_num = ch.chapter_number

        # Per-chapter word count bounds
        if (
            ch.target_word_count < lim.min_chapter_words
            or ch.target_word_count > lim.max_chapter_words
        ):
            issues.append(
                BlueprintIssue(
                    code=BlueprintIssueCode.WORD_BUDGET_OUT_OF_RANGE,
                    severity=IssueSeverity.BLOCKER,
                    chapter_number=ch_num,
                    detail=(
                        f"Chapter {ch_num} target_word_count {ch.target_word_count} is outside "
                        f"[{lim.min_chapter_words}, {lim.max_chapter_words}]."
                    ),
                    offending=[str(ch.target_word_count)],
                )
            )

        # Grounding presence
        has_grounding = bool(
            ch.person_ids
            or ch.source_recording_ids
            or ch.source_story_ids
            or ch.claim_ids
            or ch.evidence_refs
        )
        if not has_grounding:
            issues.append(
                BlueprintIssue(
                    code=BlueprintIssueCode.CHAPTER_WITHOUT_GROUNDING,
                    severity=IssueSeverity.BLOCKER,
                    chapter_number=ch_num,
                    detail=(
                        f"Chapter {ch_num} has no grounded references "
                        "(people, recordings, stories, claims, evidence)."
                    ),
                )
            )

        # Person IDs check
        unknown_people = [pid for pid in ch.person_ids if pid not in known_person_ids]
        if unknown_people:
            issues.append(
                BlueprintIssue(
                    code=BlueprintIssueCode.UNKNOWN_PERSON,
                    severity=IssueSeverity.BLOCKER,
                    chapter_number=ch_num,
                    detail=f"Chapter {ch_num} references unknown person_ids: {unknown_people}",
                    offending=unknown_people,
                )
            )

        # Recording IDs check
        unknown_recs = [rid for rid in ch.source_recording_ids if rid not in known_rec_ids]
        if unknown_recs:
            issues.append(
                BlueprintIssue(
                    code=BlueprintIssueCode.UNKNOWN_RECORDING,
                    severity=IssueSeverity.BLOCKER,
                    chapter_number=ch_num,
                    detail=f"Chapter {ch_num} references unknown recording_ids: {unknown_recs}",
                    offending=unknown_recs,
                )
            )

        # Story IDs check
        unknown_stories = [sid for sid in ch.source_story_ids if sid not in known_story_ids]
        if unknown_stories:
            issues.append(
                BlueprintIssue(
                    code=BlueprintIssueCode.UNKNOWN_STORY,
                    severity=IssueSeverity.BLOCKER,
                    chapter_number=ch_num,
                    detail=f"Chapter {ch_num} references unknown story_ids: {unknown_stories}",
                    offending=unknown_stories,
                )
            )

        # Claim IDs check
        unknown_claims = [cid for cid in ch.claim_ids if cid not in known_claim_ids]
        if unknown_claims:
            issues.append(
                BlueprintIssue(
                    code=BlueprintIssueCode.UNKNOWN_CLAIM,
                    severity=IssueSeverity.BLOCKER,
                    chapter_number=ch_num,
                    detail=f"Chapter {ch_num} references unknown claim_ids: {unknown_claims}",
                    offending=unknown_claims,
                )
            )

        # Evidence refs check
        unknown_evidence = [eid for eid in ch.evidence_refs if eid not in known_evidence_ids]
        if unknown_evidence:
            issues.append(
                BlueprintIssue(
                    code=BlueprintIssueCode.UNKNOWN_EVIDENCE,
                    severity=IssueSeverity.BLOCKER,
                    chapter_number=ch_num,
                    detail=f"Chapter {ch_num} references unknown evidence_refs: {unknown_evidence}",
                    offending=unknown_evidence,
                )
            )

        # Year check across chapter fields
        ch_years: set[int] = set()
        ch_years.update(_extract_years(ch.title))
        ch_years.update(_extract_years(ch.time_range))
        ch_years.update(_extract_years(ch.purpose))
        ch_years.update(_extract_years(ch.synopsis))
        for yr in ch_years:
            if yr not in allowed_years_set:
                issues.append(
                    BlueprintIssue(
                        code=BlueprintIssueCode.UNSUPPORTED_YEAR,
                        severity=IssueSeverity.BLOCKER,
                        chapter_number=ch_num,
                        detail=f"Chapter {ch_num} references ungrounded year {yr}.",
                        offending=[str(yr)],
                    )
                )

        # Material anchor refs check
        if ch.material_anchor_refs:
            if not blueprint.material_anchor:
                issues.append(
                    BlueprintIssue(
                        code=BlueprintIssueCode.UNSUPPORTED_MATERIAL_ANCHOR,
                        severity=IssueSeverity.BLOCKER,
                        chapter_number=ch_num,
                        detail=(
                            f"Chapter {ch_num} specifies material_anchor_refs "
                            "but book has no material_anchor."
                        ),
                        offending=ch.material_anchor_refs,
                    )
                )

    has_blockers = any(issue.severity is IssueSeverity.BLOCKER for issue in issues)
    return BlueprintValidationReport(
        valid=not has_blockers,
        issues=issues,
        repaired=False,
    )


def repair_blueprint_arithmetic(
    blueprint: BookBlueprint,
    limits: BlueprintLimits | None = None,
    snapshot: BookSourceSnapshot | None = None,
) -> BookBlueprint:
    """Deterministically repair budget, chapter numbering, and ungrounded anchors in code.

    Avoids spending an expensive LLM call on mechanical arithmetic repairs.
    """
    lim = limits or DEFAULT_BLUEPRINT_LIMITS
    raw_data: dict[str, Any] = blueprint.model_dump()

    # 1. Clamp target_total_words to configured bounds
    total_words = int(raw_data.get("target_total_words", lim.min_total_words))
    total_words = max(lim.min_total_words, min(total_words, lim.max_total_words))
    raw_data["target_total_words"] = total_words

    # 2. Repair material anchor if ungrounded
    if snapshot is not None and raw_data.get("material_anchor"):
        if not _is_anchor_grounded(raw_data["material_anchor"], snapshot):
            raw_data["material_anchor"] = None

    chapters_data: list[dict[str, Any]] = raw_data.get("chapters", [])
    num_chapters = len(chapters_data)
    if num_chapters == 0:
        return BookBlueprint.model_validate(raw_data)

    # 3. Calculate repaired per-chapter word counts
    base_per_chapter = total_words // num_chapters
    remainder = total_words % num_chapters

    repaired_chapters: list[dict[str, Any]] = []
    for idx, ch in enumerate(chapters_data):
        ch_copy = dict(ch)
        # Contiguous 1..N numbering
        ch_copy["chapter_number"] = idx + 1

        # Budget: distribute base + 1 for the first `remainder` chapters
        assigned_words = base_per_chapter + (1 if idx < remainder else 0)
        # Clamp within chapter limits
        assigned_words = max(lim.min_chapter_words, min(assigned_words, lim.max_chapter_words))
        ch_copy["target_word_count"] = assigned_words

        # If material anchor was nullified, clear anchor refs
        if raw_data.get("material_anchor") is None:
            ch_copy["material_anchor_refs"] = []

        repaired_chapters.append(ch_copy)

    # Re-verify sum matches total_words exactly after clamping
    repaired_sum = sum(c["target_word_count"] for c in repaired_chapters)
    diff = total_words - repaired_sum
    if diff != 0 and num_chapters > 0:
        # Adjust difference into the chapters while keeping within bounds
        for i in range(abs(diff)):
            idx = i % num_chapters
            if diff > 0:
                if repaired_chapters[idx]["target_word_count"] < lim.max_chapter_words:
                    repaired_chapters[idx]["target_word_count"] += 1
            else:
                if repaired_chapters[idx]["target_word_count"] > lim.min_chapter_words:
                    repaired_chapters[idx]["target_word_count"] -= 1

    raw_data["chapters"] = repaired_chapters
    return BookBlueprint.model_validate(raw_data)
