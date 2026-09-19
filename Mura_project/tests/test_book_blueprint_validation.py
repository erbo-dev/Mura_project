"""Tests for Family Book blueprint validation and deterministic repair (Layer 3)."""
# ruff: noqa: RUF001, E501

from __future__ import annotations

from mura.book.blueprint_validation import (
    DEFAULT_BLUEPRINT_LIMITS,
    BlueprintLimits,
    repair_blueprint_arithmetic,
    validate_blueprint,
)
from mura.book.snapshot import compile_source_snapshot
from mura.domain.book_models import (
    BlueprintIssueCode,
    BookBlueprint,
    BookLanguage,
    ChapterPlan,
    NarrativeVoice,
)
from mura.storage.archive_read import GroundingBundle

FAMILY_A = "fam_blueprint_test"


def _sample_snapshot():
    bundle = GroundingBundle(
        family_id=FAMILY_A,
        recordings=[
            {
                "recording_id": "rec_001",
                "family_id": FAMILY_A,
                "speaker_name": "Айгүл",
                "speaker_id": "spk_1",
                "detected_language": "kk",
            },
            {
                "recording_id": "rec_002",
                "family_id": FAMILY_A,
                "speaker_name": "Марат",
                "speaker_id": "spk_2",
                "detected_language": "ru",
            },
        ],
        pipeline_payloads={
            "rec_001": {
                "extraction": {
                    "languages": ["kk"],
                    "evidence_spans": [
                        {
                            "evidence_id": "ev_001",
                            "text": "1941 жылы майданға аттанғанда, үйде тек көне домбыра қалған еді.",
                        },
                        {
                            "evidence_id": "ev_002",
                            "text": "1978 жылы туған Айгүл осы домбыраны сақтап қалды.",
                        },
                    ],
                }
            },
            "rec_002": {
                "extraction": {
                    "languages": ["ru"],
                    "evidence_spans": [
                        {
                            "evidence_id": "ev_003",
                            "text": "В 1945 году привезли самовар из фронтового госпиталя.",
                        }
                    ],
                }
            },
        },
        people=[
            {
                "person_id": "per_kanat",
                "family_id": FAMILY_A,
                "canonical_name": "Қанат Баба",
                "normalized_name": "канат баба",
                "aliases": ["Канат"],
                "verified_aliases": [],
                "category": "core",
                "source_recording_ids": ["rec_001"],
            },
            {
                "person_id": "per_aigul",
                "family_id": FAMILY_A,
                "canonical_name": "Айгүл",
                "normalized_name": "айгул",
                "aliases": [],
                "verified_aliases": [],
                "category": "core",
                "source_recording_ids": ["rec_001"],
            },
        ],
        stories=[
            {
                "claim_id": "cl_story_1",
                "source_object_id": "story_war",
                "recording_id": "rec_001",
                "payload": {"title": "Майдан", "summary": "1941 соғыс"},
                "evidence_ids": ["ev_001"],
            }
        ],
        events=[
            {
                "claim_id": "cl_ev_1",
                "source_object_id": "event_victory",
                "recording_id": "rec_002",
                "payload": {"title": "Жеңіс", "description": "1945 жыл"},
                "evidence_ids": ["ev_003"],
            }
        ],
        claims=[
            {
                "claim_id": "cl_rel_1",
                "family_id": FAMILY_A,
                "recording_id": "rec_001",
                "object_type": "relationship",
                "source_object_id": "rel_1",
                "predicate": "grandfather",
                "evidence_ids": ["ev_001"],
                "evidence_class": "A_EXPLICIT",
            }
        ],
    )
    return compile_source_snapshot(bundle).snapshot


