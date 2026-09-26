"""Tests for Family Book planner and DeepSeek client temperature parameter (Layer 4)."""
# ruff: noqa: RUF001

from __future__ import annotations

from unittest.mock import MagicMock

from mura.book.planner import plan_book
from mura.book.prompts import BOOK_PLANNER_PROMPT_VERSION
from mura.book.snapshot import compile_source_snapshot
from mura.deepseek.client import DeepSeekClient, DeepSeekUsage
from mura.domain.book_models import (
    BookBlueprint,
    BookLanguage,
)
from mura.storage.archive_read import GroundingBundle

FAMILY_A = "fam_planner_test"


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
            }
        ],
        pipeline_payloads={
            "rec_001": {
                "extraction": {
                    "languages": ["kk"],
                    "evidence_spans": [
                        {
                            "evidence_id": "ev_001",
                            "text": "1941 жылы майданға аттанғанда, үйде тек көне домбыра қалды.",
                        }
                    ],
                }
            }
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
            }
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
        events=[],
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


def _fake_blueprint_dict() -> dict:
    return {
        "book_title": "Летопись семьи",
        "subtitle": "Память поколений",
        "central_theme": "Связь времен",
        "narrative_voice": "third_person",
        "output_language": "kk",
        "target_total_words": 24000,
        "material_anchor": "домбыра",
        "epigraph": None,
        "chapters": [
            {
                "chapter_number": i + 1,
                "title": f"Глава {i + 1}",
                "purpose": f"Цель {i + 1}",
                "synopsis": "1941 жылғы оқиғалар.",
                "target_word_count": 2400,
                "time_range": "1941",
                "person_ids": ["per_kanat"],
                "place_names": [],
                "claim_ids": ["cl_rel_1"],
                "source_recording_ids": ["rec_001"],
                "source_story_ids": ["story_war"],
                "evidence_refs": ["ev_001"],
                "material_anchor_refs": ["домбыра"] if i == 0 else [],
                "continuity_in": None,
                "continuity_out": None,
                "uncertainties": [],
                "forbidden_inventions": [],
            }
            for i in range(10)
        ],
    }


def test_plan_book_success():
    snapshot = _sample_snapshot()
    mock_client = MagicMock()
    mock_usage = DeepSeekUsage(
        model="deepseek-chat",
        finish_reason="stop",
        request_seconds=1.25,
        prompt_tokens=1500,
        completion_tokens=800,
        total_tokens=2300,
        prompt_cache_hit_tokens=500,
    )
    mock_client.request_json.return_value = (_fake_blueprint_dict(), mock_usage)

    blueprint, report, telemetry = plan_book(
        mock_client,
        snapshot,
        output_language=BookLanguage.KK,
        target_total_words=24000,
        temperature=0.4,
    )

    assert isinstance(blueprint, BookBlueprint)
    assert blueprint.book_title == "Летопись семьи"
    assert report.valid is True
    assert len(report.blockers) == 0

    # Verify client call parameters
    mock_client.request_json.assert_called_once()
    call_kwargs = mock_client.request_json.call_args.kwargs
    assert call_kwargs["temperature"] == 0.4
    assert call_kwargs["operation"] == "book_plan"

    # Verify telemetry metadata
    assert telemetry["operation"] == "book_plan"
    assert telemetry["prompt_version"] == BOOK_PLANNER_PROMPT_VERSION
    assert telemetry["model"] == "deepseek-chat"
    assert telemetry["total_tokens"] == 2300
    assert telemetry["prompt_tokens"] == 1500
    assert telemetry["completion_tokens"] == 800
    assert telemetry["cached_tokens"] == 500
    assert telemetry["request_seconds"] == 1.25


def test_plan_book_repaired_arithmetic():
    snapshot = _sample_snapshot()
    mock_client = MagicMock()
    mock_usage = DeepSeekUsage(
        model="deepseek-chat",
        finish_reason="stop",
        request_seconds=0.8,
    )

    raw_bad = _fake_blueprint_dict()
    # Introduce mismatched chapter target words (10 * 1500 = 15000 != 24000)
    for ch in raw_bad["chapters"]:
        ch["target_word_count"] = 1500

    mock_client.request_json.return_value = (raw_bad, mock_usage)

    blueprint, report, _ = plan_book(
        mock_client,
        snapshot,
        output_language=BookLanguage.KK,
        target_total_words=24000,
    )

    assert report.valid is True
    assert report.repaired is True
    assert sum(c.target_word_count for c in blueprint.chapters) == 24000


def test_deepseek_client_temperature_parameter():
    client = DeepSeekClient(api_key="test-key")

    # When temperature is None: key is omitted
    mock_post = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": '{"status":"ok"}'}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    mock_post.return_value = mock_response
    client.session.post = mock_post

    client.request_json(
        system_prompt="sys",
        payload={"foo": "bar"},
        max_tokens=100,
        temperature=None,
    )
    sent_body = mock_post.call_args.kwargs["json"]
    assert "temperature" not in sent_body

    # When temperature is specified: key is included
    client.request_json(
        system_prompt="sys",
        payload={"foo": "bar"},
        max_tokens=100,
        temperature=0.75,
    )
    sent_body_temp = mock_post.call_args.kwargs["json"]
    assert "temperature" in sent_body_temp
    assert sent_body_temp["temperature"] == 0.75
