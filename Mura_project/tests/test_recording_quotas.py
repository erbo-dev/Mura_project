from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from mura.jobs import JobStatus
from mura.quotas import RecordingQuotaService
from mura.storage.database import Database, ProcessingJobRow, RecordingRow, utcnow
from mura.storage.identity import FamilyRow, UserRow

FAMILY = "family_recording_quota"
USER_A = "user_recording_quota_a"
USER_B = "user_recording_quota_b"


def _db() -> Database:
    db = Database("sqlite+pysqlite:///:memory:")
    db.create_schema()
    with db.session_factory.begin() as session:
        session.add_all(
            [
                UserRow(
                    user_id=USER_A,
                    auth_issuer="issuer",
                    auth_subject="quota-a",
                    email=None,
                    display_name=None,
                ),
                UserRow(
                    user_id=USER_B,
                    auth_issuer="issuer",
                    auth_subject="quota-b",
                    email=None,
                    display_name=None,
                ),
            ]
        )
        session.flush()
        session.add(
            FamilyRow(
                family_id=FAMILY,
                name="Quota family",
                created_by_user_id=USER_A,
            )
        )
    return db


def _settings(**overrides: Any) -> SimpleNamespace:
    values = {
        "recording_max_active_per_family": 4,
        "recording_max_created_per_family_per_day": 100,
        "recording_max_created_per_user_per_day": 25,
        "family_max_audio_storage_bytes": 10_000,
        "recording_max_audio_seconds": 1800.0,
        "user_audio_seconds_per_day": 3600.0,
        "family_audio_seconds_per_day": 14400.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _recording(
    db: Database,
    *,
    recording_id: str,
    user_id: str,
    size_bytes: int = 100,
    duration_seconds: float | None = None,
    active: bool = False,
    hours_ago: int = 0,
) -> None:
    created_at = utcnow() - timedelta(hours=hours_ago)
    with db.session_factory.begin() as session:
        session.add(
            RecordingRow(
                recording_id=recording_id,
                family_id=FAMILY,
                created_by_user_id=user_id,
                speaker_id=f"narrator_{recording_id}",
                speaker_name="Narrator",
                original_filename="fixture.wav",
                content_type="audio/wav",
                audio_path=f"{FAMILY}/{recording_id}/fixture.wav",
                audio_size_bytes=size_bytes,
                audio_duration_seconds=duration_seconds,
                created_at=created_at,
            )
        )
        session.flush()
        session.add(
            ProcessingJobRow(
                job_id=f"job_{recording_id}",
                recording_id=recording_id,
                status=JobStatus.QUEUED.value if active else JobStatus.COMPLETED.value,
                stage="queued" if active else "completed",
                created_at=created_at,
            )
        )


def _check(
    db: Database,
    *,
    user_id: str = USER_A,
    incoming: int = 100,
    incoming_duration: float | None = None,
    **limits: Any,
) -> None:
    with db.session_factory.begin() as session:
        RecordingQuotaService.check_creation_allowed(
            session,
            family_id=FAMILY,
            user_id=user_id,
            incoming_size_bytes=incoming,
            incoming_duration_seconds=incoming_duration,
            settings=_settings(**limits),
        )


def test_recording_quota_allows_request_below_limits() -> None:
    db = _db()
    _recording(db, recording_id="rec_old", user_id=USER_A, hours_ago=48)
    _check(db)


def test_recording_quota_blocks_active_job_limit() -> None:
    db = _db()
    _recording(db, recording_id="rec_active", user_id=USER_A, active=True)
    with pytest.raises(HTTPException) as caught:
        _check(db, recording_max_active_per_family=1)
    assert caught.value.status_code == 429
    assert caught.value.detail == "recording_concurrency_limit_reached"


def test_recording_quota_blocks_family_daily_limit() -> None:
    db = _db()
    _recording(db, recording_id="rec_family", user_id=USER_B)
    with pytest.raises(HTTPException) as caught:
        _check(db, recording_max_created_per_family_per_day=1)
    assert caught.value.detail == "recording_family_daily_limit_reached"


def test_recording_quota_blocks_user_daily_limit() -> None:
    db = _db()
    _recording(db, recording_id="rec_user", user_id=USER_A)
    with pytest.raises(HTTPException) as caught:
        _check(
            db,
            recording_max_created_per_family_per_day=10,
            recording_max_created_per_user_per_day=1,
        )
    assert caught.value.detail == "recording_user_daily_limit_reached"


def test_recording_quota_blocks_family_storage_with_incoming_bytes() -> None:
    db = _db()
    _recording(db, recording_id="rec_storage", user_id=USER_B, size_bytes=900)
    with pytest.raises(HTTPException) as caught:
        _check(db, incoming=101, family_max_audio_storage_bytes=1000)
    assert caught.value.detail == "family_audio_storage_limit_reached"


def test_recording_quota_blocks_audio_too_long() -> None:
    db = _db()
    with pytest.raises(HTTPException) as caught:
        _check(db, incoming_duration=1801.0, recording_max_audio_seconds=1800.0)
    assert caught.value.status_code == 413
    assert caught.value.detail == "recording_audio_too_long"


def test_recording_quota_blocks_user_audio_daily_limit() -> None:
    db = _db()
    _recording(db, recording_id="rec_audio_1", user_id=USER_A, duration_seconds=3000.0)
    with pytest.raises(HTTPException) as caught:
        _check(
            db,
            user_id=USER_A,
            incoming_duration=601.0,
            user_audio_seconds_per_day=3600.0,
        )
    assert caught.value.status_code == 429
    assert caught.value.detail == "recording_user_audio_budget_exceeded"


def test_recording_quota_blocks_family_audio_daily_limit() -> None:
    db = _db()
    _recording(db, recording_id="rec_fam_audio_1", user_id=USER_B, duration_seconds=14000.0)
    with pytest.raises(HTTPException) as caught:
        _check(
            db,
            user_id=USER_A,
            incoming_duration=401.0,
            family_audio_seconds_per_day=14400.0,
        )
    assert caught.value.status_code == 429
    assert caught.value.detail == "recording_family_audio_budget_exceeded"
