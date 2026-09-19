"""Staging security, BOLA and privacy audit suite.

Validates:
1. Cross-Family BOLA / IDOR matrix across 10 critical endpoints (returns 404, never 403 or data leak).
2. Role & Capability matrix (Viewer blocked with 403, Editor blocked from family deletion with 403).
3. Privacy deletion cascades (recording, book, and complete family wipeout including storage cleanup).
4. Privacy data export (GDPR structured export with zero leaked secrets or internal credentials).
"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import select

from apps.api.main import create_app, get_auth_verifier, get_runtime, get_settings
from mura.config import CoreSettings
from mura.domain.book_models import (
    BookLanguage,
    BookStage,
    BookStatus,
    ChapterStatus,
    ExportFormat,
    ExportStatus,
)
from mura.identity.policy import FamilyRole
from mura.jobs import JobStatus
from mura.storage.archive import (
    ArchiveClaimRow,
    ArchivePersonRow,
    FamilyGraphEdgeRow,
)
from mura.storage.audio import LocalAudioStorage
from mura.storage.book import (
    BookChapterRepository,
    BookChapterRow,
    BookExportRepository,
    BookExportRow,
    BookJobRepository,
    BookJobRow,
    BookRepository,
    BookRow,
)
from mura.storage.book_artifacts import LocalBookArtifactStorage
from mura.storage.database import (
    Database,
    PipelineResultRow,
    ProcessingJobRow,
    RecordingRepository,
    RecordingRow,
    utcnow,
)
from mura.storage.identity import FamilyMembershipRow, FamilyRow, IdentityRepository, UserRow
from tests.authz_factories import (
    FakePrincipalVerifier,
    TestIdentity,
    create_family_with_id,
    create_membership,
    create_test_user,
)

CORE_TOKEN = "c" * 40
OPERATIONS_TOKEN = "o" * 40
WORKER_TOKEN = "w" * 40
ASR_TOKEN = "a" * 40
DEEPSEEK_KEY = "sk-" + "d" * 40

WAV_CONTENT = b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"


def _create_test_settings(tmp_path: Path) -> CoreSettings:
    return CoreSettings.model_validate(
        {
            "MURA_ENVIRONMENT": "local",
            "DEEPSEEK_API_KEY": DEEPSEEK_KEY,
            "CORE_API_KEY": CORE_TOKEN,
            "OPERATIONS_API_KEY": OPERATIONS_TOKEN,
            "WORKER_REGISTRATION_TOKEN": WORKER_TOKEN,
            "KAGGLE_ASR_API_KEY": ASR_TOKEN,
            "DATABASE_URL": "sqlite+pysqlite:///:memory:",
            "DATABASE_AUTO_CREATE": True,
            "BOOK_STORAGE_DIR": str(tmp_path / "book_artifacts"),
        }
    )


@pytest.fixture
def staging_security_env(tmp_path: Path) -> dict[str, Any]:
    db = Database("sqlite+pysqlite:///:memory:")
    db.create_schema()
    settings = _create_test_settings(tmp_path)
    identity_repo = IdentityRepository(db)

    audio_storage_dir = tmp_path / "audio"
    audio_storage_dir.mkdir(parents=True, exist_ok=True)
    audio_storage = LocalAudioStorage(audio_storage_dir, max_upload_bytes=10 * 1024 * 1024)

    book_artifacts_dir = tmp_path / "book_artifacts"
    book_artifacts_dir.mkdir(parents=True, exist_ok=True)
    book_artifact_storage = LocalBookArtifactStorage(book_artifacts_dir)

    verifier = FakePrincipalVerifier()

    # Create users
    owner_1 = create_test_user(identity_repo, subject="owner_1", email="owner1@mura.kz", display_name="Owner One", verifier=verifier)
    editor_1 = create_test_user(identity_repo, subject="editor_1", email="editor1@mura.kz", display_name="Editor One", verifier=verifier)
    viewer_1 = create_test_user(identity_repo, subject="viewer_1", email="viewer1@mura.kz", display_name="Viewer One", verifier=verifier)

    owner_2 = create_test_user(identity_repo, subject="owner_2", email="owner2@mura.kz", display_name="Owner Two", verifier=verifier)

    # Create families
    fam_1 = "fam_staging_sec_1"
    fam_2 = "fam_staging_sec_2"
    create_family_with_id(db, family_id=fam_1, name="Family 1", owner=owner_1)
    create_family_with_id(db, family_id=fam_2, name="Family 2", owner=owner_2)

    create_membership(db, family_id=fam_1, user=editor_1, role=FamilyRole.EDITOR)
    create_membership(db, family_id=fam_1, user=viewer_1, role=FamilyRole.VIEWER)

    # Seed resources in Family 1
    rec_1 = "rec_sec_001"
    job_1 = "job_sec_001"
    book_1 = "book_sec_001"

    rec_repo = RecordingRepository(db)
    stored_rec_1 = audio_storage.save(
        family_id=fam_1,
        recording_id=rec_1,
        original_filename="rec1.wav",
        content_type="audio/wav",
        source=io.BytesIO(WAV_CONTENT),
    )
    rec_repo.create_recording_and_job(
        recording_id=rec_1,
        job_id=job_1,
        family_id=fam_1,
        speaker_id="spk_1",
        speaker_name="Speaker One",
        original_filename="rec1.wav",
        content_type="audio/wav",
        audio_path=f"{fam_1}/{rec_1}.wav",
        storage_key=stored_rec_1.storage_key,
    )

    now = utcnow()
    with db.session_factory.begin() as session:
        session.add(
            BookRow(
                book_id=book_1,
                family_id=fam_1,
                title="Family 1 Story Book",
                created_by_user_id=owner_1.user_id,
                status=BookStatus.COMPLETED.value,
                stage=BookStage.COMPLETED.value,
                output_language=BookLanguage.KK.value,
                target_word_count=15000,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            ArchivePersonRow(
                person_id="person_sec_001",
                family_id=fam_1,
                canonical_name="Amina Apashka",
                normalized_name="amina apashka",
                category="family_member",
                source_recording_ids=[rec_1],
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            ArchiveClaimRow(
                claim_id="claim_sec_001",
                family_id=fam_1,
                recording_id=rec_1,
                object_type="story",
                source_object_id="story_001",
                predicate="story",
                payload={"title": "Amina's Story", "summary": "Childhood in Almaty"},
                evidence_class="a_explicit",
                verification_status="unverified",
                created_at=now,
            )
        )
        session.add(
            FamilyGraphEdgeRow(
                edge_id="edge_sec_001",
                family_id=fam_1,
                relationship_type="parent_child",
                subject_person_id="person_sec_001",
                subject_role="parent",
                object_person_id="person_sec_001",
                object_role="child",
                source_claim_ids=[],
                created_at=now,
                updated_at=now,
            )
        )

    chapter_repo = BookChapterRepository(db)
    chapter_repo.create_chapter_stubs(
        book_id=book_1,
        chapter_plans=[{"chapter_number": 1, "title": "1-тарау. Басы", "source_recording_ids": [rec_1]}],
    )
    chapter_repo.approve_chapter(book_id=book_1, chapter_number=1, final_text="Балалық шақ туралы.", word_count=3)

    # Save PDF export for Book 1
    storage_key = book_artifact_storage.store(
        family_id=fam_1,
        book_id=book_1,
        export_format=ExportFormat.PDF,
        data=b"%PDF-1.4 mock pdf content",
    )
    export_repo = BookExportRepository(db)
    export_repo.save_export(
        export_id="exp_pdf_001",
        book_id=book_1,
        format=ExportFormat.PDF.value,
        status=ExportStatus.READY.value,
        storage_key=storage_key,
        storage_backend="local",
        size_bytes=1024,
    )

    # Seed resources in Family 2
    rec_2 = "rec_sec_002"
    job_2 = "job_sec_002"
    book_2 = "book_sec_002"

    audio_storage.save(
        family_id=fam_2,
        recording_id=rec_2,
        original_filename="rec2.wav",
        content_type="audio/wav",
        source=io.BytesIO(WAV_CONTENT),
    )
    rec_repo.create_recording_and_job(
        recording_id=rec_2,
        job_id=job_2,
        family_id=fam_2,
        speaker_id="spk_2",
        speaker_name="Speaker Two",
        original_filename="rec2.wav",
        content_type="audio/wav",
        audio_path=f"{fam_2}/{rec_2}.wav",
    )
    with db.session_factory.begin() as session:
        session.add(
            BookRow(
                book_id=book_2,
                family_id=fam_2,
                title="Family 2 Secret Book",
                created_by_user_id=owner_2.user_id,
                status=BookStatus.COMPLETED.value,
                stage=BookStage.COMPLETED.value,
                output_language=BookLanguage.RU.value,
                target_word_count=20000,
                created_at=now,
                updated_at=now,
            )
        )

    # Build FastAPI test client with injected dependencies
    app = create_app(settings)
    runtime = SimpleNamespace(
        database=db,
        settings=settings,
        repository=rec_repo,
        storage=audio_storage,
        artifact_storage=book_artifact_storage,
        book_artifact_storage=book_artifact_storage,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_runtime] = lambda: runtime
    app.dependency_overrides[get_auth_verifier] = lambda: verifier

    client = TestClient(app, raise_server_exceptions=False)

    return {
        "client": client,
        "db": db,
        "audio_storage": audio_storage,
        "book_artifact_storage": book_artifact_storage,
        "fam_1": fam_1,
        "fam_2": fam_2,
        "owner_1": owner_1,
        "editor_1": editor_1,
        "viewer_1": viewer_1,
        "owner_2": owner_2,
        "rec_1": rec_1,
        "book_1": book_1,
        "rec_2": rec_2,
        "book_2": book_2,
        "storage_key": storage_key,
        "audio_storage_key_1": stored_rec_1.storage_key,
    }


# ==============================================================================
# 1. CROSS-FAMILY BOLA MATRIX (10 Endpoints)
# ==============================================================================


@pytest.mark.staging
def test_bola_cross_family_isolation_matrix(staging_security_env: dict[str, Any]) -> None:
    """Verifies that an authenticated member of Family 1 receives 404 (never 403 or 200)

    when targeting Family 2 endpoints, preventing identifier oracle and data leakage.
    """
    client: TestClient = staging_security_env["client"]
    owner_1: TestIdentity = staging_security_env["owner_1"]
    fam_2 = staging_security_env["fam_2"]
    rec_2 = staging_security_env["rec_2"]
    book_2 = staging_security_env["book_2"]

    endpoints = [
        ("GET", f"/v1/families/{fam_2}"),
        ("GET", f"/v1/families/{fam_2}/members"),
        ("GET", f"/v1/families/{fam_2}/archive"),
        ("GET", f"/v1/families/{fam_2}/people"),
        ("GET", f"/v1/families/{fam_2}/stories"),
        ("GET", f"/v1/families/{fam_2}/recordings/{rec_2}"),
        ("GET", f"/v1/families/{fam_2}/books"),
        ("GET", f"/v1/families/{fam_2}/books/{book_2}"),
        ("GET", f"/v1/families/{fam_2}/books/{book_2}/chapters"),
        ("POST", f"/v1/families/{fam_2}/books"),
        ("DELETE", f"/v1/families/{fam_2}/recordings/{rec_2}"),
        ("DELETE", f"/v1/families/{fam_2}/books/{book_2}"),
        ("GET", f"/v1/families/{fam_2}/privacy/export"),
    ]

    for method, path in endpoints:
        if method == "GET":
            resp = client.get(path, headers=owner_1.headers)
        elif method == "POST":
            resp = client.post(
                path,
                headers=owner_1.headers,
                json={"title": "Infiltrate Book", "output_language": "ru"},
            )
        elif method == "DELETE":
            resp = client.delete(path, headers=owner_1.headers)
        else:
            continue

        # Must return 404 to avoid disclosing whether resource or family exists
        assert resp.status_code == status.HTTP_404_NOT_FOUND, (
            f"BOLA failure on {method} {path}: expected 404, got {resp.status_code}"
        )
        assert fam_2 not in resp.text
        assert book_2 not in resp.text
        assert rec_2 not in resp.text


# ==============================================================================
# 2. ROLE & CAPABILITY MATRIX
# ==============================================================================


@pytest.mark.staging
def test_role_matrix_viewer_cannot_mutate_or_create(staging_security_env: dict[str, Any]) -> None:
    """Verifies that VIEWER role is blocked from uploading, book generation, and deletions."""
    client: TestClient = staging_security_env["client"]
    viewer_1: TestIdentity = staging_security_env["viewer_1"]
    fam_1 = staging_security_env["fam_1"]
    rec_1 = staging_security_env["rec_1"]
    book_1 = staging_security_env["book_1"]

    # 1. Viewer cannot create book -> 403
    resp = client.post(
        f"/v1/families/{fam_1}/books",
        headers=viewer_1.headers,
        json={"title": "Unauthorized Book", "output_language": "ru"},
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN

    # 2. Viewer cannot delete book -> 403
    resp = client.delete(
        f"/v1/families/{fam_1}/books/{book_1}",
        headers=viewer_1.headers,
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN

    # 3. Viewer cannot delete recording -> 403
    resp = client.delete(
        f"/v1/families/{fam_1}/recordings/{rec_1}",
        headers=viewer_1.headers,
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN

    # 4. Viewer cannot delete family -> 403
    resp = client.request(
        "DELETE",
        f"/v1/families/{fam_1}",
        headers=viewer_1.headers,
        json={"confirm_family_id": fam_1},
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.staging
def test_role_matrix_editor_cannot_delete_family(staging_security_env: dict[str, Any]) -> None:
    """Verifies that EDITOR role cannot delete the entire family (only OWNER can)."""
    client: TestClient = staging_security_env["client"]
    editor_1: TestIdentity = staging_security_env["editor_1"]
    fam_1 = staging_security_env["fam_1"]

    resp = client.request(
        "DELETE",
        f"/v1/families/{fam_1}",
        headers=editor_1.headers,
        json={"confirm_family_id": fam_1},
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN


# ==============================================================================
# 3. PRIVACY DELETION CASCADES
# ==============================================================================


@pytest.mark.staging
def test_privacy_cascade_delete_recording(staging_security_env: dict[str, Any]) -> None:
    """Verifies that deleting a recording cleans up storage and database records."""
    client: TestClient = staging_security_env["client"]
    owner_1: TestIdentity = staging_security_env["owner_1"]
    db: Database = staging_security_env["db"]
    audio_storage: LocalAudioStorage = staging_security_env["audio_storage"]
    fam_1 = staging_security_env["fam_1"]
    rec_1 = staging_security_env["rec_1"]

    # Delete recording as owner -> 204 NO CONTENT
    resp = client.delete(f"/v1/families/{fam_1}/recordings/{rec_1}", headers=owner_1.headers)
    assert resp.status_code == status.HTTP_204_NO_CONTENT

    # Audio file must be completely wiped from storage
    assert not audio_storage.exists(staging_security_env["audio_storage_key_1"])

    # Database records must be removed
    with db.session_factory() as session:
        rec = session.scalar(select(RecordingRow).where(RecordingRow.recording_id == rec_1))
        assert rec is None
        job = session.scalar(select(ProcessingJobRow).where(ProcessingJobRow.recording_id == rec_1))
        assert job is None


@pytest.mark.staging
def test_privacy_cascade_delete_book(staging_security_env: dict[str, Any]) -> None:
    """Verifies that deleting a book cascades to chapters, exports, and artifact storage."""
    client: TestClient = staging_security_env["client"]
    owner_1: TestIdentity = staging_security_env["owner_1"]
    db: Database = staging_security_env["db"]
    fam_1 = staging_security_env["fam_1"]
    book_1 = staging_security_env["book_1"]

    # Delete book as owner -> 204 NO CONTENT
    resp = client.delete(f"/v1/families/{fam_1}/books/{book_1}", headers=owner_1.headers)
    assert resp.status_code == status.HTTP_204_NO_CONTENT

    # Book, chapters, exports must be wiped from database
    with db.session_factory() as session:
        b = session.scalar(select(BookRow).where(BookRow.book_id == book_1))
        assert b is None
        ch = session.scalars(select(BookChapterRow).where(BookChapterRow.book_id == book_1)).all()
        assert len(ch) == 0
        exp = session.scalars(select(BookExportRow).where(BookExportRow.book_id == book_1)).all()
        assert len(exp) == 0

    # Artifact storage must be cleaned up
    book_artifacts: LocalBookArtifactStorage = staging_security_env["book_artifact_storage"]
    assert not book_artifacts.exists(storage_key=staging_security_env["storage_key"])


@pytest.mark.staging
def test_privacy_cascade_delete_entire_family(staging_security_env: dict[str, Any]) -> None:
    """Verifies that deleting a family wipes all members, graph edges, claims, people, and the family row."""
    client: TestClient = staging_security_env["client"]
    owner_1: TestIdentity = staging_security_env["owner_1"]
    db: Database = staging_security_env["db"]
    fam_1 = staging_security_env["fam_1"]

    # Delete family with confirm_family_id -> 204 NO CONTENT
    resp = client.request(
        "DELETE",
        f"/v1/families/{fam_1}",
        headers=owner_1.headers,
        json={"confirm_family_id": fam_1},
    )
    assert resp.status_code == status.HTTP_204_NO_CONTENT

    # Verify complete wipeout in DB
    with db.session_factory() as session:
        fam = session.scalar(select(FamilyRow).where(FamilyRow.family_id == fam_1))
        assert fam is None
        members = session.scalars(select(FamilyMembershipRow).where(FamilyMembershipRow.family_id == fam_1)).all()
        assert len(members) == 0
        claims = session.scalars(select(ArchiveClaimRow).where(ArchiveClaimRow.family_id == fam_1)).all()
        assert len(claims) == 0
        edges = session.scalars(select(FamilyGraphEdgeRow).where(FamilyGraphEdgeRow.family_id == fam_1)).all()
        assert len(edges) == 0
        people = session.scalars(select(ArchivePersonRow).where(ArchivePersonRow.family_id == fam_1)).all()
        assert len(people) == 0


# ==============================================================================
# 4. PRIVACY DATA EXPORT & SECRET LEAK AUDIT
# ==============================================================================


@pytest.mark.staging
def test_privacy_export_structure_and_zero_leakage(staging_security_env: dict[str, Any]) -> None:
    """Verifies structured GDPR/privacy export contains family data with zero leaked secret keys."""
    client: TestClient = staging_security_env["client"]
    owner_1: TestIdentity = staging_security_env["owner_1"]
    fam_1 = staging_security_env["fam_1"]
    rec_1 = staging_security_env["rec_1"]
    book_1 = staging_security_env["book_1"]

    resp = client.get(f"/v1/families/{fam_1}/privacy/export", headers=owner_1.headers)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()

    # Verify structure
    assert data["family"]["family_id"] == fam_1
    assert "members" in data
    assert "recordings" in data
    assert "people" in data
    assert "relationships" in data
    assert "stories" in data
    assert "books" in data

    # Verify members
    member_user_ids = {m["user_id"] for m in data["members"]}
    assert owner_1.user_id in member_user_ids

    # Verify recordings
    rec_ids = {r["recording_id"] for r in data["recordings"]}
    assert rec_1 in rec_ids

    # Verify people & stories
    assert len(data["people"]) >= 1
    assert data["people"][0]["canonical_name"] == "Amina Apashka"
    assert len(data["stories"]) >= 1
    assert data["stories"][0]["title"] == "Amina's Story"

    # Verify books
    book_ids = {b["book_id"] for b in data["books"]}
    assert book_1 in book_ids

    # LEAK AUDIT: Ensure no API keys, tokens, or hashes are in the exported JSON
    export_str = resp.text
    forbidden_tokens = [
        DEEPSEEK_KEY,
        CORE_TOKEN,
        OPERATIONS_TOKEN,
        WORKER_TOKEN,
        ASR_TOKEN,
        "service_role_key",
        "jwt_secret",
    ]
    for token in forbidden_tokens:
        assert token not in export_str, f"Privacy violation: token {token} found in privacy export!"
