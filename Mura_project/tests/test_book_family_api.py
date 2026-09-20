"""Tests for the 10 family-scoped Family Book API endpoints.

Covers:
- Role permissions (Viewer vs Editor/Owner)
- BOLA isolation (cross-family 404)
- Source eligibility check
- Truthful progress reporting
- TOC listing and approval gate for chapter reading
- Artifact streaming download
- Cooperative cancellation
- Non-destructive regeneration (supersedes_book_id)
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app, get_auth_verifier, get_runtime, get_settings
from mura.config import CoreSettings
from mura.domain.book_models import (
    BookSourceSnapshot,
    BookStage,
    BookStatus,
    ExportFormat,
    ExportStatus,
    SnapshotManifest,
)
from mura.identity.policy import FamilyRole
from mura.jobs import JobStatus
from mura.storage.book import (
    BookChapterRepository,
    BookExportRepository,
    BookJobRepository,
    BookRepository,
    BookSourceSnapshotRepository,
)
from mura.storage.book_artifacts import LocalBookArtifactStorage
from mura.storage.database import (
    Database,
    PipelineResultRow,
    ProcessingJobRow,
    RecordingRow,
    utcnow,
)
from mura.storage.identity import IdentityRepository
from tests.authz_factories import (
    FakePrincipalVerifier,
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


def _seed_database() -> Database:
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    with database.session_factory.begin() as session:
        # Eligible recording: job completed + pipeline result
        session.add(
            RecordingRow(
                recording_id="rec_eligible_1",
                family_id="fam_alpha",
                speaker_id="spk_1",
                speaker_name="Асан",
                original_filename="memory.mp3",
                content_type="audio/mp3",
                audio_path="/tmp/memory.mp3",
            )
        )
        session.add(
            ProcessingJobRow(
                job_id="pjob_1",
                recording_id="rec_eligible_1",
                status=JobStatus.COMPLETED.value,
                stage="completed",
            )
        )
        session.add(
            PipelineResultRow(
                recording_id="rec_eligible_1",
                payload={
                    "stories": [{"story_id": "st_1", "title": "First memory"}],
                    "extracted_entities": [{"person_id": "p_1", "canonical_name": "Асан"}],
                },
            )
        )

        # Ineligible recording for beta family (job still queued, no pipeline result)
        session.add(
            RecordingRow(
                recording_id="rec_ineligible_beta",
                family_id="fam_beta",
                speaker_id="spk_2",
                speaker_name="Берік",
                original_filename="beta.mp3",
                content_type="audio/mp3",
                audio_path="/tmp/beta.mp3",
            )
        )
        session.add(
            ProcessingJobRow(
                job_id="pjob_2",
                recording_id="rec_ineligible_beta",
                status=JobStatus.QUEUED.value,
                stage="queued",
            )
        )
    return database


@pytest.fixture
def api_env(tmp_path: Path):
    database = _seed_database()
    settings = _settings(tmp_path)
    identity = IdentityRepository(database)
    verifier = FakePrincipalVerifier()

    editor = create_test_user(identity, subject="book-editor", verifier=verifier)
    viewer = create_test_user(identity, subject="book-viewer", verifier=verifier)
    foreigner = create_test_user(identity, subject="foreign-user", verifier=verifier)

    create_family_with_id(database, family_id="fam_alpha", name="Alpha Family")
    create_membership(database, family_id="fam_alpha", user=editor, role=FamilyRole.EDITOR)
    create_membership(database, family_id="fam_alpha", user=viewer, role=FamilyRole.VIEWER)

    create_family_with_id(database, family_id="fam_beta", name="Beta Family")
    create_membership(database, family_id="fam_beta", user=foreigner, role=FamilyRole.EDITOR)

    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_runtime] = lambda: SimpleNamespace(
        database=database, settings=settings
    )
    app.dependency_overrides[get_auth_verifier] = lambda: verifier
    client = TestClient(app, raise_server_exceptions=False)

    return SimpleNamespace(
        client=client,
        database=database,
        settings=settings,
        editor=editor,
        viewer=viewer,
        foreigner=foreigner,
        tmp_path=tmp_path,
    )


def test_create_family_book_role_and_eligibility(api_env) -> None:
    client = api_env.client

    # 1. Unauthenticated gets 401
    res = client.post("/v1/families/fam_alpha/books", json={"title": "Test Book"})
    assert res.status_code == 401

    # 2. Viewer gets 403 (insufficient family role)
    res = client.post(
        "/v1/families/fam_alpha/books",
        headers=api_env.viewer.headers,
        json={"title": "Test Book"},
    )
    assert res.status_code == 403

    # 3. Foreigner targeting fam_alpha gets 404 (BOLA isolation)
    res = client.post(
        "/v1/families/fam_alpha/books",
        headers=api_env.foreigner.headers,
        json={"title": "Test Book"},
    )
    assert res.status_code == 404

    # 4. Family with NO eligible recordings gets 400 Bad Request
    res = client.post(
        "/v1/families/fam_beta/books",
        headers=api_env.foreigner.headers,
        json={"title": "Beta Book"},
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "bad_request"

    # 5. Editor with eligible recordings succeeds with 202 Accepted
    res = client.post(
        "/v1/families/fam_alpha/books",
        headers=api_env.editor.headers,
        json={
            "title": "Хроника семьи Асановых",
            "output_language": "kk",
            "target_word_count": 15000,
            "target_word_count": 25000,
        },
    )
    assert res.status_code == 202
    data = res.json()
    assert data["book_id"].startswith("book_")
    assert data["job_id"].startswith("bjob_")
    assert data["status"] == "queued"

    # Verify source snapshot was created atomically in database
    snapshot_repo = BookSourceSnapshotRepository(api_env.database)
    snapshot = snapshot_repo.get_snapshot(data["book_id"])
    assert snapshot is not None
    assert snapshot.source_recording_count == 1
    assert snapshot.manifest["source_recording_ids"] == ["rec_eligible_1"]


def test_create_family_book_word_count_limits(api_env) -> None:
    client = api_env.client

    # Target word count < 20000 fails validation
    res = client.post(
        "/v1/families/fam_alpha/books",
        headers=api_env.editor.headers,
        json={"title": "Too Short", "target_word_count": 15000},
    )
    assert res.status_code == 422

    # Target word count > 30000 fails validation
    res = client.post(
        "/v1/families/fam_alpha/books",
        headers=api_env.editor.headers,
        json={"title": "Too Long", "target_word_count": 35000},
    )
    assert res.status_code == 422


def test_create_family_book_requested_recordings_isolation(api_env) -> None:
    client = api_env.client

    # Requesting a non-existent recording ID -> 400
    res = client.post(
        "/v1/families/fam_alpha/books",
        headers=api_env.editor.headers,
        json={"title": "Bad Source", "requested_recording_ids": ["rec_non_existent"]},
    )
    assert res.status_code == 400

    # Requesting a valid eligible recording ID -> 202
    res = client.post(
        "/v1/families/fam_alpha/books",
        headers=api_env.editor.headers,
        json={"title": "Good Source", "requested_recording_ids": ["rec_eligible_1"]},
    )
    assert res.status_code == 202
    book_id = res.json()["book_id"]

    snapshot_repo = BookSourceSnapshotRepository(api_env.database)
    snapshot = snapshot_repo.get_snapshot(book_id)
    assert snapshot is not None
    assert snapshot.manifest["source_recording_ids"] == ["rec_eligible_1"]


def test_list_family_book_sources(api_env) -> None:
    client = api_env.client

    # Alpha family has 1 eligible source
    res = client.get("/v1/families/fam_alpha/books/sources", headers=api_env.viewer.headers)
    assert res.status_code == 200
    sources = res.json()
    assert len(sources) == 1
    assert sources[0]["recording_id"] == "rec_eligible_1"
    assert sources[0]["speaker_name"] == "Асан"
    assert sources[0]["story_count"] == 1
    assert sources[0]["person_count"] == 1

    # Foreigner targeting fam_alpha gets 404
    res = client.get("/v1/families/fam_alpha/books/sources", headers=api_env.foreigner.headers)
    assert res.status_code == 404


def test_list_and_get_book_detail_and_bola(api_env) -> None:
    client = api_env.client
    book_repo = BookRepository(api_env.database)

    # Create a book in fam_alpha
    book = book_repo.create_book(
        family_id="fam_alpha",
        created_by_user_id=api_env.editor.user_id,
        title="Семейная сага",
        output_language="ru",
        target_word_count=20000,
    )

    # List books in fam_alpha
    res = client.get("/v1/families/fam_alpha/books", headers=api_env.viewer.headers)
    assert res.status_code == 200
    page = res.json()
    assert page["page"]["total"] == 1
    assert page["items"][0]["book_id"] == book.book_id
    assert page["items"][0]["progress"]["stage"] == BookStage.PREPARING_SOURCES.value

    # Get book detail
    res = client.get(
        f"/v1/families/fam_alpha/books/{book.book_id}",
        headers=api_env.viewer.headers,
    )
    assert res.status_code == 200
    detail = res.json()
    assert detail["book_id"] == book.book_id
    assert detail["title"] == "Семейная сага"
    assert detail["output_language"] == "ru"
    assert detail["status"] == "queued"

    # Foreigner trying to access fam_alpha book gets 404
    res = client.get(
        f"/v1/families/fam_alpha/books/{book.book_id}",
        headers=api_env.foreigner.headers,
    )
    assert res.status_code == 404

    # Accessing via fam_beta URL path with alpha book_id returns 404
    res = client.get(
        f"/v1/families/fam_beta/books/{book.book_id}",
        headers=api_env.foreigner.headers,
    )
    assert res.status_code == 404


def test_get_book_status_progress(api_env) -> None:
    client = api_env.client
    book_repo = BookRepository(api_env.database)

    book = book_repo.create_book(
        family_id="fam_alpha",
        created_by_user_id=api_env.editor.user_id,
        title="Книга памяти",
        output_language="kk",
        target_word_count=15000,
    )
    book_repo.update_stage(
        book.book_id,
        stage=BookStage.WRITING_CHAPTER.value,
        status=BookStatus.WRITING.value,
        chapters_total=12,
        chapters_approved=3,
        current_chapter_number=4,
    )

    res = client.get(
        f"/v1/families/fam_alpha/books/{book.book_id}/status",
        headers=api_env.viewer.headers,
    )
    assert res.status_code == 200
    progress = res.json()
    assert progress["stage"] == BookStage.WRITING_CHAPTER.value
    assert progress["chapters_total"] == 12
    assert progress["chapters_approved"] == 3
    assert progress["current_chapter_number"] == 4


def test_chapters_toc_and_approval_gate(api_env) -> None:
    client = api_env.client
    book_repo = BookRepository(api_env.database)
    chapter_repo = BookChapterRepository(api_env.database)

    book = book_repo.create_book(
        family_id="fam_alpha",
        created_by_user_id=api_env.editor.user_id,
        title="Шежіре",
        output_language="kk",
        target_word_count=20000,
    )

    # Add chapters via stubs
    chapter_repo.create_chapter_stubs(
        book_id=book.book_id,
        chapter_plans=[
            {"chapter_number": 1, "title": "Бастау", "target_word_count": 1500},
            {"chapter_number": 2, "title": "Көш", "target_word_count": 1600},
        ],
    )
    # Approve chapter 1
    chapter_repo.approve_chapter(
        book_id=book.book_id,
        chapter_number=1,
        final_text="Бұл біздің әулеттің басы еді.",
        word_count=6,
    )
    # Update chapter 2 draft only (status will be REVIEWING, not approved)
    chapter_repo.update_chapter_draft(
        book_id=book.book_id,
        chapter_number=2,
        draft_text="Екінші тарау әлі жазылуда...",
        word_count=5,
        writer_prompt_version="v1",
        writer_model="deepseek",
    )

    # TOC lists both chapters
    res = client.get(
        f"/v1/families/fam_alpha/books/{book.book_id}/chapters",
        headers=api_env.viewer.headers,
    )
    assert res.status_code == 200
    toc = res.json()
    assert len(toc["items"]) == 2
    assert toc["items"][0]["approved"] is True
    assert toc["items"][1]["approved"] is False

    # Chapter 1 (approved) can be read
    res = client.get(
        f"/v1/families/fam_alpha/books/{book.book_id}/chapters/1",
        headers=api_env.viewer.headers,
    )
    assert res.status_code == 200
    ch1 = res.json()
    assert ch1["chapter_number"] == 1
    assert ch1["title"] == "Бастау"
    assert "Бұл біздің әулеттің басы еді." in ch1["text"]

    # Chapter 2 (unapproved draft) CANNOT be read -> 404 gate prevents ungrounded leak
    res = client.get(
        f"/v1/families/fam_alpha/books/{book.book_id}/chapters/2",
        headers=api_env.viewer.headers,
    )
    assert res.status_code == 404


def test_download_family_book_artifact(api_env) -> None:
    client = api_env.client
    book_repo = BookRepository(api_env.database)
    export_repo = BookExportRepository(api_env.database)

    book = book_repo.create_book(
        family_id="fam_alpha",
        created_by_user_id=api_env.editor.user_id,
        title="Өмір жолы",
        output_language="kk",
        target_word_count=10000,
    )

    # 1. Download while not completed returns 400
    res = client.get(
        f"/v1/families/fam_alpha/books/{book.book_id}/download?format=pdf",
        headers=api_env.viewer.headers,
    )
    assert res.status_code == 400

    # 2. Mark book completed
    book_repo.complete_book(book.book_id, word_count=10000)

    # Store a fake PDF artifact
    storage = LocalBookArtifactStorage(api_env.settings.book_storage_dir)
    key = storage.store(
        family_id="fam_alpha",
        book_id=book.book_id,
        export_format=ExportFormat.PDF,
        data=b"%PDF-1.4 test content",
    )
    export_repo.save_export(
        book_id=book.book_id,
        format="pdf",
        status=ExportStatus.READY.value,
        storage_key=key,
        size_bytes=21,
    )

    # 3. Successful PDF download
    res = client.get(
        f"/v1/families/fam_alpha/books/{book.book_id}/download?format=pdf",
        headers=api_env.viewer.headers,
    )
    assert res.status_code == 200
    assert res.content == b"%PDF-1.4 test content"
    assert "attachment" in res.headers["content-disposition"]
    assert "no-store" in res.headers["cache-control"]


def _persist_book_source_snapshot(
    database: Database,
    *,
    book_id: str,
    family_id: str = "fam_alpha",
    recording_ids: list[str] | None = None,
) -> None:
    source_ids = recording_ids or ["rec_eligible_1"]
    snapshot = BookSourceSnapshot(
        compiler_version="test-api",
        family_id=family_id,
        manifest=SnapshotManifest(
            source_recording_ids=source_ids,
            created_at=utcnow(),
        ),
    )
    BookSourceSnapshotRepository(database).save_snapshot(
        book_id=book_id,
        family_id=family_id,
        compiler_version=snapshot.compiler_version,
        content_hash="0" * 64,
        payload=snapshot.model_dump(mode="json"),
        manifest=snapshot.manifest.model_dump(mode="json"),
        source_recording_count=len(source_ids),
    )


def test_cancel_and_regenerate_family_book(api_env) -> None:
    client = api_env.client
    book_repo = BookRepository(api_env.database)
    job_repo = BookJobRepository(api_env.database)

    book = book_repo.create_book(
        family_id="fam_alpha",
        created_by_user_id=api_env.editor.user_id,
        title="Бастапқы нұсқа",
        output_language="kk",
        target_word_count=20000,
    )
    job_repo.create_job(book_id=book.book_id, family_id="fam_alpha")
    _persist_book_source_snapshot(api_env.database, book_id=book.book_id)

    # Viewer cannot cancel
    res = client.post(
        f"/v1/families/fam_alpha/books/{book.book_id}/cancel",
        headers=api_env.viewer.headers,
    )
    assert res.status_code == 403

    # Editor can cancel
    res = client.post(
        f"/v1/families/fam_alpha/books/{book.book_id}/cancel",
        headers=api_env.editor.headers,
    )
    assert res.status_code == 200
    assert book_repo.is_cancel_requested(book.book_id)

    # Regenerate creates a new book row linking supersedes_book_id
    res = client.post(
        f"/v1/families/fam_alpha/books/{book.book_id}/regenerate",
        headers=api_env.editor.headers,
        json={"title": "Жаңа басылым", "target_word_count": 25000},
    )
    assert res.status_code == 202
    new_data = res.json()
    new_book_id = new_data["book_id"]
    assert new_book_id != book.book_id

    new_book = book_repo.get_book(family_id="fam_alpha", book_id=new_book_id)
    assert new_book is not None
    assert new_book.supersedes_book_id == book.book_id
    assert new_book.title == "Жаңа басылым"
    assert new_book.target_word_count == 25000

    # Original book still exists intact
    old_book = book_repo.get_book(family_id="fam_alpha", book_id=book.book_id)
    assert old_book is not None
    assert old_book.title == "Бастапқы нұсқа"



def test_regenerate_reuses_original_source_list_and_excludes_new_archive_recordings(api_env) -> None:
    book_repo = BookRepository(api_env.database)
    original = book_repo.create_book(
        family_id="fam_alpha",
        created_by_user_id=api_env.editor.user_id,
        title="Frozen sources",
        output_language="ru",
        target_word_count=20000,
    )
    book_repo.complete_book(original.book_id, word_count=0)
    _persist_book_source_snapshot(api_env.database, book_id=original.book_id)

    with api_env.database.session_factory.begin() as session:
        session.add(
            RecordingRow(
                recording_id="rec_added_later",
                family_id="fam_alpha",
                speaker_id="spk_later",
                speaker_name="Later",
                original_filename="later.mp3",
                content_type="audio/mp3",
                audio_path="/tmp/later.mp3",
            )
        )
        session.add(
            ProcessingJobRow(
                job_id="pjob_added_later",
                recording_id="rec_added_later",
                status=JobStatus.COMPLETED.value,
                stage="completed",
            )
        )
        session.add(
            PipelineResultRow(
                recording_id="rec_added_later",
                payload={"stories": [], "extracted_entities": []},
            )
        )

    response = api_env.client.post(
        f"/v1/families/fam_alpha/books/{original.book_id}/regenerate",
        headers=api_env.editor.headers,
    )
    assert response.status_code == 202
    regenerated_id = response.json()["book_id"]
    regenerated_snapshot = BookSourceSnapshotRepository(api_env.database).get_snapshot(
        regenerated_id
    )
    assert regenerated_snapshot is not None
    assert regenerated_snapshot.manifest["source_recording_ids"] == ["rec_eligible_1"]
    assert "rec_added_later" not in regenerated_snapshot.manifest["source_recording_ids"]


def test_regenerate_missing_snapshot_without_explicit_sources_fails_closed(api_env) -> None:
    book_repo = BookRepository(api_env.database)
    original = book_repo.create_book(
        family_id="fam_alpha",
        created_by_user_id=api_env.editor.user_id,
        title="Missing snapshot",
        output_language="ru",
        target_word_count=20000,
    )
    book_repo.complete_book(original.book_id, word_count=0)

    response = api_env.client.post(
        f"/v1/families/fam_alpha/books/{original.book_id}/regenerate",
        headers=api_env.editor.headers,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "book_source_snapshot_missing"


def test_regenerate_missing_snapshot_allows_explicit_valid_sources(api_env) -> None:
    book_repo = BookRepository(api_env.database)
    original = book_repo.create_book(
        family_id="fam_alpha",
        created_by_user_id=api_env.editor.user_id,
        title="Explicit recovery",
        output_language="ru",
        target_word_count=20000,
    )
    book_repo.complete_book(original.book_id, word_count=0)

    response = api_env.client.post(
        f"/v1/families/fam_alpha/books/{original.book_id}/regenerate",
        headers=api_env.editor.headers,
        json={"requested_recording_ids": ["rec_eligible_1"]},
    )
    assert response.status_code == 202


def test_regenerate_explicit_cross_family_source_is_not_accessible(api_env) -> None:
    book_repo = BookRepository(api_env.database)
    original = book_repo.create_book(
        family_id="fam_alpha",
        created_by_user_id=api_env.editor.user_id,
        title="Cross-family source",
        output_language="ru",
        target_word_count=20000,
    )
    book_repo.complete_book(original.book_id, word_count=0)

    response = api_env.client.post(
        f"/v1/families/fam_alpha/books/{original.book_id}/regenerate",
        headers=api_env.editor.headers,
        json={"requested_recording_ids": ["rec_ineligible_beta"]},
    )
    assert response.status_code == 400
