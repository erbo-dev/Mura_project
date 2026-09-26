"""PostgreSQL concurrency tests for AI cost guards and audio duration quotas.

Validates:
1. Transactional advisory locking (pg_advisory_xact_lock) serializing
   concurrent AI budget reservations.
2. Row-level locking (with_for_update) serializing concurrent recording
   quota checks and inserts.
"""

from __future__ import annotations

import concurrent.futures
import os
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import delete

from mura.cost_guards import AICostBudgetExceededError, CostGuardService
from mura.quotas import RecordingQuotaService
from mura.storage.ai_usage import (
    AIUsageEventRow,
    AIUsageReservationRow,
    ReservationStatus,
)
from mura.storage.database import (
    Database,
    ProcessingJobRow,
    RecordingRow,
    utcnow,
)
from mura.storage.identity import FamilyMembershipRow, FamilyRow, UserRow

POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.skipif(
        not POSTGRES_URL,
        reason="TEST_POSTGRES_URL is required for PostgreSQL concurrency tests",
    ),
    pytest.mark.skipif(
        bool(POSTGRES_URL) and "test" not in (POSTGRES_URL or "").rsplit("/", 1)[-1].lower(),
        reason="TEST_POSTGRES_URL database name must contain 'test' to be treated as disposable",
    ),
]


def _cleanup(database: Database) -> None:
    with database.session_factory.begin() as session:
        session.execute(delete(AIUsageReservationRow))
        session.execute(delete(AIUsageEventRow))
        session.execute(delete(ProcessingJobRow))
        session.execute(delete(RecordingRow))
        session.execute(delete(FamilyMembershipRow))
        session.execute(delete(FamilyRow))
        session.execute(delete(UserRow))


@pytest.fixture
def pg_db() -> Database:
    assert POSTGRES_URL is not None
    db = Database(POSTGRES_URL)
    _cleanup(db)
    yield db
    _cleanup(db)


def _seed_identity(
    database: Database,
    *,
    family_id: str = "fam_conc_test",
    user_ids: list[str] | None = None,
) -> None:
    if user_ids is None:
        user_ids = ["usr_conc_1"]
    now = utcnow()
    with database.session_factory.begin() as s:
        for uid in user_ids:
            s.add(
                UserRow(
                    user_id=uid,
                    auth_issuer="test_issuer",
                    auth_subject=f"sub_{uid}",
                    created_at=now,
                    updated_at=now,
                )
            )
        s.flush()
        s.add(
            FamilyRow(
                family_id=family_id,
                name="Concurrency Family",
                created_by_user_id=user_ids[0],
                created_at=now,
                updated_at=now,
            )
        )
        s.flush()
        for uid in user_ids:
            s.add(
                FamilyMembershipRow(
                    membership_id=f"mem_{uid}",
                    family_id=family_id,
                    user_id=uid,
                    role="owner" if uid == user_ids[0] else "editor",
                    created_at=now,
                    updated_at=now,
                )
            )


def test_concurrent_ai_budget_reservations_never_exceed_limit(pg_db: Database) -> None:
    """Test that concurrent reservation attempts serialized by pg_advisory_xact_lock

    never over-allocate budget.
    """
    _seed_identity(pg_db)

    # Budget $10.00. 10 workers each try to reserve $2.50 concurrently.
    # Exactly 4 should succeed ($10.00), 6 should raise AICostBudgetExceededError.
    daily_budget = Decimal("10.00")
    cost_service = CostGuardService(
        database=pg_db,
        global_daily_budget_usd=daily_budget,
        ttl_seconds=300.0,
    )

    num_threads = 10
    cost_per_attempt = Decimal("2.50")

    successes: list[str] = []
    failures: list[Exception] = []

    def _attempt_reservation(thread_idx: int) -> None:
        try:
            res = cost_service.reserve_budget(
                operation="book_draft",
                provider="deepseek",
                model="deepseek-chat",
                estimated_cost_usd=cost_per_attempt,
                family_id="fam_conc_test",
                user_id="usr_conc_1",
            )
            successes.append(res.reservation_id)
        except Exception as exc:
            failures.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(_attempt_reservation, i) for i in range(num_threads)]
        concurrent.futures.wait(futures)

    assert len(successes) == 4, f"Expected 4 successes, got {len(successes)}"
    assert len(failures) == 6, f"Expected 6 failures, got {len(failures)}"
    for f in failures:
        assert isinstance(f, AICostBudgetExceededError)

    # Verify database state
    with pg_db.session_factory() as s:
        reservations = (
            s.query(AIUsageReservationRow).filter_by(status=ReservationStatus.RESERVED.value).all()
        )
        assert len(reservations) == 4
        total_reserved = sum(r.reserved_cost_usd for r in reservations)
        assert total_reserved == daily_budget


