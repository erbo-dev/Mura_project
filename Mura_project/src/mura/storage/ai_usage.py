"""Durable AI usage ledger and cost accounting.

Persists operational metadata for every AI provider call (DeepSeek, Whisper).
NEVER stores prompts, responses, transcripts, person names, or family content.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from pydantic import Field
from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, func, select
from sqlalchemy.orm import Mapped, mapped_column

from mura.cost import calculate_ai_cost
from mura.domain.models import StrictModel
from mura.logging import (
    attempt_ctx,
    book_id_ctx,
    chapter_number_ctx,
    family_id_ctx,
    job_id_ctx,
    recording_id_ctx,
    request_id_ctx,
)
from mura.storage.database import Base, Database, utcnow


class AIUsageEventRow(Base):
    __tablename__ = "ai_usage_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    provider: Mapped[str] = mapped_column(String(64), index=True)
    model: Mapped[str] = mapped_column(String(64))
    operation: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    recording_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    book_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    chapter_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    family_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_seconds: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    pricing_version: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AIUsageEvent(StrictModel):
    event_id: str = Field(min_length=1, max_length=64)
    created_at: datetime = Field(default_factory=utcnow)
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=64)
    operation: str = Field(min_length=1, max_length=64)
    request_id: str | None = None
    job_id: str | None = None
    recording_id: str | None = None
    book_id: str | None = None
    chapter_number: int | None = None
    family_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    audio_seconds: Decimal | None = None
    latency_ms: int = Field(ge=0)
    success: bool
    attempt: int = Field(default=1, ge=1)
    error_code: str | None = None
    estimated_cost_usd: Decimal | None = None
    pricing_version: str | None = None


class AIUsageLedger:
    def __init__(self, database: Database) -> None:
        self.database = database

    def record_usage(
        self,
        *,
        provider: str,
        model: str,
        operation: str,
        latency_ms: int,
        success: bool,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        audio_seconds: float | int | Decimal | None = None,
        attempt: int | None = None,
        error_code: str | None = None,
        request_id: str | None = None,
        job_id: str | None = None,
        recording_id: str | None = None,
        book_id: str | None = None,
        chapter_number: int | None = None,
        family_id: str | None = None,
    ) -> AIUsageEvent:
        """Record an AI invocation event with automatic correlation and cost calculation."""
        # Inherit correlation identifiers from ContextVars if not explicitly given
        resolved_req_id = request_id if request_id is not None else request_id_ctx.get()
        resolved_job_id = job_id if job_id is not None else job_id_ctx.get()
        resolved_rec_id = recording_id if recording_id is not None else recording_id_ctx.get()
        resolved_book_id = book_id if book_id is not None else book_id_ctx.get()
        resolved_chap_num = (
            chapter_number if chapter_number is not None else chapter_number_ctx.get()
        )
        resolved_fam_id = family_id if family_id is not None else family_id_ctx.get()
        resolved_attempt = (
            attempt
            if attempt is not None
            else (attempt_ctx.get() or 1)
        )

        audio_sec_dec = (
            Decimal(str(round(float(audio_seconds), 3)))
            if audio_seconds is not None
            else None
        )

        cost, pricing_version = calculate_ai_cost(
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            audio_seconds=audio_sec_dec,
        )

        event_id = f"ai_{uuid.uuid4().hex}"
        now = utcnow()

        event = AIUsageEvent(
            event_id=event_id,
            created_at=now,
            provider=provider,
            model=model,
            operation=operation,
            request_id=resolved_req_id,
            job_id=resolved_job_id,
            recording_id=resolved_rec_id,
            book_id=resolved_book_id,
            chapter_number=resolved_chap_num,
            family_id=resolved_fam_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            audio_seconds=audio_sec_dec,
            latency_ms=max(0, latency_ms),
            success=success,
            attempt=resolved_attempt,
            error_code=error_code,
            estimated_cost_usd=cost,
            pricing_version=pricing_version,
        )

        with self.database.session_factory.begin() as session:
            row = AIUsageEventRow(
                event_id=event.event_id,
                created_at=event.created_at,
                provider=event.provider,
                model=event.model,
                operation=event.operation,
                request_id=event.request_id,
                job_id=event.job_id,
                recording_id=event.recording_id,
                book_id=event.book_id,
                chapter_number=event.chapter_number,
                family_id=event.family_id,
                input_tokens=event.input_tokens,
                output_tokens=event.output_tokens,
                cached_input_tokens=event.cached_input_tokens,
                audio_seconds=event.audio_seconds,
                latency_ms=event.latency_ms,
                success=event.success,
                attempt=event.attempt,
                error_code=event.error_code,
                estimated_cost_usd=event.estimated_cost_usd,
                pricing_version=event.pricing_version,
            )
            session.add(row)

        return event

    def get_summary_24h(self, now: datetime | None = None) -> dict[str, Any]:
        """Aggregate AI usage and costs across the last 24 hours."""
        moment = now or utcnow()
        since = moment - timedelta(hours=24)

        with self.database.session_factory() as session:
            statement = select(
                func.count(AIUsageEventRow.event_id),
                func.coalesce(func.sum(AIUsageEventRow.input_tokens), 0),
                func.coalesce(func.sum(AIUsageEventRow.output_tokens), 0),
                func.coalesce(func.sum(AIUsageEventRow.cached_input_tokens), 0),
                func.coalesce(func.sum(AIUsageEventRow.audio_seconds), 0),
                func.coalesce(func.sum(AIUsageEventRow.estimated_cost_usd), 0),
            ).where(AIUsageEventRow.created_at >= since)

            row = session.execute(statement).one()
            req_count, in_tok, out_tok, cached_tok, audio_sec, total_cost = row

            return {
                "requests_count": int(req_count),
                "input_tokens": int(in_tok),
                "output_tokens": int(out_tok),
                "cached_input_tokens": int(cached_tok),
                "audio_seconds": float(audio_sec),
                "estimated_cost_usd": str(Decimal(str(total_cost)).quantize(Decimal("0.000001"))),
            }

