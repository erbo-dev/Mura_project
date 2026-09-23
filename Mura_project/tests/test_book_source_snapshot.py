"""Tests for Family Book source snapshot compilation (Layer 2)."""
# ruff: noqa: RUF001, E501

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mura.book.snapshot import (
    COMPILER_VERSION,
    compile_source_snapshot,
)
from mura.domain.book_models import (
    SNAPSHOT_SCHEMA_VERSION,
    BookSourceSnapshot,
    CompiledSnapshot,
)
from mura.jobs import JobStatus
from mura.storage.archive_read import (
    ArchiveResourceNotFound,
    GroundingBundle,
    grounding_bundle,
)
from mura.storage.database import (
    Database,
    PipelineResultRow,
    ProcessingJobRow,
    RecordingRow,
)
from mura.storage.identity import FamilyRow, UserRow

FAMILY_A = "fam_snapshot_test_aaa"
FAMILY_B = "fam_snapshot_test_bbb"


def _sample_bundle() -> GroundingBundle:
    return GroundingBundle(
        family_id=FAMILY_A,
        recordings=[
            {
                "recording_id": "rec_001",
                "family_id": FAMILY_A,
                "speaker_name": "Айгүл",
                "speaker_id": "spk_aigul",
                "detected_language": "kk",
                "created_at": "2026-09-01T10:00:00Z",
            },
            {
                "recording_id": "rec_002",
                "family_id": FAMILY_A,
                "speaker_name": "Марат",
                "speaker_id": "spk_marat",
                "detected_language": "ru",
                "created_at": "2026-09-02T11:00:00Z",
            },
        ],
        pipeline_payloads={
            "rec_001": {
                "extraction": {
                    "languages": ["kk"],
                    "evidence_spans": [
                        {
                            "evidence_id": "ev_001",
                            "text": "Атам 1941 жылы майданға аттанды, үйде тек көне домбыра қалды.",
                        },
                        {
                            "evidence_id": "ev_002",
                            "text": "Әжем 1978 жылы бізге осы күйді үйретті.",
                        },
                    ],
                },
            },
            "rec_002": {
                "extraction": {
                    "languages": ["ru"],
                    "evidence_spans": [
                        {
                            "evidence_id": "ev_003",
                            "text": "В 1945 году дедушка вернулся с победой и привёз старый самовар.",
                        },
                        {
                            "evidence_id": "ev_004",
                            "text": "Мы бережно храним дедушкины медали и наградные часы.",
                        },
                    ],
                },
            },
        },
        people=[
            {
                "person_id": "per_kanat",
                "family_id": FAMILY_A,
                "canonical_name": "Қанат Баба",
                "normalized_name": "канат баба",
                "aliases": ["Канат"],
                "verified_aliases": ["Канат-ата"],
                "category": "core",
                "relations_to_speakers": {"spk_aigul": "дедушка"},
                "birth_date": {
                    "value": "1915",
                    "precision": "year",
                    "original_expression": "он бесінші жылы туған",
                    "approximate": False,
                },
                "professions": ["музыкант"],
                "locations": ["Алматы"],
                "source_recording_ids": ["rec_001", "rec_002"],
            },
            {
                "person_id": "per_aigul",
                "family_id": FAMILY_A,
                "canonical_name": "Айгүл",
                "normalized_name": "айгул",
                "aliases": [],
                "verified_aliases": [],
                "category": "core",
                "relations_to_speakers": {"spk_aigul": "self"},
                "birth_date": {
                    "value": "1978-05-12",
                    "precision": "day",
                    "original_expression": "12 мая 1978",
                    "approximate": False,
                },
                "professions": ["учитель"],
                "locations": ["Алматы"],
                "source_recording_ids": ["rec_001"],
            },
        ],
        relationships=[
            {
                "edge_id": "edge_kanat_aigul",
                "family_id": FAMILY_A,
                "relationship_type": "grandfather",
                "subject_person_id": "per_kanat",
                "subject_role": "дедушка",
                "object_person_id": "per_aigul",
                "object_role": "внучка",
                "source_claim_ids": ["cl_rel_1"],
            }
        ],
        stories=[
            {
                "claim_id": "cl_story_1",
                "source_object_id": "story_war",
                "recording_id": "rec_001",
                "payload": {
                    "title": "Майданға аттану",
                    "summary": "1941 жылы Қанат соғысқа аттанған күні.",
                    "person_mention_ids": ["men_kanat"],
                },
                "evidence_ids": ["ev_001"],
            }
        ],
        events=[
            {
                "claim_id": "cl_ev_1",
                "source_object_id": "event_return",
                "recording_id": "rec_002",
                "payload": {
                    "title": "Возвращение с фронта",
                    "event_type": "return",
                    "description": "Победный 1945 год в Алматы.",
                    "location": "Алматы",
                    "date": {
                        "value": "1945",
                        "precision": "year",
                        "original_expression": "в сорок пятом",
                        "approximate": False,
                    },
                    "participant_mention_ids": ["men_kanat"],
                },
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
                "subject_person_id": "per_kanat",
                "object_person_id": "per_aigul",
                "payload": {},
                "evidence_ids": ["ev_001"],
                "evidence_class": "A_EXPLICIT",
                "assertion_mode": "explicit",
                "verification_status": "verified",
            }
        ],
        corrections=[
            {
                "correction_id": "cor_city",
                "family_id": FAMILY_A,
                "recording_id": "rec_002",
                "kind": "city_name",
                "subject": "Дедушкин госпиталь",
                "original_value": "Сталинград",
                "corrected_value": "Ленинград",
                "explanation": "Рассказчик оговорился и тут же уточнил, что госпиталь был в Ленинграде.",
                "confidence": "high",
            }
        ],
        conflicts=[
            {
                "conflict_id": "conf_birth_year",
                "family_id": FAMILY_A,
                "conflict_type": "birth_date",
                "status": "open",
                "detected_by": "deterministic",
                "claim_ids": ["cl_rel_1"],
                "preferred_claim_id": None,
                "rationale": "В одной записи указан 1915 год, в другой 1916 год.",
                "resolution_note": None,
            }
        ],
        unresolved_questions=[
            {
                "claim_id": "q_grandfather_medal",
                "family_id": FAMILY_A,
                "recording_id": "rec_002",
                "object_type": "question",
                "source_object_id": "q1",
                "predicate": "Каким именно орденом был награждён дедушка?",
                "payload": {"question": "Каким именно орденом был награждён дедушка?"},
                "evidence_ids": ["ev_004"],
            }
        ],
        resolved_mentions={
            "rec_001:men_kanat": "per_kanat",
            "rec_002:men_kanat": "per_kanat",
        },
    )


