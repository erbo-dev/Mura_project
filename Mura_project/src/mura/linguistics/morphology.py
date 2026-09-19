"""Inflection-tolerant word matching for Russian and Kazakh.

Evidence checks used to require every significant word of a statement to occur
in the transcript in exactly the same form. Russian declines nouns and Kazakh
stacks case and possessive suffixes, so a faithful retelling of «Асан ушёл на
фронт» as «письма Асана», or of «Менің әжем Алматыда тұрды» as «Әжесі Алматыда
тұрды», was rejected as unsupported. Short recordings passed because the model
copied them almost verbatim; anything longer was condensed and lost its stories.

This module decides only whether two surface forms are the same word. It is
deliberately narrow, because the check it serves is what keeps invented family
facts out of the archive:

* Only declension, possessive, plural and reflexive endings are removed. Verb
  endings such as «-ат» and «-ал» are not: stripping them would collapse
  «Марат» and «Марал» into one stem and let an invented name through.
* Surname-forming endings («-ов», «-ев», «-ин») are never removed, so «Асанов»
  does not match «Асан».
* The Kazakh dative «-на/-не» is left out even though it is a real suffix:
  removing it turns «Марина» into «мари», the same stem as «Мария», and those
  are two different women.
* Numbers and words shorter than three letters must match exactly.
* Two words match only if one reaches the other's stem by removing at most two
  endings, and the stem left behind is at least three letters long.
"""

from __future__ import annotations

from functools import lru_cache

_RUSSIAN_ENDINGS = (
    "ами ями ого его ому ему ыми ими ией иям ием иях "
    "ой ей ий ый ая яя ое ее ую юю ых их ом ем ам ям ах ях ью ия ие ии "
    "ся сь а я у ю е о ы и ь"
).split()

_KAZAKH_ENDINGS = (
    "ның нің дың дің тың тің ға ге қа ке нда нде да де та те "
    "дан ден тан тен нан нен ды ді ты ті ны ні мен бен пен "
    "лар лер дар дер тар тер ым ім ың ің сы сі м ң"
).split()

_ENDINGS = tuple(sorted(set(_RUSSIAN_ENDINGS + _KAZAKH_ENDINGS), key=len, reverse=True))
_MINIMUM_STEM = 3
_MAXIMUM_STRIPS = 2


@lru_cache(maxsize=8192)
def inflection_stems(token: str) -> frozenset[str]:
    """The word itself plus every stem reachable by removing known endings."""

    if token.isdigit() or len(token) < _MINIMUM_STEM:
        return frozenset({token})

    stems = {token}
    frontier = {token}
    for _ in range(_MAXIMUM_STRIPS):
        reached: set[str] = set()
        for word in frontier:
            for ending in _ENDINGS:
                if word.endswith(ending) and len(word) - len(ending) >= _MINIMUM_STEM:
                    reached.add(word[: -len(ending)])
        reached -= stems
        if not reached:
            break
        stems |= reached
        frontier = reached
    return frozenset(stems)


def same_word(left: str, right: str) -> bool:
    """Whether two normalised tokens are forms of the same word."""

    if left == right:
        return True
    if left.isdigit() or right.isdigit():
        return False
    if len(left) < _MINIMUM_STEM or len(right) < _MINIMUM_STEM:
        return False
    return bool(inflection_stems(left) & inflection_stems(right))
