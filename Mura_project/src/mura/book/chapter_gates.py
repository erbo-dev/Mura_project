"""Deterministic chapter verification gates.

The current pipeline applies eleven independent checks before acceptance:
named people, years, locations, relationships, corrections, direct speech,
unsupported factual assertions, unresolved conflicts, evidence coverage, word
count, and output language. Writer self-reported metadata is never authoritative.
"""
# ruff: noqa: E501, RUF001

from __future__ import annotations

import re
import unicodedata

from mura.book.prose_grounding import (
    analyze_prose,
    contains_rejected_correction,
    normalize_text,
    rejected_year_patterns,
)
from mura.domain.book_models import (
    GATE_SCHEMA_VERSION,
    BookLanguage,
    BookSourceSnapshot,
    ChapterDraft,
    ChapterPlan,
    GateCode,
    GateIssue,
    GateReport,
    IssueSeverity,
)

_YEAR_REGEX = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")
_QUOTE_REGEX = re.compile(r"[«\"]([^»\"\n]{5,})[»\"]")

_KZ_GRAPHEMES = set("әіңғүұқөһӘІҢҒҮҰҚӨҺ")

_SOFT_TERMS: set[str] = {
    # Months & Days (Russian & Kazakh)
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
    "қаңтар",
    "ақпан",
    "наурыз",
    "сәуір",
    "мамыр",
    "маусым",
    "шілде",
    "тамыз",
    "қыркүйек",
    "қазан",
    "қараша",
    "желтоқсан",
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
    # Common kinship / titles
    "ата",
    "әже",
    "әке",
    "шеше",
    "ана",
    "аға",
    "іні",
    "әпке",
    "сіңлі",
    "қарындас",
    "бала",
    "қыз",
    "ұл",
    "немере",
    "шөбере",
    "баба",
    "келін",
    "күйеу",
    "жезде",
    "дедушка",
    "бабушка",
    "отец",
    "мать",
    "папа",
    "мама",
    "брат",
    "сестра",
    "сын",
    "дочь",
    "внук",
    "внучка",
    "дядя",
    "тётя",
    "тетя",
    "прадед",
    "прабабушка",
    "предок",
    "потомок",
    # Cultural & historical terms
    "совет",
    "ссср",
    "союз",
    "партия",
    "фронт",
    "война",
    "победа",
    "армия",
    "госпиталь",
    "колхоз",
    "совхоз",
    "завод",
    "школа",
    "институт",
    "университет",
    "район",
    "область",
    "ауыл",
    "аул",
    "күй",
    "домбыра",
    "домбра",
    "шапан",
    "бесік",
    "тұмар",
    "құран",
    "бог",
    "алла",
    "құдай",
    "жаратқан",
    "жаным",
    "ботам",
    "жарығым",
    "күнім",
    "батыр",
    "хан",
    "би",
    "болыс",
    "ақсақал",
    "ақын",
    "жырау",
    # Pronouns & Demonstratives (Kazakh & Russian)
    "бұл",
    "осы",
    "сол",
    "ол",
    "олар",
    "біз",
    "мен",
    "сен",
    "сіз",
    "бәрі",
    "барлығы",
    "он",
    "она",
    "оно",
    "они",
    "мы",
    "вы",
    "я",
    "ты",
    "это",
    "этот",
    "эта",
    "тот",
    "та",
    "все",
    "всё",
    "каждый",
    "никто",
    "ничто",
    "кто",
    "что",
    "где",
    "когда",
    "как",
}

_NORM_RELATIONS: dict[str, str] = {
    "parent": "parent",
    "father": "parent",
    "mother": "parent",
    "отец": "parent",
    "мать": "parent",
    "папа": "parent",
    "мама": "parent",
    "әке": "parent",
    "шеше": "parent",
    "ана": "parent",
    "child": "child",
    "son": "child",
    "daughter": "child",
    "сын": "child",
    "дочь": "child",
    "ұл": "child",
    "қыз": "child",
    "бала": "child",
    "spouse": "spouse",
    "husband": "spouse",
    "wife": "spouse",
    "муж": "spouse",
    "жена": "spouse",
    "супруг": "spouse",
    "супруга": "spouse",
    "күйеу": "spouse",
    "әйел": "spouse",
    "sibling": "sibling",
    "brother": "sibling",
    "sister": "sibling",
    "брат": "sibling",
    "сестра": "sibling",
    "аға": "sibling",
    "іні": "sibling",
    "әпке": "sibling",
    "сіңлі": "sibling",
    "қарындас": "sibling",
}


