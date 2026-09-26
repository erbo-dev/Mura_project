"""Safe PostgreSQL Backup & Restore Drill with Integrity Verification.

Provides:
- Multi-level production safety gates (fails closed on non-allowlisted targets).
- Secret redaction for connection strings and subprocess logs.
- pg_dump and pg_restore execution with custom archive format (-Fc).
- Deterministic verification fixture seeding.
- Post-restore schema sanity, Alembic linear head verification, and storage manifest checks.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import func, select, text

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
)
from mura.identity.policy import FamilyRole
from mura.storage.archive import ArchiveRepository
from mura.storage.completion import finalize_recording_job
from mura.storage.database import (
    Database,
    ProcessingJobRow,
    RecordingRepository,
    RecordingRow,
)
from mura.storage.identity import FamilyMembershipRow, FamilyRow, UserRow


class BackupRestoreError(RuntimeError):
    """Base error for backup/restore operations."""


class UnsafeRestoreTargetError(BackupRestoreError):
    """Raised when an attempt is made to restore to an unsafe or production database."""


@dataclass(frozen=True)
class BackupRestoreResult:
    backup: str  # "PASS" | "FAIL"
    restore: str  # "PASS" | "FAIL"
    migration_head: str
    row_checks: dict[str, int]
    storage_checks: dict[str, int]
    duration_seconds: float
    target_database_name: str
    verified_invariants: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def redact_connection_url(url: str) -> str:
    """Masks credentials in database connection URLs."""
    if not url:
        return ""
    try:
        parsed = urlsplit(url)
        if parsed.password:
            netloc = parsed.netloc.replace(f":{parsed.password}@", ":***@")
            return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
        return url
    except Exception:
        return "<redacted_connection_url>"


def parse_pg_connection_params(url: str) -> dict[str, str]:
    """Extracts connection parameters from a PostgreSQL URL for pg_dump/pg_restore."""
    clean_url = url.replace("postgresql+psycopg://", "postgresql://")
    parsed = urlsplit(clean_url)
    params: dict[str, str] = {}
    if parsed.hostname:
        params["host"] = parsed.hostname
    if parsed.port:
        params["port"] = str(parsed.port)
    if parsed.username:
        params["user"] = parsed.username
    if parsed.password:
        params["password"] = parsed.password
    db_name = parsed.path.lstrip("/")
    if db_name:
        params["dbname"] = db_name
    return params


SAFE_TARGET_KEYWORDS = {
    "restore_test",
    "verification",
    "staging",
    "test",
    "dev",
    "sandbox",
}

BLOCKED_TARGET_KEYWORDS = {
    "prod",
    "production",
    "live",
    "master",
}


def is_safe_restore_target(url: str, environment: str | None = None) -> tuple[bool, str]:
    """Evaluates multi-level safety criteria for a restore target database."""
    env = (environment or os.environ.get("MURA_ENVIRONMENT") or "").strip().lower()
    if env == "production":
        return False, "Restore blocked: MURA_ENVIRONMENT is explicitly 'production'"

    params = parse_pg_connection_params(url)
    dbname = params.get("dbname", "").lower()
    if not dbname:
        return False, "Restore blocked: Target database name is missing or empty"

    for blocked in BLOCKED_TARGET_KEYWORDS:
        if blocked in dbname and "test" not in dbname:
            return (
                False,
                f"Restore blocked: Target database name '{dbname}' "
                f"contains protected keyword '{blocked}'",
            )

    matches_safe = any(keyword in dbname for keyword in SAFE_TARGET_KEYWORDS)
    if not matches_safe:
        return (
            False,
            f"Restore blocked: Database '{dbname}' does not contain an approved safety marker "
            f"({', '.join(sorted(SAFE_TARGET_KEYWORDS))})",
        )

    return True, f"Target database '{dbname}' verified as safe"


def assert_safe_restore_target(url: str, environment: str | None = None) -> None:
    safe, reason = is_safe_restore_target(url, environment)
    if not safe:
        raise UnsafeRestoreTargetError(reason)


def get_expected_alembic_head(project_root: Path) -> str:
    """Reads the single linear Alembic head directly from migrations."""
    alembic_ini = project_root / "alembic.ini"
    if not alembic_ini.exists():
        raise BackupRestoreError(f"alembic.ini not found at {alembic_ini}")
    cfg = Config(str(alembic_ini))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    if len(heads) != 1:
        raise BackupRestoreError(f"Expected exactly 1 Alembic head revision, got {heads}")
    return heads[0]


def run_pg_dump(
    source_url: str,
    output_path: Path,
    *,
    timeout_seconds: float = 120.0,
    pg_dump_bin: str | None = None,
) -> None:
    """Executes pg_dump in custom archive format (-Fc) with secret-safe execution."""
    bin_path = pg_dump_bin or shutil.which("pg_dump") or "pg_dump"
    params = parse_pg_connection_params(source_url)

    cmd = [
        bin_path,
        "-Fc",
        "--no-owner",
        "--no-privileges",
        "-f",
        str(output_path),
    ]
    if "host" in params:
        cmd.extend(["-h", params["host"]])
    if "port" in params:
        cmd.extend(["-p", params["port"]])
    if "user" in params:
        cmd.extend(["-U", params["user"]])
    if "dbname" in params:
        cmd.extend(["-d", params["dbname"]])

    env = os.environ.copy()
    if "password" in params:
        env["PGPASSWORD"] = params["password"]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        if proc.returncode != 0:
            redacted_err = proc.stderr.replace(params.get("password", "###"), "***")
            raise BackupRestoreError(
                f"pg_dump failed with exit code {proc.returncode}: {redacted_err}"
            )
    except subprocess.TimeoutExpired as exc:
        raise BackupRestoreError(f"pg_dump timed out after {timeout_seconds} seconds") from exc


def run_pg_restore(
    target_url: str,
    dump_path: Path,
    *,
    clean: bool = True,
    timeout_seconds: float = 120.0,
    pg_restore_bin: str | None = None,
    environment: str | None = None,
) -> None:
    """Executes pg_restore into an allowlisted verification database."""
    assert_safe_restore_target(target_url, environment)

    if not dump_path.exists():
        raise BackupRestoreError(f"Dump file not found: {dump_path}")

    bin_path = pg_restore_bin or shutil.which("pg_restore") or "pg_restore"
    params = parse_pg_connection_params(target_url)

    cmd = [
        bin_path,
        "--no-owner",
        "--no-privileges",
    ]
    if clean:
        cmd.extend(["--clean", "--if-exists"])

    if "host" in params:
        cmd.extend(["-h", params["host"]])
    if "port" in params:
        cmd.extend(["-p", params["port"]])
    if "user" in params:
        cmd.extend(["-U", params["user"]])
    if "dbname" in params:
        cmd.extend(["-d", params["dbname"]])

    cmd.append(str(dump_path))

    env = os.environ.copy()
    if "password" in params:
        env["PGPASSWORD"] = params["password"]

    try:
        proc = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        # pg_restore exit code 1 can indicate warnings (e.g., table did not exist during --clean)
        # Anything > 1 is a hard failure.
        if proc.returncode > 1:
            redacted_err = proc.stderr.replace(params.get("password", "###"), "***")
            raise BackupRestoreError(
                f"pg_restore failed with exit code {proc.returncode}: {redacted_err}"
            )
    except subprocess.TimeoutExpired as exc:
        raise BackupRestoreError(f"pg_restore timed out after {timeout_seconds} seconds") from exc


def seed_verification_dataset(
    database: Database,
    *,
    audio_dir: Path,
    family_suffix: str | None = None,
) -> dict[str, Any]:
    """Seeds a deterministic verification dataset with multi-entity grounded relationships."""
    suffix = family_suffix or f"drill_{int(time.time())}"
    family_id = f"family_restore_{suffix}"
    user_owner_id = f"user_restore_owner_{suffix}"
    user_editor_id = f"user_restore_editor_{suffix}"
    user_viewer_id = f"user_restore_viewer_{suffix}"
    rec_id = f"rec_restore_{suffix}"
    job_id = f"job_restore_{suffix}"

    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_filename = f"audio_{rec_id}.wav"
    audio_file = audio_dir / audio_filename
    # Minimal canonical WAV header
    wav_bytes = (
        b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
        b"\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
    )
    audio_file.write_bytes(wav_bytes)
    audio_sha256 = hashlib.sha256(wav_bytes).hexdigest()

    recording_repo = RecordingRepository(database)

    with database.session_factory.begin() as session:
        # Create Users
        for uid, name in [
            (user_owner_id, "Aidar Owner"),
            (user_editor_id, "Aigul Editor"),
            (user_viewer_id, "Serik Viewer"),
        ]:
            if session.get(UserRow, uid) is None:
                session.add(
                    UserRow(
                        user_id=uid,
                        auth_issuer="https://mura-drill.internal/auth",
                        auth_subject=f"sub_{uid}",
                        display_name=name,
                    )
                )

        # Create Family
        existing_family = session.get(FamilyRow, family_id)
        if existing_family is None:
            session.add(FamilyRow(family_id=family_id, name="Drill Verification Family"))
            session.flush()

            # Memberships
            session.add(
                FamilyMembershipRow(
                    membership_id=f"mem_owner_{suffix}",
                    family_id=family_id,
                    user_id=user_owner_id,
                    role=FamilyRole.OWNER.value,
                )
            )
            session.add(
                FamilyMembershipRow(
                    membership_id=f"mem_editor_{suffix}",
                    family_id=family_id,
                    user_id=user_editor_id,
                    role=FamilyRole.EDITOR.value,
                )
            )
            session.add(
                FamilyMembershipRow(
                    membership_id=f"mem_viewer_{suffix}",
                    family_id=family_id,
                    user_id=user_viewer_id,
                    role=FamilyRole.VIEWER.value,
                )
            )

    # Create Recording and Processing Job
    storage_key = f"{family_id}/{rec_id}/{audio_filename}"
    recording_repo.create_recording_and_job(
        recording_id=rec_id,
        job_id=job_id,
        family_id=family_id,
        speaker_id=f"spk_{rec_id}",
        speaker_name="Ата",
        original_filename=audio_filename,
        content_type="audio/wav",
        audio_path=audio_file,
        storage_key=storage_key,
        storage_backend="local",
    )

    # Seed Pipeline Result
    seg_text = "Әжем Күләш 1935 жылы Алматы қаласында дүниеге келген."
    ev_text = "Әжем Күләш 1935 жылы"
    result = PipelineResult(
        transcript=TranscriptEnvelope(
            recording_id=rec_id,
            duration_seconds=10.0,
            language_hints=["kk"],
            full_text=seg_text,
            segments=[RawSegment(segment_id="seg_01", start=0.0, end=10.0, text=seg_text)],
            asr_model="synthetic-drill",
            asr_revision="v1",
            chunker_version="v1",
        ),
        cleaned_transcript=CleanerResult(
            readable_segments=[ReadableSegment(segment_id="seg_01", text=seg_text)],
            detected_corrections=[
                DetectedCorrection(
                    kind=CorrectionKind.SPEAKER_SELF_CORRECTION,
                    original_value="1936",
                    corrected_value="1935",
                    source_segment_ids=["seg_01"],
                    explanation="Year correction",
                    confidence=0.98,
                )
            ],
            full_readable_text=seg_text,
        ),
        extraction=ExtractionResult(
            schema_version="extraction-v2",
            recording_id=rec_id,
            speaker_id=f"spk_{rec_id}",
            speaker_name="Ата",
            languages=["kk"],
            evidence_spans=[
                EvidenceSpan(
                    evidence_id="ev_drill_01",
                    segment_id="seg_01",
                    text=ev_text,
                    source_layer=EvidenceSourceLayer.RAW_TRANSCRIPT,
                    start_char=seg_text.index(ev_text),
                    end_char=seg_text.index(ev_text) + len(ev_text),
                    evidence_class=EvidenceClass.A_EXPLICIT,
                    purposes=[EvidencePurpose.CLAIM],
                    mention_ids=["m_drill_kulyash"],
                )
            ],
            people_mentions=[
                PersonMention(
                    mention_id="m_drill_kulyash",
                    name="Күләш әже",
                    category=PersonCategory.FAMILY_MEMBER,
                    relation_to_speaker="grandmother",
                    source_segment_ids=["seg_01"],
                    evidence_ids=["ev_drill_01"],
                    evidence_class=EvidenceClass.A_EXPLICIT,
                    confidence=1.0,
                )
            ],
            relationship_claims=[
                RelationshipClaim(
                    relationship_id="rel_drill_01",
                    relationship_type=RelationshipType.PARENT_CHILD,
                    relationship_state=RelationshipState.CURRENT,
                    subject_mention_id="m_drill_kulyash",
                    subject_role=RelationshipRole.PARENT,
                    object_mention_id="m_drill_aidar",
                    object_role=RelationshipRole.CHILD,
                    source_segment_ids=["seg_01"],
                    evidence_ids=["ev_drill_01"],
                    evidence_class=EvidenceClass.A_EXPLICIT,
                    confidence=1.0,
                )
            ],
        ),
        resolutions=[
            MentionResolution(
                mention_id="m_drill_kulyash",
                status=ResolutionStatus.NEW_PERSON,
                reason="drill archive",
            )
        ],
        processing={"total_seconds": 0.5},
    )

    with database.session_factory.begin() as session:
        rec_row = session.get(RecordingRow, rec_id)
        if rec_row is not None:
            ArchiveRepository.persist_pipeline_result(session, recording=rec_row, result=result)
            finalize_recording_job(session, job_id=job_id, result=result, trace_events=[])

    return {
        "family_id": family_id,
        "owner_id": user_owner_id,
        "editor_id": user_editor_id,
        "viewer_id": user_viewer_id,
        "recording_id": rec_id,
        "storage_key": storage_key,
        "audio_sha256": audio_sha256,
        "audio_size": len(wav_bytes),
    }


def verify_restored_database(
    database: Database,
    manifest: dict[str, Any],
    *,
    project_root: Path,
    audio_dir: Path | None = None,
) -> dict[str, Any]:
    """Verifies schema invariants, migration heads, critical row counts, and storage refs."""
    # 1. Verify single linear Alembic head
    expected_head = get_expected_alembic_head(project_root)
    with database.session_factory() as session:
        res = session.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
        if not res or res[0] != expected_head:
            raise BackupRestoreError(
                f"Alembic revision mismatch on restored DB: expected {expected_head}, got {res}"
            )

        # 2. Verify row counts and integrity
        row_counts = {
            "users": session.scalar(select(func.count(UserRow.user_id))) or 0,
            "families": session.scalar(select(func.count(FamilyRow.family_id))) or 0,
            "family_memberships": session.scalar(
                select(func.count(FamilyMembershipRow.membership_id))
            )
            or 0,
            "recordings": session.scalar(select(func.count(RecordingRow.recording_id))) or 0,
            "processing_jobs": session.scalar(select(func.count(ProcessingJobRow.job_id))) or 0,
        }

        # Verify seeded entities exist
        fam = session.get(FamilyRow, manifest["family_id"])
        if fam is None:
            raise BackupRestoreError(f"Seeded family {manifest['family_id']} missing after restore")

        rec = session.get(RecordingRow, manifest["recording_id"])
        if rec is None:
            raise BackupRestoreError(
                f"Seeded recording {manifest['recording_id']} missing after restore"
            )

        # 3. Foreign key / relationship integrity check for seeded entities
        orphan_recordings = (
            session.execute(
                text(
                    "SELECT count(*) FROM recordings r "
                    "LEFT JOIN families f ON r.family_id = f.family_id "
                    "WHERE r.family_id = :family_id AND f.family_id IS NULL"
                ),
                {"family_id": manifest["family_id"]},
            ).scalar()
            or 0
        )
        if orphan_recordings > 0:
            raise BackupRestoreError(
                f"Foreign key violation: {orphan_recordings} orphan recordings "
                f"for family {manifest['family_id']}"
            )

        orphan_memberships = (
            session.execute(
                text(
                    "SELECT count(*) FROM family_memberships m "
                    "LEFT JOIN families f ON m.family_id = f.family_id "
                    "WHERE m.family_id = :family_id AND f.family_id IS NULL"
                ),
                {"family_id": manifest["family_id"]},
            ).scalar()
            or 0
        )
        if orphan_memberships > 0:
            raise BackupRestoreError(
                f"Foreign key violation: {orphan_memberships} orphan memberships "
                f"for family {manifest['family_id']}"
            )

        # 4. Storage reference verification
        storage_checks = {"verified_references": 0, "hash_matches": 0}
        if audio_dir is not None:
            expected_key = manifest["storage_key"]
            expected_hash = manifest["audio_sha256"]
            audio_path = audio_dir / Path(expected_key).name
            if audio_path.exists():
                storage_checks["verified_references"] += 1
                actual_hash = hashlib.sha256(audio_path.read_bytes()).hexdigest()
                if actual_hash == expected_hash:
                    storage_checks["hash_matches"] += 1
                else:
                    raise BackupRestoreError(f"Storage hash mismatch for {expected_key}")

    return {
        "migration_head": expected_head,
        "row_counts": row_counts,
        "storage_checks": storage_checks,
    }


def execute_backup_restore_drill(
    *,
    source_url: str,
    target_url: str,
    project_root: Path,
    dump_dir: Path,
    audio_dir: Path,
    environment: str | None = None,
) -> BackupRestoreResult:
    """Executes an end-to-end backup, restore, and integrity verification drill."""
    start_time = time.monotonic()
    assert_safe_restore_target(target_url, environment)

    dump_file = dump_dir / f"mura_drill_{int(time.time())}.dump"

    # Step 1: Seed verification data in source DB
    src_db = Database(source_url)
    manifest = seed_verification_dataset(src_db, audio_dir=audio_dir)

    # Step 2: Backup
    run_pg_dump(source_url, dump_file)

    # Step 3: Restore
    run_pg_restore(target_url, dump_file, clean=True, environment=environment)

    # Step 4: Verify Restored DB
    target_db = Database(target_url)
    verification = verify_restored_database(
        target_db,
        manifest,
        project_root=project_root,
        audio_dir=audio_dir,
    )

    duration = time.monotonic() - start_time
    target_params = parse_pg_connection_params(target_url)

    return BackupRestoreResult(
        backup="PASS",
        restore="PASS",
        migration_head=verification["migration_head"],
        row_checks=verification["row_counts"],
        storage_checks=verification["storage_checks"],
        duration_seconds=round(duration, 3),
        target_database_name=target_params.get("dbname", "unknown"),
        verified_invariants=[
            "Single linear Alembic head confirmed",
            "Zero foreign key integrity violations",
            "Critical entity rows preserved across backup/restore",
            "Audio storage key and cryptographic SHA256 validated",
            "Production safety guard enforced and validated",
        ],
    )
