"""Tests for Book generation job retry semantics and failure classification."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from mura.domain.book_models import BookJobStatus, BookLanguage, BookStatus
from mura.leases import LeaseOwnershipLost
from mura.orchestration.books import BookJobWorker
from mura.reliability.failures import (
    ClassifiedFailure,
    FailureCategory,
    FailureDisposition,
    calculate_retry_delay,
    classify_failure,
)
from mura.storage.book import (
    BookCreationRepository,
    BookJobRepository,
    BookJobRow,
    BookRepository,
)
from mura.storage.book_artifacts import LocalBookArtifactStorage
from mura.storage.database import Database, utcnow
from mura.storage.identity import FamilyRow, UserRow


def _aware(dt: datetime) -> datetime:
    from datetime import timezone
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

def _make_mock_response(status_code: int, headers: dict[str, str] | None = None) -> Any:
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    return resp


def test_classify_provider_rate_limit_with_retry_after() -> None:
    exc = Exception("Rate limit exceeded")
    setattr(exc, "response", _make_mock_response(429, {"Retry-After": "45"}))
    classified = classify_failure(exc)
    assert classified.category == FailureCategory.PROVIDER_RATE_LIMIT
    assert classified.disposition == FailureDisposition.RETRY
    assert classified.is_retryable is True
    assert classified.retry_after_seconds == 45.0
    assert classified.error_code == "provider_rate_limit"


def test_classify_provider_server_error() -> None:
    exc = Exception("Internal Server Error 503")
    setattr(exc, "response", _make_mock_response(503))
    classified = classify_failure(exc)
    assert classified.category == FailureCategory.PROVIDER_SERVER_ERROR
    assert classified.disposition == FailureDisposition.RETRY
    assert classified.error_code == "provider_server_error"


def test_classify_provider_auth_error_terminal() -> None:
    exc = Exception("Unauthorized 401")
    setattr(exc, "response", _make_mock_response(401))
    classified = classify_failure(exc)
    assert classified.category == FailureCategory.PROVIDER_AUTH_ERROR
    assert classified.disposition == FailureDisposition.TERMINAL
    assert classified.is_retryable is False
    assert classified.error_code == "provider_auth_error"


def test_classify_timeouts() -> None:
    exc = TimeoutError("Connection to provider timed out")
    classified = classify_failure(exc)
    assert classified.category == FailureCategory.PROVIDER_TIMEOUT
    assert classified.disposition == FailureDisposition.RETRY
    assert classified.error_code == "provider_timeout"


def test_classify_lease_lost_terminal() -> None:
    exc = LeaseOwnershipLost("job_test_123")
    classified = classify_failure(exc)
    assert classified.category == FailureCategory.LEASE_LOST
    assert classified.disposition == FailureDisposition.TERMINAL
    assert classified.is_retryable is False


def test_classify_grounding_blocked_terminal() -> None:
    exc = RuntimeError("Chapter 2 failed validation gates after 2 repairs")
    classified = classify_failure(exc)
    assert classified.category == FailureCategory.GROUNDING_BLOCKED
    assert classified.disposition == FailureDisposition.TERMINAL
    assert classified.error_code == "gate_failed"


def test_calculate_retry_delay_exponential_and_bounds() -> None:
    # Attempt 1: base = 5.0
    d1 = calculate_retry_delay(1, base_seconds=5.0, max_seconds=300.0, jitter=False)
    assert d1 == 5.0

    # Attempt 2: base * 2 = 10.0
    d2 = calculate_retry_delay(2, base_seconds=5.0, max_seconds=300.0, jitter=False)
    assert d2 == 10.0

    # Attempt 3: base * 4 = 20.0
    d3 = calculate_retry_delay(3, base_seconds=5.0, max_seconds=300.0, jitter=False)
    assert d3 == 20.0

    # Respects retry_after
    d_after = calculate_retry_delay(1, base_seconds=5.0, max_seconds=300.0, retry_after=60.0)
    assert d_after == 60.0

    # Clamps to max_seconds
    d_max = calculate_retry_delay(10, base_seconds=50.0, max_seconds=120.0, jitter=False)
    assert d_max == 120.0



@pytest.fixture
def db() -> Database:
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    return database


def test_claim_next_job_excludes_exhausted_attempts(db: Database) -> None:
    now = utcnow()
    with db.session_factory.begin() as session:
        session.add(FamilyRow(family_id="fam_retry_test", name="Retry Fam", created_at=now, updated_at=now))
        session.add(
            UserRow(
                user_id="user_retry_test",
                auth_issuer="https://auth.mura.test",
                auth_subject="sub_retry_test",
                email="retry@test.com",
                created_at=now,
                updated_at=now,
            )
        )

    book_repo = BookRepository(db)
    job_repo = BookJobRepository(db)

    book = book_repo.create_book(
        book_id="book_exhausted_1",
        family_id="fam_retry_test",
        created_by_user_id="user_retry_test",
        title="Exhausted Book",
        output_language=BookLanguage.RU.value,
        target_word_count=5000,
    )
    job = job_repo.create_job(
        book_id=book.book_id,
        family_id="fam_retry_test",
        max_attempts=3,
    )

    # Simulate 3 attempts already consumed
    with db.session_factory.begin() as session:
        j_row = session.get(BookJobRow, job.job_id)
        assert j_row is not None
        j_row.attempts = 3
        j_row.lease_owner = None
        j_row.lease_expires_at = None

    # Claim should return None because attempts >= max_attempts
    claimed = job_repo.claim_next_job(lease_owner="worker_test_1", lease_seconds=60.0)
    assert claimed is None


def test_defer_job_resets_lease_and_updates_next_attempt(db: Database) -> None:
    now = utcnow()
    with db.session_factory.begin() as session:
        session.add(FamilyRow(family_id="fam_defer_test", name="Defer Fam", created_at=now, updated_at=now))
        session.add(
            UserRow(
                user_id="user_defer_test",
                auth_issuer="https://auth.mura.test",
                auth_subject="sub_defer_test",
                email="defer@test.com",
                created_at=now,
                updated_at=now,
            )
        )

    book_repo = BookRepository(db)
    job_repo = BookJobRepository(db)

    book = book_repo.create_book(
        book_id="book_defer_1",
        family_id="fam_defer_test",
        created_by_user_id="user_defer_test",
        title="Defer Book",
        output_language=BookLanguage.RU.value,
        target_word_count=5000,
    )
    job = job_repo.create_job(
        book_id=book.book_id,
        family_id="fam_defer_test",
    )

    # Claim the job
    claimed = job_repo.claim_next_job(lease_owner="worker_defer_1", lease_seconds=60.0)
    assert claimed is not None
    assert claimed.lease_owner == "worker_defer_1"
    assert claimed.status == "running"

    # Defer the job
    future_time = utcnow() + timedelta(seconds=120)
    deferred = job_repo.defer_job(
        job_id=claimed.job_id,
        next_attempt_at=future_time,
        error_code="provider_rate_limit",
        error_detail="HTTP 429",
        lease_owner="worker_defer_1",
    )
    assert deferred is not None
    assert deferred.status == "queued"
    assert deferred.lease_owner is None
    assert deferred.lease_expires_at is None
    assert deferred.error_code == "provider_rate_limit"
    assert deferred.next_attempt_at == future_time
    assert _aware(deferred.next_attempt_at) == _aware(future_time)


def test_book_worker_defers_on_retryable_provider_error(db: Database, tmp_path: Any) -> None:
    now = utcnow()
    with db.session_factory.begin() as session:
        session.add(FamilyRow(family_id="fam_worker_retry", name="Worker Retry", created_at=now, updated_at=now))
        session.add(
            UserRow(
                user_id="user_worker_retry",
                auth_issuer="https://auth.mura.test",
                auth_subject="sub_worker_retry",
                email="worker_retry@test.com",
                created_at=now,
                updated_at=now,
            )
        )

    book_repo = BookRepository(db)
    job_repo = BookJobRepository(db)
    artifact_storage = LocalBookArtifactStorage(tmp_path / "artifacts")

    book = book_repo.create_book(
        book_id="book_worker_retry_1",
        family_id="fam_worker_retry",
        created_by_user_id="user_worker_retry",
        title="Retry Flow Book",
        output_language=BookLanguage.RU.value,
        target_word_count=5000,
    )
    job = job_repo.create_job(
        book_id=book.book_id,
        family_id="fam_worker_retry",
        max_attempts=3,
    )

    # Mock client that raises HTTP 429
    failing_client = MagicMock()
    err = Exception("DeepSeek Rate Limit")
    setattr(err, "response", _make_mock_response(429, {"Retry-After": "30"}))
    failing_client.side_effect = err
    failing_client.request_json.side_effect = err

    worker = BookJobWorker(
        db=db,
        deepseek_client=failing_client,
        artifact_storage=artifact_storage,
        retry_base_seconds=5.0,
        retry_max_seconds=60.0,
    )

    # Process once -> should catch 429, defer job
    processed = worker.process_once()
    assert processed is True

    # Check job state: should be deferred (queued with next_attempt_at in the future)
    updated_job = job_repo.get_job(job.job_id)
    assert updated_job is not None
    assert updated_job.status == "queued"
    assert updated_job.attempts == 1
    assert updated_job.error_code == "provider_rate_limit"
    assert updated_job.lease_owner is None
    assert _aware(updated_job.next_attempt_at) > utcnow()

    # Book should NOT be failed
    updated_book = book_repo.get_book_unscoped(book.book_id)
    assert updated_book is not None
    assert updated_book.status != BookStatus.FAILED.value

