"""Independent deterministic inspection of Family Book chapter prose.

Writer-declared metadata is useful telemetry, but it is not a truth boundary.
This module derives factual candidates from the prose itself and matches only
against the immutable selected-source snapshot.
"""
# ruff: noqa: RUF001

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from mura.domain.book_models import BookSourceSnapshot


_CYR = "A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі"
_CAPITALIZED_TOKEN = re.compile(
    rf"(?<![\w-])([A-ZА-ЯЁӘҒҚҢӨҰҮҺІ][{_CYR}'’-]{{2,}})"
)

_NON_PERSON_WORDS = {
    "это", "этот", "эта", "тогда", "потом", "когда", "однажды", "сначала",
    "после", "перед", "домой", "семья", "война", "победа", "госпиталь",
    "үйде", "ауылдағы", "сол", "осы", "бұл", "кейін", "соғыс", "жеңіс",
    "атамыз", "әжеміз", "дедушка", "бабушка", "мама", "папа",
}

_PERSON_ACTION = re.compile(
    r"^(?:\s|[,—–-])*(?:"
    r"приехал(?:а)?|уехал(?:а)?|родил(?:ся|ась)|жил(?:а)?|работал(?:а)?|"
    r"сказал(?:а)?|вспомнил(?:а)?|был(?:а)?|стал(?:а)?|"
    r"келді|кетті|туды|тұрды|айтты|болды|еді"
    r")\b",
    re.IGNORECASE,
)

# High-signal factual predicates about a known person. These are intentionally
# narrower than natural language in general: the goal is to close obvious
# invented biographical/scene facts deterministically without pretending to be
# a full semantic theorem prover.
_FACTUAL_PERSON_VERB = re.compile(
    r"\b(?:"
    r"был(?:а|и)?|стал(?:а|и)?|жил(?:а|и)?|работал(?:а|и)?|"
    r"родил(?:ся|ась)|учил(?:ся|ась)|служил(?:а)?|любил(?:а)?|"
    r"переехал(?:а)?|приехал(?:а)?|уехал(?:а)?|окончил(?:а)?|"
    r"тұрды|жұмыс\s+істеді|туды|оқыды|қызмет\s+етті|жақсы\s+көрді|"
    r"көшті|келді|кетті|болды|еді"
    r")\b",
    re.IGNORECASE,
)

_SENTENCE = re.compile(r"[^.!?…\n]+(?:[.!?…]+|$)", re.UNICODE)

_LOCATION_PREP = re.compile(
    rf"\b(?i:в|во|из|из\s+города|в\s+городе|в\s+селе|в\s+ауле|"
    rf"қаласында|ауылында)\s+"
    rf"([A-ZА-ЯЁӘҒҚҢӨҰҮҺІ][{_CYR}'’-]{{2,}})"
)

_RU_RELATION = re.compile(
    rf"\b(?P<subject>[A-ZА-ЯЁӘҒҚҢӨҰҮҺІ][{_CYR}'’-]{{2,}})\s+"
    rf"(?i:был(?:а)?|являл(?:ся|ась)|приходил(?:ся|ась))\s+"
    rf"(?P<relation>(?i:братом|сестрой|отцом|матерью|сыном|дочерью|мужем|женой|тётей|тетей|дядей))\s+"
    rf"(?P<object>[A-ZА-ЯЁӘҒҚҢӨҰҮҺІ][{_CYR}'’-]{{2,}})"
)
_RU_PRONOUN_RELATION = re.compile(
    rf"\b(?i:его|её|ее|ему|ей)\s+"
    rf"(?P<relation>(?i:брат|сестра|отец|мать|сын|дочь|тётя|тетя|дядя))\s+"
    rf"(?P<person>[A-ZА-ЯЁӘҒҚҢӨҰҮҺІ][{_CYR}'’-]{{2,}})"
)
_RU_REVERSE_PRONOUN = re.compile(
    rf"\b(?P<person>[A-ZА-ЯЁӘҒҚҢӨҰҮҺІ][{_CYR}'’-]{{2,}})\s+"
    rf"(?i:приходил(?:ся|ась))\s+(?i:ему|ей)\s+"
    rf"(?P<relation>(?i:братом|сестрой|отцом|матерью|сыном|дочерью|тётей|тетей|дядей))"
)
_KZ_RELATION = re.compile(
    rf"\b(?P<object>[A-ZА-ЯЁӘҒҚҢӨҰҮҺІ][{_CYR}'’-]{{2,}}?)(?:ның|нің|дың|дің|тың|тің)\s+"
    rf"(?P<relation>(?i:ағасы|інісі|әпкесі|сіңлісі|қарындасы|әкесі|анасы|шешесі|ұлы|қызы))\s+"
    rf"(?P<subject>[A-ZА-ЯЁӘҒҚҢӨҰҮҺІ][{_CYR}'’-]{{2,}})"
)

