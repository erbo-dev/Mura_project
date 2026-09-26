"""Concurrency-safe AI cost guards and reservation lifecycle.

Guarantees global AI dollar budget enforcement with PostgreSQL advisory
transaction locks, two-phase reservations (reserve -> commit/release),
and zero-provider-call semantics when budget limits are breached.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, select, text
from sqlalchemy.orm import Session

from mura.storage.ai_usage import (
    AIUsageEventRow,
    AIUsageReservationRow,
    ReservationStatus,
)
from mura.storage.database import Database, utcnow

logger = logging.getLogger("mura.cost_guards")

DEFAULT_GLOBAL_AI_COST_USD_PER_DAY = Decimal("25.00")
DEFAULT_AI_RESERVATION_TTL_SECONDS = 1800.0


class AICostBudgetExceededError(Exception):
    """Raised when a proposed AI operation would exceed the global daily cost budget."""

    def __init__(
        self,
        message: str = "Global AI cost budget exceeded",
        *,
        current_consumed_usd: Decimal | None = None,
        requested_cost_usd: Decimal | None = None,
        daily_budget_usd: Decimal | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.current_consumed_usd = current_consumed_usd
        self.requested_cost_usd = requested_cost_usd
        self.daily_budget_usd = daily_budget_usd


class CostGuardService:
    """Manages AI reservations and enforces daily dollar spending budgets."""

    def __init__(
        self,
        database: Database,
        *,
        global_daily_budget_usd: Decimal | float | None = None,
        ttl_seconds: float = DEFAULT_AI_RESERVATION_TTL_SECONDS,
    ) -> None:
        self.database = database
        if global_daily_budget_usd is not None:
            self.global_daily_budget_usd = Decimal(str(global_daily_budget_usd))
        else:
            self.global_daily_budget_usd = DEFAULT_GLOBAL_AI_COST_USD_PER_DAY
        self.ttl_seconds = ttl_seconds

    @classmethod
    def from_settings(cls, database: Database, settings: Any) -> CostGuardService:
        budget = getattr(settings, "global_ai_cost_usd_per_day", DEFAULT_GLOBAL_AI_COST_USD_PER_DAY)
        ttl = getattr(settings, "ai_reservation_ttl_seconds", DEFAULT_AI_RESERVATION_TTL_SECONDS)
        return cls(
            database=database,
            global_daily_budget_usd=budget,
            ttl_seconds=ttl,
        )

    def _acquire_lock_if_postgres(self, session: Session) -> None:
        """Serialize global AI budget check across processes under PostgreSQL."""
        bind = session.get_bind()
        if bind is not None and bind.dialect.name == "postgresql":
            session.execute(text("SELECT pg_advisory_xact_lock(hashtext('global_ai_cost_budget'))"))

    def check_budget_available(
        self,
        session: Session,
        additional_cost_usd: Decimal = Decimal("0"),
    ) -> bool:
        """Check whether additional_cost_usd can be reserved without exceeding budget."""
        now = utcnow()
        since_24h = now - timedelta(hours=24)

        committed = session.scalar(
            select(func.coalesce(func.sum(AIUsageEventRow.estimated_cost_usd), 0)).where(
                AIUsageEventRow.created_at >= since_24h
            )
        ) or Decimal("0")

        reserved = session.scalar(
            select(func.coalesce(func.sum(AIUsageReservationRow.reserved_cost_usd), 0)).where(
                and_(
                    AIUsageReservationRow.status == ReservationStatus.RESERVED.value,
                    AIUsageReservationRow.expires_at > now,
                )
            )
        ) or Decimal("0")

        total = committed + reserved + additional_cost_usd
        return total <= self.global_daily_budget_usd

    def reserve_budget(
        self,
        *,
        operation: str,
        provider: str,
        model: str,
        estimated_cost_usd: Decimal | float,
        session: Session | None = None,
        reserved_audio_seconds: float | None = None,
        family_id: str | None = None,
        user_id: str | None = None,
        recording_id: str | None = None,
        job_id: str | None = None,
        book_id: str | None = None,
        idempotency_key: str | None = None,
        ttl_seconds: float | None = None,
        reservation_id: str | None = None,
    ) -> AIUsageReservationRow:
        """Reserve AI budget under atomic serialization.

        Raises AICostBudgetExceededError if the daily limit would be breached.
        """
        cost_dec = Decimal(str(estimated_cost_usd))
        effective_ttl = ttl_seconds if ttl_seconds is not None else self.ttl_seconds

        def _execute(s: Session) -> AIUsageReservationRow:
            self._acquire_lock_if_postgres(s)

            # Idempotency check: return active or committed reservation if key matches
            if idempotency_key is not None:
                existing = s.scalar(
                    select(AIUsageReservationRow).where(
                        AIUsageReservationRow.idempotency_key == idempotency_key
                    )
                )
                if existing is not None and existing.status in (
                    ReservationStatus.RESERVED.value,
                    ReservationStatus.COMMITTED.value,
                ):
                    return existing

            now = utcnow()
            since_24h = now - timedelta(hours=24)

            committed_cost = s.scalar(
                select(func.coalesce(func.sum(AIUsageEventRow.estimated_cost_usd), 0)).where(
                    AIUsageEventRow.created_at >= since_24h
                )
            ) or Decimal("0")

            active_reserved_cost = s.scalar(
                select(func.coalesce(func.sum(AIUsageReservationRow.reserved_cost_usd), 0)).where(
                    and_(
                        AIUsageReservationRow.status == ReservationStatus.RESERVED.value,
                        AIUsageReservationRow.expires_at > now,
                    )
                )
            ) or Decimal("0")

            current_consumed = committed_cost + active_reserved_cost
            projected = current_consumed + cost_dec

            if projected > self.global_daily_budget_usd:
                logger.warning(
                    "ai_cost_budget_exceeded",
                    extra={
                        "event": "ai_cost_budget_exceeded",
                        "current_consumed_usd": str(current_consumed),
                        "requested_cost_usd": str(cost_dec),
                        "projected_cost_usd": str(projected),
                        "daily_budget_usd": str(self.global_daily_budget_usd),
                        "operation": operation,
                        "provider": provider,
                        "model": model,
                    },
                )
                raise AICostBudgetExceededError(
                    f"Global daily AI budget of ${self.global_daily_budget_usd:.2f} exceeded "
                    f"(current consumed: ${current_consumed:.4f}, requested: ${cost_dec:.4f})",
                    current_consumed_usd=current_consumed,
                    requested_cost_usd=cost_dec,
                    daily_budget_usd=self.global_daily_budget_usd,
                )

            res_id = reservation_id or f"res_{uuid.uuid4().hex}"
            expires_at = now + timedelta(seconds=effective_ttl)

            row = AIUsageReservationRow(
                reservation_id=res_id,
                status=ReservationStatus.RESERVED.value,
                operation=operation,
                provider=provider,
                model=model,
                family_id=family_id,
                user_id=user_id,
                recording_id=recording_id,
                job_id=job_id,
                book_id=book_id,
                reserved_cost_usd=cost_dec,
                reserved_audio_seconds=(
                    Decimal(str(reserved_audio_seconds))
                    if reserved_audio_seconds is not None
                    else None
                ),
                expires_at=expires_at,
                created_at=now,
                idempotency_key=idempotency_key,
            )
            s.add(row)
            s.flush()
            return row

        if session is not None:
            return _execute(session)
        with self.database.session_factory.begin() as s:
            return _execute(s)

    def commit_reservation(
        self,
        reservation_id: str,
        *,
        actual_cost_usd: Decimal | float | None = None,
        actual_audio_seconds: float | None = None,
        session: Session | None = None,
    ) -> None:
        """Mark reservation as committed and record actual usage."""

        def _execute(s: Session) -> None:
            row = s.get(AIUsageReservationRow, reservation_id)
            if row is not None:
                row.status = ReservationStatus.COMMITTED.value
                if actual_cost_usd is not None:
                    row.actual_cost_usd = Decimal(str(actual_cost_usd))
                if actual_audio_seconds is not None:
                    row.actual_audio_seconds = Decimal(str(actual_audio_seconds))

        if session is not None:
            _execute(session)
        else:
            with self.database.session_factory.begin() as s:
                _execute(s)

    def release_reservation(
        self,
        reservation_id: str,
        *,
        session: Session | None = None,
    ) -> None:
        """Release an unused reservation so budget is freed immediately."""

        def _execute(s: Session) -> None:
            row = s.get(AIUsageReservationRow, reservation_id)
            if row is not None and row.status == ReservationStatus.RESERVED.value:
                row.status = ReservationStatus.RELEASED.value

        if session is not None:
            _execute(session)
        else:
            with self.database.session_factory.begin() as s:
                _execute(s)

    def expire_stale_reservations(self, *, session: Session | None = None) -> int:
        """Mark past-TTL reservations as EXPIRED."""
        now = utcnow()

        def _execute(s: Session) -> int:
            stale_rows = s.scalars(
                select(AIUsageReservationRow).where(
                    and_(
                        AIUsageReservationRow.status == ReservationStatus.RESERVED.value,
                        AIUsageReservationRow.expires_at <= now,
                    )
                )
            ).all()
            for r in stale_rows:
                r.status = ReservationStatus.EXPIRED.value
            return len(stale_rows)

        if session is not None:
            return _execute(session)
        with self.database.session_factory.begin() as s:
            return _execute(s)
