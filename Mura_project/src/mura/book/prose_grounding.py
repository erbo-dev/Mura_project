"""Independent deterministic inspection of Family Book chapter prose.

Writer-declared metadata is useful telemetry, but it is not a truth boundary.
This module derives factual candidates from the prose itself and matches only
against the immutable selected-source snapshot.
"""
# ruff: noqa: RUF001

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from mura.domain.book_models import BookSourceSnapshot

_CYR = "A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі"
_CAPITALIZED_TOKEN = re.compile(rf"(?<![\w-])([A-ZА-ЯЁӘҒҚҢӨҰҮҺІ][{_CYR}'’-]{{2,}})")

_NON_PERSON_WORDS = {
    "это",
    "этот",
    "эта",
    "тогда",
    "потом",
    "когда",
    "однажды",
    "сначала",
    "после",
    "перед",
    "домой",
    "семья",
    "война",
    "победа",
    "госпиталь",
    "дом",
    "город",
    "деревня",
    "село",
    "улица",
    "фотография",
    "фото",
    "письмо",
    "день",
    "вечер",
    "утро",
    "ночь",
    "год",
    "жизнь",
    "работа",
    "школа",
    "история",
    "источники",
    "источник",
    "үйде",
    "ауылдағы",
    "сол",
    "осы",
    "бұл",
    "кейін",
    "соғыс",
    "жеңіс",
    "үй",
    "қала",
    "ауыл",
    "көше",
    "сурет",
    "хат",
    "күн",
    "кеш",
    "таң",
    "түн",
    "жыл",
    "өмір",
    "жұмыс",
    "мектеп",
    "атамыз",
    "әжеміз",
    "дедушка",
    "бабушка",
    "мама",
    "папа",
}

_PERSON_ACTION = re.compile(
    r"^(?:\s|[,—–-])*(?:"
    r"приехал(?:а)?|уехал(?:а)?|родил(?:ся|ась)|жил(?:а)?|работал(?:а)?|"
    r"сказал(?:а)?|вспомнил(?:а)?|был(?:а)?|стал(?:а)?|"
    r"келді|кетті|туды|тұрды|айтты|болды|еді"
    r")\b",
    re.IGNORECASE,
)
_LOWER_WORD = re.compile(r"[а-яёәғқңөұүһі-]+", re.IGNORECASE)
_RU_ENTITY_VERB_ENDING = re.compile(
    r"(?:лся|лась|лись|ил|ила|или|ыл|ыла|ыли|ал|ала|али|ял|яла|яли|"
    r"ел|ела|ели|ул|ула|ули|нул|нула|нули|овал|овала|овали|"
    r"ивал|ивала|ивали)$",
    re.IGNORECASE,
)
_KK_ENTITY_VERB_ENDING = re.compile(
    r"(?:ды|ді|ты|ті|ған|ген|қан|кен|ды|ді|атын|етін|йтын|йтін)$",
    re.IGNORECASE,
)


def _sentence_start_entity_predicate(tail: str) -> bool:
    if _PERSON_ACTION.search(tail):
        return True
    # A short adverb ("долго", "тихо", "сразу") may sit between the
    # subject and predicate, so inspect a bounded local window rather than one
    # hard-coded next word.
    for token in _LOWER_WORD.findall(tail)[:4]:
        if _RU_ENTITY_VERB_ENDING.search(token) or _KK_ENTITY_VERB_ENDING.search(token):
            return True
    return False


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
_KZ_EXTENDED_RELATION = re.compile(
    rf"\b(?P<object>[A-ZА-ЯЁӘҒҚҢӨҰҮҺІ][{_CYR}'’-]{{2,}}?)(?:ның|нің|дың|дің|тың|тің)\s+"
    rf"(?P<relation>(?i:тәтесі|нағашы\s+апасы|нағашы\s+ағасы))\s+"
    rf"(?P<subject>[A-ZА-ЯЁӘҒҚҢӨҰҮҺІ][{_CYR}'’-]{{2,}})"
)

