"""Sentry error tracking and lightweight tracing integration.

Configured with strict privacy defaults:
- send_default_pii = False
- Request bodies, prompts, transcripts, audio bytes, and cookies are explicitly stripped
- No Session Replay
- /health and /ready routes are excluded from trace sampling
- Fully optional: disabled cleanly when SENTRY_DSN is absent
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any, cast

from mura.logging import (
    LogSanitizer,
    job_id_ctx,
    recording_id_ctx,
    request_id_ctx,
    worker_id_ctx,
)

logger = logging.getLogger("mura.sentry")

_SENTRY_INITIALIZED = False


def is_sentry_active() -> bool:
    return _SENTRY_INITIALIZED


def _before_send_sanitizer(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Sanitize all outgoing Sentry events, breadcrumbs, and request metadata."""
    try:
        # Strip request body/data entirely to prevent leaking family stories or form data
        if "request" in event and isinstance(event["request"], dict):
            req = event["request"]
            req.pop("data", None)
            req.pop("body", None)
            if "headers" in req and isinstance(req["headers"], dict):
                req["headers"] = {
                    k: "[REDACTED]" if LogSanitizer.is_sensitive_key(k) else v
                    for k, v in req["headers"].items()
                }
            if "cookies" in req:
                req["cookies"] = "[REDACTED]"

        # Sanitize extra context
        if "extra" in event and isinstance(event["extra"], dict):
            event["extra"] = LogSanitizer.sanitize_dict(event["extra"])

        # Sanitize tags
        if "tags" in event and isinstance(event["tags"], dict):
            event["tags"] = {
                k: "[REDACTED]" if LogSanitizer.is_sensitive_key(k) else str(v)[:64]
                for k, v in event["tags"].items()
            }

        # Sanitize breadcrumbs
        if "breadcrumbs" in event and isinstance(event["breadcrumbs"], dict):
            crumbs = event["breadcrumbs"].get("values", [])
            for crumb in crumbs:
                if isinstance(crumb, dict) and "data" in crumb and isinstance(crumb["data"], dict):
                    crumb["data"] = LogSanitizer.sanitize_dict(crumb["data"])

        # Inject active correlation IDs as safe tags if present
        tags = event.setdefault("tags", {})
        req_id = request_id_ctx.get()
        if req_id and "request_id" not in tags:
            tags["request_id"] = req_id
        job_id = job_id_ctx.get()
        if job_id and "job_id" not in tags:
            tags["job_id"] = job_id
        rec_id = recording_id_ctx.get()
        if rec_id and "recording_id" not in tags:
            tags["recording_id"] = rec_id
        worker_id = worker_id_ctx.get()
        if worker_id and "worker_id" not in tags:
            tags["worker_id"] = worker_id

    except Exception:
        # Never crash inside before_send
        pass

    return event


def _traces_sampler(sampling_context: dict[str, Any], default_rate: float) -> float:
    """Drop traces for /health and /ready to prevent tracing noise."""
    asgi_scope = sampling_context.get("asgi_scope")
    if asgi_scope and isinstance(asgi_scope, dict):
        path = asgi_scope.get("path", "")
        if path in {"/health", "/ready"}:
            return 0.0

    wsgi_environ = sampling_context.get("wsgi_environ")
    if wsgi_environ and isinstance(wsgi_environ, dict):
        path = wsgi_environ.get("PATH_INFO", "")
        if path in {"/health", "/ready"}:
            return 0.0

    return default_rate


def init_sentry(
    service: str,
    *,
    dsn: str | None,
    environment: str = "production",
    traces_sample_rate: float = 0.05,
    release: str | None = None,
) -> bool:
    """Initialize Sentry with strict privacy controls. Returns True if initialized."""
    global _SENTRY_INITIALIZED

    if not dsn:
        _SENTRY_INITIALIZED = False
        logger.debug("Sentry DSN not provided; Sentry disabled for %s", service)
        return False

    try:
        import sentry_sdk
    except ImportError:
        logger.warning("sentry-sdk not installed; error tracking disabled")
        _SENTRY_INITIALIZED = False
        return False

    resolved_release = (
        release
        or os.environ.get("RAILWAY_GIT_COMMIT_SHA")
        or os.environ.get("GIT_COMMIT_SHA")
        or os.environ.get("VERCEL_GIT_COMMIT_SHA")
        or "1.0.0rc1"
    )

    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            release=resolved_release,
            send_default_pii=False,
            before_send=cast(Any, _before_send_sanitizer),
            traces_sampler=lambda ctx: _traces_sampler(ctx, traces_sample_rate),
        )
        sentry_sdk.set_tag("service", service)
        _SENTRY_INITIALIZED = True
        logger.info("Sentry initialized for service=%s environment=%s", service, environment)
        return True
    except Exception as exc:
        logger.warning("Failed to initialize Sentry: %s", exc)
        _SENTRY_INITIALIZED = False
        return False


def capture_exception(
    exc: BaseException,
    *,
    extra: Mapping[str, Any] | None = None,
    tags: Mapping[str, str] | None = None,
) -> None:
    """Safely capture an unhandled exception to Sentry if active."""
    if not _SENTRY_INITIALIZED:
        return
    try:
        import sentry_sdk

        with sentry_sdk.push_scope() as scope:
            if tags:
                for k, v in tags.items():
                    if not LogSanitizer.is_sensitive_key(k):
                        scope.set_tag(k, str(v)[:64])
            if extra:
                sanitized_extra = LogSanitizer.sanitize_dict(extra)
                for k, v in sanitized_extra.items():
                    scope.set_extra(k, v)
            sentry_sdk.capture_exception(exc)
    except Exception:
        pass


def flush_sentry(timeout_seconds: float = 2.0) -> None:
    """Flush pending Sentry events on process shutdown."""
    if not _SENTRY_INITIALIZED:
        return
    try:
        import sentry_sdk

        sentry_sdk.flush(timeout=timeout_seconds)
    except Exception:
        pass

