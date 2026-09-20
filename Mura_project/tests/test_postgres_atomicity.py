from __future__ import annotations

import os
from threading import Event, Thread
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select

from mura.domain.book_models import BookSourceSnapshot, CompiledSnapshot, SnapshotManifest
from mura.domain.models import (
    CleanerResult,
    ExtractionResult,
    PipelineResult,
    RawSegment,
    ReadableSegment,
    TranscriptEnvelope,
)
from mura.jobs import JobStatus
from mura.observability import ProcessingTrace, TraceOutcome, TraceRepository
from mura.quotas import BookQuotaService
from mura.release_control import (
    CURRENT_RELEASE_ID,
    RELEASE_CONTROL_KEY,
    ReleaseControlRow,
)
from mura.storage.archive import ArchivePersonRow
from mura.storage.completion import finalize_recording_job
from mura.storage.book import (
    BookCreationRepository,
    BookJobRow,
    BookRow,
    BookSourceSnapshotRow,
)
from mura.storage.database import Database, RecordingRepository, utcnow
from mura.storage.identity import FamilyRow, UserRow

POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="TEST_POSTGRES_URL is required for the PostgreSQL integration smoke test",
)


def _result(recording_id: str) -> PipelineResult:
    transcript = TranscriptEnvelope(
        recording_id=recording_id,
        duration_seconds=1.0,
        full_text="synthetic postgres fixture",
        segments=[
            RawSegment(
                segment_id="seg_001",
                start=0,
                end=1,
                text="synthetic postgres fixture",
            )
        ],
        asr_model="fixture",
        asr_revision="fixture-v1",
        chunker_version="fixture-v1",
    )
    return PipelineResult(
        transcript=transcript,
        cleaned_transcript=CleanerResult(
            readable_segments=[
                ReadableSegment(segment_id="seg_001", text="Synthetic PostgreSQL fixture.")
            ],
            full_readable_text="Synthetic PostgreSQL fixture.",
        ),
        extraction=ExtractionResult(
            recording_id=recording_id,
            speaker_id="speaker_postgres",
            speaker_name="Synthetic Speaker",
        ),
        processing={"total_seconds": 0.1},
    )


def test_postgres_migration_and_atomic_completion(tmp_path: Path) -> None:
    assert POSTGRES_URL is not None
    suffix = uuid.uuid4().hex[:12]
    recording_id = f"rec_pg_{suffix}"
    job_id = f"job_pg_{suffix}"
    person_id = f"person_pg_{suffix}"

    database = Database(POSTGRES_URL)
    with database.session_factory() as session:
        release_control = session.get(ReleaseControlRow, RELEASE_CONTROL_KEY)
        assert release_control is not None
        assert release_control.active_release_id == CURRENT_RELEASE_ID
        assert release_control.generation == 1

    repository = RecordingRepository(database)
    audio_path = tmp_path / f"{recording_id}.wav"
    audio_path.write_bytes(b"audio")
    repository.create_recording_and_job(
        recording_id=recording_id,
        job_id=job_id,
        family_id="family_postgres",
        speaker_id="speaker_postgres",
        speaker_name="Synthetic Speaker",
        original_filename="story.wav",
        content_type="audio/wav",
        audio_path=audio_path,
    )
    claimed = None
    for _ in range(50):
        c = repository.claim_next_job(lease_owner="worker_test", lease_seconds=300)
        if c is not None and c.job_id == job_id:
            claimed = c
            break
        if c is None:
            break
    assert claimed is not None and claimed.job_id == job_id

    trace = ProcessingTrace(
        job_id=job_id,
        recording_id=recording_id,
        family_id="family_postgres",
        attempt=1,
    )
    trace.start("archive_persistence")
    trace.finish(
        "archive_persistence",
        outcome=TraceOutcome.INFO,
        event_name="transaction_prepared",
    )

    with pytest.raises(RuntimeError, match="force postgres rollback"):
        with database.session_factory.begin() as session:
            session.add(
                ArchivePersonRow(
                    person_id=person_id,
                    family_id="family_postgres",
                    canonical_name="Synthetic Person",
                    normalized_name="synthetic person",
                    aliases=[],
                    verified_aliases=[],
                    category="family_member",
                    relations_to_speakers={},
                    source_recording_ids=[recording_id],
                )
            )
            finalize_recording_job(
                session,
                job_id=job_id,
                result=_result(recording_id),
                trace_events=trace.events,
            )
            raise RuntimeError("force postgres rollback")

    assert repository.get_pipeline_result(recording_id) is None
    rolled_back_job = repository.get_job(job_id)
    assert rolled_back_job is not None
    assert rolled_back_job.status == JobStatus.TRANSCRIBING.value
    assert TraceRepository(database).get_job_trace(job_id=job_id) is None
    with database.session_factory() as session:
        assert (
            session.scalar(select(ArchivePersonRow).where(ArchivePersonRow.person_id == person_id))
            is None
        )

    with database.session_factory.begin() as session:
        session.add(
            ArchivePersonRow(
                person_id=person_id,
                family_id="family_postgres",
                canonical_name="Synthetic Person",
                normalized_name="synthetic person",
                aliases=[],
                verified_aliases=[],
                category="family_member",
                relations_to_speakers={},
                source_recording_ids=[recording_id],
            )
        )
        finalize_recording_job(
            session,
            job_id=job_id,
            result=_result(recording_id),
            trace_events=trace.events,
        )

    completed_job = repository.get_job(job_id)
    assert completed_job is not None
    assert completed_job.status == JobStatus.COMPLETED.value
    stored_result = repository.get_pipeline_result(recording_id)
    assert stored_result is not None
    assert stored_result.transcript == _result(recording_id).transcript
    assert stored_result.processing["release_id"] == CURRENT_RELEASE_ID
    budget = stored_result.processing["runtime_budget"]
    assert isinstance(budget, dict)
    assert budget["passed"] is True
    assert TraceRepository(database).get_job_trace(job_id=job_id) is not None



