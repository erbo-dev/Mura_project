"""Deterministic chapter verification gates.

Applies 8 mechanical verification gates to a chapter draft before acceptance:
1. GateCode.NAMED_PERSON: every proper name must be grounded in canonical people or places.
2. GateCode.YEAR: every 4-digit year must be in snapshot.allowed_years.
3. GateCode.RELATIONSHIP: kinship references must be grounded.
4. GateCode.CORRECTION: superseded values from self-corrections are strictly forbidden.
5. GateCode.QUOTE: direct quotations must be verbatim substrings of evidence spans.
6. GateCode.EVIDENCE_COVERAGE: checks coverage of planned evidence.
7. GateCode.WORD_COUNT: word count must fall within [min_words, max_words].
8. GateCode.LANGUAGE: text must match designated output language.
"""
# ruff: noqa: E501, RUF001

from __future__ import annotations

import re
import unicodedata

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
    "январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь",
    "октябрь", "ноябрь", "декабрь", "қаңтар", "ақпан", "наурыз", "сәуір", "мамыр",
    "маусым", "шілде", "тамыз", "қыркүйек", "қазан", "қараша", "желтоқсан",
    "понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье",
    # Common kinship / titles
    "ата", "әже", "әке", "шеше", "ана", "аға", "іні", "әпке", "сіңлі", "қарындас",
    "бала", "қыз", "ұл", "немере", "шөбере", "баба", "келін", "күйеу", "жезде",
    "дедушка", "бабушка", "отец", "мать", "папа", "мама", "брат", "сестра", "сын", "дочь",
    "внук", "внучка", "дядя", "тётя", "тетя", "прадед", "прабабушка", "предок", "потомок",
    # Cultural & historical terms
    "совет", "ссср", "союз", "партия", "фронт", "война", "победа", "армия", "госпиталь",
    "колхоз", "совхоз", "завод", "школа", "институт", "университет", "район", "область",
    "ауыл", "аул", "күй", "домбыра", "домбра", "шапан", "бесік", "тұмар", "құран",
    "бог", "алла", "құдай", "жаратқан", "жаным", "ботам", "жарығым", "күнім",
    "батыр", "хан", "би", "болыс", "ақсақал", "ақын", "жырау",
    # Pronouns & Demonstratives (Kazakh & Russian)
    "бұл", "осы", "сол", "ол", "олар", "біз", "мен", "сен", "сіз", "бәрі", "барлығы",
    "он", "она", "оно", "они", "мы", "вы", "я", "ты", "это", "этот", "эта", "тот", "та",
    "все", "всё", "каждый", "никто", "ничто", "кто", "что", "где", "когда", "как",
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
    """Run all 8 deterministic verification gates on a chapter draft."""
    blockers: list[GateIssue] = []
    warnings: list[GateIssue] = []
    text = draft.text

    # Gate 1: NAMED_PERSON
    # Build anchor set of known names, aliases, and known places
    known_name_stems: set[str] = set()
    for p in snapshot.people:
        for name_str in [p.display_name, *p.aliases]:
            for part in re.findall(r"\w+", name_str.lower()):
                if len(part) >= 3:
                    known_name_stems.add(part)
    for place in snapshot.known_places:
        for part in re.findall(r"\w+", place.lower()):
            if len(part) >= 3:
                known_name_stems.add(part)

    # Search for capitalized tokens that are mid-sentence
    for m in re.finditer(r"\b([A-ZА-ЯЁӘҒҚҢӨҰҮҺІ][\w]{2,25})\b", text):
        tok = m.group(1)
        low = tok.lower()
        if low in _SOFT_TERMS:
            continue
        if any(stem in low for stem in known_name_stems):
            continue
        if _is_sentence_start(text, m.start()):
            continue

        # Found unanchored proper name mid-sentence
        blockers.append(
            GateIssue(
                code=GateCode.NAMED_PERSON,
                severity=IssueSeverity.BLOCKER,
                issue_type=GateCode.NAMED_PERSON.issue_type,
                detail=f"Proper name '{tok}' at position {m.start()} is not grounded in family archive.",
                location=f"offset {m.start()}",
                offending=[tok],
            )
        )

    # Gate 2: YEAR
    found_years = [int(y) for y in _YEAR_REGEX.findall(text)]
    allowed_years_set = set(snapshot.allowed_years)
    ungrounded_years = [str(y) for y in found_years if y not in allowed_years_set]
    if ungrounded_years:
        blockers.append(
            GateIssue(
                code=GateCode.YEAR,
                severity=IssueSeverity.BLOCKER,
                issue_type=GateCode.YEAR.issue_type,
                detail=f"Chapter contains ungrounded years: {sorted(set(ungrounded_years))}.",
                offending=sorted(set(ungrounded_years)),
            )
        )

    # Gate 3: RELATIONSHIP (heuristic check on unsupported kinship frames)
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

    for assertion in draft.relationship_assertions:
        s_id = assertion.subject_person_id
        o_id = assertion.object_person_id
        r_str = assertion.relation.strip().lower()
        r_norm = _NORM_RELATIONS.get(r_str, r_str)

        if s_id not in known_pids or o_id not in known_pids:
            blockers.append(
                GateIssue(
                    code=GateCode.RELATIONSHIP,
                    severity=IssueSeverity.BLOCKER,
                    issue_type=GateCode.RELATIONSHIP.issue_type,
                    detail=f"Relationship assertion refers to unknown person(s): ({s_id}, {assertion.relation}, {o_id})",
                    offending=[s_id, o_id],
                )
            )
            continue

        if (s_id, r_norm, o_id) not in grounded_relationships and (s_id, r_str, o_id) not in grounded_relationships:
            blockers.append(
                GateIssue(
                    code=GateCode.RELATIONSHIP,
                    severity=IssueSeverity.BLOCKER,
                    issue_type=GateCode.RELATIONSHIP.issue_type,
                    detail=(
                        f"Ungrounded relationship assertion: person '{s_id}' as '{assertion.relation}' of '{o_id}' "
                        "is not supported by family archive relationships."
                    ),
                    location=assertion.text_span or None,
                    offending=[s_id, assertion.relation, o_id],
                )
            )

    # Gate 4: CORRECTION (Forbidden original value)
    for cor in snapshot.corrections:
        wrong = cor.original_value.strip()
        if wrong and re.search(r"(?<![\w])" + re.escape(wrong) + r"(?![\w])", text, re.IGNORECASE):
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

    # Gate 5: QUOTE (Direct quotations must be verbatim substrings of evidence)
    evidence_blobs = [_norm(ev.text.lower()) for ev in snapshot.evidence]
    for q_match in _QUOTE_REGEX.finditer(text):
        quote_body = _norm(q_match.group(1).lower())
        if len(quote_body) < 8:
            continue
        # Check if verbatim in any evidence span
        is_verbatim = any(quote_body in blob for blob in evidence_blobs)
        if not is_verbatim:
            # Check if majority of quote tokens are grounded
            quote_tokens = quote_body.split()
            if len(quote_tokens) >= 3:
                blockers.append(
                    GateIssue(
                        code=GateCode.QUOTE,
                        severity=IssueSeverity.BLOCKER,
                        issue_type=GateCode.QUOTE.issue_type,
                        detail=f"Quotation «{q_match.group(1)[:60]}...» is not a verbatim evidence quote.",
                        location=f"offset {q_match.start()}",
                        offending=[q_match.group(1)],
                    )
                )

    # Gate 6: EVIDENCE_COVERAGE
    total_planned = len(chapter_plan.evidence_refs)
    if total_planned > 0:
        covered = sum(1 for eid in chapter_plan.evidence_refs if eid in draft.evidence_usage)
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
                    offending=list(set(chapter_plan.evidence_refs) - set(draft.evidence_usage)),
                )
            )
    else:
        coverage_ratio = 1.0

    # Gate 7: WORD_COUNT
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

    # Gate 8: LANGUAGE
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
