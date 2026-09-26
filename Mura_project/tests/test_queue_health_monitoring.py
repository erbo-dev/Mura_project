from __future__ import annotations

from datetime import timedelta

from mura.jobs import JobStatus
from mura.monitoring import MonitoringThresholds, QueueHealthService
from mura.storage.database import Database, ProcessingJobRow, RecordingRow, utcnow


def _seed_job(
    database: Database,
    *,
    job_id: str,
    status: JobStatus,
    stage: str = "queued",
    attempts: int = 0,
    created_at_offset_seconds: float = 0.0,
    lease_owner: str | None = None,
    lease_expires_at_offset_seconds: float | None = None,
    completed_at_offset_seconds: float | None = None,
) -> None:
    now = utcnow()
    created = now - timedelta(seconds=created_at_offset_seconds)
    lease_expires = (
        now + timedelta(seconds=lease_expires_at_offset_seconds)
        if lease_expires_at_offset_seconds is not None
        else None
    )
    completed = (
        now - timedelta(seconds=completed_at_offset_seconds)
        if completed_at_offset_seconds is not None
        else None
    )

    with database.session_factory.begin() as session:
        rec_id = f"rec_{job_id}"
        if session.get(RecordingRow, rec_id) is None:
            session.add(
                RecordingRow(
                    recording_id=rec_id,
                    family_id="fam_test",
                    speaker_id="spk_test",
                    speaker_name="Test Speaker",
                    original_filename="audio.wav",
                    audio_path="/tmp/audio.wav",
                    created_at=created,
                )
            )
            session.flush()

        session.add(
            ProcessingJobRow(
                job_id=job_id,
                recording_id=rec_id,
                status=status.value,
                stage=stage,
                attempts=attempts,
                lease_owner=lease_owner,
                lease_expires_at=lease_expires,
                created_at=created,
                completed_at=completed,
            )
        )


def test_queue_metrics_and_oldest_pending_calculation() -> None:
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    service = QueueHealthService(database)

    now = utcnow()

    # Seed jobs:
    # 2 queued (one created 120s ago, one 30s ago)
    _seed_job(database, job_id="job_q1", status=JobStatus.QUEUED, created_at_offset_seconds=120)
    _seed_job(database, job_id="job_q2", status=JobStatus.QUEUED, created_at_offset_seconds=30)
    # 1 running (transcribing with active lease)
    _seed_job(
        database,
        job_id="job_r1",
        status=JobStatus.TRANSCRIBING,
        lease_owner="worker_1",
        lease_expires_at_offset_seconds=200,
    )
    # 1 running with expired lease (expired 10s ago)
    _seed_job(
        database,
        job_id="job_r2",
        status=JobStatus.CLEANING,
        lease_owner="worker_2",
        lease_expires_at_offset_seconds=-10,
    )
    # 1 failed
    _seed_job(database, job_id="job_f1", status=JobStatus.FAILED)

    metrics = service.get_queue_metrics(now)
    assert metrics.pending == 2
    assert metrics.running == 2
    assert metrics.failed == 1
    assert metrics.expired_leases == 1
    assert metrics.oldest_pending_seconds is not None
    assert 119.0 <= metrics.oldest_pending_seconds <= 125.0


def test_pipeline_24h_metrics() -> None:
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    service = QueueHealthService(database)

    # 1 completed 2 hours ago (within 24h)
    _seed_job(
        database,
        job_id="job_c1",
        status=JobStatus.COMPLETED,
        completed_at_offset_seconds=7200,
    )
    # 1 completed 28 hours ago (outside 24h)
    _seed_job(
        database,
        job_id="job_c2",
        status=JobStatus.COMPLETED,
        completed_at_offset_seconds=100800,
    )
    # 1 failed 1 hour ago (within 24h)
    _seed_job(
        database,
        job_id="job_f1",
        status=JobStatus.FAILED,
        completed_at_offset_seconds=3600,
    )

    pipeline = service.get_pipeline_24h()
    assert pipeline.completed == 1
    assert pipeline.failed == 1


