from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from mura.cost_guards import AICostBudgetExceededError, CostGuardService
from mura.jobs import JobStatus
from mura.orchestration.books import BookJobWorker
from mura.orchestration.recordings import RecordingJobWorker
from mura.quotas import BookQuotaService
from mura.storage.ai_usage import AIUsageLedger, AIUsageReservationRow, ReservationStatus
from mura.storage.book import BookJobRow, BookRow
from mura.storage.database import (
    Database,
    ProcessingJobRow,
    RecordingRepository,
    RecordingRow,
    utcnow,
)
from mura.storage.identity import FamilyRow, UserRow


def _db() -> Database:
    db = Database("sqlite+pysqlite:///:memory:")
    db.create_schema()
    return db


def _seed_identity(db: Database, family_id: str = "fam_cg", user_id: str = "usr_cg") -> None:
    with db.session_factory.begin() as session:
        session.add(
            UserRow(
                user_id=user_id,
                auth_issuer="issuer",
                auth_subject="sub",
            )
        )
        session.flush()
        session.add(
            FamilyRow(
                family_id=family_id,
                name="Cost Guard Family",
                created_by_user_id=user_id,
            )
        )


def test_reserve_budget_success_and_lifecycle() -> None:
    db = _db()
    service = CostGuardService(db, global_daily_budget_usd=Decimal("10.00"))

    # Reserve $3.00
    res1 = service.reserve_budget(
        operation="asr",
        provider="whisper",
        model="whisper-1",
        estimated_cost_usd=Decimal("3.000000"),
        family_id="fam_1",
    )
    assert res1.reservation_id.startswith("res_")
    assert res1.status == ReservationStatus.RESERVED.value

    # Commit $2.50
    service.commit_reservation(
        res1.reservation_id,
        actual_cost_usd=Decimal("2.500000"),
    )

    with db.session_factory() as s:
        row = s.get(AIUsageReservationRow, res1.reservation_id)
        assert row is not None
        assert row.status == ReservationStatus.COMMITTED.value
        assert row.actual_cost_usd == Decimal("2.500000")


def test_reserve_budget_blocks_when_limit_exceeded() -> None:
    db = _db()
    service = CostGuardService(db, global_daily_budget_usd=Decimal("5.00"))

    # Reserve $4.00
    service.reserve_budget(
        operation="extraction",
        provider="deepseek",
        model="deepseek-v4-flash",
        estimated_cost_usd=Decimal("4.000000"),
    )

    # Attempt to reserve $2.00 -> total projected $6.00 > $5.00 limit
    with pytest.raises(AICostBudgetExceededError) as exc_info:
        service.reserve_budget(
            operation="extraction",
            provider="deepseek",
            model="deepseek-v4-flash",
            estimated_cost_usd=Decimal("2.000000"),
        )
    assert exc_info.value.daily_budget_usd == Decimal("5.00")
    assert exc_info.value.requested_cost_usd == Decimal("2.000000")


def test_release_reservation_frees_budget() -> None:
    db = _db()
    service = CostGuardService(db, global_daily_budget_usd=Decimal("5.00"))

    res = service.reserve_budget(
        operation="extraction",
        provider="deepseek",
        model="deepseek-v4-flash",
        estimated_cost_usd=Decimal("4.500000"),
    )

    # Next reservation of $1.00 should fail
    with pytest.raises(AICostBudgetExceededError):
        service.reserve_budget(
            operation="extraction",
            provider="deepseek",
            model="deepseek-v4-flash",
            estimated_cost_usd=Decimal("1.000000"),
        )

    # Release first reservation
    service.release_reservation(res.reservation_id)

    # Now reservation should succeed
    res2 = service.reserve_budget(
        operation="extraction",
        provider="deepseek",
        model="deepseek-v4-flash",
        estimated_cost_usd=Decimal("1.000000"),
    )
    assert res2.status == ReservationStatus.RESERVED.value


def test_expired_reservations_do_not_block_budget() -> None:
    db = _db()
    service = CostGuardService(db, global_daily_budget_usd=Decimal("5.00"))

    # Reserve $4.50 with -10 second TTL (already expired)
    service.reserve_budget(
        operation="extraction",
        provider="deepseek",
        model="deepseek-v4-flash",
        estimated_cost_usd=Decimal("4.500000"),
        ttl_seconds=-10.0,
    )

    # New reservation of $2.00 succeeds because expired reservations don't block
    res = service.reserve_budget(
        operation="extraction",
        provider="deepseek",
        model="deepseek-v4-flash",
        estimated_cost_usd=Decimal("2.000000"),
    )
    assert res.status == ReservationStatus.RESERVED.value


