from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mura.logging import RequestContextManager, WorkerJobContextManager
from mura.sentry import (
    _before_send_sanitizer,
    _traces_sampler,
    capture_exception,
    init_sentry,
    is_sentry_active,
)


def test_init_sentry_disabled_without_dsn():
    res = init_sentry("mura-api", dsn=None)
    assert res is False
    assert is_sentry_active() is False

    res_empty = init_sentry("mura-api", dsn="")
    assert res_empty is False
    assert is_sentry_active() is False


def test_before_send_sanitizer_strips_request_body_and_sensitive_data():
    event = {
        "request": {
            "url": "https://api.mura.kz/v1/families/fam_1/recordings",
            "method": "POST",
            "body": "Private story about grandmother...",
            "data": {"story": "Secret family tree notes"},
            "headers": {
                "Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.test",
                "Content-Type": "application/json",
                "Cookie": "session=sensitive_cookie_123",
            },
            "cookies": {"session": "token_val"},
        },
        "extra": {
            "api_key": "sk-secret-key",
            "prompt": "Extract person names...",
            "safe_count": 5,
        },
        "tags": {
            "token": "leaked_tag",
            "service": "mura-api",
        },
        "breadcrumbs": {
            "values": [
                {
                    "message": "Loaded profile",
                    "data": {"password": "pwd", "family_id": "fam_1"},
                }
            ]
        },
    }

    sanitized = _before_send_sanitizer(event, {})
    assert sanitized is not None

    req = sanitized["request"]
    assert "body" not in req
    assert "data" not in req
    assert req["headers"]["Authorization"] == "[REDACTED]"
    assert req["headers"]["Content-Type"] == "application/json"
    assert req["headers"]["Cookie"] == "[REDACTED]"
    assert req["cookies"] == "[REDACTED]"

    extra = sanitized["extra"]
    assert extra["api_key"] == "[REDACTED]"
    assert extra["prompt"] == "[REDACTED]"
    assert extra["safe_count"] == 5

    tags = sanitized["tags"]
    assert tags["token"] == "[REDACTED]"
    assert tags["service"] == "mura-api"

    crumb_data = sanitized["breadcrumbs"]["values"][0]["data"]
    assert crumb_data["password"] == "[REDACTED]"
    assert crumb_data["family_id"] == "fam_1"


def test_before_send_sanitizer_injects_correlation_tags():
    event = {"tags": {"environment": "test"}}

    with RequestContextManager("req_sentry_corr_123"):
        with WorkerJobContextManager(
            job_id="job_sentry_456",
            recording_id="rec_sentry_789",
            family_id="fam_sentry_001",
            attempt=1,
            worker_id="worker_sentry_999",
        ):
            sanitized = _before_send_sanitizer(event, {})
            assert sanitized is not None
            tags = sanitized["tags"]
            assert tags["request_id"] == "req_sentry_corr_123"
            assert tags["job_id"] == "job_sentry_456"
            assert tags["recording_id"] == "rec_sentry_789"
            assert tags["worker_id"] == "worker_sentry_999"


def test_traces_sampler_suppresses_health_and_ready():
    # Health checks return 0.0
    assert _traces_sampler({"asgi_scope": {"path": "/health"}}, default_rate=0.05) == 0.0
    assert _traces_sampler({"asgi_scope": {"path": "/ready"}}, default_rate=0.05) == 0.0
    assert _traces_sampler({"wsgi_environ": {"PATH_INFO": "/health"}}, default_rate=0.05) == 0.0
    assert _traces_sampler({"wsgi_environ": {"PATH_INFO": "/ready"}}, default_rate=0.05) == 0.0

    # Normal application paths return default configured rate
    assert _traces_sampler({"asgi_scope": {"path": "/v1/families"}}, default_rate=0.05) == 0.05
    assert _traces_sampler({"asgi_scope": {"path": "/v1/recordings"}}, default_rate=0.1) == 0.1


def test_capture_exception_safe_when_sentry_inactive():
    # When Sentry is not active, capture_exception should safely no-op without errors
    try:
        raise ValueError("Simulated unhandled exception")
    except ValueError as exc:
        capture_exception(exc, tags={"route": "/test"}, extra={"safe_key": "val"})

