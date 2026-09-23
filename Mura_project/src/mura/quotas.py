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
    FAMILY_NOT_FOUND,
)
from mura.domain.book_models import TERMINAL_BOOK_STATUSES
from mura.storage.book import BookRow
from mura.storage.database import utcnow
from mura.storage.identity import FamilyRow


class BookQuotaService:
    @staticmethod
    def check_creation_allowed(
        session: Session,
        family_id: str,
        settings: Any,
    ) -> None:
        """Atomically verifies that a family has not exceeded active or daily book generation limits.

        Locks the family row using `FOR UPDATE` to serialize concurrent creation requests.
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