def _norm(s: str) -> str:
    normalized = unicodedata.normalize("NFC", s)
    return " ".join(normalized.split())


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def _is_sentence_start(text: str, idx: int) -> bool:
    pre = text[:idx]
    if not pre.strip():
        return True
    if re.search(r"[\.\!\?…]\s*[»”\"\)\—\-]?\s*$", pre):
        return True
    if re.search(r"[\n]\s*$", pre):
        return True
    return False


def run_chapter_gates(
    draft: ChapterDraft,
    chapter_plan: ChapterPlan,
    snapshot: BookSourceSnapshot,
    output_language: BookLanguage,
    *,
    min_chapter_words: int = 700,
    max_chapter_words: int = 3500,
) -> GateReport:
    """Run deterministic verification gates over prose and frozen provenance."""
    blockers: list[GateIssue] = []
    warnings: list[GateIssue] = []
    text = draft.text
    analysis = analyze_prose(
        text,
        snapshot,
        planned_evidence_ids=chapter_plan.evidence_refs,
    )

    # Gate 1: NAMED_PERSON — derived from prose, including sentence-start
    # candidates when their local grammar looks person-like.
    for surface in analysis.unknown_people:
        blockers.append(
            GateIssue(
                code=GateCode.NAMED_PERSON,
                severity=IssueSeverity.BLOCKER,
                issue_type=GateCode.NAMED_PERSON.issue_type,
                detail=f"Named person '{surface}' is not present in the selected-source snapshot.",
                offending=[surface],
            )
        )

    # Gate 2: YEAR — includes normalized short/textual year forms when they can
    # be resolved deterministically against the snapshot.
    allowed_years_set = set(snapshot.allowed_years)
    ungrounded_years = [str(year) for year in analysis.years if year not in allowed_years_set]
    if ungrounded_years or analysis.ambiguous_short_years:
        blockers.append(
            GateIssue(
                code=GateCode.YEAR,
                severity=IssueSeverity.BLOCKER,
                issue_type=GateCode.YEAR.issue_type,
                detail="Chapter contains unsupported or ambiguous factual year expressions.",
                offending=sorted(set(ungrounded_years) | set(analysis.ambiguous_short_years)),
            )
        )

    # Gate 3: LOCATION — plausible geography is not evidence.
    for surface in analysis.locations:
        blockers.append(
            GateIssue(
                code=GateCode.LOCATION,
                severity=IssueSeverity.BLOCKER,
                issue_type=GateCode.LOCATION.issue_type,
                detail=f"Location '{surface}' is not grounded in selected sources.",
                offending=[surface],
            )
        )

    # Gate 4: RELATIONSHIP. Writer metadata is checked below as a secondary
    # signal, but prose-derived kinship assertions are the truth boundary.
    # Check that any referenced person_ids_used exist in snapshot
    known_pids = {p.person_id for p in snapshot.people}
    unknown_pids = [pid for pid in draft.person_ids_used if pid not in known_pids]
    if unknown_pids:
        blockers.append(
            GateIssue(
                code=GateCode.RELATIONSHIP,
                severity=IssueSeverity.BLOCKER,
                issue_type=GateCode.RELATIONSHIP.issue_type,
                detail=f"Chapter claims usage of unknown person_ids: {unknown_pids}",
                offending=unknown_pids,
            )
        )

    # Build grounded relationship triples from snapshot.relationships
    grounded_relationships: set[tuple[str, str, str]] = set()
    for rel in snapshot.relationships:
        s_pid = rel.subject_person_id
        o_pid = rel.object_person_id
        rtype = rel.relationship_type.lower()
        s_role = rel.subject_role.lower()
        o_role = rel.object_role.lower()

        s_norm = _NORM_RELATIONS.get(s_role, s_role)
        o_norm = _NORM_RELATIONS.get(o_role, o_role)
        grounded_relationships.add((s_pid, s_norm, o_pid))
        grounded_relationships.add((o_pid, o_norm, s_pid))
        grounded_relationships.add((s_pid, rtype, o_pid))
        grounded_relationships.add((o_pid, rtype, s_pid))

        if rtype in ("spouse",) or s_norm == "spouse" or o_norm == "spouse":
            grounded_relationships.add((s_pid, "spouse", o_pid))
            grounded_relationships.add((o_pid, "spouse", s_pid))
        if rtype in ("sibling",) or s_norm == "sibling" or o_norm == "sibling":
            grounded_relationships.add((s_pid, "sibling", o_pid))
            grounded_relationships.add((o_pid, "sibling", s_pid))
        if rtype in ("parent_child",) or s_norm == "parent" or o_norm == "child":
            grounded_relationships.add((s_pid, "parent", o_pid))
            grounded_relationships.add((o_pid, "child", s_pid))

    for assertion in analysis.relationships:
        if assertion.subject_person_id is None or assertion.object_person_id is None:
            blockers.append(
                GateIssue(
                    code=GateCode.RELATIONSHIP,
                    severity=IssueSeverity.BLOCKER,
                    issue_type=GateCode.RELATIONSHIP.issue_type,
                    detail=(
                        "Prose contains a kinship assertion whose person endpoint "
                        "cannot be resolved inside the selected-source snapshot."
                    ),
                    location=assertion.text_span,
                    offending=list(assertion.unresolved_surfaces),
                )
            )
            continue
        if (
            assertion.subject_person_id,
            assertion.relation,
            assertion.object_person_id,
        ) not in grounded_relationships:
            blockers.append(
                GateIssue(
                    code=GateCode.RELATIONSHIP,
                    severity=IssueSeverity.BLOCKER,
                    issue_type=GateCode.RELATIONSHIP.issue_type,
                    detail="Prose contains an unsupported kinship/relationship assertion.",
                    location=assertion.text_span,
                    offending=[
                        assertion.subject_person_id,
                        assertion.relation,
                        assertion.object_person_id,
                    ],
                )
            )

    # Writer-reported assertions may add stricter checks, but omitting them can
    # never hide a prose assertion.
    for reported_assertion in draft.relationship_assertions:
        s_id = reported_assertion.subject_person_id
        o_id = reported_assertion.object_person_id
        r_str = reported_assertion.relation.strip().lower()
        r_norm = _NORM_RELATIONS.get(r_str, r_str)

        if s_id is None or o_id is None or s_id not in known_pids or o_id not in known_pids:
            blockers.append(
                GateIssue(
                    code=GateCode.RELATIONSHIP,
                    severity=IssueSeverity.BLOCKER,
                    issue_type=GateCode.RELATIONSHIP.issue_type,
                    detail=(
                        "Relationship assertion refers to unknown person(s): "
                        f"({s_id}, {reported_assertion.relation}, {o_id})"
                    ),
                    offending=[person_id for person_id in (s_id, o_id) if person_id is not None],
                )
            )
            continue

        if (s_id, r_norm, o_id) not in grounded_relationships and (
            s_id,
            r_str,
            o_id,
        ) not in grounded_relationships:
            blockers.append(
                GateIssue(
                    code=GateCode.RELATIONSHIP,
                    severity=IssueSeverity.BLOCKER,
                    issue_type=GateCode.RELATIONSHIP.issue_type,
                    detail=(
                        f"Ungrounded relationship assertion: person '{s_id}' as "
                        f"'{reported_assertion.relation}' of '{o_id}' is not supported "
                        "by family archive relationships."
                    ),
                    location=reported_assertion.text_span or None,
                    offending=[s_id, reported_assertion.relation, o_id],
                )
            )

    # Gate 5: CORRECTION. Exact rejected values remain forbidden; rejected
    # years also block obvious short/textual paraphrases such as "24-м году".
    for cor in snapshot.corrections:
        wrong = cor.original_value.strip()
        found_rejected = bool(wrong and contains_rejected_correction(text, wrong))
        if wrong.isdigit() and len(wrong) == 4:
            normalized_prose = normalize_text(text)
            found_rejected = found_rejected or any(
                pattern.search(normalized_prose) for pattern in rejected_year_patterns(int(wrong))
            )
        if found_rejected:
            blockers.append(
                GateIssue(
                    code=GateCode.CORRECTION,
                    severity=IssueSeverity.BLOCKER,
                    issue_type=GateCode.CORRECTION.issue_type,
                    detail=(
                        f"Chapter contains superseded correction variant '{wrong}'. "
                        f"Correct value is '{cor.corrected_value}'."
                    ),
                    offending=[wrong],
                )
            )

    # Gate 6: QUOTE / DIALOGUE. Paired quotes and em-dash direct speech must
    # occur verbatim in selected evidence; narrative paraphrase should not be
    # dressed up as remembered dialogue.
    evidence_blobs = [normalize_text(ev.text) for ev in snapshot.evidence]
    for direct_speech in analysis.direct_speech:
        quote_body = normalize_text(direct_speech)
        if len(quote_body) < 5:
            continue
        if not any(quote_body in blob for blob in evidence_blobs):
            blockers.append(
                GateIssue(
                    code=GateCode.QUOTE,
                    severity=IssueSeverity.BLOCKER,
                    issue_type=GateCode.QUOTE.issue_type,
                    detail="Direct speech is not a verbatim selected-source evidence span.",
                    offending=[direct_speech],
                )
            )

    # Gate 7: FACTUAL_ASSERTION. Known names are not a license to invent
    # biography or scene facts about them. High-signal factual clauses derived
    # independently from prose must have selected-source lexical support.
    for clause in analysis.unsupported_factual_clauses:
        blockers.append(
            GateIssue(
                code=GateCode.FACTUAL_ASSERTION,
                severity=IssueSeverity.BLOCKER,
                issue_type=GateCode.FACTUAL_ASSERTION.issue_type,
                detail="Chapter contains a factual clause about a known person with no support in selected sources.",
                offending=[clause],
            )
        )

    # Gate 8: CONFLICT. An unresolved selected-source conflict may be narrated,
    # but not silently collapsed into certainty.
    plan_claim_ids = set(chapter_plan.claim_ids)
    relevant_open_conflicts = [
        conflict
        for conflict in snapshot.conflicts
        if conflict.status not in {"resolved", "dismissed"}
        and plan_claim_ids.intersection(conflict.claim_ids)
    ]
    if relevant_open_conflicts:
        folded = normalize_text(text)
        preserves_uncertainty = bool(
            re.search(
                r"\b(?:источник\w*\s+расход\w*|по\s+одной\s+версии|"
                r"по\s+другой\s+версии|неясн\w*|неизвестн\w*|возможн\w*|"
                r"вероятн\w*|мәлімет\w*\s+әртүрлі|анық\s+емес|болжам\w*)\b",
                folded,
            )
        )
        if not preserves_uncertainty:
            blockers.append(
                GateIssue(
                    code=GateCode.CONFLICT,
                    severity=IssueSeverity.BLOCKER,
                    issue_type=GateCode.CONFLICT.issue_type,
                    detail="Chapter uses unresolved conflicting source claims without preserving disagreement.",
                    offending=[conflict.conflict_id for conflict in relevant_open_conflicts],
                )
            )

    # Gate 9: EVIDENCE_COVERAGE — inferred from prose, never trusted from
    # draft.evidence_usage.
    total_planned = len(chapter_plan.evidence_refs)
    if total_planned > 0:
        actual_evidence_ids = set(analysis.actual_evidence_ids)
        covered = sum(1 for eid in chapter_plan.evidence_refs if eid in actual_evidence_ids)
        coverage_ratio = covered / total_planned
        if covered == 0:
            blockers.append(
                GateIssue(
                    code=GateCode.EVIDENCE_COVERAGE,
                    severity=IssueSeverity.BLOCKER,
                    issue_type=GateCode.EVIDENCE_COVERAGE.issue_type,
                    detail=(
                        f"Zero planned evidence used (0/{total_planned}). "
                        "Chapter draft must incorporate at least one planned evidence quote."
                    ),
                    offending=list(chapter_plan.evidence_refs),
                )
            )
        elif coverage_ratio < 0.5:
            warnings.append(
                GateIssue(
                    code=GateCode.EVIDENCE_COVERAGE,
                    severity=IssueSeverity.WARNING,
                    issue_type=GateCode.EVIDENCE_COVERAGE.issue_type,
                    detail=(
                        f"Evidence coverage {coverage_ratio:.2f} is below target 0.5 "
                        f"({covered}/{total_planned} planned evidence quotes used)."
                    ),
                    offending=list(set(chapter_plan.evidence_refs) - actual_evidence_ids),
                )
            )
    else:
        coverage_ratio = 1.0

    # Gate 10: WORD_COUNT
    wc = _word_count(text)
    if wc < min_chapter_words or wc > max_chapter_words:
        blockers.append(
            GateIssue(
                code=GateCode.WORD_COUNT,
                severity=IssueSeverity.BLOCKER,
                issue_type=GateCode.WORD_COUNT.issue_type,
                detail=f"Chapter word count {wc} is outside allowed range [{min_chapter_words}, {max_chapter_words}].",
                offending=[str(wc)],
            )
        )

    # Gate 11: LANGUAGE
    cyrillic_chars = len(re.findall(r"[а-яА-ЯёЁәіңғүұқөһӘІҢҒҮҰҚӨҺ]", text))
    latin_chars = len(re.findall(r"[a-zA-Z]", text))
    total_alpha = cyrillic_chars + latin_chars

    if total_alpha > 50:
        if output_language == BookLanguage.KK:
            has_kz = bool(set(text) & _KZ_GRAPHEMES)
            if cyrillic_chars < latin_chars:
                blockers.append(
                    GateIssue(
                        code=GateCode.LANGUAGE,
                        severity=IssueSeverity.BLOCKER,
                        issue_type=GateCode.LANGUAGE.issue_type,
                        detail="Expected Kazakh language, but text is not Cyrillic-dominant.",
                    )
                )
            elif not has_kz and wc > 300:
                warnings.append(
                    GateIssue(
                        code=GateCode.LANGUAGE,
                        severity=IssueSeverity.WARNING,
                        issue_type=GateCode.LANGUAGE.issue_type,
                        detail="Language is Kazakh (kk), but text lacks standard Kazakh Cyrillic graphemes.",
                    )
                )
        elif output_language == BookLanguage.RU:
            if cyrillic_chars < latin_chars:
                blockers.append(
                    GateIssue(
                        code=GateCode.LANGUAGE,
                        severity=IssueSeverity.BLOCKER,
                        issue_type=GateCode.LANGUAGE.issue_type,
                        detail="Expected Russian language, but text is not Cyrillic-dominant.",
                    )
                )
        elif output_language == BookLanguage.EN:
            if latin_chars < cyrillic_chars:
                blockers.append(
                    GateIssue(
                        code=GateCode.LANGUAGE,
                        severity=IssueSeverity.BLOCKER,
                        issue_type=GateCode.LANGUAGE.issue_type,
                        detail="Expected English language, but text is Cyrillic-dominant.",
                    )
                )

    passed = len(blockers) == 0
    return GateReport(
        schema_version=GATE_SCHEMA_VERSION,
        chapter_number=draft.chapter_number,
        passed=passed,
        blockers=blockers,
        warnings=warnings,
        word_count=wc,
        grounding_coverage=coverage_ratio,
        deterministically_repairable=False,
    )
