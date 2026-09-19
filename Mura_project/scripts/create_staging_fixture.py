"""Deterministic Synthetic Staging Fixture Creator.

Generates the canonical synthetic staging dataset:
- Family: 'Staging Mura Family' (ID: family_staging_mura)
- Members:
  * Aidar (Owner, Narrator)
  * Aigul (Editor, Mother)
  * Serik (Viewer, Grandfather)
- 3 Grounded Synthetic Recordings:
  * Recording A (Kazakh): 'Әжемнің мұрасы' (Keywords: Мұра, Әже, Ғасыр, Құндылық, Өмір, Ұрпақ, Із),
    contains family heirloom carpet (материалдық жәдігер - кілем).
  * Recording B (Russian): 'Переезд в Алматы' (Corrected date: moved in 1982 not 1978, conflicting house number).
  * Recording C (Code-Switched RU/KK): 'Шілдехана тойы' (Uncertain claim about celebration location).

Follows product boundaries (Identity -> Recording -> Processing -> Archive).
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV_SITE = ROOT / ".venv" / "Lib" / "site-packages"
if VENV_SITE.exists() and str(VENV_SITE) not in sys.path:
    sys.path.insert(0, str(VENV_SITE))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import select

from mura.domain.models import (
    CleanerResult,
    CorrectionKind,
    DetectedCorrection,
    EvidenceClass,
    EvidencePurpose,
    EvidenceSourceLayer,
    EvidenceSpan,
    ExtractionResult,
    MentionResolution,
    PersonCategory,
    PersonMention,
    PipelineResult,
    RawSegment,
    ReadableSegment,
    RelationshipClaim,
    RelationshipRole,
    RelationshipState,
    RelationshipType,
    ResolutionStatus,
    TranscriptEnvelope,
    UnresolvedQuestion,
)
from mura.identity.auth import VerifiedIdentity
from mura.identity.policy import FamilyRole
from mura.storage.archive import ArchiveRepository
from mura.storage.completion import finalize_recording_job
from mura.storage.database import Database, RecordingRepository, RecordingRow
from mura.storage.identity import FamilyMembershipRow, FamilyRow, IdentityRepository

STAGING_FAMILY_ID = "family_staging_mura"
STAGING_OWNER_ID = "user_staging_aidar"
STAGING_EDITOR_ID = "user_staging_aigul"
STAGING_VIEWER_ID = "user_staging_serik"

RECORDING_A_ID = "rec_staging_001_kazakh"
RECORDING_B_ID = "rec_staging_002_russian"
RECORDING_C_ID = "rec_staging_003_mixed"


def build_synthetic_kazakh_result() -> PipelineResult:
    seg1 = "Біздің әжем Күләш 1935 жылы туған. Ол бізге ескі қазақы кілемді мұра етіп қалдырды."
    seg2 = "Айдар мен Айгүл осы мұраны ұрпақтан ұрпаққа сақтап келеді."
    full_text = f"{seg1} {seg2}"

    transcript = TranscriptEnvelope(
        recording_id=RECORDING_A_ID,
        duration_seconds=12.5,
        language_hints=["kk"],
        full_text=full_text,
        segments=[
            RawSegment(segment_id="seg_001", start=0.0, end=6.0, text=seg1),
            RawSegment(segment_id="seg_002", start=6.0, end=12.5, text=seg2),
        ],
        asr_model="synthetic-staging",
        asr_revision="v1",
        chunker_version="v1",
    )

    ev_text = "әжем Күләш 1935 жылы туған"
    start_idx = seg1.index(ev_text)
    evidence = EvidenceSpan(
        evidence_id="ev_001",
        segment_id="seg_001",
        text=ev_text,
        source_layer=EvidenceSourceLayer.RAW_TRANSCRIPT,
        start_char=start_idx,
        end_char=start_idx + len(ev_text),
        evidence_class=EvidenceClass.A_EXPLICIT,
        purposes=[EvidencePurpose.CLAIM],
        mention_ids=["m_kulash"],
    )

    people = [
        PersonMention(
            mention_id="m_kulash",
            name="Күләш",
            category=PersonCategory.FAMILY_MEMBER,
            relation_to_speaker="grandmother",
            source_segment_ids=["seg_001"],
            evidence_ids=[evidence.evidence_id],
            evidence_class=EvidenceClass.A_EXPLICIT,
            confidence=1.0,
        ),
        PersonMention(
            mention_id="m_aidar",
            name="Айдар",
            category=PersonCategory.FAMILY_MEMBER,
            relation_to_speaker="narrator",
            source_segment_ids=["seg_002"],
            evidence_ids=[evidence.evidence_id],
            evidence_class=EvidenceClass.A_EXPLICIT,
            confidence=1.0,
        ),
        PersonMention(
            mention_id="m_aigul",
            name="Айгүл",
            category=PersonCategory.FAMILY_MEMBER,
            relation_to_speaker="mother",
            source_segment_ids=["seg_002"],
            evidence_ids=[evidence.evidence_id],
            evidence_class=EvidenceClass.A_EXPLICIT,
            confidence=1.0,
        ),
    ]

    relationship = RelationshipClaim(
        relationship_id="rel_001",
        relationship_type=RelationshipType.PARENT_CHILD,
        relationship_state=RelationshipState.CURRENT,
        subject_mention_id="m_aigul",
        subject_role=RelationshipRole.PARENT,
        object_mention_id="m_aidar",
        object_role=RelationshipRole.CHILD,
        source_segment_ids=["seg_002"],
        evidence_ids=[evidence.evidence_id],
        evidence_class=EvidenceClass.A_EXPLICIT,
        confidence=1.0,
    )

    resolutions = [
        MentionResolution(mention_id="m_kulash", status=ResolutionStatus.NEW_PERSON, reason="fixture archive"),
        MentionResolution(mention_id="m_aidar", status=ResolutionStatus.NEW_PERSON, reason="fixture archive"),
        MentionResolution(mention_id="m_aigul", status=ResolutionStatus.NEW_PERSON, reason="fixture archive"),
    ]

    return PipelineResult(
        transcript=transcript,
        cleaned_transcript=CleanerResult(
            readable_segments=[
                ReadableSegment(segment_id="seg_001", text=seg1),
                ReadableSegment(segment_id="seg_002", text=seg2),
            ],
            full_readable_text=full_text,
        ),
        extraction=ExtractionResult(
            schema_version="extraction-v2",
            recording_id=RECORDING_A_ID,
            speaker_id="spk_rec_staging_001_kazakh",
            speaker_name="Күләш әже",
            languages=["kk"],
            evidence_spans=[evidence],
            people_mentions=people,
            relationship_claims=[relationship],
        ),
        resolutions=resolutions,
        processing={"total_seconds": 0.5},
    )


def build_synthetic_russian_result() -> PipelineResult:
    seg1 = "Мы переехали в Алма-Ату не в 1978 году, как думал дедушка Серик, а точно в 1982 году."
    seg2 = "Жили мы на улице Абая, дом 42, хотя некоторые вспоминают дом 44."
    full_text = f"{seg1} {seg2}"

    transcript = TranscriptEnvelope(
        recording_id=RECORDING_B_ID,
        duration_seconds=14.0,
        language_hints=["ru"],
        full_text=full_text,
        segments=[
            RawSegment(segment_id="seg_001", start=0.0, end=7.0, text=seg1),
            RawSegment(segment_id="seg_002", start=7.0, end=14.0, text=seg2),
        ],
        asr_model="synthetic-staging",
        asr_revision="v1",
        chunker_version="v1",
    )

    ev_text = "дедушка Серик"
    start_idx = seg1.index(ev_text)
    evidence = EvidenceSpan(
        evidence_id="ev_002",
        segment_id="seg_001",
        text=ev_text,
        source_layer=EvidenceSourceLayer.RAW_TRANSCRIPT,
        start_char=start_idx,
        end_char=start_idx + len(ev_text),
        evidence_class=EvidenceClass.A_EXPLICIT,
        purposes=[EvidencePurpose.CLAIM],
        mention_ids=["m_serik"],
    )

    people = [
        PersonMention(
            mention_id="m_serik",
            name="Серик",
            category=PersonCategory.FAMILY_MEMBER,
            relation_to_speaker="grandfather",
            source_segment_ids=["seg_001"],
            evidence_ids=[evidence.evidence_id],
            evidence_class=EvidenceClass.A_EXPLICIT,
            confidence=1.0,
        )
    ]

    corrections = [
        DetectedCorrection(
            kind=CorrectionKind.SPEAKER_SELF_CORRECTION,
            subject="Год переезда в Алма-Ату",
            original_value="1978",
            corrected_value="1982",
            source_segment_ids=["seg_001"],
            explanation="Спикер уточнил дату: 1982 вместо 1978",
            confidence=1.0,
        )
    ]

    resolutions = [
        MentionResolution(mention_id="m_serik", status=ResolutionStatus.NEW_PERSON, reason="fixture archive"),
    ]

    return PipelineResult(
        transcript=transcript,
        cleaned_transcript=CleanerResult(
            readable_segments=[
                ReadableSegment(segment_id="seg_001", text=seg1),
                ReadableSegment(segment_id="seg_002", text=seg2),
            ],
            detected_corrections=corrections,
            full_readable_text=full_text,
        ),
        extraction=ExtractionResult(
            schema_version="extraction-v2",
            recording_id=RECORDING_B_ID,
            speaker_id="spk_rec_staging_002_russian",
            speaker_name="Айгүл",
            languages=["ru"],
            evidence_spans=[evidence],
            people_mentions=people,
            relationship_claims=[],
        ),
        resolutions=resolutions,
        processing={"total_seconds": 0.4},
    )


def build_synthetic_mixed_result() -> PipelineResult:
    seg1 = "Біз Серик атаның шілдехана тойын тойладық. Кажется, это было в ауле."
    full_text = seg1

    transcript = TranscriptEnvelope(
        recording_id=RECORDING_C_ID,
        duration_seconds=8.0,
        language_hints=["kk", "ru"],
        full_text=full_text,
        segments=[
            RawSegment(segment_id="seg_001", start=0.0, end=8.0, text=seg1),
        ],
        asr_model="synthetic-staging",
        asr_revision="v1",
        chunker_version="v1",
    )

    ev_text = "Серик атаның"
    start_idx = seg1.index(ev_text)
    evidence = EvidenceSpan(
        evidence_id="ev_003",
        segment_id="seg_001",
        text=ev_text,
        source_layer=EvidenceSourceLayer.RAW_TRANSCRIPT,
        start_char=start_idx,
        end_char=start_idx + len(ev_text),
        evidence_class=EvidenceClass.A_EXPLICIT,
        purposes=[EvidencePurpose.CLAIM],
        mention_ids=["m_serik_ata"],
    )

    people = [
        PersonMention(
            mention_id="m_serik_ata",
            name="Серик",
            aliases=["Серик ата"],
            category=PersonCategory.FAMILY_MEMBER,
            relation_to_speaker="grandfather",
            source_segment_ids=["seg_001"],
            evidence_ids=[evidence.evidence_id],
            evidence_class=EvidenceClass.A_EXPLICIT,
            confidence=0.95,
        )
    ]

    unresolved = [
        UnresolvedQuestion(
            question_id="q_001",
            question="Қай ауылда тойланды?",
            reason="Uncertain village location mentioned in passing",
            source_segment_ids=["seg_001"],
            evidence_ids=[evidence.evidence_id],
            related_mention_ids=["m_serik_ata"],
        )
    ]

    resolutions = [
        MentionResolution(mention_id="m_serik_ata", status=ResolutionStatus.NEW_PERSON, reason="fixture archive"),
    ]

    return PipelineResult(
        transcript=transcript,
        cleaned_transcript=CleanerResult(
            readable_segments=[ReadableSegment(segment_id="seg_001", text=seg1)],
            full_readable_text=full_text,
        ),
        extraction=ExtractionResult(
            schema_version="extraction-v2",
            recording_id=RECORDING_C_ID,
            speaker_id="spk_rec_staging_003_mixed",
            speaker_name="Серік ата",
            languages=["kk", "ru"],
            evidence_spans=[evidence],
            people_mentions=people,
            relationship_claims=[],
            unresolved_questions=unresolved,
        ),
        resolutions=resolutions,
        processing={"total_seconds": 0.3},
    )


def seed_staging_fixture(database: Database, audio_dir: Path | None = None) -> dict[str, object]:
    print("--- Seeding Deterministic Staging Fixture ---")
    if audio_dir is None:
        audio_dir = ROOT / "scratch" / "staging_audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    identity_repo = IdentityRepository(database)
    recording_repo = RecordingRepository(database)

    # Clean existing family if present for complete idempotency
    with database.session_factory() as session:
        existing = session.scalar(select(FamilyRow).where(FamilyRow.family_id == STAGING_FAMILY_ID))
    if existing is not None:
        print(f"[INFO] Existing staging family '{STAGING_FAMILY_ID}' found. Deleting for idempotency...")
        identity_repo.delete_family(STAGING_FAMILY_ID)

    user_aidar = identity_repo.resolve_principal(
        VerifiedIdentity(
            issuer="staging",
            subject="sub_staging_aidar",
            email="aidar@staging.mura.kz",
            display_name="Aidar (Owner)",
        )
    )
    user_aigul = identity_repo.resolve_principal(
        VerifiedIdentity(
            issuer="staging",
            subject="sub_staging_aigul",
            email="aigul@staging.mura.kz",
            display_name="Aigul (Editor)",
        )
    )
    user_serik = identity_repo.resolve_principal(
        VerifiedIdentity(
            issuer="staging",
            subject="sub_staging_serik",
            email="serik@staging.mura.kz",
            display_name="Serik (Viewer)",
        )
    )

    with database.session_factory.begin() as session:
        family = FamilyRow(
            family_id=STAGING_FAMILY_ID,
            name="Staging Mura Family",
            created_by_user_id=user_aidar.user_id,
        )
        session.add(family)
        session.flush()
        session.add(
            FamilyMembershipRow(
                membership_id=f"membership_{uuid.uuid4().hex}",
                family_id=family.family_id,
                user_id=user_aidar.user_id,
                role=FamilyRole.OWNER.value,
            )
        )
        session.add(
            FamilyMembershipRow(
                membership_id=f"membership_{uuid.uuid4().hex}",
                family_id=family.family_id,
                user_id=user_aigul.user_id,
                role=FamilyRole.EDITOR.value,
            )
        )
        session.add(
            FamilyMembershipRow(
                membership_id=f"membership_{uuid.uuid4().hex}",
                family_id=family.family_id,
                user_id=user_serik.user_id,
                role=FamilyRole.VIEWER.value,
            )
        )

    print(f"[OK] Family created: '{family.name}' (ID: {family.family_id}) with 3 members.")

    fixtures = [
        (RECORDING_A_ID, "job_staging_001", "rec_a_kazakh.wav", "Күләш әже", build_synthetic_kazakh_result()),
        (RECORDING_B_ID, "job_staging_002", "rec_b_russian.wav", "Айгүл", build_synthetic_russian_result()),
        (RECORDING_C_ID, "job_staging_003", "rec_c_mixed.wav", "Серік ата", build_synthetic_mixed_result()),
    ]

    for rec_id, job_id, filename, speaker, result in fixtures:
        audio_file = audio_dir / filename
        audio_file.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00")
        recording_repo.create_recording_and_job(
            recording_id=rec_id,
            job_id=job_id,
            family_id=family.family_id,
            speaker_id=f"spk_{rec_id}",
            speaker_name=speaker,
            original_filename=filename,
            content_type="audio/wav",
            audio_path=audio_file,
            storage_key=f"{family.family_id}/{rec_id}/{filename}",
            storage_backend="local",
        )
        with database.session_factory.begin() as session:
            rec_row = session.get(RecordingRow, rec_id)
            if rec_row is None:
                raise RuntimeError(f"Recording {rec_id} not found after insert")
            ArchiveRepository.persist_pipeline_result(
                session,
                recording=rec_row,
                result=result,
            )
            finalize_recording_job(
                session,
                job_id=job_id,
                result=result,
                trace_events=[],
            )
        print(f"[OK] Grounded recording materialized: {rec_id} ({filename})")

    return {
        "family_id": family.family_id,
        "owner_id": user_aidar.user_id,
        "editor_id": user_aigul.user_id,
        "viewer_id": user_serik.user_id,
        "recordings": [RECORDING_A_ID, RECORDING_B_ID, RECORDING_C_ID],
    }


if __name__ == "__main__":
    db_url = os.environ.get("DATABASE_URL", "postgresql+psycopg://mura_test@127.0.0.1:5432/mura_leases_test")
    db = Database(db_url)
    manifest = seed_staging_fixture(db)
    print("RESULT: Deterministic staging fixture successfully created.")
    print(manifest)