def _valid_10_chapter_blueprint() -> BookBlueprint:
    # 10 chapters, each 2400 words, total 24000 words
    chapters = [
        ChapterPlan(
            chapter_number=i + 1,
            title=f"Глава {i + 1}",
            purpose=f"Цель главы {i + 1}",
            synopsis=f"Синопсис главы {i + 1} в 1941 году.",
            target_word_count=2400,
            time_range="1941-1945",
            person_ids=["per_kanat"],
            source_recording_ids=["rec_001"],
            source_story_ids=["story_war"],
            claim_ids=["cl_rel_1"],
            evidence_refs=["ev_001"],
            material_anchor_refs=["домбыра"] if i == 0 else [],
        )
        for i in range(10)
    ]
    return BookBlueprint(
        book_title="Летопись семьи",
        central_theme="Связь поколений через испытания",
        narrative_voice=NarrativeVoice.THIRD_PERSON,
        output_language=BookLanguage.KK,
        target_total_words=24000,
        material_anchor="домбыра",
        chapters=chapters,
    )


def test_valid_blueprint():
    snapshot = _sample_snapshot()
    blueprint = _valid_10_chapter_blueprint()

    report = validate_blueprint(blueprint, snapshot)
    assert report.valid is True
    assert len(report.blockers) == 0
    assert len(report.issues) == 0


def test_chapter_count_out_of_range():
    snapshot = _sample_snapshot()
    blueprint = _valid_10_chapter_blueprint()

    # 9 chapters (too few)
    blueprint.chapters = blueprint.chapters[:9]
    report = validate_blueprint(blueprint, snapshot)
    assert report.valid is False
    codes = [i.code for i in report.blockers]
    assert BlueprintIssueCode.CHAPTER_COUNT_OUT_OF_RANGE in codes

    # 16 chapters (too many)
    extra_chapters = list(blueprint.chapters)
    while len(extra_chapters) < 16:
        extra_chapters.append(
            extra_chapters[0].model_copy(update={"chapter_number": len(extra_chapters) + 1})
        )
    blueprint.chapters = extra_chapters
    report = validate_blueprint(blueprint, snapshot)
    assert report.valid is False
    codes = [i.code for i in report.blockers]
    assert BlueprintIssueCode.CHAPTER_COUNT_OUT_OF_RANGE in codes


def test_duplicate_and_gap_chapter_numbers():
    snapshot = _sample_snapshot()
    blueprint = _valid_10_chapter_blueprint()

    # Duplicate chapter number: 1, 2, 2, 4, ...
    blueprint.chapters[2].chapter_number = 2
    report = validate_blueprint(blueprint, snapshot)
    assert report.valid is False
    assert BlueprintIssueCode.DUPLICATE_CHAPTER_NUMBER in [i.code for i in report.blockers]

    # Gap in chapter numbers: 1, 2, 4, 5, 6, 7, 8, 9, 10, 11
    blueprint = _valid_10_chapter_blueprint()
    blueprint.chapters[2].chapter_number = 4
    for idx in range(3, 10):
        blueprint.chapters[idx].chapter_number = idx + 2
    report = validate_blueprint(blueprint, snapshot)
    assert report.valid is False
    assert BlueprintIssueCode.CHAPTER_NUMBER_GAP in [i.code for i in report.blockers]


def test_word_budget_out_of_range_and_mismatch():
    snapshot = _sample_snapshot()
    blueprint = _valid_10_chapter_blueprint()

    # Total target words out of range (15000 < 20000)
    blueprint.target_total_words = 15000
    report = validate_blueprint(blueprint, snapshot)
    assert report.valid is False
    codes = [i.code for i in report.blockers]
    assert BlueprintIssueCode.WORD_BUDGET_OUT_OF_RANGE in codes
    assert BlueprintIssueCode.CHAPTER_TOTAL_MISMATCH in codes

    # Chapter target word count below min (600 < 700)
    blueprint = _valid_10_chapter_blueprint()
    blueprint.chapters[0].target_word_count = 600
    blueprint.chapters[1].target_word_count = 4200  # also above 3500
    report = validate_blueprint(blueprint, snapshot)
    assert report.valid is False
    assert BlueprintIssueCode.WORD_BUDGET_OUT_OF_RANGE in [i.code for i in report.blockers]