def test_postgres_concurrent_book_creation_serializes_on_family_lock() -> None:
    """Two real PostgreSQL transactions cannot both pass the active-book check."""

    assert POSTGRES_URL is not None
    suffix = uuid.uuid4().hex[:12]
    family_id = f"family_quota_pg_{suffix}"
    user_id = f"user_quota_pg_{suffix}"
    database = Database(POSTGRES_URL)
    now = utcnow()

    with database.session_factory.begin() as session:
        session.add(
            UserRow(
                user_id=user_id,
                auth_issuer="https://auth.mura.test",
                auth_subject=f"quota-{suffix}",
                email=None,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            FamilyRow(
                family_id=family_id,
                name="Postgres quota race",
                created_by_user_id=user_id,
                created_at=now,
                updated_at=now,
            )
        )

    snapshot = BookSourceSnapshot(
        compiler_version="postgres-quota-test",
        family_id=family_id,
        manifest=SnapshotManifest(created_at=now),
    )
    compiled = CompiledSnapshot(snapshot=snapshot, content_hash="0" * 64)
    settings = type(
        "QuotaSettings",
        (),
        {
            "book_max_active_per_family": 1,
            "book_max_created_per_family_per_day": 3,
        },
    )()

    first_inserted = Event()
    release_first = Event()
    second_started = Event()
    second_done = Event()
    outcomes: dict[str, object] = {}

    def first_transaction() -> None:
        try:
            with database.session_factory.begin() as session:
                BookQuotaService.check_creation_allowed(session, family_id, settings)
                BookCreationRepository(database).create_queued_book(
                    family_id=family_id,
                    created_by_user_id=user_id,
                    title="First",
                    output_language="ru",
                    target_word_count=20000,
                    compiled_snapshot=compiled,
                    session=session,
                )
                first_inserted.set()
                if not release_first.wait(timeout=5):
                    raise RuntimeError("test did not release first transaction")
            outcomes["first"] = "committed"
        except BaseException as exc:
            outcomes["first"] = exc
            first_inserted.set()
            release_first.set()

    def second_transaction() -> None:
        if not first_inserted.wait(timeout=5):
            outcomes["second"] = RuntimeError("first transaction did not reach lock")
            second_done.set()
            return
        try:
            with database.session_factory.begin() as session:
                second_started.set()
                BookQuotaService.check_creation_allowed(session, family_id, settings)
                BookCreationRepository(database).create_queued_book(
                    family_id=family_id,
                    created_by_user_id=user_id,
                    title="Second",
                    output_language="ru",
                    target_word_count=20000,
                    compiled_snapshot=compiled,
                    session=session,
                )
            outcomes["second"] = "created"
        except HTTPException as exc:
            outcomes["second"] = (exc.status_code, exc.detail)
        except BaseException as exc:
            outcomes["second"] = exc
        finally:
            second_done.set()

    first = Thread(target=first_transaction, daemon=True)
    second = Thread(target=second_transaction, daemon=True)
    try:
        first.start()
        assert first_inserted.wait(timeout=5)
        assert outcomes.get("first") is None

        second.start()
        assert second_started.wait(timeout=5)
        # Transaction B must be waiting on FamilyRow FOR UPDATE while A owns it.
        assert second_done.wait(timeout=0.25) is False

        release_first.set()
        first.join(timeout=5)
        second.join(timeout=5)
        assert not first.is_alive()
        assert not second.is_alive()
        assert outcomes["first"] == "committed"
        assert outcomes["second"] == (409, "book_generation_already_active")

        with database.session_factory() as session:
            assert (
                session.scalar(
                    select(func.count(BookRow.book_id)).where(BookRow.family_id == family_id)
                )
                == 1
            )
            assert (
                session.scalar(
                    select(func.count(BookSourceSnapshotRow.snapshot_id)).where(
                        BookSourceSnapshotRow.family_id == family_id
                    )
                )
                == 1
            )
            assert (
                session.scalar(
                    select(func.count(BookJobRow.job_id)).where(
                        BookJobRow.family_id == family_id
                    )
                )
                == 1
            )
    finally:
        release_first.set()
        first.join(timeout=5)
        second.join(timeout=5)
        with database.session_factory.begin() as session:
            session.execute(delete(FamilyRow).where(FamilyRow.family_id == family_id))
            session.execute(delete(UserRow).where(UserRow.user_id == user_id))