def test_idempotent_reservation() -> None:
    db = _db()
    service = CostGuardService(db, global_daily_budget_usd=Decimal("5.00"))

    key = "idem_key_12345"
    res1 = service.reserve_budget(
        operation="asr",
        provider="whisper",
        model="whisper-1",
        estimated_cost_usd=Decimal("3.000000"),
        idempotency_key=key,
    )

    # Calling again with same idempotency key returns same reservation and does not double-count
    res2 = service.reserve_budget(
        operation="asr",
        provider="whisper",
        model="whisper-1",
        estimated_cost_usd=Decimal("3.000000"),
        idempotency_key=key,
    )
    assert res1.reservation_id == res2.reservation_id


def test_recording_worker_never_calls_asr_on_budget_exhaustion() -> None:
    db = _db()
    _seed_identity(db)
    recording_repo = RecordingRepository(db)

    # Exhaust budget ($0.00 limit)
    cost_guards = CostGuardService(db, global_daily_budget_usd=Decimal("0.00"))

    mock_asr = MagicMock()
    mock_pipeline = MagicMock()

    worker = RecordingJobWorker(
        repository=recording_repo,
        pipeline=mock_pipeline,
        asr_client=mock_asr,
        cost_guards=cost_guards,
    )
    # Ensure asr_client is recognized as paid/whisper without tunnel worker registration
    mock_asr.provider = "whisper"
    mock_asr.model = "whisper-1"
    mock_asr.requires_registered_worker = False

    # Create recording
    recording_id = "rec_test_cg"
    job_id = "job_test_cg"
    recording_repo.create_recording_and_job(
        recording_id=recording_id,
        job_id=job_id,
        family_id="fam_cg",
        created_by_user_id="usr_cg",
        speaker_id="narrator_test",
        speaker_name="Narrator",
        original_filename="audio.wav",
        content_type="audio/wav",
        audio_path="fam_cg/rec_test_cg/audio.wav",
        audio_duration_seconds=120.0,
    )

    with db.session_factory.begin() as s:
        job_row = s.get(ProcessingJobRow, job_id)
        assert job_row is not None
        job_row.lease_owner = worker.worker_id

    with db.session_factory() as s:
        job = s.get(ProcessingJobRow, job_id)
        recording = s.get(RecordingRow, recording_id)
        assert job is not None
        assert recording is not None

        # Execute single job attempt
        worker._execute_job_attempt(job, recording, attempt=1)

    # ZERO PROVIDER CALL GUARANTEE: mock_asr.transcribe was NEVER called
    assert mock_asr.transcribe.call_count == 0
    assert mock_pipeline.process.call_count == 0

    # Job is deferred with ai_cost_budget_exceeded
    with db.session_factory() as s:
        updated_job = s.get(ProcessingJobRow, job_id)
        assert updated_job is not None
        assert updated_job.error_code == "ai_cost_budget_exceeded"


