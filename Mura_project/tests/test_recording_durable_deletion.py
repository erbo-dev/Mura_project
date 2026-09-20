from __future__ import annotations

from mura.storage.cleanup import StorageCleanupJobRow, StorageCleanupStatus
from mura.storage.database import Database, RecordingRepository
from mura.storage.deletion import RecordingDeletionService
from mura.storage.identity import FamilyRow
from mura.storage.database import utcnow


def _db() -> Database:
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    return database


def test_recording_deletion_persists_cleanup_in_same_transaction() -> None:
    db = _db()
    now = utcnow()
    with db.session_factory.begin() as session:
        session.add(FamilyRow(family_id="fam_delete", name="Delete", created_at=now, updated_at=now))
    RecordingRepository(db).create_recording_and_job(
        recording_id="rec_delete",
        job_id="job_delete",
        family_id="fam_delete",
        speaker_id="speaker",
        speaker_name="Speaker",
        original_filename="a.wav",
        content_type="audio/wav",
        audio_path="family/fam_delete/recordings/rec_delete/original.wav",
        storage_key="family/fam_delete/recordings/rec_delete/original.wav",
        storage_backend="supabase",
    )

    result = RecordingDeletionService(db, default_storage_backend="supabase").delete_recording(
        family_id="fam_delete",
        recording_id="rec_delete",
    )
    assert result.rows_deleted
    assert result.cleanup_enqueued
    with db.session_factory() as session:
        assert RecordingRepository(db).get_recording("rec_delete") is None
        jobs = list(session.query(StorageCleanupJobRow).all())
        assert len(jobs) == 1
        assert jobs[0].status == StorageCleanupStatus.QUEUED.value
        assert jobs[0].storage_backend == "supabase"


def test_recording_deletion_rollback_keeps_recording_when_cleanup_enqueue_fails(monkeypatch) -> None:
    db = _db()
    now = utcnow()
    with db.session_factory.begin() as session:
        session.add(FamilyRow(family_id="fam_rollback", name="Rollback", created_at=now, updated_at=now))
    RecordingRepository(db).create_recording_and_job(
        recording_id="rec_rollback",
        job_id="job_rollback",
        family_id="fam_rollback",
        speaker_id="speaker",
        speaker_name="Speaker",
        original_filename="a.wav",
        content_type="audio/wav",
        audio_path="family/fam_rollback/recordings/rec_rollback/original.wav",
        storage_key="family/fam_rollback/recordings/rec_rollback/original.wav",
        storage_backend="local",
    )
    service = RecordingDeletionService(db)
    monkeypatch.setattr(
        service.cleanup_repository,
        "enqueue_cleanup",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("enqueue failed")),
    )

    try:
        service.delete_recording(family_id="fam_rollback", recording_id="rec_rollback")
    except RuntimeError as exc:
        assert str(exc) == "enqueue failed"
    else:
        raise AssertionError("expected cleanup enqueue failure")

    assert RecordingRepository(db).get_recording("rec_rollback") is not None
    with db.session_factory() as session:
        assert session.query(StorageCleanupJobRow).count() == 0
