"""Inflection tolerance in evidence support, and the facts it must still refuse.

Russian declines nouns and Kazakh stacks suffixes, so requiring identical word
forms rejected faithful retellings and left longer recordings without stories.
The tolerance is only worth having if every kind of invented fact the check
exists to stop is still stopped, so both sides are pinned here.
"""

from __future__ import annotations

import pytest

from mura.factual_support import FactualSupportStatus, evaluate_factual_support
from mura.linguistics.morphology import same_word


@pytest.mark.parametrize(
    ("statement", "evidence"),
    [
        # Instrumental in the transcript, nominative in the retelling.
        ("Отец Асан был учитель", "Её отец Асан был учителем в школе."),
        # Kazakh first-person possessive retold as third-person possessive.
        ("Әжесі Алматыда тұрды", "Менің әжем Алматыда тұрды."),
        # Kazakh locative dropped in the retelling.
        ("Әжем Алматы тұрды", "Менің әжем Алматыда тұрды."),
        # Past tense agreeing with a different subject.
        ("Бабушка жила в Алматы", "Бабушка с дедушкой жили в Алматы."),
    ],
)
def test_a_declined_retelling_is_supported(statement: str, evidence: str) -> None:
    assert evaluate_factual_support(statement, evidence).supported


@pytest.mark.parametrize(
    ("statement", "evidence", "why"),
    [
        ("Ерлан был учителем", "Асан был учителем.", "a different person"),
        ("Марал работал на заводе", "Марат работал на заводе.", "a name one letter away"),
        ("Асанов был учителем", "Асан был учителем.", "a surname invented from a first name"),
        ("Мария работала врачом", "Марина работала врачом.", "two different women"),
        ("Они поженились в 1961 году", "Они поженились в 1960 году.", "a different year"),
        ("Бабушка жила в Шымкенте", "Бабушка жила в Алматы.", "a different place"),
    ],
)
def test_an_invented_fact_is_still_unsupported(statement: str, evidence: str, why: str) -> None:
    assert not evaluate_factual_support(statement, evidence).supported, why


def test_a_dropped_negation_is_still_rejected() -> None:
    result = evaluate_factual_support("Асан вернулся с фронта", "Асан не вернулся с фронта.")
    assert result.status is FactualSupportStatus.DROPS_NEGATION


def test_added_causality_is_still_rejected() -> None:
    result = evaluate_factual_support(
        "Асан ушёл на фронт потому что его призвали", "Асан ушёл на фронт, его призвали."
    )
    assert result.status is FactualSupportStatus.ADDS_CAUSALITY


def test_swapped_roles_are_still_rejected_through_declension() -> None:
    # Declension must not let who-did-what-to-whom be reversed.
    result = evaluate_factual_support(
        "Гульнара вышла замуж за Марата", "Марат женился на Гульнаре, это было давно."
    )
    assert not result.supported


@pytest.mark.parametrize(
    ("left", "right"),
    [("марат", "марал"), ("асан", "асанов"), ("марина", "мария"), ("1960", "1961")],
)
def test_distinct_words_never_share_a_stem(left: str, right: str) -> None:
    assert not same_word(left, right)
