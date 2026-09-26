"""Structured JSON logging, correlation context, and centralized log sanitization.

Production log lines are formatted as single-line JSON objects indexed by Railway
Log Explorer, with concurrency-safe request_id and job_id correlation via ContextVar.
Centralized sanitization prevents secrets, tokens, transcripts, and personal family
data from ever entering log streams.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Mapping
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

# Concurrency-safe correlation context variables
request_id_ctx: ContextVar[str | None] = ContextVar("mura_request_id", default=None)
job_id_ctx: ContextVar[str | None] = ContextVar("mura_job_id", default=None)
recording_id_ctx: ContextVar[str | None] = ContextVar("mura_recording_id", default=None)
book_id_ctx: ContextVar[str | None] = ContextVar("mura_book_id", default=None)
family_id_ctx: ContextVar[str | None] = ContextVar("mura_family_id", default=None)
attempt_ctx: ContextVar[int | None] = ContextVar("mura_attempt", default=None)
worker_id_ctx: ContextVar[str | None] = ContextVar("mura_worker_id", default=None)
chapter_number_ctx: ContextVar[int | None] = ContextVar("mura_chapter_number", default=None)

# Known safe opaque identifiers that must never be stripped
SAFE_OPAQUE_KEYS = frozenset(
    {
        "request_id",
        "job_id",
        "recording_id",
        "book_id",
        "chapter_number",
        "family_id",
        "trace_id",
        "worker_id",
        "attempt",
        "status_code",
        "duration_ms",
        "method",
        "route",
        "service",
        "environment",
        "level",
        "timestamp",
        "logger",
        "backend",
        "size_bytes",
        "mime_type",
        "content_type",
        "content-type",
        "content_length",
        "content-length",
        "user-agent",
        "host",
        "accept",
        "provider",
        "model",
        "operation",
        "error_code",
        "exception_class",
        "event",
        "retryable",
        "poll_interval_seconds",
        "lease_seconds",
        "heartbeat_seconds",
        "chapters_total",
        "chapters_approved",
        "word_count",
        "target_word_count",
        "output_language",
        "stage",
        "format",
        "export_format",
        "plan_version",
        "repair_attempts",
        "status",
    }
)

# Sensitive key substrings that must be redacted
SENSITIVE_KEY_SUBSTRINGS = (
    "authorization",
    "cookie",
    "set-cookie",
    "token",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "api_key",
    "service_role_key",
    "signed_url",
    "transcript",
    "prompt",
    "response_content",
    "story_content",
    "body",
    "audio_bytes",
    "email",
    "first_name",
    "last_name",
    "speaker_name",
    "story",
    "quote",
    "text",
    "content",
    "prose",
    "draft_text",
    "final_text",
    "evidence_quote",
    "unresolved_question",
)

REDACTED_STR = "[REDACTED]"
REDACTED_TOKEN_STR = "[REDACTED_TOKEN]"

_BEARER_PATTERN = re.compile(r"Bearer\s+[A-Za-z0-9-_=.]+", re.IGNORECASE)
_JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9-_]+\.eyJ[A-Za-z0-9-_]+\.[A-Za-z0-9-_]+\b")


class LogSanitizer:
    """Centralized sanitization for log records, extra contexts, and Sentry events."""

    @staticmethod
    def is_sensitive_key(key: str) -> bool:
        lowered = key.strip().lower()
        if lowered in SAFE_OPAQUE_KEYS:
            return False
        if lowered in {"content", "text"}:
            return True
        return any(sub in lowered for sub in SENSITIVE_KEY_SUBSTRINGS)

    @classmethod
    def sanitize_value(cls, value: Any, depth: int = 0) -> Any:
        if depth > 5:
            return "[NESTING_LIMIT]"
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            sanitized = _BEARER_PATTERN.sub("Bearer [REDACTED]", value)
            sanitized = _JWT_PATTERN.sub(REDACTED_TOKEN_STR, sanitized)
            return sanitized
        if isinstance(value, Mapping):
            return {
                k: REDACTED_STR
                if cls.is_sensitive_key(str(k))
                else cls.sanitize_value(v, depth + 1)
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [cls.sanitize_value(v, depth + 1) for v in value]
        # Never serialize raw arbitrary class instances or request bodies
        return f"<{value.__class__.__name__}>"

    @classmethod
    def sanitize_dict(cls, data: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for k, v in data.items():
            key_str = str(k)
            if cls.is_sensitive_key(key_str):
                result[key_str] = REDACTED_STR
            else:
                result[key_str] = cls.sanitize_value(v, depth=0)
        return result


class CorrelationFilter(logging.Filter):
    """Enriches log records with active context variables (request_id, job_id, etc.)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        record.job_id = job_id_ctx.get()
        record.recording_id = recording_id_ctx.get()
        record.book_id = book_id_ctx.get()
        record.chapter_number = chapter_number_ctx.get()
        record.family_id = family_id_ctx.get()
        record.attempt = attempt_ctx.get()
        record.worker_id = worker_id_ctx.get()
        return True