_PAIR_QUOTE = re.compile(r"[«“„\"]([^»”“\n]{5,})[»”\"]")
_DASH_QUOTE = re.compile(
    r"(?m)(?:^|\n|:\s*)[ \t]*[—–]\s*([^\n—–]{5,}?)(?=\s*,\s*[—–]|\s*$)"
)

_FOUR_DIGIT_YEAR = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")
_SHORT_YEAR = re.compile(r"\bв\s+(\d{2})(?:\s*-?\s*(?:м|ом))?\s+году\b", re.IGNORECASE)

_RU_20S_ORDINAL = {
    "перв": 21,
    "втор": 22,
    "трет": 23,
    "четверт": 24,
    "пят": 25,
    "шест": 26,
    "седьм": 27,
    "восьм": 28,
    "девят": 29,
}

_RU_DECADE_WORDS = {
    "двадцат": 20,
    "тридцат": 30,
    "сороков": 40,
    "пятидесят": 50,
    "шестидесят": 60,
    "семидесят": 70,
    "восьмидесят": 80,
    "девяност": 90,
}


@dataclass(frozen=True)
class ProseRelationship:
    subject_person_id: str | None
    relation: str
    object_person_id: str | None
    text_span: str
    unresolved_surfaces: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProseGroundingAnalysis:
    unknown_people: tuple[str, ...]
    locations: tuple[str, ...]
    relationships: tuple[ProseRelationship, ...]
    years: tuple[int, ...]
    ambiguous_short_years: tuple[str, ...]
    direct_speech: tuple[str, ...]
    actual_evidence_ids: tuple[str, ...]
    unsupported_factual_clauses: tuple[str, ...]


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    normalized = re.sub(r"[^\w\s-]+", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.replace("_", " ").split())


def _stem_token(value: str) -> str:
    token = normalize_text(value)
    if len(token) <= 4:
        return token
    return token[: max(4, min(6, len(token)))]


def _person_forms(snapshot: BookSourceSnapshot) -> dict[str, set[str]]:
    forms: dict[str, set[str]] = {}
    for person in snapshot.people:
        values = [person.display_name, *person.aliases]
        person_forms: set[str] = set()
        for value in values:
            normalized = normalize_text(value)
            if normalized:
                person_forms.add(normalized)
                person_forms.update(
                    _stem_token(part) for part in normalized.split() if len(part) >= 3
                )
        forms[person.person_id] = person_forms
    return forms


def resolve_person_surface(
    surface: str,
    snapshot: BookSourceSnapshot,
) -> str | None:
    normalized = normalize_text(surface)
    stem = _stem_token(normalized)
    matches: list[str] = []
    for person_id, forms in _person_forms(snapshot).items():
        if normalized in forms or stem in forms:
            matches.append(person_id)
            continue
        if any(
            len(form) >= 4
            and (
                normalized.startswith(form)
                or form.startswith(normalized)
                or _stem_token(form) == stem
            )
            for form in forms
        ):
            matches.append(person_id)
    unique = sorted(set(matches))
    return unique[0] if len(unique) == 1 else None


def _place_matches(surface: str, known_places: list[str]) -> bool:
    normalized = normalize_text(surface)
    stem = _stem_token(normalized)
    for place in known_places:
        pnorm = normalize_text(place)
        if normalized == pnorm:
            return True
        if len(pnorm) >= 4 and _stem_token(pnorm) == stem:
            return True
    return False


