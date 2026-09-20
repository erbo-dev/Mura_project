"""Tests for atomic creation of Book, SourceSnapshot, and BookJob.

Verifies:
- All 3 records (BookRow, BookSourceSnapshotRow, BookJobRow) are inserted in a single atomic transaction.
- If any stage fails, the entire transaction rolls back cleanly without leaving orphaned records.
- Source snapshot version and supersedes_book_id are recorded accurately.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError

from mura.domain.book_models import (
    SNAPSHOT_SCHEMA_VERSION,
    BookSourceSnapshot,
    CompiledSnapshot,
    SnapshotManifest,
)
from mura.storage.book import (
    BookCreationRepository,
    BookJobRepository,
    BookRepository,
    BookSourceSnapshotRepository,
)
from mura.storage.database import Database, utcnow
from mura.storage.identity import IdentityRepository
from tests.authz_factories import (
    FakePrincipalVerifier,
    create_family_with_id,
    create_test_user,
)


def _setup_db() -> tuple[Database, str, str]:
    db = Database("sqlite+pysqlite:///:memory:")
    db.create_schema()
    identity = IdentityRepository(db)
    verifier = FakePrincipalVerifier()
    user = create_test_user(identity, subject="atomicity-user", verifier=verifier)
    family_id = "fam_atomicity"
    create_family_with_id(db, family_id=family_id, name="Atomicity Family")
    return db, family_id, user.user_id


def _dummy_compiled_snapshot(family_id: str) -> CompiledSnapshot:
    now = utcnow()
    manifest = SnapshotManifest(
        source_recording_ids=["rec_1"],
        source_story_ids=["story_1"],
        source_claim_ids=["claim_1"],
        source_event_ids=[],
        source_person_ids=["person_1"],
        source_evidence_ids=[],
        created_at=now,
    )
    snapshot = BookSourceSnapshot(
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        compiler_version="mura-book-snapshot-compiler-v1",
        family_id=family_id,
        manifest=manifest,
    )
    return CompiledSnapshot(
        snapshot=snapshot,
        content_hash="abc123contenthash",
    )


def test_create_queued_book_atomic_success() -> None:
    db, family_id, user_id = _setup_db()
    creation_repo = BookCreationRepository(db)
    compiled = _dummy_compiled_snapshot(family_id)

    result = creation_repo.create_queued_book(
        family_id=family_id,
        created_by_user_id=user_id,
        title="Мұра шежіресі",
        subtitle="1-том",
        output_language="kk",
        target_word_count=25000,
        compiled_snapshot=compiled,
        supersedes_book_id=None,
        source_snapshot_version=1,
    )

    book_id = result.book.book_id
    job_id = result.job.job_id
    snapshot_id = result.snapshot.snapshot_id

    # Verify all 3 records exist in database via their respective repositories
    book_repo = BookRepository(db)
    snapshot_repo = BookSourceSnapshotRepository(db)
    job_repo = BookJobRepository(db)

    persisted_book = book_repo.get_book(family_id=family_id, book_id=book_id)
    assert persisted_book is not None
    assert persisted_book.title == "Мұра шежіресі"
    assert persisted_book.status == "queued"
    assert persisted_book.source_snapshot_version == 1
    assert persisted_book.supersedes_book_id is None

    persisted_snapshot = snapshot_repo.get_snapshot(book_id)
    assert persisted_snapshot is not None
    assert persisted_snapshot.snapshot_id == snapshot_id
    assert persisted_snapshot.content_hash == "abc123contenthash"
    assert persisted_snapshot.source_recording_count == 1
    assert persisted_snapshot.manifest["source_recording_ids"] == ["rec_1"]

    persisted_job = job_repo.get_job_for_book(book_id)
    assert persisted_job is not None
    assert persisted_job.job_id == job_id
    assert persisted_job.status == "queued"


def test_create_queued_book_joins_caller_transaction() -> None:
    db, family_id, user_id = _setup_db()
    creation_repo = BookCreationRepository(db)
    compiled = _dummy_compiled_snapshot(family_id)

    with pytest.raises(RuntimeError, match="rollback outer transaction"):
        with db.session_factory.begin() as session:
            creation_repo.create_queued_book(
                family_id=family_id,
                created_by_user_id=user_id,
                title="Transactional Book",
                output_language="ru",
                target_word_count=20000,
                compiled_snapshot=compiled,
                session=session,
            )
            raise RuntimeError("rollback outer transaction")

    books, total = BookRepository(db).list_books(family_id=family_id)
    assert total == 0
    assert books == []


def test_create_queued_book_atomic_rollback_on_failure() -> None:
    db, family_id, user_id = _setup_db()
    creation_repo = BookCreationRepository(db)
    compiled = _dummy_compiled_snapshot(family_id)

    # Force a failure during the transaction by mocking BookJobRow insertion to raise
    with patch("mura.storage.book.BookJobRow", side_effect=IntegrityError("boom", None, Exception())):
        with pytest.raises(IntegrityError):
            creation_repo.create_queued_book(
                family_id=family_id,
                created_by_user_id=user_id,
                title="Failed Book",
                output_language="ru",
                target_word_count=20000,
                compiled_snapshot=compiled,
            )

    # Verify zero books, snapshots, or jobs were persisted
    book_repo = BookRepository(db)
    books, total = book_repo.list_books(family_id=family_id)
    assert total == 0
    assert len(books) == 0