class StructuredJsonFormatter(logging.Formatter):
    """Single-line JSON formatter optimized for Railway Log Explorer indexing."""

    def __init__(
        self,
        service: str = "mura",
        environment: str = "production",
    ) -> None:
        super().__init__()
        self.service = service
        self.environment = environment

    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname.lower(),
            "message": LogSanitizer.sanitize_value(record.getMessage()),
            "logger": record.name,
            "service": self.service,
            "environment": self.environment,
        }

        # Correlation fields
        for field in (
            "request_id",
            "job_id",
            "recording_id",
            "book_id",
            "chapter_number",
            "family_id",
            "attempt",
            "worker_id",
        ):
            val = getattr(record, field, None)
            if val is not None:
                data[field] = val

        # Custom extra fields passed to logger.info(..., extra={...})
        standard_attrs = {
            "args",
            "asctime",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "module",
            "msecs",
            "message",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "thread",
            "threadName",
            "taskName",
            "request_id",
            "job_id",
            "recording_id",
            "book_id",
            "family_id",
            "attempt",
            "worker_id",
            "chapter_number",
        }
        extras: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key not in standard_attrs and not key.startswith("_"):
                extras[key] = value

        if extras:
            sanitized_extras = LogSanitizer.sanitize_dict(extras)
            data.update(sanitized_extras)

        # Exception formatting
        if record.exc_info and record.exc_info[0]:
            exc_type, exc_val, _ = record.exc_info
            data["exception_class"] = exc_type.__name__ if exc_type else "Exception"
            data["exception_message"] = LogSanitizer.sanitize_value(str(exc_val))

        return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


class HumanReadableFormatter(logging.Formatter):
    """Color-free, structured human-readable formatter for local development."""

    def __init__(self, service: str = "mura") -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        ctx_parts: list[str] = []
        for field in ("request_id", "job_id", "recording_id", "book_id", "chapter_number"):
            val = getattr(record, field, None)
            if val:
                ctx_parts.append(f"{field}={val}")
        ctx_str = f" [{', '.join(ctx_parts)}]" if ctx_parts else ""
        time_str = datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
        msg = LogSanitizer.sanitize_value(record.getMessage())
        return f"{time_str} {record.levelname:<7} {self.service} {record.name}{ctx_str} {msg}"


def configure_logging(
    service: str,
    *,
    environment: str = "development",
    log_level: str = "INFO",
    log_format: str = "auto",
) -> None:
    """Configures root logger with single-line JSON (production) or human-readable (local)."""
    root_logger = logging.getLogger()
    level = getattr(logging, log_level.upper(), logging.INFO)
    root_logger.setLevel(level)

    # Determine format: if auto, use json for production/staging and text otherwise
    use_json = log_format == "json" or (
        log_format == "auto" and environment.lower() in {"production", "staging"}
    )

    formatter: logging.Formatter
    if use_json:
        formatter = StructuredJsonFormatter(service=service, environment=environment)
    else:
        formatter = HumanReadableFormatter(service=service)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(formatter)
    handler.addFilter(CorrelationFilter())

    # Replace existing handlers cleanly
    root_logger.handlers.clear()
    root_logger.addHandler(handler)


class RequestContextManager:
    """Context manager for binding request_id_ctx with guaranteed reset in finally block."""

    def __init__(self, request_id: str | None) -> None:
        self.request_id = request_id
        self._token: Token[str | None] | None = None

    def __enter__(self) -> str | None:
        self._token = request_id_ctx.set(self.request_id)
        return self.request_id

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._token is not None:
            request_id_ctx.reset(self._token)


class WorkerJobContextManager:
    """Context manager for binding worker job correlation with guaranteed reset in finally block."""

    def __init__(
        self,
        *,
        job_id: str,
        recording_id: str,
        family_id: str,
        attempt: int,
        worker_id: str | None = None,
    ) -> None:
        self.job_id = job_id
        self.recording_id = recording_id
        self.family_id = family_id
        self.attempt = attempt
        self.worker_id = worker_id
        self._tokens: list[tuple[ContextVar[Any], Token[Any]]] = []

    def __enter__(self) -> WorkerJobContextManager:
        self._tokens.append((job_id_ctx, job_id_ctx.set(self.job_id)))
        self._tokens.append((recording_id_ctx, recording_id_ctx.set(self.recording_id)))
        self._tokens.append((family_id_ctx, family_id_ctx.set(self.family_id)))
        self._tokens.append((attempt_ctx, attempt_ctx.set(self.attempt)))
        if self.worker_id is not None:
            self._tokens.append((worker_id_ctx, worker_id_ctx.set(self.worker_id)))
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        for var, token in reversed(self._tokens):
            var.reset(token)


class WorkerBookJobContextManager:
    """Context manager for binding book worker job correlation with guaranteed reset in finally."""

    def __init__(
        self,
        *,
        job_id: str,
        book_id: str,
        family_id: str,
        attempt: int,
        worker_id: str | None = None,
    ) -> None:
        self.job_id = job_id
        self.book_id = book_id
        self.family_id = family_id
        self.attempt = attempt
        self.worker_id = worker_id
        self._tokens: list[tuple[ContextVar[Any], Token[Any]]] = []

    def __enter__(self) -> WorkerBookJobContextManager:
        self._tokens.append((job_id_ctx, job_id_ctx.set(self.job_id)))
        self._tokens.append((book_id_ctx, book_id_ctx.set(self.book_id)))
        self._tokens.append((family_id_ctx, family_id_ctx.set(self.family_id)))
        self._tokens.append((attempt_ctx, attempt_ctx.set(self.attempt)))
        if self.worker_id is not None:
            self._tokens.append((worker_id_ctx, worker_id_ctx.set(self.worker_id)))
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        for var, token in reversed(self._tokens):
            var.reset(token)


class BookChapterContextManager:
    """Context manager for binding current chapter number during book generation."""

    def __init__(self, chapter_number: int | None) -> None:
        self.chapter_number = chapter_number
        self._token: Token[int | None] | None = None

    def __enter__(self) -> int | None:
        self._token = chapter_number_ctx.set(self.chapter_number)
        return self.chapter_number

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._token is not None:
            chapter_number_ctx.reset(self._token)