def extract_locations(text: str, snapshot: BookSourceSnapshot) -> tuple[str, ...]:
    unknown: list[str] = []
    for match in _LOCATION_PREP.finditer(text):
        surface = match.group(1)
        if not _place_matches(surface, snapshot.known_places):
            unknown.append(surface)
    return tuple(dict.fromkeys(unknown))


def _sentence_start(text: str, offset: int) -> bool:
    prefix = text[:offset]
    return not prefix.strip() or bool(re.search(r"[.!?…]\s*$|\n\s*$", prefix))


def extract_unknown_people(
    text: str,
    snapshot: BookSourceSnapshot,
    *,
    unknown_locations: tuple[str, ...] = (),
) -> tuple[str, ...]:
    unknown_location_norm = {normalize_text(value) for value in unknown_locations}
    unknown: list[str] = []
    for match in _CAPITALIZED_TOKEN.finditer(text):
        surface = match.group(1)
        normalized = normalize_text(surface)
        if normalized in _NON_PERSON_WORDS:
            continue
        if normalized in unknown_location_norm or _place_matches(surface, snapshot.known_places):
            continue
        if resolve_person_surface(surface, snapshot) is not None:
            continue

        if _sentence_start(text, match.start()):
            tail = text[match.end() : match.end() + 40]
            if not _PERSON_ACTION.search(tail):
                # Sentence-initial capitalization alone is not enough to call
                # an arbitrary literary word a person.
                continue
        unknown.append(surface)
    return tuple(dict.fromkeys(unknown))


def _relation_kind(surface: str) -> str:
    value = normalize_text(surface)
    if value.startswith(("брат", "сестр", "тет", "тёт", "дяд", "аға", "іні", "әпке", "сіңлі", "қарында")):
        return "sibling"
    if value.startswith(("отц", "мат", "әк", "ан", "шеш")):
        return "parent"
    if value.startswith(("сын", "доч", "ұл", "қыз")):
        return "child"
    if value.startswith(("муж", "жен", "күйеу", "әйел")):
        return "spouse"
    return value


def extract_relationships(
    text: str,
    snapshot: BookSourceSnapshot,
) -> tuple[ProseRelationship, ...]:
    found: list[ProseRelationship] = []

    for match in _RU_RELATION.finditer(text):
        subject_surface = match.group("subject")
        object_surface = match.group("object")
        subject = resolve_person_surface(subject_surface, snapshot)
        obj = resolve_person_surface(object_surface, snapshot)
        unresolved = tuple(
            surface
            for surface, person_id in (
                (subject_surface, subject),
                (object_surface, obj),
            )
            if person_id is None
        )
        found.append(
            ProseRelationship(
                subject_person_id=subject,
                relation=_relation_kind(match.group("relation")),
                object_person_id=obj,
                text_span=match.group(0),
                unresolved_surfaces=unresolved,
            )
        )

    for match in _KZ_RELATION.finditer(text):
        subject_surface = match.group("subject")
        object_surface = match.group("object")
        subject = resolve_person_surface(subject_surface, snapshot)
        obj = resolve_person_surface(object_surface, snapshot)
        unresolved = tuple(
            surface
            for surface, person_id in (
                (subject_surface, subject),
                (object_surface, obj),
            )
            if person_id is None
        )
        found.append(
            ProseRelationship(
                subject_person_id=subject,
                relation=_relation_kind(match.group("relation")),
                object_person_id=obj,
                text_span=match.group(0),
                unresolved_surfaces=unresolved,
            )
        )

    for regex in (_RU_PRONOUN_RELATION, _RU_REVERSE_PRONOUN):
        for match in regex.finditer(text):
            person_surface = match.group("person")
            person_id = resolve_person_surface(person_surface, snapshot)
            unresolved = [surface for surface in [person_surface] if person_id is None]
            unresolved.append("<coreference>")
            found.append(
                ProseRelationship(
                    subject_person_id=person_id,
                    relation=_relation_kind(match.group("relation")),
                    object_person_id=None,
                    text_span=match.group(0),
                    unresolved_surfaces=tuple(unresolved),
                )
            )

    return tuple(found)


