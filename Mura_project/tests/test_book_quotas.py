"""Tests for durable family book generation quotas and abuse prevention."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock

from fastapi import HTTPException
import pytest
from sqlalchemy import select

from apps.api.errors import (
    BOOK_DAILY_LIMIT_REACHED,
    BOOK_GENERATION_ALREADY_ACTIVE,
)
from mura.config import CoreSettings
from mura.domain.book_models import BookLanguage, BookStatus
from mura.quotas import BookQuotaService
from mura.storage.book import BookRepository, BookRow
from mura.storage.database import Database, utcnow
from mura.storage.identity import FamilyRow, UserRow


@pytest.fixture
def db() -> Database:
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    return database


@pytest.fixture
def settings() -> CoreSettings:
    return CoreSettings.model_validate(
        {
            "DEEPSEEK_API_KEY": "sk-" + "d" * 40,
            "CORE_API_KEY": "c" * 40,
            "OPERATIONS_API_KEY": "o" * 40,
            "WORKER_REGISTRATION_TOKEN": "w" * 40,
            "KAGGLE_ASR_API_KEY": "k" * 40,
            "DATABASE_URL": "sqlite+pysqlite:///:memory:",
            "DATABASE_AUTO_CREATE": True,
            "BOOK_MAX_ACTIVE_PER_FAMILY": 1,
            "BOOK_MAX_CREATED_PER_FAMILY_PER_DAY": 3,
        }
    )


def test_quota_service_allows_creation_when_empty(db: Database, settings: CoreSettings) -> None:
    now = utcnow()
    with db.session_factory.begin() as session:
        session.add(FamilyRow(family_id="fam_q1", name="Quota Fam 1", created_at=now, updated_at=now))
        session.flush()
        # Should not raise
        BookQuotaService.check_creation_allowed(session, "fam_q1", settings)


def test_quota_service_blocks_when_active_book_exists(db: Database, settings: CoreSettings) -> None:
    now = utcnow()
    with db.session_factory.begin() as session:
        session.add(FamilyRow(family_id="fam_q2", name="Quota Fam 2", created_at=now, updated_at=now))
        session.add(
            UserRow(
                user_id="user_q2",
                auth_issuer="https://auth.mura.test",
                auth_subject="sub_q2",
                email="q2@test.com",
                created_at=now,
                updated_at=now,
            )
        )
        # Active book (status=queued)
        session.add(
            BookRow(
                book_id="book_active_1",
                family_id="fam_q2",
                created_by_user_id="user_q2",
                title="Active Book",
                status=BookStatus.QUEUED.value,
                stage="preparing_sources",
                output_language=BookLanguage.RU.value,
                target_word_count=5000,
                chapters_total=0,
                chapters_approved=0,
                word_count=0,
                created_at=now,
                updated_at=now,
            )
        )

    with db.session_factory.begin() as session:
        with pytest.raises(HTTPException) as exc_info:
            BookQuotaService.check_creation_allowed(session, "fam_q2", settings)

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail == BOOK_GENERATION_ALREADY_ACTIVE


def test_quota_service_terminal_books_do_not_block_active_quota(
    db: Database, settings: CoreSettings
) -> None:
    now = utcnow()
    with db.session_factory.begin() as session:
        session.add(FamilyRow(family_id="fam_q3", name="Quota Fam 3", created_at=now, updated_at=now))
        session.add(
            UserRow(
                user_id="user_q3",
                auth_issuer="https://auth.mura.test",
                auth_subject="sub_q3",
                email="q3@test.com",
                created_at=now,
                updated_at=now,
            )
        )
        # Completed book (terminal)
        session.add(
            BookRow(
                book_id="book_completed_1",
                family_id="fam_q3",
                created_by_user_id="user_q3",
                title="Completed Book",
                status=BookStatus.COMPLETED.value,
                stage="completed",
                output_language=BookLanguage.RU.value,
                target_word_count=5000,
                chapters_total=10,
                chapters_approved=10,
                word_count=5000,
                created_at=now - timedelta(hours=30),  # older than 24h
                updated_at=now - timedelta(hours=29),
            )
        )
        # Failed book (terminal)
        session.add(
            BookRow(
                book_id="book_failed_1",
                family_id="fam_q3",
                created_by_user_id="user_q3",
                title="Failed Book",
                status=BookStatus.FAILED.value,
                stage="failed",
                output_language=BookLanguage.RU.value,
                target_word_count=5000,
                chapters_total=10,
                chapters_approved=2,
                word_count=1000,
                created_at=now - timedelta(hours=26),  # older than 24h
                updated_at=now - timedelta(hours=25),
            )
        )

    with db.session_factory.begin() as session:
        # Terminal books do not block active quota
        BookQuotaService.check_creation_allowed(session, "fam_q3", settings)


def test_quota_service_blocks_when_daily_limit_reached(
    db: Database, settings: CoreSettings
) -> None:
    now = utcnow()
    with db.session_factory.begin() as session:
        session.add(FamilyRow(family_id="fam_q4", name="Quota Fam 4", created_at=now, updated_at=now))
        session.add(
            UserRow(
                user_id="user_q4",
                auth_issuer="https://auth.mura.test",
                auth_subject="sub_q4",
                email="q4@test.com",
                created_at=now,
                updated_at=now,
            )
        )
        # 3 Completed books within the last 24h (reaches daily limit of 3)
        for i in range(3):
            session.add(
                BookRow(
                    book_id=f"book_daily_{i}",
                    family_id="fam_q4",
                    created_by_user_id="user_q4",
                    title=f"Book {i}",
                    status=BookStatus.COMPLETED.value,
                    stage="completed",
                    output_language=BookLanguage.RU.value,
                    target_word_count=5000,
                    chapters_total=10,
                    chapters_approved=10,
                    word_count=5000,
                    created_at=now - timedelta(hours=i + 1),
                    updated_at=now - timedelta(hours=i),
                )
            )

    with db.session_factory.begin() as session:
        with pytest.raises(HTTPException) as exc_info:
            BookQuotaService.check_creation_allowed(session, "fam_q4", settings)

        assert exc_info.value.status_code == 429
        assert exc_info.value.detail == BOOK_DAILY_LIMIT_REACHED