_PAIR_QUOTES = (
    re.compile(r"«([^»\n]{5,})»"),
    re.compile(r"“([^”\n]{5,})”"),
    re.compile(r"„([^“\n]{5,})“"),
    re.compile(r"\"([^\"\n]{5,})\""),
)
_DASH_QUOTE = re.compile(r"(?m)(?:^|\n|:\s*)[ \t]*[—–]\s*([^\n—–]{5,}?)(?=\s*,\s*[—–]|\s*$)")

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
            tail = text[match.end() : match.end() + 48]
            if not _sentence_start_entity_predicate(tail):
                # Capitalization alone is not enough. But an unknown
                # sentence-initial token acting as the grammatical subject of
                # a finite narrative verb is treated as a possible person and
                # fails closed, independent of a tiny verb allowlist.
                continue
        unknown.append(surface)
    return tuple(dict.fromkeys(unknown))


def _relation_kind(surface: str) -> str:
    value = normalize_text(surface)
    if value.startswith(("тет", "тёт", "дяд", "тәте", "нағашы")):
        # Aunt/uncle is not a direct sibling relation to the niece/nephew.
        # The current canonical graph has no aunt/uncle edge type, so prose
        # using it must fail closed unless a future deterministic path prover
        # explicitly establishes that derived kinship.
        return "aunt_uncle"
    if value.startswith(("брат", "сестр", "аға", "іні", "әпке", "сіңлі", "қарында")):
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

    for match in list(_KZ_RELATION.finditer(text)) + list(_KZ_EXTENDED_RELATION.finditer(text)):
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
            unresolved_surfaces = [surface for surface in [person_surface] if person_id is None]
            unresolved_surfaces.append("<coreference>")
            found.append(
                ProseRelationship(
                    subject_person_id=person_id,
                    relation=_relation_kind(match.group("relation")),
                    object_person_id=None,
                    text_span=match.group(0),
                    unresolved_surfaces=tuple(unresolved_surfaces),
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
        decade_match = re.search(
            rf"\b(?:в\s+)?(?:начале\s+|середине\s+|конце\s+)?{stem}\w*\b",
            folded,
        )
        if not decade_match:
            continue
        matching_decades = sorted(
            decade for decade in allowed_decades if decade % 100 == short_decade
        )
        if len(matching_decades) != 1:
            ambiguous.append(decade_match.group(0))

    return tuple(years), tuple(dict.fromkeys(ambiguous))


_RU_SMALL_NUMBERS = {
    "ноль": 0,
    "один": 1,
    "одна": 1,
    "два": 2,
    "две": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
    "тринадцать": 13,
    "четырнадцать": 14,
    "пятнадцать": 15,
    "шестнадцать": 16,
    "семнадцать": 17,
    "восемнадцать": 18,
    "девятнадцать": 19,
    "двадцать": 20,
}
_KK_SMALL_NUMBERS = {
    "нөл": 0,
    "бір": 1,
    "екі": 2,
    "үш": 3,
    "төрт": 4,
    "бес": 5,
    "алты": 6,
    "жеті": 7,
    "сегіз": 8,
    "тоғыз": 9,
    "он": 10,
    "он бір": 11,
    "он екі": 12,
    "он үш": 13,
    "он төрт": 14,
    "он бес": 15,
    "он алты": 16,
    "он жеті": 17,
    "он сегіз": 18,
    "он тоғыз": 19,
    "жиырма": 20,
}
_NUMBER_WORDS = {**_RU_SMALL_NUMBERS, **_KK_SMALL_NUMBERS}
_NUMBER_TO_WORDS: dict[int, set[str]] = {}
for _word, _number in _NUMBER_WORDS.items():
    _NUMBER_TO_WORDS.setdefault(_number, set()).add(_word)


def normalize_correction_text(value: str) -> str:
    normalized = normalize_text(value)
    normalized = normalized.replace("-", " ").replace("–", " ").replace("—", " ")
    return " ".join(normalized.split())


def correction_value_variants(value: str) -> tuple[str, ...]:
    """Deterministic surface variants for simple names/places and small numbers."""

    base = normalize_correction_text(value)
    if not base:
        return ()

    variants: set[str] = {base}
    compact_number = re.fullmatch(r"(\d{1,2})(?:\s+(?:лет|год(?:а|у)?|жас))?", base)
    if compact_number is not None:
        number = int(compact_number.group(1))
        variants.add(str(number))
        variants.update(_NUMBER_TO_WORDS.get(number, set()))

    for word, number in _NUMBER_WORDS.items():
        if base == word or base.startswith(f"{word} "):
            variants.add(str(number))
            variants.add(word)

    return tuple(sorted(variants, key=lambda item: (-len(item), item)))


def contains_rejected_correction(text: str, original_value: str) -> bool:
    prose = f" {normalize_correction_text(text)} "
    for variant in correction_value_variants(original_value):
        if re.search(r"(?<![\w])" + re.escape(variant) + r"(?![\w])", prose):
            return True
    return False


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
    for regex in (*_PAIR_QUOTES, _DASH_QUOTE):
        for match in regex.finditer(text):
            quote = " ".join(match.group(1).split()).strip(" ,;:—–-")
            if len(quote) >= 5:
                values.append(quote)
    return tuple(dict.fromkeys(values))


_EVIDENCE_STOPWORDS = {
    "и",
    "в",
    "во",
    "на",
    "с",
    "со",
    "к",
    "по",
    "что",
    "это",
    "он",
    "она",
    "мы",
    "я",
    "the",
    "a",
    "an",
    "and",
    "to",
    "of",
    "бұл",
    "сол",
    "ол",
    "мен",
}

_NEGATION_MARKERS = (" не ", " никогда ", " емес ", " ешқашан ", " never ", " not ")
_BEFORE_MARKERS = (" до ", " перед ", " раньше ", " дейін ", " бұрын ", " before ", " earlier ")
_AFTER_MARKERS = (" после ", " позже ", " кейін ", " соң ", " after ", " later ")
_OLDER_MARKERS = (" старш", " аға", " әпке", " older ")
_YOUNGER_MARKERS = (" младш", " іні", " сіңлі", " қарында", " younger ")
_PARENT_MARKERS = (" отец", " мать", " пап", " мам", " әке", " ана", " шеше", " father", " mother")
_CHILD_MARKERS = (" сын", " дочь", " ұл", " қыз", " son", " daughter")


def _content_stems(text: str) -> set[str]:
    tokens = re.findall(r"[\w-]+", normalize_text(text), flags=re.UNICODE)
    return {
        _stem_token(token)
        for token in tokens
        if len(token) >= 3 and token not in _EVIDENCE_STOPWORDS
    }


def _contains_marker(normalized: str, markers: tuple[str, ...]) -> bool:
    padded = f" {normalized} "
    return any(marker in padded for marker in markers)


def _semantic_signature(text: str) -> dict[str, str | bool | None]:
    normalized = normalize_text(text)
    negated = _contains_marker(normalized, _NEGATION_MARKERS)

    temporal: str | None = None
    if _contains_marker(normalized, _BEFORE_MARKERS):
        temporal = "before"
    if _contains_marker(normalized, _AFTER_MARKERS):
        temporal = "after" if temporal is None else "conflicting"

    sibling_order: str | None = None
    if _contains_marker(normalized, _OLDER_MARKERS):
        sibling_order = "older"
    if _contains_marker(normalized, _YOUNGER_MARKERS):
        sibling_order = "younger" if sibling_order is None else "conflicting"

    kinship_role: str | None = None
    if _contains_marker(normalized, _PARENT_MARKERS):
        kinship_role = "parent"
    if _contains_marker(normalized, _CHILD_MARKERS):
        kinship_role = "child" if kinship_role is None else "conflicting"

    return {
        "negated": negated,
        "temporal": temporal,
        "sibling_order": sibling_order,
        "kinship_role": kinship_role,
    }


def _semantically_compatible(assertion: str, support: str) -> bool:
    """Reject obvious polarity/order/kinship inversions before lexical matching."""

    left = _semantic_signature(assertion)
    right = _semantic_signature(support)

    if left["negated"] != right["negated"]:
        return False

    for key in ("temporal", "sibling_order", "kinship_role"):
        assertion_value = left[key]
        support_value = right[key]
        if assertion_value == "conflicting" or support_value == "conflicting":
            return False
        # Adding a modifier not present in the source is unsupported
        # specificity; a source may safely be more specific than the prose.
        if assertion_value is not None and assertion_value != support_value:
            return False
    return True


def _person_anchor_stems(snapshot: BookSourceSnapshot) -> set[str]:
    anchors: set[str] = set()
    for forms in _person_forms(snapshot).values():
        for form in forms:
            anchors.update(_stem_token(part) for part in form.split() if len(part) >= 3)
    return anchors


def _high_signal_stems(text: str, *, ignored: set[str]) -> set[str]:
    result: set[str] = set()
    for token in re.findall(r"[\w-]+", normalize_text(text), flags=re.UNICODE):
        if len(token) < 3 or token in _EVIDENCE_STOPWORDS or token.isdigit():
            continue
        stem = _stem_token(token)
        if stem not in ignored:
            result.add(stem)
    return result


_PROFESSION_FRAME = re.compile(
    r"\b(?:был(?:а)?|работал(?:а)?|стал(?:а)?|жұмыс\s+істеді|болды|еді)\s+([\w-]{4,})",
    re.IGNORECASE,
)


def _matching_profession_complement(left: str, right: str) -> bool:
    left_match = _PROFESSION_FRAME.search(normalize_text(left))
    right_match = _PROFESSION_FRAME.search(normalize_text(right))
    if left_match is None or right_match is None:
        return False
    return _stem_token(left_match.group(1)) == _stem_token(right_match.group(1))


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
    snapshot: BookSourceSnapshot,
) -> bool:
    normalized_clause = normalize_text(clause)
    if not normalized_clause:
        return True

    ignored = _person_anchor_stems(snapshot)
    clause_stems = _high_signal_stems(clause, ignored=ignored)
    if not clause_stems:
        return True

    for blob in support_blobs:
        if not _semantically_compatible(clause, blob):
            continue
        normalized_blob = normalize_text(blob)
        if normalized_clause in normalized_blob or normalized_blob in normalized_clause:
            return True
        if _matching_profession_complement(clause, blob):
            return True
        blob_stems = _high_signal_stems(blob, ignored=ignored)
        if not blob_stems:
            continue
        overlap = len(clause_stems & blob_stems)
        # Names and bare years are removed above; two shared substantive stems
        # are required for generic lexical support.
        if overlap >= 2:
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
        if not _clause_supported_by_selected_sources(
            sentence,
            support_blobs,
            snapshot,
        ):
            unsupported.append(sentence)
    return tuple(dict.fromkeys(unsupported))


def evidence_refs_used_by_prose(
    text: str,
    snapshot: BookSourceSnapshot,
    *,
    candidate_ids: list[str],
) -> tuple[str, ...]:
    normalized_text = normalize_text(text)
    ignored = _person_anchor_stems(snapshot)
    sentences = [
        " ".join(match.group(0).split()).strip()
        for match in _SENTENCE.finditer(text)
        if match.group(0).strip()
    ]
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

        evidence_stems = _high_signal_stems(evidence.text, ignored=ignored)
        if len(evidence_stems) < 2:
            # A family name, year, or one generic content word is not proof
            # that this evidence was actually incorporated.
            continue

        for sentence in sentences:
            if not _semantically_compatible(sentence, evidence.text):
                continue
            sentence_stems = _high_signal_stems(sentence, ignored=ignored)
            overlap = len(evidence_stems & sentence_stems)
            required = max(2, min(4, max(2, len(evidence_stems) // 3)))
            if overlap >= required:
                used.append(evidence_id)
                break
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