def test_book_worker_never_calls_deepseek_on_budget_exhaustion() -> None:
    db = _db()
    _seed_identity(db)

    cost_guards = CostGuardService(db, global_daily_budget_usd=Decimal("0.00"))
    mock_deepseek = MagicMock()
    mock_deepseek.model = "deepseek-chat"

    artifact_storage = MagicMock()

    worker = BookJobWorker(
        db=db,
        deepseek_client=mock_deepseek,
        artifact_storage=artifact_storage,
        cost_guards=cost_guards,
    )

    book_id = "book_test_cg"
    job_id = "bjob_test_cg"

    # Create book, job, and snapshot
    now = utcnow()
    with db.session_factory.begin() as s:
        s.add(
            BookRow(
                book_id=book_id,
                family_id="fam_cg",
                created_by_user_id="usr_cg",
                title="Family Book",
                status="queued",
                stage="queued",
                output_language="ru",
                target_word_count=5000,
            )
        )
        s.add(
            BookJobRow(
                job_id=job_id,
                book_id=book_id,
                family_id="fam_cg",
                status=JobStatus.QUEUED.value,
                stage="queued",
                next_attempt_at=now,
                lease_owner=worker.worker_id,
            )
        )

    from mura.book.snapshot import compile_source_snapshot
    from mura.storage.archive_read import GroundingBundle
    from mura.storage.book import BookSourceSnapshotRepository

    bundle = GroundingBundle(
        family_id="fam_cg",
        recordings=[
            {
                "recording_id": "rec_1",
                "family_id": "fam_cg",
                "speaker_name": "Narrator",
                "speaker_id": "spk_1",
                "detected_language": "ru",
            }
        ],
        pipeline_payloads={
            "rec_1": {
                "extraction": {
                    "languages": ["ru"],
                    "evidence_spans": [{"evidence_id": "ev_1", "text": "Текст памяти."}],
                }
            }
        },
        people=[],
        stories=[],
        events=[],
        claims=[
            {
                "claim_id": "cl_1",
                "family_id": "fam_cg",
                "recording_id": "rec_1",
                "object_type": "description",
                "source_object_id": "desc_1",
                "predicate": "description",
                "payload": {"description": "Текст памяти."},
                "evidence_ids": ["ev_1"],
                "evidence_class": "A_EXPLICIT",
                "assertion_mode": "explicit",
                "verification_status": "unreviewed",
                "archive_status": "active",
            }
        ],
    )
    compiled = compile_source_snapshot(bundle)
    snapshot = compiled.snapshot
    snap_repo = BookSourceSnapshotRepository(db)
    snap_repo.save_snapshot(
        book_id=book_id,
        family_id="fam_cg",
        compiler_version=snapshot.compiler_version,
        content_hash=compiled.content_hash,
        payload=snapshot.model_dump(mode="json"),
        manifest=snapshot.manifest.model_dump(mode="json"),
        source_recording_count=1,
        source_story_count=0,
        source_claim_count=1,
    )

    with db.session_factory() as s:
        book = s.get(BookRow, book_id)
        job = s.get(BookJobRow, job_id)
        assert book is not None
        assert job is not None

        # Execute single job
        worker._process_job(job)

    # ZERO CALL GUARANTEE: DeepSeek client was never invoked
    assert mock_deepseek.call_count == 0
    assert mock_deepseek.request_json.call_count == 0

    # Book job is deferred with ai_cost_budget_exceeded
    with db.session_factory() as s:
        updated_job = s.get(BookJobRow, job_id)
        assert updated_job is not None
        assert updated_job.error_code == "ai_cost_budget_exceeded"


def test_book_quota_service_blocks_creation_when_global_ai_budget_exhausted() -> None:
    db = _db()
    _seed_identity(db)

    # Record usage that consumes $30.00 (above $25.00 limit)
    ledger = AIUsageLedger(db)
    ledger.record_usage(
        provider="deepseek",
        model="deepseek-v4-pro",
        operation="cleaner",
        latency_ms=500,
        success=True,
        input_tokens=50_000_000,
        output_tokens=10_000_000,
    )

    settings = MagicMock()
    settings.book_max_active_per_family = 5
    settings.book_max_created_per_family_per_day = 10
    settings.global_ai_cost_usd_per_day = 25.00

    with db.session_factory.begin() as s:
        with pytest.raises(HTTPException) as exc:
            BookQuotaService.check_creation_allowed(s, "fam_cg", settings)
        assert exc.value.status_code == 429
        assert exc.value.detail == "global_ai_cost_budget_exceeded"


def test_f001_provider_success_with_ledger_failure_enforces_budget() -> None:
    """F-001 regression: If AIUsageLedger.record_usage fails, committed cost is still enforced."""
    db = _db()
    service = CostGuardService(db, global_daily_budget_usd=Decimal("1.00"))

    # 1. Reserve $0.60
    res = service.reserve_budget(
        operation="asr",
        provider="whisper",
        model="whisper-1",
        estimated_cost_usd=Decimal("0.60"),
    )
    assert res.status == ReservationStatus.RESERVED.value

    # 2. Simulate provider success + AIUsageLedger.record_usage failure (e.g. dropped/errored)
    # No AIUsageEventRow is inserted into the database.

    # 3. Settle reservation with actual cost $0.60
    service.commit_reservation(res.reservation_id, actual_cost_usd=Decimal("0.60"))

    # 4. Attempt another reservation of $0.60 -> total would be $1.20 > $1.00 limit -> REJECTED
    with pytest.raises(AICostBudgetExceededError) as exc_info:
        service.reserve_budget(
            operation="extraction",
            provider="deepseek",
            model="deepseek-v4-flash",
            estimated_cost_usd=Decimal("0.60"),
        )
    assert exc_info.value.daily_budget_usd == Decimal("1.00")
    assert exc_info.value.current_consumed_usd == Decimal("0.60")

    # 5. Attempt reservation of $0.30 -> total is $0.90 <= $1.00 limit -> ALLOWED
    res2 = service.reserve_budget(
        operation="extraction",
        provider="deepseek",
        model="deepseek-v4-flash",
        estimated_cost_usd=Decimal("0.30"),
    )
    assert res2.status == ReservationStatus.RESERVED.value