def extract_years(
    text: str,
    *,
    allowed_years: list[int],
) -> tuple[tuple[int, ...], tuple[str, ...]]:
    years = [int(value) for value in _FOUR_DIGIT_YEAR.findall(text)]
    ambiguous: list[str] = []

    for match in _SHORT_YEAR.finditer(text):
        short = int(match.group(1))
        matches = [year for year in allowed_years if year % 100 == short]
        if len(matches) == 1:
            years.append(matches[0])
        elif len(matches) == 0:
            # Preserve a deterministic representative so the normal year gate
            # can reject the unsupported short form.
            years.append(1900 + short if short >= 30 else 2000 + short)
        else:
            ambiguous.append(match.group(0))

    folded = normalize_text(text)
    if "двадцать" in folded:
        for stem, short in _RU_20S_ORDINAL.items():
            if re.search(rf"\bдвадцать\s+{stem}\w*", folded):
                matches = [year for year in allowed_years if year % 100 == short]
                if len(matches) == 1:
                    years.append(matches[0])
                elif len(matches) == 0:
                    years.append(2000 + short)
                else:
                    ambiguous.append(f"двадцать {stem}...")

    # Decade expressions are intentionally coarser than years. Accept one only
    # when the frozen snapshot makes its century/decade unambiguous; otherwise
    # fail closed rather than guessing "1940s" from "сороковых".
    allowed_decades = {year // 10 * 10 for year in allowed_years}
    for stem, short_decade in _RU_DECADE_WORDS.items():
        match = re.search(
            rf"\b(?:в\s+)?(?:начале\s+|середине\s+|конце\s+)?{stem}\w*\b",
            folded,
        )
        if not match:
            continue
        matching_decades = sorted(
            decade for decade in allowed_decades if decade % 100 == short_decade
        )
        if len(matching_decades) != 1:
            ambiguous.append(match.group(0))

    return tuple(years), tuple(dict.fromkeys(ambiguous))


def rejected_year_patterns(year: int) -> tuple[re.Pattern[str], ...]:
    short = year % 100
    patterns = [
        re.compile(rf"(?<!\d){year}(?!\d)", re.IGNORECASE),
        re.compile(
            rf"\bв\s+{short:02d}(?:\s*-?\s*(?:м|ом))?\s+году\b",
            re.IGNORECASE,
        ),
    ]
    if 21 <= short <= 29:
        stem = next((key for key, value in _RU_20S_ORDINAL.items() if value == short), None)
        if stem:
            patterns.append(
                re.compile(rf"\b(?:в\s+)?двадцать\s+{stem}\w*(?:\s+году)?\b", re.IGNORECASE)
            )
    return tuple(patterns)


def extract_direct_speech(text: str) -> tuple[str, ...]:
    values: list[str] = []
    for regex in (_PAIR_QUOTE, _DASH_QUOTE):
        for match in regex.finditer(text):
            quote = " ".join(match.group(1).split()).strip(" ,;:—–-")
            if len(quote) >= 5:
                values.append(quote)
    return tuple(dict.fromkeys(values))


_EVIDENCE_STOPWORDS = {
    "и", "в", "во", "на", "с", "со", "к", "по", "что", "это", "он", "она",
    "мы", "я", "the", "a", "an", "and", "to", "of", "бұл", "сол", "ол", "мен",
}


def _content_stems(text: str) -> set[str]:
    tokens = re.findall(r"[\w-]+", normalize_text(text), flags=re.UNICODE)
    return {
        _stem_token(token)
        for token in tokens
        if len(token) >= 3 and token not in _EVIDENCE_STOPWORDS
    }


def _known_person_ids_in_sentence(
    sentence: str,
    snapshot: BookSourceSnapshot,
) -> set[str]:
    found: set[str] = set()
    for match in _CAPITALIZED_TOKEN.finditer(sentence):
        person_id = resolve_person_surface(match.group(1), snapshot)
        if person_id is not None:
            found.add(person_id)
    return found


def _factual_support_blobs(snapshot: BookSourceSnapshot) -> tuple[str, ...]:
    values: list[str] = [item.text for item in snapshot.evidence if item.text.strip()]
    values.extend(
        claim.summary
        for claim in snapshot.claims
        if isinstance(claim.summary, str) and claim.summary.strip()
    )
    values.extend(
        story.summary
        for story in snapshot.stories
        if isinstance(story.summary, str) and story.summary.strip()
    )
    values.extend(
        event.description
        for event in snapshot.events
        if isinstance(event.description, str) and event.description.strip()
    )
    return tuple(dict.fromkeys(values))


def _clause_supported_by_selected_sources(
    clause: str,
    support_blobs: tuple[str, ...],
) -> bool:
    normalized_clause = normalize_text(clause)
    if not normalized_clause:
        return True

    clause_stems = _content_stems(clause)
    if not clause_stems:
        return True

    for blob in support_blobs:
        normalized_blob = normalize_text(blob)
        if normalized_clause in normalized_blob or normalized_blob in normalized_clause:
            return True
        blob_stems = _content_stems(blob)
        if not blob_stems:
            continue
        overlap = len(clause_stems & blob_stems)
        # Require at least two independent content stems and roughly half of a
        # short factual clause. This accepts harmless inflection/paraphrase such
        # as "был врачом" vs "работал врачом" but rejects unrelated biography.
        required = max(2, min(4, (len(clause_stems) + 1) // 2))
        if overlap >= required:
            return True
    return False


def extract_unsupported_factual_clauses(
    text: str,
    snapshot: BookSourceSnapshot,
) -> tuple[str, ...]:
    """Find obvious known-person factual clauses with no selected-source support.

    This is deliberately conservative and high-signal. It does not claim to
    extract every proposition from literary prose; it blocks common biographical
    assertions that would otherwise bypass the structured gates simply because
    the person name itself is known.
    """

    support_blobs = _factual_support_blobs(snapshot)
    unsupported: list[str] = []
    for match in _SENTENCE.finditer(text):
        sentence = " ".join(match.group(0).split()).strip()
        if not sentence or not _FACTUAL_PERSON_VERB.search(sentence):
            continue
        if not _known_person_ids_in_sentence(sentence, snapshot):
            continue
        if not _clause_supported_by_selected_sources(sentence, support_blobs):
            unsupported.append(sentence)
    return tuple(dict.fromkeys(unsupported))


def evidence_refs_used_by_prose(
    text: str,
    snapshot: BookSourceSnapshot,
    *,
    candidate_ids: list[str],
) -> tuple[str, ...]:
    text_stems = _content_stems(text)
    normalized_text = normalize_text(text)
    used: list[str] = []
    by_id = {item.evidence_id: item for item in snapshot.evidence}
    for evidence_id in candidate_ids:
        evidence = by_id.get(evidence_id)
        if evidence is None:
            continue
        normalized_evidence = normalize_text(evidence.text)
        if normalized_evidence and normalized_evidence in normalized_text:
            used.append(evidence_id)
            continue
        stems = _content_stems(evidence.text)
        if not stems:
            continue
        overlap = len(stems & text_stems)
        required = min(5, max(3, len(stems) // 3))
        if overlap >= required:
            used.append(evidence_id)
    return tuple(used)


def analyze_prose(
    text: str,
    snapshot: BookSourceSnapshot,
    *,
    planned_evidence_ids: list[str],
) -> ProseGroundingAnalysis:
    locations = extract_locations(text, snapshot)
    unknown_people = extract_unknown_people(
        text,
        snapshot,
        unknown_locations=locations,
    )
    years, ambiguous = extract_years(text, allowed_years=snapshot.allowed_years)
    return ProseGroundingAnalysis(
        unknown_people=unknown_people,
        locations=locations,
        relationships=extract_relationships(text, snapshot),
        years=years,
        ambiguous_short_years=ambiguous,
        direct_speech=extract_direct_speech(text),
        actual_evidence_ids=evidence_refs_used_by_prose(
            text,
            snapshot,
            candidate_ids=planned_evidence_ids,
        ),
        unsupported_factual_clauses=extract_unsupported_factual_clauses(
            text,
            snapshot,
        ),
    )