def test_concurrent_recording_audio_duration_quota(pg_db: Database) -> None:
    """Test that concurrent recording creations serialized by with_for_update

    on UserRow and FamilyRow never exceed user or family daily audio duration quotas.
    """
    _seed_identity(pg_db)

    # User limit 1000s, incoming recording 300s each.
    # 8 concurrent attempts -> exactly 3 succeed (900s), 5 fail with 429.
    settings = MagicMock()
    settings.recording_max_audio_seconds = 1800.0
    settings.user_audio_seconds_per_day = 1000.0
    settings.family_audio_seconds_per_day = 5000.0
    settings.recording_max_active_per_family = 100
    settings.recording_max_created_per_family_per_day = 100
    settings.recording_max_created_per_user_per_day = 100
    settings.family_max_audio_storage_bytes = 10**9

    num_threads = 8
    successes: list[str] = []
    failures: list[Exception] = []

    def _attempt_recording(idx: int) -> None:
        rec_id = f"rec_conc_{idx}"
        try:
            with pg_db.session_factory.begin() as s:
                RecordingQuotaService.check_creation_allowed(
                    session=s,
                    family_id="fam_conc_test",
                    user_id="usr_conc_1",
                    incoming_size_bytes=1000,
                    settings=settings,
                    incoming_duration_seconds=300.0,
                )
                now = utcnow()
                s.add(
                    RecordingRow(
                        recording_id=rec_id,
                        family_id="fam_conc_test",
                        created_by_user_id="usr_conc_1",
                        speaker_id="narrator",
                        speaker_name="Narrator",
                        original_filename=f"rec_{idx}.wav",
                        content_type="audio/wav",
                        audio_path=f"fam_conc_test/{rec_id}.wav",
                        audio_duration_seconds=300.0,
                        created_at=now,
                    )
                )
            successes.append(rec_id)
        except Exception as exc:
            failures.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(_attempt_recording, i) for i in range(num_threads)]
        concurrent.futures.wait(futures)

    from apps.api.errors import RECORDING_USER_AUDIO_BUDGET_EXCEEDED

    assert len(successes) == 3, f"Expected 3 successes, got {len(successes)}"
    assert len(failures) == 5, f"Expected 5 failures, got {len(failures)}"
    for f in failures:
        assert isinstance(f, HTTPException)
        assert f.status_code == 429
        assert f.detail == RECORDING_USER_AUDIO_BUDGET_EXCEEDED

    with pg_db.session_factory() as s:
        records = s.query(RecordingRow).all()
        assert len(records) == 3
        total_duration = sum(r.audio_duration_seconds for r in records)
        assert total_duration == 900.0


def test_concurrent_family_audio_duration_quota(pg_db: Database) -> None:
    """Test that concurrent uploads across different family members serialized by with_for_update

    on FamilyRow never exceed the family daily audio duration quota.
    """
    from apps.api.errors import RECORDING_FAMILY_AUDIO_BUDGET_EXCEEDED

    _seed_identity(pg_db, family_id="fam_conc_family", user_ids=["usr_f1", "usr_f2"])

    # Family limit 800s, user limit 1000s, incoming recording 300s each.
    # 6 attempts (3 from usr_f1, 3 from usr_f2):
    # Exactly 2 succeed (600s), 4 fail with RECORDING_FAMILY_AUDIO_BUDGET_EXCEEDED.
    settings = MagicMock()
    settings.recording_max_audio_seconds = 1800.0
    settings.user_audio_seconds_per_day = 1000.0
    settings.family_audio_seconds_per_day = 800.0
    settings.recording_max_active_per_family = 100
    settings.recording_max_created_per_family_per_day = 100
    settings.recording_max_created_per_user_per_day = 100
    settings.family_max_audio_storage_bytes = 10**9

    num_threads = 6
    successes: list[str] = []
    failures: list[Exception] = []

    def _attempt_recording(idx: int) -> None:
        user_id = "usr_f1" if idx % 2 == 0 else "usr_f2"
        rec_id = f"rec_fam_{idx}"
        try:
            with pg_db.session_factory.begin() as s:
                RecordingQuotaService.check_creation_allowed(
                    session=s,
                    family_id="fam_conc_family",
                    user_id=user_id,
                    incoming_size_bytes=1000,
                    settings=settings,
                    incoming_duration_seconds=300.0,
                )
                now = utcnow()
                s.add(
                    RecordingRow(
                        recording_id=rec_id,
                        family_id="fam_conc_family",
                        created_by_user_id=user_id,
                        speaker_id="narrator",
                        speaker_name="Narrator",
                        original_filename=f"rec_{idx}.wav",
                        content_type="audio/wav",
                        audio_path=f"fam_conc_family/{rec_id}.wav",
                        audio_duration_seconds=300.0,
                        created_at=now,
                    )
                )
            successes.append(rec_id)
        except Exception as exc:
            failures.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(_attempt_recording, i) for i in range(num_threads)]
        concurrent.futures.wait(futures)

    assert len(successes) == 2, f"Expected 2 successes, got {len(successes)}"
    assert len(failures) == 4, f"Expected 4 failures, got {len(failures)}"
    for f in failures:
        assert isinstance(f, HTTPException)
        assert f.status_code == 429
        assert f.detail == RECORDING_FAMILY_AUDIO_BUDGET_EXCEEDED

    with pg_db.session_factory() as s:
        records = s.query(RecordingRow).all()
        assert len(records) == 2
        total_duration = sum(r.audio_duration_seconds for r in records)
        assert total_duration == 600.0
