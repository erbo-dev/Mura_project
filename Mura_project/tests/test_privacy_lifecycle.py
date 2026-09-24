"""Tests for Privacy Deletion Lifecycle & Data Export (Phase 2.3 Part F).

Covers:
- recording deletion authorization, DB cascade, audio cleanup, and BOLA;
- Book deletion authorization, DB cascade, artifact cleanup, and BOLA;
- family deletion confirmation, sole-owner invariant, DB/storage cleanup;
- complete structured family privacy export.
"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app, get_auth_verifier, get_runtime, get_settings
from mura.config import CoreSettings
from mura.domain.book_models import ExportFormat, ExportStatus
from mura.identity.policy import FamilyRole
from mura.jobs import JobStatus
from mura.orchestration.cleanup import StorageCleanupWorker
from mura.storage.archive import (
    ArchiveClaimRow,
    ArchivePersonRow,
    FamilyGraphEdgeRow,
)
from mura.storage.audio import LegacyLocalAudioStorage, LocalAudioStorage
from mura.storage.book import (
    BookChapterRow,
    BookExportRepository,
    BookRepository,
)
from mura.storage.book_artifacts import LocalBookArtifactStorage
from mura.storage.cleanup import (
    StorageCleanupJobRow,
    StorageCleanupRepository,
    StorageCleanupStatus,
    StorageKind,
)
from mura.storage.database import (
    Database,
    PipelineResultRow,
    ProcessingJobRow,
    RecordingRepository,
    RecordingRow,
)
from mura.storage.identity import FamilyRow, IdentityRepository
from tests.authz_factories import (
    FakePrincipalVerifier,
    TestIdentity,
    create_family_with_id,
    create_membership,
    create_test_user,
)

DEEPSEEK_KEY = "sk-" + "d" * 40
REGISTRATION_TOKEN = "r" * 40
ASR_TOKEN = "a" * 40
CORE_TOKEN = "c" * 40


def _settings(tmp_path: Path) -> CoreSettings:
    return CoreSettings.model_validate(
        {
            "DEEPSEEK_API_KEY": DEEPSEEK_KEY,
            "CORE_API_KEY": CORE_TOKEN,
            "WORKER_REGISTRATION_TOKEN": REGISTRATION_TOKEN,
            "KAGGLE_ASR_API_KEY": ASR_TOKEN,
            "OPERATIONS_API_KEY": "o" * 40,
            "DATABASE_URL": "sqlite+pysqlite:///:memory:",
            "BOOK_STORAGE_DIR": str(tmp_path / "book_artifacts"),
        }
    )


def _drain_cleanup(test_setup: dict[str, object]) -> None:
    db: Database = test_setup["database"]
    audio_storage: LocalAudioStorage = test_setup["audio_storage"]
    book_storage: LocalBookArtifactStorage = test_setup["book_storage"]
    worker = StorageCleanupWorker(
        repository=StorageCleanupRepository(db),
        storage_targets={
            (StorageKind.AUDIO.value, "local"): audio_storage,
            (StorageKind.AUDIO.value, "legacy_local"): LegacyLocalAudioStorage(),
            (StorageKind.BOOK_ARTIFACT.value, "local"): book_storage,
        },
        worker_id="worker_privacy_cleanup",
    )
    while worker.process_once():
        pass


@pytest.fixture
def test_setup(tmp_path: Path) -> dict[str, object]:
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    settings = _settings(tmp_path)
    identity_repo = IdentityRepository(database)

    audio_storage = LocalAudioStorage(tmp_path / "audio", max_upload_bytes=10 * 1024 * 1024)
    book_storage = LocalBookArtifactStorage(tmp_path / "book_artifacts")

    verifier = FakePrincipalVerifier()

    # Users
    user_owner = create_test_user(
        identity_repo,
        subject="owner",
        email="owner@test.kz",
        display_name="Owner User",
        verifier=verifier,
    )
    user_owner2 = create_test_user(
        identity_repo,
        subject="owner2",
        email="owner2@test.kz",
        display_name="Owner 2",
        verifier=verifier,
    )
    user_editor = create_test_user(
        identity_repo,
        subject="editor",
        email="editor@test.kz",
        display_name="Editor User",
        verifier=verifier,
    )
    user_viewer = create_test_user(
        identity_repo,
        subject="viewer",
        email="viewer@test.kz",
        display_name="Viewer User",
        verifier=verifier,
    )
    user_stranger = create_test_user(
        identity_repo,
        subject="stranger",
        email="stranger@test.kz",
        display_name="Stranger User",
        verifier=verifier,
    )

    # Families
    create_family_with_id(database, family_id="fam_alpha", name="Alpha Family", owner=user_owner)
    create_family_with_id(database, family_id="fam_beta", name="Beta Family", owner=user_stranger)

    # Additional Memberships in Alpha
    create_membership(database, family_id="fam_alpha", user=user_editor, role=FamilyRole.EDITOR)
    create_membership(database, family_id="fam_alpha", user=user_viewer, role=FamilyRole.VIEWER)

    # App and clients
    app = create_app(settings)

    recording_repo = RecordingRepository(database)
    mock_runtime = SimpleNamespace(
        database=database,
        settings=settings,
        storage=audio_storage,
        repository=recording_repo,
    )

    app.dependency_overrides[get_auth_verifier] = lambda: verifier
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_runtime] = lambda: mock_runtime

    client = TestClient(app)

    return {
        "database": database,
        "settings": settings,
        "audio_storage": audio_storage,
        "book_storage": book_storage,
        "client": client,
        "tmp_path": tmp_path,
        "user_owner": user_owner,
        "user_owner2": user_owner2,
        "user_editor": user_editor,
        "user_viewer": user_viewer,
        "user_stranger": user_stranger,
    }


def test_delete_recording_success_and_storage_cleanup(test_setup: dict[str, object]) -> None:
    db: Database = test_setup["database"]
    audio_storage: LocalAudioStorage = test_setup["audio_storage"]
    client: TestClient = test_setup["client"]
    user_owner: TestIdentity = test_setup["user_owner"]

    # Save physical audio in storage
    stored = audio_storage.save(
        family_id="fam_alpha",
        recording_id="rec_to_delete_1",
        original_filename="audio.wav",
        content_type="audio/wav",
        source=io.BytesIO(b"RIFF....WAVEfmt ....data...."),
    )
    assert audio_storage.exists(stored.storage_key)

    # Seed DB records
    with db.session_factory.begin() as session:
        session.add(
            RecordingRow(
                recording_id="rec_to_delete_1",
                family_id="fam_alpha",
                speaker_id="spk_del",
                speaker_name="Speaker Alpha",
                original_filename="audio.wav",
                content_type="audio/wav",
                audio_path=stored.storage_key,
                storage_key=stored.storage_key,
            )
        )
        session.add(
            ProcessingJobRow(
                job_id="job_to_delete_1",
                recording_id="rec_to_delete_1",
                status=JobStatus.COMPLETED.value,
                stage="completed",
            )
        )
        session.add(
            PipelineResultRow(
                recording_id="rec_to_delete_1",
                payload={"stories": [{"story_id": "st_1", "title": "Memory"}]},
            )
        )
        session.add(
            ArchiveClaimRow(
                claim_id="cl_story_1",
                family_id="fam_alpha",
                recording_id="rec_to_delete_1",
                object_type="story",
                source_object_id="story_1",
                predicate="story",
                payload={"title": "Memory", "summary": "Text of memory"},
                evidence_class="A_explicit",
                verification_status="unreviewed",
            )
        )

    # Delete with Owner token
    resp = client.delete(
        "/v1/families/fam_alpha/recordings/rec_to_delete_1",
        headers=user_owner.headers,
    )
    assert resp.status_code == 204

    # Relational deletion is synchronous; physical erasure is durable async work.
    assert audio_storage.exists(stored.storage_key)
    with db.session_factory() as session:
        cleanup = list(session.query(StorageCleanupJobRow).all())
        assert any(row.storage_key == stored.storage_key for row in cleanup)
        assert all(row.status == StorageCleanupStatus.QUEUED.value for row in cleanup)
    _drain_cleanup(test_setup)
    assert not audio_storage.exists(stored.storage_key)

    # Assert DB cleanup
    with db.session_factory() as session:
        assert session.get(RecordingRow, "rec_to_delete_1") is None
        assert session.get(ProcessingJobRow, "job_to_delete_1") is None
        assert session.get(PipelineResultRow, "rec_to_delete_1") is None
        assert session.get(ArchiveClaimRow, "cl_story_1") is None


def test_delete_recording_viewer_forbidden(test_setup: dict[str, object]) -> None:
    client: TestClient = test_setup["client"]
    user_viewer: TestIdentity = test_setup["user_viewer"]
    resp = client.delete(
        "/v1/families/fam_alpha/recordings/rec_to_delete_1",
        headers=user_viewer.headers,
    )
    assert resp.status_code == 403


def test_delete_recording_nonexistent_or_other_family_404(test_setup: dict[str, object]) -> None:
    client: TestClient = test_setup["client"]
    user_owner: TestIdentity = test_setup["user_owner"]
    user_stranger: TestIdentity = test_setup["user_stranger"]

    # Nonexistent recording
    resp = client.delete(
        "/v1/families/fam_alpha/recordings/rec_nonexistent",
        headers=user_owner.headers,
    )
    assert resp.status_code == 404

    # Stranger trying to access fam_alpha
    resp = client.delete(
        "/v1/families/fam_alpha/recordings/rec_to_delete_1",
        headers=user_stranger.headers,
    )
    assert resp.status_code == 404


def test_delete_book_success_and_artifacts_cleanup(test_setup: dict[str, object]) -> None:
    db: Database = test_setup["database"]
    book_storage: LocalBookArtifactStorage = test_setup["book_storage"]
    client: TestClient = test_setup["client"]
    user_editor: TestIdentity = test_setup["user_editor"]

    book_repo = BookRepository(db)
    export_repo = BookExportRepository(db)

    # Create book and chapter
    book = book_repo.create_book(
        book_id="book_to_delete_1",
        family_id="fam_alpha",
        created_by_user_id="user_owner",
        title="Delete Test Book",
        output_language="ru",
        target_word_count=5000,
    )
    with db.session_factory.begin() as session:
        session.add(
            BookChapterRow(
                chapter_id="ch_del_1",
                book_id=book.book_id,
                chapter_number=1,
                status="approved",
                title="Chapter 1",
                plan={},
            )
        )

    # Store physical artifacts in book_storage
    pdf_key = book_storage.store(
        family_id="fam_alpha",
        book_id=book.book_id,
        export_format=ExportFormat.PDF,
        data=b"%PDF-1.4 test artifact data",
    )
    epub_key = book_storage.store(
        family_id="fam_alpha",
        book_id=book.book_id,
        export_format=ExportFormat.EPUB,
        data=b"PK\x03\x04 epub test artifact data",
    )

    assert book_storage.exists(storage_key=pdf_key)
    assert book_storage.exists(storage_key=epub_key)

    # Record export rows
    export_repo.save_export(
        book_id=book.book_id,
        format=ExportFormat.PDF.value,
        storage_backend="local",
        storage_key=pdf_key,
        status=ExportStatus.READY.value,
    )
    export_repo.save_export(
        book_id=book.book_id,
        format=ExportFormat.EPUB.value,
        storage_backend="local",
        storage_key=epub_key,
        status=ExportStatus.READY.value,
    )

    # Delete with Editor token (Editor has CREATE_BOOK capability)
    resp = client.delete(
        f"/v1/families/fam_alpha/books/{book.book_id}",
        headers=user_editor.headers,
    )
    assert resp.status_code == 204

    assert book_storage.exists(storage_key=pdf_key)
    assert book_storage.exists(storage_key=epub_key)
    _drain_cleanup(test_setup)
    assert not book_storage.exists(storage_key=pdf_key)
    assert not book_storage.exists(storage_key=epub_key)

    # Assert DB cleanup
    with db.session_factory() as session:
        assert book_repo.get_book(family_id="fam_alpha", book_id=book.book_id) is None
        assert session.get(BookChapterRow, "ch_del_1") is None
        assert len(export_repo.list_exports(book.book_id)) == 0


def test_delete_book_viewer_forbidden_and_bola_404(test_setup: dict[str, object]) -> None:
    client: TestClient = test_setup["client"]
    user_viewer: TestIdentity = test_setup["user_viewer"]
    user_stranger: TestIdentity = test_setup["user_stranger"]

    # Viewer forbidden
    resp = client.delete(
        "/v1/families/fam_alpha/books/book_to_delete_1",
        headers=user_viewer.headers,
    )
    assert resp.status_code == 403

    # Stranger receives 404
    resp = client.delete(
        "/v1/families/fam_alpha/books/book_to_delete_1",
        headers=user_stranger.headers,
    )
    assert resp.status_code == 404


def test_delete_family_confirmation_mismatch_400(test_setup: dict[str, object]) -> None:
    client: TestClient = test_setup["client"]
    user_owner: TestIdentity = test_setup["user_owner"]
    resp = client.request(
        "DELETE",
        "/v1/families/fam_alpha",
        json={"confirm_family_id": "fam_wrong"},
        headers=user_owner.headers,
    )
    assert resp.status_code == 400


def test_delete_family_insufficient_role_403(test_setup: dict[str, object]) -> None:
    client: TestClient = test_setup["client"]
    user_editor: TestIdentity = test_setup["user_editor"]
    # Editor tries to delete
    resp = client.request(
        "DELETE",
        "/v1/families/fam_alpha",
        json={"confirm_family_id": "fam_alpha"},
        headers=user_editor.headers,
    )
    assert resp.status_code == 403


def test_delete_family_multiple_owners_409(test_setup: dict[str, object]) -> None:
    db: Database = test_setup["database"]
    client: TestClient = test_setup["client"]
    user_owner: TestIdentity = test_setup["user_owner"]
    user_owner2: TestIdentity = test_setup["user_owner2"]

    # Add a second owner to fam_alpha
    create_membership(db, family_id="fam_alpha", user=user_owner2, role=FamilyRole.OWNER)

    resp = client.request(
        "DELETE",
        "/v1/families/fam_alpha",
        json={"confirm_family_id": "fam_alpha"},
        headers=user_owner.headers,
    )
    assert resp.status_code == 409
    data = resp.json()
    assert data["error"]["code"] == "sole_owner_required"


def test_delete_family_success_complete_cleanup(test_setup: dict[str, object]) -> None:
    db: Database = test_setup["database"]
    audio_storage: LocalAudioStorage = test_setup["audio_storage"]
    book_storage: LocalBookArtifactStorage = test_setup["book_storage"]
    client: TestClient = test_setup["client"]
    user_owner: TestIdentity = test_setup["user_owner"]

    # Create a new family with single owner
    create_family_with_id(db, family_id="fam_to_destroy", name="To Destroy", owner=user_owner)

    # Store audio
    stored_audio = audio_storage.save(
        family_id="fam_to_destroy",
        recording_id="rec_destroy_1",
        original_filename="audio.wav",
        content_type="audio/wav",
        source=io.BytesIO(b"RIFF....WAVEfmt ....data...."),
    )
    assert audio_storage.exists(stored_audio.storage_key)

    # Store book artifact
    stored_book_key = book_storage.store(
        family_id="fam_to_destroy",
        book_id="book_destroy_1",
        export_format=ExportFormat.PDF,
        data=b"%PDF-1.4 destroy test data",
    )
    assert book_storage.exists(storage_key=stored_book_key)

    # Seed DB records
    with db.session_factory.begin() as session:
        session.add(
            RecordingRow(
                recording_id="rec_destroy_1",
                family_id="fam_to_destroy",
                speaker_id="spk_d",
                speaker_name="Speaker",
                original_filename="audio.wav",
                content_type="audio/wav",
                audio_path=stored_audio.storage_key,
                storage_key=stored_audio.storage_key,
            )
        )
        session.add(
            ProcessingJobRow(
                job_id="job_destroy_1",
                recording_id="rec_destroy_1",
                status=JobStatus.COMPLETED.value,
                stage="completed",
            )
        )
        session.add(
            ArchivePersonRow(
                person_id="p_destroy_1",
                family_id="fam_to_destroy",
                canonical_name="Person Destroy",
                normalized_name="person destroy",
                category="family",
            )
        )
        session.add(
            FamilyGraphEdgeRow(
                edge_id="edge_destroy_1",
                family_id="fam_to_destroy",
                relationship_type="parent",
                subject_person_id="p_destroy_1",
                subject_role="parent",
                object_person_id="p_destroy_1",
                object_role="child",
                source_claim_ids=[],
            )
        )

    book_repo = BookRepository(db)
    book = book_repo.create_book(
        book_id="book_destroy_1",
        family_id="fam_to_destroy",
        created_by_user_id=user_owner.user_id,
        title="Destroy Book",
        output_language="ru",
        target_word_count=5000,
    )
    export_repo = BookExportRepository(db)
    export_repo.save_export(
        book_id=book.book_id,
        format=ExportFormat.PDF.value,
        storage_backend="local",
        storage_key=stored_book_key,
        status=ExportStatus.READY.value,
    )

    # Delete family
    resp = client.request(
        "DELETE",
        "/v1/families/fam_to_destroy",
        json={"confirm_family_id": "fam_to_destroy"},
        headers=user_owner.headers,
    )
    assert resp.status_code == 204

    assert audio_storage.exists(stored_audio.storage_key)
    assert book_storage.exists(storage_key=stored_book_key)
    _drain_cleanup(test_setup)
    assert not audio_storage.exists(stored_audio.storage_key)
    assert not book_storage.exists(storage_key=stored_book_key)

    # Assert DB cleanup
    with db.session_factory() as session:
        assert session.get(FamilyRow, "fam_to_destroy") is None
        assert session.get(RecordingRow, "rec_destroy_1") is None
        assert session.get(ProcessingJobRow, "job_destroy_1") is None
        assert session.get(ArchivePersonRow, "p_destroy_1") is None
        assert session.get(FamilyGraphEdgeRow, "edge_destroy_1") is None
        assert book_repo.get_book_unscoped("book_destroy_1") is None


def test_privacy_export_success(test_setup: dict[str, object]) -> None:
    db: Database = test_setup["database"]
    client: TestClient = test_setup["client"]
    user_viewer: TestIdentity = test_setup["user_viewer"]

    with db.session_factory.begin() as session:
        session.add(
            RecordingRow(
                recording_id="rec_export_1",
                family_id="fam_alpha",
                speaker_id="spk_exp",
                speaker_name="Narrator",
                original_filename="rec.mp3",
                content_type="audio/mp3",
                audio_path="fam_alpha/rec_export_1/rec.mp3",
            )
        )
        session.add(
            PipelineResultRow(
                recording_id="rec_export_1",
                payload={"stories": [{"story_id": "st_exp", "title": "Exported Story"}]},
            )
        )
        session.add(
            ArchivePersonRow(
                person_id="p_exp_1",
                family_id="fam_alpha",
                canonical_name="Grandpa",
                normalized_name="grandpa",
                category="family",
            )
        )
        session.add(
            FamilyGraphEdgeRow(
                edge_id="edge_exp_1",
                family_id="fam_alpha",
                relationship_type="parent",
                subject_person_id="p_exp_1",
                subject_role="parent",
                object_person_id="p_exp_1",
                object_role="child",
                source_claim_ids=[],
            )
        )
        session.add(
            ArchiveClaimRow(
                claim_id="st_exp",
                family_id="fam_alpha",
                recording_id="rec_export_1",
                object_type="story",
                source_object_id="story_exp",
                predicate="story",
                payload={"title": "Exported Story", "summary": "A wonderful memory"},
                evidence_class="A_explicit",
                verification_status="unreviewed",
            )
        )

    resp = client.get(
        "/v1/families/fam_alpha/privacy/export",
        headers=user_viewer.headers,
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["family"]["family_id"] == "fam_alpha"
    assert data["family"]["name"] == "Alpha Family"
    assert len(data["members"]) == 3
    assert any(m["role"] == "owner" for m in data["members"])
    assert len(data["recordings"]) >= 1
    assert any(r["recording_id"] == "rec_export_1" for r in data["recordings"])
    assert any(p["person_id"] == "p_exp_1" for p in data["people"])
    assert any(s["story_id"] == "st_exp" for s in data["stories"])
    assert any(claim["claim_id"] == "st_exp" for claim in data["claims"])
    assert data["schema_version"] == "mura-family-export-v2"
    assert data["binary_objects_included"] is False
    assert "object_manifest" in data
    assert "exported_at" in data

    serialized = resp.text
    assert "auth_subject" not in serialized
    assert "auth_issuer" not in serialized
    # Legacy filesystem locators are implementation details, not portable data.
    assert "fam_alpha/rec_export_1/rec.mp3" not in serialized


def test_privacy_export_non_member_404(test_setup: dict[str, object]) -> None:
    client: TestClient = test_setup["client"]
    user_stranger: TestIdentity = test_setup["user_stranger"]
    resp = client.get(
        "/v1/families/fam_alpha/privacy/export",
        headers=user_stranger.headers,
    )
    assert resp.status_code == 404
