"""Layer 11: Privacy, Observability, and AI Usage Attribution tests for Family Book.

Verifies:
1. Zero literary prose, prompt, or evidence leakage into logs and AI telemetry.
2. AI usage events record book_id and chapter_number for per-book cost tracking.
3. Cost accounting calculates valid USD amounts or sets NULL for unknown models.
4. LogSanitizer sanitizes book prose, chapter text, and quotes while preserving opaque book IDs.
"""

from __future__ import annotations

from decimal import Decimal

from mura.logging import LogSanitizer, book_id_ctx
from mura.storage.ai_usage import AIUsageEvent, AIUsageEventRow, AIUsageLedger
from mura.storage.database import Database
from mura.storage.identity import FamilyMembershipRow, FamilyRow, UserRow  # noqa: F401


def test_ai_usage_ledger_records_book_id_and_chapter() -> None:
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    ledger = AIUsageLedger(database)

    # Record usage for chapter 3 of a family book
    ledger.record_usage(
        provider="deepseek",
        model="deepseek-chat",
        operation="write_chapter",
        latency_ms=2500,
        success=True,
        input_tokens=4000,
        output_tokens=1500,
        cached_input_tokens=1000,
        book_id="book_test_123",
        chapter_number=3,
        family_id="fam_alpha",
    )

    with database.session_factory() as session:
        from sqlalchemy import select

        stmt = select(AIUsageEventRow).where(AIUsageEventRow.book_id == "book_test_123")
        row = session.scalar(stmt)
        assert row is not None
        assert row.book_id == "book_test_123"
        assert row.chapter_number == 3
        assert row.family_id == "fam_alpha"
        assert row.operation == "write_chapter"
        assert row.input_tokens == 4000
        assert row.output_tokens == 1500
        assert row.cached_input_tokens == 1000
        assert row.estimated_cost_usd is not None
        assert row.estimated_cost_usd > Decimal("0")


def test_ai_usage_event_schema_has_zero_literary_fields() -> None:
    """Verifies privacy by construction: AIUsageEvent has no slots for literary content."""
    fields = AIUsageEvent.model_fields.keys()
    forbidden_substrings = [
        "prompt",
        "response",
        "transcript",
        "prose",
        "text",
        "content",
        "quote",
        "draft",
        "chapter_text",
        "story",
    ]
    for field in fields:
        for forbidden in forbidden_substrings:
            msg = f"Forbidden field '{field}' found in AIUsageEvent"
            assert forbidden not in field.lower(), msg


def test_log_sanitizer_scrubs_book_prose_and_preserves_identifiers() -> None:
    """LogSanitizer must redact all draft/final prose and quotes."""
    extra = {
        "book_id": "book_abc123",
        "chapter_number": 4,
        "chapters_total": 12,
        "chapters_approved": 3,
        "word_count": 1850,
        "target_word_count": 2000,
        "output_language": "kk",
        "stage": "writing_chapter",
        # Sensitive literary fields that must be redacted:
        "prose": "Бұл құпия естелік мәтіні...",
        "draft_text": "Draft of the family story with private details.",
        "final_text": "Approved text of the chapter with family names.",
        "evidence_quote": "Атамыз 1941 жылы майданға аттанды.",
        "unresolved_question": "Қай жылы көшіп келген?",
    }

    sanitized = LogSanitizer.sanitize_dict(extra)

    # Safe keys are preserved verbatim
    assert sanitized["book_id"] == "book_abc123"
    assert sanitized["chapter_number"] == 4
    assert sanitized["chapters_total"] == 12
    assert sanitized["chapters_approved"] == 3
    assert sanitized["word_count"] == 1850
    assert sanitized["target_word_count"] == 2000
    assert sanitized["output_language"] == "kk"
    assert sanitized["stage"] == "writing_chapter"

    # Literary content is strictly redacted
    assert sanitized["prose"] == "[REDACTED]"
    assert sanitized["draft_text"] == "[REDACTED]"
    assert sanitized["final_text"] == "[REDACTED]"
    assert sanitized["evidence_quote"] == "[REDACTED]"
    assert sanitized["unresolved_question"] == "[REDACTED]"


def test_contextvar_book_id_propagation() -> None:
    """book_id_ctx safely carries correlation through async tasks."""
    assert book_id_ctx.get() is None
    token = book_id_ctx.set("book_correlation_456")
    try:
        assert book_id_ctx.get() == "book_correlation_456"
    finally:
        book_id_ctx.reset(token)
    assert book_id_ctx.get() is None
