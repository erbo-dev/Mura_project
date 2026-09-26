"""Tests for AI usage ledger chapter number correlation and ContextVar safety.

Verifies:
- BookChapterContextManager correctly sets chapter_number_ctx in local execution context.
- AIUsageLedger automatically extracts chapter_number from chapter_number_ctx.
- ContextVar is safely reset on exiting the context manager (including on exceptions).
- Explicit chapter_number argument is respected.
"""

from __future__ import annotations

import pytest

from mura.logging import BookChapterContextManager, chapter_number_ctx
from mura.storage.ai_usage import AIUsageLedger
from mura.storage.database import Database


def test_chapter_context_manager_lifecycle() -> None:
    assert chapter_number_ctx.get() is None

    with BookChapterContextManager(4):
        assert chapter_number_ctx.get() == 4

        with BookChapterContextManager(5):
            assert chapter_number_ctx.get() == 5

        assert chapter_number_ctx.get() == 4

    assert chapter_number_ctx.get() is None


def test_chapter_context_manager_resets_on_exception() -> None:
    assert chapter_number_ctx.get() is None

    with pytest.raises(RuntimeError):
        with BookChapterContextManager(2):
            assert chapter_number_ctx.get() == 2
            raise RuntimeError("drafting failed")

    assert chapter_number_ctx.get() is None


def test_ai_usage_ledger_records_chapter_number_from_context() -> None:
    db = Database("sqlite+pysqlite:///:memory:")
    db.create_schema()
    ledger = AIUsageLedger(db)

    # 1. Outside context -> chapter_number is None
    event_outside = ledger.record_usage(
        provider="deepseek",
        model="deepseek-v4-flash",
        operation="plan_book",
        latency_ms=1200,
        success=True,
    )
    assert event_outside.chapter_number is None

    # 2. Inside context -> chapter_number is 3
    with BookChapterContextManager(3):
        event_inside = ledger.record_usage(
            provider="deepseek",
            model="deepseek-v4-flash",
            operation="draft_chapter",
            latency_ms=4500,
            success=True,
        )
        assert event_inside.chapter_number == 3

    # 3. Outside context again -> chapter_number is None
    event_after = ledger.record_usage(
        provider="deepseek",
        model="deepseek-v4-flash",
        operation="export_epub",
        latency_ms=300,
        success=True,
    )
    assert event_after.chapter_number is None

    # 4. Explicit parameter overrides context
    with BookChapterContextManager(1):
        event_override = ledger.record_usage(
            provider="deepseek",
            model="deepseek-v4-flash",
            operation="repair_chapter",
            latency_ms=1000,
            success=True,
            chapter_number=7,
        )
        assert event_override.chapter_number == 7