def test_stuck_job_detection() -> None:
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    thresholds = MonitoringThresholds(
        stuck_pending_threshold_seconds=600.0,
        lease_grace_seconds=60.0,
        max_attempts=3,
    )
    service = QueueHealthService(database, thresholds=thresholds)

    # Job 1: queued for 700 seconds (> 600) -> pending_too_long
    _seed_job(
        database, job_id="job_stuck_q", status=JobStatus.QUEUED, created_at_offset_seconds=700
    )

    # Job 2: active with lease expired 120s ago (> 60 grace) -> lease_expired
    _seed_job(
        database,
        job_id="job_stuck_l",
        status=JobStatus.EXTRACTING,
        lease_owner="worker_dead",
        lease_expires_at_offset_seconds=-120,
    )

    # Job 3: attempted 3 times and still queued -> max_retries_exceeded
    _seed_job(
        database,
        job_id="job_stuck_retries",
        status=JobStatus.QUEUED,
        attempts=3,
        created_at_offset_seconds=100,
    )

    # Job 4: normal queued job (50s) -> should NOT be flagged
    _seed_job(
        database, job_id="job_normal_q", status=JobStatus.QUEUED, created_at_offset_seconds=50
    )

    stuck = service.get_stuck_jobs()
    reasons_by_id = {item.job_id: item.reason for item in stuck}

    assert "job_stuck_q" in reasons_by_id
    assert reasons_by_id["job_stuck_q"] == "pending_too_long"

    assert "job_stuck_l" in reasons_by_id
    assert reasons_by_id["job_stuck_l"] == "lease_expired"

    assert "job_stuck_retries" in reasons_by_id
    assert reasons_by_id["job_stuck_retries"] == "max_retries_exceeded"

    assert "job_normal_q" not in reasons_by_id


def test_thresholds_from_runtime_settings() -> None:
    thresholds = MonitoringThresholds.from_settings(
        job_lease_seconds=300.0,
        job_heartbeat_seconds=60.0,
        asr_request_timeout_seconds=900.0,
    )
    # Sized from runtime config: max(300*2, 900/2) = max(600, 450) = 600.0
    assert thresholds.stuck_pending_threshold_seconds == 600.0
    assert thresholds.lease_grace_seconds == 60.0
    assert thresholds.max_attempts == 3


def test_book_queue_metrics_and_stuck_detection() -> None:
    from mura.domain.book_models import BookJobStatus
    from mura.storage.book import BookJobRow

    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    service = QueueHealthService(database)
    now = utcnow()

    with database.session_factory.begin() as session:
        # Normal running book job
        session.add(
            BookJobRow(
                job_id="bjob_normal",
                book_id="book_1",
                family_id="fam_1",
                status=BookJobStatus.RUNNING.value,
                stage="writing_chapter",
                attempts=1,
                max_attempts=3,
                lease_owner="worker_1",
                lease_expires_at=now + timedelta(seconds=300),
                created_at=now - timedelta(seconds=100),
                next_attempt_at=now,
                updated_at=now,
            )
        )
        # Stuck pending job (created 1500s ago, threshold is 1200s)
        session.add(
            BookJobRow(
                job_id="bjob_stuck_pending",
                book_id="book_2",
                family_id="fam_1",
                status=BookJobStatus.QUEUED.value,
                stage="preparing_sources",
                attempts=0,
                max_attempts=3,
                created_at=now - timedelta(seconds=1500),
                next_attempt_at=now,
                updated_at=now,
            )
        )
        # Stuck running job with expired lease (expired 100s ago)
        session.add(
            BookJobRow(
                job_id="bjob_expired_lease",
                book_id="book_3",
                family_id="fam_1",
                status=BookJobStatus.RUNNING.value,
                stage="planning",
                attempts=1,
                max_attempts=3,
                lease_owner="worker_dead",
                lease_expires_at=now - timedelta(seconds=100),
                created_at=now - timedelta(seconds=500),
                next_attempt_at=now,
                updated_at=now,
            )
        )
        # Job exceeding max retries
        session.add(
            BookJobRow(
                job_id="bjob_retries_exceeded",
                book_id="book_4",
                family_id="fam_1",
                status=BookJobStatus.QUEUED.value,
                stage="preparing_sources",
                attempts=3,
                max_attempts=3,
                created_at=now - timedelta(seconds=200),
                next_attempt_at=now,
                updated_at=now,
            )
        )

    metrics = service.get_book_queue_metrics()
    assert metrics.queued == 2
    assert metrics.running == 2
    assert metrics.expired_leases == 1
    assert metrics.oldest_queued_seconds is not None
    assert metrics.oldest_queued_seconds >= 1400.0

    stuck_items = service.get_book_stuck_jobs()
    reasons_by_id = {item.job_id: item.reason for item in stuck_items}
    assert reasons_by_id.get("bjob_stuck_pending") == "pending_too_long"
    assert reasons_by_id.get("bjob_expired_lease") == "lease_expired"
    assert reasons_by_id.get("bjob_retries_exceeded") == "max_retries_exceeded"
    assert "bjob_normal" not in reasons_by_id

    summary = service.get_summary()
    assert summary.book_queue is not None
    assert summary.book_queue.queued == 2
    assert len(summary.book_stuck_jobs) == 3