def test_unknown_ids_rejected():
    snapshot = _sample_snapshot()
    blueprint = _valid_10_chapter_blueprint()

    # Unknown person
    blueprint.chapters[0].person_ids = ["per_ghost"]
    # Unknown recording
    blueprint.chapters[1].source_recording_ids = ["rec_unknown"]
    # Unknown story
    blueprint.chapters[2].source_story_ids = ["story_fake"]
    # Unknown claim
    blueprint.chapters[3].claim_ids = ["cl_phantom"]
    # Unknown evidence
    blueprint.chapters[4].evidence_refs = ["ev_fabrication"]

    report = validate_blueprint(blueprint, snapshot)
    assert report.valid is False
    codes = [i.code for i in report.blockers]
    assert BlueprintIssueCode.UNKNOWN_PERSON in codes
    assert BlueprintIssueCode.UNKNOWN_RECORDING in codes
    assert BlueprintIssueCode.UNKNOWN_STORY in codes
    assert BlueprintIssueCode.UNKNOWN_CLAIM in codes
    assert BlueprintIssueCode.UNKNOWN_EVIDENCE in codes


def test_unsupported_year_rejected():
    snapshot = _sample_snapshot()
    blueprint = _valid_10_chapter_blueprint()

    # Mention year 1999 which is not in snapshot allowed_years (1941, 1945, 1978)
    blueprint.chapters[0].synopsis = "События происходят в 1999 году."
    report = validate_blueprint(blueprint, snapshot)
    assert report.valid is False
    assert BlueprintIssueCode.UNSUPPORTED_YEAR in [i.code for i in report.blockers]


def test_chapter_without_grounding():
    snapshot = _sample_snapshot()
    blueprint = _valid_10_chapter_blueprint()

    # Chapter with zero grounding
    ch = blueprint.chapters[0]
    ch.person_ids = []
    ch.source_recording_ids = []
    ch.source_story_ids = []
    ch.claim_ids = []
    ch.evidence_refs = []

    report = validate_blueprint(blueprint, snapshot)
    assert report.valid is False
    assert BlueprintIssueCode.CHAPTER_WITHOUT_GROUNDING in [i.code for i in report.blockers]


def test_unsupported_material_anchor():
    snapshot = _sample_snapshot()
    blueprint = _valid_10_chapter_blueprint()

    # Fabricate an unsupported heirloom
    blueprint.material_anchor = "золотой кубок хана"
    blueprint.chapters[0].material_anchor_refs = ["золотой кубок хана"]

    report = validate_blueprint(blueprint, snapshot)
    assert report.valid is False
    assert BlueprintIssueCode.UNSUPPORTED_MATERIAL_ANCHOR in [i.code for i in report.blockers]


def test_deterministic_arithmetic_repair():
    snapshot = _sample_snapshot()
    blueprint = _valid_10_chapter_blueprint()

    # Introduce arithmetic defects:
    # 1. Non-matching word count sum (all 1500 = 15000 != 24000)
    for ch in blueprint.chapters:
        ch.target_word_count = 1500
    # 2. Non-contiguous chapter numbering
    blueprint.chapters[0].chapter_number = 5
    # 3. Fabricated material anchor
    blueprint.material_anchor = "выдуманная шкатулка"
    blueprint.chapters[0].material_anchor_refs = ["выдуманная шкатулка"]

    # Initial validation fails
    report_bad = validate_blueprint(blueprint, snapshot)
    assert report_bad.valid is False

    # Repair blueprint deterministically
    repaired = repair_blueprint_arithmetic(blueprint, snapshot=snapshot)

    # Check repaired properties
    assert repaired.target_total_words == 24000
    assert sum(c.target_word_count for c in repaired.chapters) == 24000
    assert [c.chapter_number for c in repaired.chapters] == list(range(1, 11))
    assert repaired.material_anchor is None
    assert repaired.chapters[0].material_anchor_refs == []

    # Validating the repaired blueprint must now pass!
    report_good = validate_blueprint(repaired, snapshot)
    assert report_good.valid is True
    assert len(report_good.blockers) == 0

