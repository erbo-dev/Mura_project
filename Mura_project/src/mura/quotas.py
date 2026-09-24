"""Durable product quotas and concurrency limits."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from apps.api.errors import (
    BOOK_DAILY_LIMIT_REACHED,
    BOOK_GENERATION_ALREADY_ACTIVE,
    FAMILY_AUDIO_STORAGE_LIMIT_REACHED,
    FAMILY_NOT_FOUND,
    RECORDING_CONCURRENCY_LIMIT_REACHED,
    RECORDING_FAMILY_DAILY_LIMIT_REACHED,
    RECORDING_USER_DAILY_LIMIT_REACHED,
)
from mura.domain.book_models import TERMINAL_BOOK_STATUSES
from mura.jobs import JobStatus
from mura.storage.book import BookRow
from mura.storage.database import ProcessingJobRow, RecordingRow, utcnow
from mura.storage.identity import FamilyRow


class BookQuotaService:
    @staticmethod
    def check_creation_allowed(
        session: Session,
        family_id: str,
        settings: Any,
    ) -> None:
        """Verify active and daily Book limits under the family row lock.

        The `FOR UPDATE` lock serializes concurrent creation requests.
        """
        # 1. Exclusive row lock on the family row
        locked_family_id = session.scalar(
            select(FamilyRow.family_id)
            .where(FamilyRow.family_id == family_id)
            .with_for_update()
        )
        if locked_family_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=FAMILY_NOT_FOUND,
            )

        max_active = getattr(settings, "book_max_active_per_family", 1)
        max_daily = getattr(settings, "book_max_created_per_family_per_day", 3)

        # 2. Check active books limit (in-progress generations)
        active_count = session.scalar(
            select(func.count(BookRow.book_id)).where(
                BookRow.family_id == family_id,
                BookRow.status.notin_(TERMINAL_BOOK_STATUSES),
            )
        ) or 0

        if active_count >= max_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=BOOK_GENERATION_ALREADY_ACTIVE,
            )

        # 3. Check daily creation limit (books created in past 24 hours)
        now = utcnow()
        since_24h = now - timedelta(hours=24)
        daily_count = session.scalar(
            select(func.count(BookRow.book_id)).where(
                BookRow.family_id == family_id,
                BookRow.created_at >= since_24h,
            )
        ) or 0

        if daily_count >= max_daily:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=BOOK_DAILY_LIMIT_REACHED,
            )




class RecordingQuotaService:
    """Concurrency-safe recording creation and storage limits."""

    @staticmethod
    def check_creation_allowed(
        session: Session,
        *,
        family_id: str,
        user_id: str,
        incoming_size_bytes: int,
        settings: Any,
    ) -> None:
        if incoming_size_bytes < 0:
            raise ValueError("incoming_size_bytes must be non-negative")

        locked_family_id = session.scalar(
            select(FamilyRow.family_id)
            .where(FamilyRow.family_id == family_id)
            .with_for_update()
        )
        if locked_family_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=FAMILY_NOT_FOUND,
            )

        max_active = getattr(settings, "recording_max_active_per_family", 4)
        max_family_daily = getattr(
            settings, "recording_max_created_per_family_per_day", 100
        )
        max_user_daily = getattr(
            settings, "recording_max_created_per_user_per_day", 25
        )
        max_storage_bytes = getattr(
            settings, "family_max_audio_storage_bytes", 10 * 1024 * 1024 * 1024
        )

        active_count = (
            session.scalar(
                select(func.count(ProcessingJobRow.job_id))
                .join(
                    RecordingRow,
                    RecordingRow.recording_id == ProcessingJobRow.recording_id,
                )
                .where(
                    RecordingRow.family_id == family_id,
                    ProcessingJobRow.status.notin_(
                        (JobStatus.COMPLETED.value, JobStatus.FAILED.value)
                    ),
                )
            )
            or 0
        )
        if active_count >= max_active:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=RECORDING_CONCURRENCY_LIMIT_REACHED,
            )

        since_24h = utcnow() - timedelta(hours=24)
        family_daily = (
            session.scalar(
                select(func.count(RecordingRow.recording_id)).where(
                    RecordingRow.family_id == family_id,
                    RecordingRow.created_at >= since_24h,
                )
            )
            or 0
        )
        if family_daily >= max_family_daily:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=RECORDING_FAMILY_DAILY_LIMIT_REACHED,
            )

        user_daily = (
            session.scalar(
                select(func.count(RecordingRow.recording_id)).where(
                    RecordingRow.created_by_user_id == user_id,
                    RecordingRow.created_at >= since_24h,
                )
            )
            or 0
        )
        if user_daily >= max_user_daily:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=RECORDING_USER_DAILY_LIMIT_REACHED,
            )

        stored_bytes = int(
            session.scalar(
                select(
                    func.coalesce(func.sum(RecordingRow.audio_size_bytes), 0)
                ).where(RecordingRow.family_id == family_id)
            )
            or 0
        )
        if stored_bytes + incoming_size_bytes > max_storage_bytes:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=FAMILY_AUDIO_STORAGE_LIMIT_REACHED,
            )