def test_compile_source_snapshot_basic():
    bundle = _sample_bundle()
    ts = datetime(2026, 9, 18, 12, 0, 0, tzinfo=UTC)
    compiled = compile_source_snapshot(bundle, created_at=ts)

    assert isinstance(compiled, CompiledSnapshot)
    assert len(compiled.content_hash) == 64
    snapshot = compiled.snapshot
    assert isinstance(snapshot, BookSourceSnapshot)
    assert snapshot.schema_version == SNAPSHOT_SCHEMA_VERSION
    assert snapshot.compiler_version == COMPILER_VERSION
    assert snapshot.family_id == FAMILY_A

    # Manifest checks
    assert snapshot.manifest.source_recording_ids == ["rec_001", "rec_002"]
    assert snapshot.manifest.source_story_ids == ["story_war"]
    assert snapshot.manifest.source_event_ids == ["event_return"]
    assert snapshot.manifest.source_person_ids == ["per_aigul", "per_kanat"]
    assert snapshot.manifest.correction_count == 1
    assert snapshot.manifest.uncertainty_count == 1
    assert snapshot.manifest.conflict_count == 1
    assert snapshot.manifest.created_at == ts

    # People & Date precision
    kanat = next(p for p in snapshot.people if p.person_id == "per_kanat")
    assert kanat.display_name == "Қанат Баба"
    assert kanat.birth_date is not None
    assert kanat.birth_date.value == "1915"
    assert kanat.birth_date.precision == "year"
    assert kanat.birth_date.original_expression == "он бесінші жылы туған"

    # Corrections preserved (forbidden original_value)
    assert len(snapshot.corrections) == 1
    cor = snapshot.corrections[0]
    assert cor.original_value == "Сталинград"
    assert cor.corrected_value == "Ленинград"

    # Conflicts preserved
    assert len(snapshot.conflicts) == 1
    conf = snapshot.conflicts[0]
    assert conf.status == "open"
    assert "1915" in conf.rationale

    # Uncertainties preserved
    assert len(snapshot.uncertainties) == 1
    assert "орденом" in snapshot.uncertainties[0].text