def test_f001_normal_path_no_double_counting() -> None:
    """F-001 regression: Normal path with both ledger event and committed reservation
    does NOT double count.
    """
    db = _db()
    service = CostGuardService(db, global_daily_budget_usd=Decimal("1.00"))
    ledger = AIUsageLedger(db)

    # 1. Reserve $0.60
    res = service.reserve_budget(
        operation="extraction",
        provider="deepseek",
        model="deepseek-v4-flash",
        estimated_cost_usd=Decimal("0.60"),
    )

    # 2. Telemetry event successfully records usage of $0.60
    ledger.record_usage(
        provider="deepseek",
        model="deepseek-v4-flash",
        operation="extraction",
        latency_ms=300,
        success=True,
        input_tokens=1_000_000,
        output_tokens=200_000,
    )

    # 3. Reservation commits actual cost of $0.60
    service.commit_reservation(res.reservation_id, actual_cost_usd=Decimal("0.60"))

    # 4. Next request for $0.30 -> allowed (consumed is $0.60, projected $0.90 <= $1.00)
    res_allowed = service.reserve_budget(
        operation="extraction",
        provider="deepseek",
        model="deepseek-v4-flash",
        estimated_cost_usd=Decimal("0.30"),
    )
    assert res_allowed.status == ReservationStatus.RESERVED.value

    # 5. Next request for $0.20 -> rejected (consumed is $0.90, projected $1.10 > $1.00)
    with pytest.raises(AICostBudgetExceededError):
        service.reserve_budget(
            operation="extraction",
            provider="deepseek",
            model="deepseek-v4-flash",
            estimated_cost_usd=Decimal("0.20"),
        )


def test_f001_worker_crash_preserves_durable_cost() -> None:
    """F-001 regression: Worker crash after reservation settlement preserves cost authority."""
    db = _db()
    service = CostGuardService(db, global_daily_budget_usd=Decimal("1.00"))

    # Provider succeeded and reservation was committed with actual cost
    res = service.reserve_budget(
        operation="asr",
        provider="whisper",
        model="whisper-1",
        estimated_cost_usd=Decimal("0.60"),
    )
    service.commit_reservation(res.reservation_id, actual_cost_usd=Decimal("0.60"))

    # Worker crashes: new worker/process instance checks budget
    new_service_instance = CostGuardService(db, global_daily_budget_usd=Decimal("1.00"))
    with db.session_factory() as session:
        # $0.50 cannot be reserved because $0.60 is already committed
        assert not new_service_instance.check_budget_available(
            session, additional_cost_usd=Decimal("0.50")
        )
        # $0.30 can be reserved
        assert new_service_instance.check_budget_available(
            session, additional_cost_usd=Decimal("0.30")
        )


def test_f001_idempotent_retry_after_settlement() -> None:
    """F-001 regression: Retry of settled job does not create duplicate cost or
    double reservation.
    """
    db = _db()
    service = CostGuardService(db, global_daily_budget_usd=Decimal("1.00"))

    key = "job_retry_test_key"
    res1 = service.reserve_budget(
        operation="asr",
        provider="whisper",
        model="whisper-1",
        estimated_cost_usd=Decimal("0.60"),
        idempotency_key=key,
    )
    service.commit_reservation(res1.reservation_id, actual_cost_usd=Decimal("0.60"))

    # Retry with same idempotency key
    res_retry = service.reserve_budget(
        operation="asr",
        provider="whisper",
        model="whisper-1",
        estimated_cost_usd=Decimal("0.60"),
        idempotency_key=key,
    )
    assert res_retry.reservation_id == res1.reservation_id
    assert res_retry.status == ReservationStatus.COMMITTED.value

    # Total consumed budget is still exactly $0.60
    with db.session_factory() as session:
        assert service.check_budget_available(session, additional_cost_usd=Decimal("0.35"))
        assert not service.check_budget_available(session, additional_cost_usd=Decimal("0.45"))
