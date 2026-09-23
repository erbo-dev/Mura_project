"""Typed physical-storage deletion failures.

The cleanup worker needs one provider-neutral contract: 404/already absent is a
normal idempotent result, while transient transport/provider failures retry and
permanent authentication/configuration failures terminate.
"""

from __future__ import annotations

from typing import Any


class StorageDeleteError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        retryable: bool,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
        message: str = "storage deletion failed",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


def _retry_after(headers: Any) -> float | None:
    value = headers.get("Retry-After") if hasattr(headers, "get") else None
    if value is None:
        return None
    try:
        seconds = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


def storage_delete_http_error(status_code: int, headers: Any = None) -> StorageDeleteError:
    if status_code == 429:
        return StorageDeleteError(
            code="storage_rate_limited",
            retryable=True,
            status_code=status_code,
            retry_after_seconds=_retry_after(headers or {}),
            message="storage deletion was rate limited",
        )
    if status_code in {408, 504}:
        return StorageDeleteError(
            code="storage_timeout",
            retryable=True,
            status_code=status_code,
            message="storage deletion timed out",
        )
    if 500 <= status_code < 600:
        return StorageDeleteError(
            code="storage_unavailable",
            retryable=True,
            status_code=status_code,
            message="storage deletion is temporarily unavailable",
        )
    if status_code in {401, 403}:
        return StorageDeleteError(
            code="storage_auth_failed",
            retryable=False,
            status_code=status_code,
            message="storage deletion authentication failed",
        )
    return StorageDeleteError(
        code="storage_delete_rejected",
        retryable=False,
        status_code=status_code,
        message="storage deletion was permanently rejected",
    )