def test_content_hash_deterministic():
    bundle = _sample_bundle()
    t1 = datetime(2026, 9, 18, 10, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 9, 18, 22, 0, 0, tzinfo=UTC)

    # Identical content with different created_at must yield identical content_hash
    compiled1 = compile_source_snapshot(bundle, created_at=t1)
    compiled2 = compile_source_snapshot(bundle, created_at=t2)
    assert compiled1.content_hash == compiled2.content_hash

    # Mutating content must change hash
    bundle_mutated = _sample_bundle()
    bundle_mutated.people[0]["canonical_name"] = "Қанат Ата Жаңа"
    compiled_mutated = compile_source_snapshot(bundle_mutated, created_at=t1)
    assert compiled_mutated.content_hash != compiled1.content_hash


def test_allowed_years_grounded_only():
    bundle = _sample_bundle()
    compiled = compile_source_snapshot(bundle)
    years = compiled.snapshot.allowed_years

    # Grounded years present in evidence/dates: 1915, 1941, 1945, 1978
    assert 1915 in years
    assert 1941 in years
    assert 1945 in years
    assert 1978 in years

    # Ungrounded year must never be admitted
    assert 1999 not in years
    assert 2010 not in years
    assert 1920 not in years


def test_material_anchor_candidates_grounded():
    bundle = _sample_bundle()
    compiled = compile_source_snapshot(bundle)
    candidates = compiled.snapshot.material_anchor_candidates

    # Evidence contains: "домбыра", "самовар", "медали", "часы"
    assert "домбыра" in candidates
    assert "самовар" in candidates
    assert "медали" in candidates
    assert "часы" in candidates

    # Verify that every candidate is a substring of at least one evidence text
    all_evidence_text = " ".join(e.text.lower() for e in compiled.snapshot.evidence)
    for c in candidates:
        assert c in all_evidence_text, f"candidate {c} is not in evidence text!"


def test_max_evidence_quotes_cap():
    bundle = _sample_bundle()
    # Request cap of 2 evidence quotes
    compiled = compile_source_snapshot(bundle, max_evidence_quotes=2)
    assert len(compiled.snapshot.evidence) == 2
    # The referenced ones (ev_001, ev_003) must be prioritized over unreferenced
    e_ids = {e.evidence_id for e in compiled.snapshot.evidence}
    assert "ev_001" in e_ids or "ev_003" in e_ids


def test_grounding_bundle_database_isolation():
    db = Database("sqlite:///:memory:")
    db.create_schema()

    with db.session_factory.begin() as session:
        # Seed users
        session.add(
            UserRow(
                user_id="usr_owner",
                email="owner@example.com",
                display_name="Owner",
                auth_issuer="test",
                auth_subject="sub1",
            )
        )
        # Seed Family A & Family B
        session.add(FamilyRow(family_id=FAMILY_A, name="Family A", created_by_user_id="usr_owner"))
        session.add(FamilyRow(family_id=FAMILY_B, name="Family B", created_by_user_id="usr_owner"))

        # Family A: 1 completed recording
        session.add(
            RecordingRow(
                recording_id="rec_a_done",
                family_id=FAMILY_A,
                speaker_name="Айгүл",
                speaker_id="spk_1",
                original_filename="rec_a.m4a",
                content_type="audio/mp4",
                audio_path="dummy_a.m4a",
            )
        )
        session.add(
            ProcessingJobRow(
                job_id="job_a_done",
                recording_id="rec_a_done",
                status=JobStatus.COMPLETED.value,
            )
        )
        session.add(
            PipelineResultRow(
                recording_id="rec_a_done",
                payload={
                    "extraction": {
                        "languages": ["kk"],
                        "evidence_spans": [
                            {"evidence_id": "ev_db_1", "text": "1960 жылы біз көшіп келдік."}
                        ],
                    }
                },
            )
        )

        # Family A: 1 uncompleted recording (should be excluded)
        session.add(
            RecordingRow(
                recording_id="rec_a_queued",
                family_id=FAMILY_A,
                speaker_name="Айгүл",
                speaker_id="spk_1",
                original_filename="rec_q.m4a",
                content_type="audio/mp4",
                audio_path="dummy_q.m4a",
            )
        )
        session.add(
            ProcessingJobRow(
                job_id="job_a_queued",
                recording_id="rec_a_queued",
                status=JobStatus.QUEUED.value,
            )
        )

        # Family B: 1 completed recording
        session.add(
            RecordingRow(
                recording_id="rec_b_done",
                family_id=FAMILY_B,
                speaker_name="Берік",
                speaker_id="spk_2",
                original_filename="rec_b.m4a",
                content_type="audio/mp4",
                audio_path="dummy_b.m4a",
            )
        )
        session.add(
            ProcessingJobRow(
                job_id="job_b_done",
                recording_id="rec_b_done",
                status=JobStatus.COMPLETED.value,
            )
        )
        session.add(
            PipelineResultRow(
                recording_id="rec_b_done",
                payload={
                    "extraction": {
                        "languages": ["ru"],
                        "evidence_spans": [
                            {"evidence_id": "ev_db_b", "text": "Секретные данные семьи Б."}
                        ],
                    }
                },
            )
        )

    # 1. Family A without specific IDs: only eligible completed recording is returned
    bundle = grounding_bundle(db, family_id=FAMILY_A)
    assert len(bundle.recordings) == 1
    assert bundle.recordings[0]["recording_id"] == "rec_a_done"

    # 2. Family A requesting specific valid recording
    bundle_req = grounding_bundle(db, family_id=FAMILY_A, recording_ids=["rec_a_done"])
    assert len(bundle_req.recordings) == 1

    # 3. Family A requesting Family B's recording -> 404 (ResourceNotFound)
    with pytest.raises(ArchiveResourceNotFound):
        grounding_bundle(db, family_id=FAMILY_A, recording_ids=["rec_b_done"])

    # 4. Family A requesting non-existent recording -> 404 (ResourceNotFound)
    with pytest.raises(ArchiveResourceNotFound):
        grounding_bundle(db, family_id=FAMILY_A, recording_ids=["rec_nonexistent"])

    # 5. Compile snapshot directly from Database source
    compiled = compile_source_snapshot(db, family_id=FAMILY_A)
    assert compiled.snapshot.manifest.source_recording_ids == ["rec_a_done"]
    assert 1960 in compiled.snapshot.allowed_years


