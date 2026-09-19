"""Central failure classification, retry semantics, and backoff policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import logging
import random
import re
from typing import Any

from mura.leases import LeaseOwnershipLost

logger = logging.getLogger(__name__)


class FailureDisposition(StrEnum):
    """Whether a failure should be retried or marked permanently failed."""

    RETRY = "retry"
    TERMINAL = "terminal"


class FailureCategory(StrEnum):
    """High-level taxonomy of failure causes across external and internal systems."""

    PROVIDER_RATE_LIMIT = "provider_rate_limit"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_SERVER_ERROR = "provider_server_error"
    PROVIDER_AUTH_ERROR = "provider_auth_error"
    PROVIDER_INVALID_REQUEST = "provider_invalid_request"
    STORAGE_TIMEOUT = "storage_timeout"
    STORAGE_UNAVAILABLE = "storage_unavailable"
    GROUNDING_BLOCKED = "grounding_blocked"
    LEASE_LOST = "lease_lost"
    CANCELED = "canceled"
    UNKNOWN_ERROR = "unknown_error"


@dataclass(frozen=True)
class ClassifiedFailure:
    category: FailureCategory
    disposition: FailureDisposition
    error_code: str
    error_detail: str
    retry_after_seconds: float | None = None

    @property
    def is_retryable(self) -> bool:
        return self.disposition == FailureDisposition.RETRY


def _parse_retry_after(header_val: str | None) -> float | None:
    if not header_val:
        return None
    try:
        # Check integer/float seconds
        val = float(header_val.strip())
        if val >= 0:
            return val
    except (ValueError, TypeError):
        pass
    return None


def classify_failure(exc: BaseException) -> ClassifiedFailure:
    """Classify any exception encountered during job execution into retry or terminal."""
    error_str = str(exc)
    exc_type_name = type(exc).__name__

    # 1. Lease ownership lost -> Must NOT be deferred or retried by current worker
    if isinstance(exc, LeaseOwnershipLost) or "leaseownershiplost" in exc_type_name.lower():
        return ClassifiedFailure(
            category=FailureCategory.LEASE_LOST,
            disposition=FailureDisposition.TERMINAL,
            error_code="lease_ownership_lost",
            error_detail=error_str or "Worker lease ownership was lost or expired",
        )

    # 2. Export engine missing
    if "exportengineunavailable" in exc_type_name.lower() or "export engine unavailable" in error_str.lower():
        return ClassifiedFailure(
            category=FailureCategory.UNKNOWN_ERROR,
            disposition=FailureDisposition.TERMINAL,
            error_code="book_export_incomplete",
            error_detail=error_str,
        )

    # 3. Grounding validation blockers
    if (
        "failed validation gates" in error_str
        or "blueprint validation failed" in error_str.lower()
        or "gate_failed" in error_str.lower()
    ):
        return ClassifiedFailure(
            category=FailureCategory.GROUNDING_BLOCKED,
            disposition=FailureDisposition.TERMINAL,
            error_code="gate_failed" if "gate" in error_str.lower() else "blueprint_validation_failed",
            error_detail=error_str,
        )

    # 4. HTTP response status inspections (requests / httpx)
    response: Any = getattr(exc, "response", None)
    status_code: int | None = getattr(response, "status_code", None)
    headers: Any = getattr(response, "headers", None) or {}

    # Check if status_code is embedded in exception attributes or message
    if status_code is None:
        match = re.search(r"\b(429|500|502|503|504|408|401|403|400|422)\b", error_str)
        if match:
            try:
                status_code = int(match.group(1))
            except ValueError:
                status_code = None

    if status_code is not None:
        if status_code == 429:
            retry_after = _parse_retry_after(headers.get("Retry-After") if hasattr(headers, "get") else None)
            return ClassifiedFailure(
                category=FailureCategory.PROVIDER_RATE_LIMIT,
                disposition=FailureDisposition.RETRY,
                error_code="provider_rate_limit",
                error_detail=error_str,
                retry_after_seconds=retry_after,
            )
        if status_code in (500, 502, 503):
            return ClassifiedFailure(
                category=FailureCategory.PROVIDER_SERVER_ERROR,
                disposition=FailureDisposition.RETRY,
                error_code="provider_server_error",
                error_detail=error_str,
            )
        if status_code in (408, 504):
            return ClassifiedFailure(
                category=FailureCategory.PROVIDER_TIMEOUT,
                disposition=FailureDisposition.RETRY,
                error_code="provider_timeout",
                error_detail=error_str,
            )
        if status_code in (401, 403):
            return ClassifiedFailure(
                category=FailureCategory.PROVIDER_AUTH_ERROR,
                disposition=FailureDisposition.TERMINAL,
                error_code="provider_auth_error",
                error_detail=error_str,
            )
        if status_code in (400, 422):
            return ClassifiedFailure(
                category=FailureCategory.PROVIDER_INVALID_REQUEST,
                disposition=FailureDisposition.TERMINAL,
                error_code="provider_invalid_request",
                error_detail=error_str,
            )

    # 5. Network / Timeout exceptions
    if isinstance(exc, (TimeoutError, ConnectionError)) or "timeout" in exc_type_name.lower():
        return ClassifiedFailure(
            category=FailureCategory.PROVIDER_TIMEOUT,
            disposition=FailureDisposition.RETRY,
            error_code="provider_timeout",
            error_detail=error_str or "Network or provider request timed out",
        )

    if "connectionerror" in exc_type_name.lower() or "connection error" in error_str.lower():
        return ClassifiedFailure(
            category=FailureCategory.PROVIDER_SERVER_ERROR,
            disposition=FailureDisposition.RETRY,
            error_code="provider_connection_error",
            error_detail=error_str,
        )

    # 6. Storage exceptions
    if "artifactstorage" in exc_type_name.lower() or "storage" in exc_type_name.lower():
        if "timeout" in error_str.lower() or "504" in error_str or "503" in error_str:
            return ClassifiedFailure(
                category=FailureCategory.STORAGE_TIMEOUT,
                disposition=FailureDisposition.RETRY,
                error_code="storage_timeout",
                error_detail=error_str,
            )
        return ClassifiedFailure(
            category=FailureCategory.STORAGE_UNAVAILABLE,
            disposition=FailureDisposition.RETRY,
            error_code="storage_unavailable",
            error_detail=error_str,
        )

    # 7. Fallback for unclassified errors
    return ClassifiedFailure(
        category=FailureCategory.UNKNOWN_ERROR,
        disposition=FailureDisposition.TERMINAL,
        error_code="execution_failed",
        error_detail=error_str or exc_type_name,
    )


def calculate_retry_delay(
    attempt: int,
    *,
    base_seconds: float = 5.0,
    max_seconds: float = 300.0,
    retry_after: float | None = None,
    jitter: bool = True,
) -> float:
    """Compute exponential backoff with jitter, respecting Retry-After if provided."""
    if retry_after is not None and retry_after > 0:
        return min(float(retry_after), max_seconds)

    # Exponential: attempt 1 -> base*1, attempt 2 -> base*2, attempt 3 -> base*4...
    backoff = min(base_seconds * (2 ** max(0, attempt - 1)), max_seconds)
    if jitter:
        # Uniform jitter in range [0.85, 1.15]
        factor = random.uniform(0.85, 1.15)
        backoff = min(backoff * factor, max_seconds)

    return max(0.5, backoff)