def test_no_synthetic_corpus_reads():
    """Verify that compile_source_snapshot never accesses the synthetic MURA/ samples directory."""
    # Inspect module source code to ensure no references to synthetic dataset samples
    import inspect

    import mura.book.snapshot as snap_mod
    src = inspect.getsource(snap_mod)
    assert "MURA/samples" not in src
    assert "MURA\\samples" not in src
    assert "synthetic" not in src.lower()



def test_selected_source_snapshot_does_not_import_excluded_family_graph_context():
    """Regression: a Book selecting A+B must not inherit C-only people/edges."""
    bundle = GroundingBundle(
        family_id=FAMILY_A,
        recordings=[
            {"recording_id": "rec_a", "family_id": FAMILY_A, "speaker_name": "Narrator"},
            {"recording_id": "rec_b", "family_id": FAMILY_A, "speaker_name": "Narrator"},
        ],
        pipeline_payloads={
            "rec_a": {
                "extraction": {
                    "evidence_spans": [
                        {"evidence_id": "ev_a", "text": "Алихан — мой дед."}
                    ]
                }
            },
            "rec_b": {
                "extraction": {
                    "evidence_spans": [
                        {"evidence_id": "ev_b", "text": "Мы жили в Семее."}
                    ]
                }
            },
        },
        people=[
            {
                "person_id": "per_alikhan",
                "family_id": FAMILY_A,
                "canonical_name": "Алихан",
                "normalized_name": "алихан",
                "aliases": [],
                "verified_aliases": [],
                "category": "core",
                "source_recording_ids": ["rec_a", "rec_c"],
            },
            {
                "person_id": "per_murat",
                "family_id": FAMILY_A,
                "canonical_name": "Мурат",
                "normalized_name": "мурат",
                "aliases": [],
                "verified_aliases": [],
                "category": "core",
                "source_recording_ids": ["rec_c"],
            },
        ],
        relationships=[
            {
                "edge_id": "edge_alikhan_murat",
                "family_id": FAMILY_A,
                "relationship_type": "sibling",
                "subject_person_id": "per_alikhan",
                "subject_role": "brother",
                "object_person_id": "per_murat",
                "object_role": "brother",
                "source_claim_ids": ["cl_c_sibling"],
            }
        ],
        claims=[],
        stories=[],
        events=[],
        corrections=[],
        conflicts=[],
        unresolved_questions=[],
        resolved_mentions={},
    )

    snapshot = compile_source_snapshot(
        bundle,
        recording_ids=["rec_a", "rec_b"],
        created_at=datetime(2026, 9, 23, tzinfo=UTC),
    ).snapshot

    assert snapshot.manifest.source_recording_ids == ["rec_a", "rec_b"]
    assert {person.display_name for person in snapshot.people} == {"Алихан"}
    assert snapshot.relationships == []
