from __future__ import annotations

import json
import logging

import pytest

from mura.logging import (
    CorrelationFilter,
    LogSanitizer,
    RequestContextManager,
    StructuredJsonFormatter,
    WorkerJobContextManager,
    attempt_ctx,
    family_id_ctx,
    job_id_ctx,
    recording_id_ctx,
    request_id_ctx,
    worker_id_ctx,
)


def test_log_sanitizer_redacts_sensitive_keys():
    data = {
        "authorization": "Bearer secret-token-12345",
        "api_key": "sk-1234567890",
        "service_role_key": "super-secret-key",
        "password": "mypassword",
        "cookie": "session=abcde",
        "transcript": "Once upon a time in Almaty...",
        "prompt": "Extract the family members from this story...",
        "response_content": "Aigul is the daughter of Bolat",
        "story": "Secret family legend",
        "email": "user@example.com",
        "first_name": "Kairat",
        "last_name": "Nurtas",
    }
    sanitized = LogSanitizer.sanitize_dict(data)
    for key in data:
        assert sanitized[key] == "[REDACTED]"


def test_log_sanitizer_preserves_safe_opaque_keys():
    data = {
        "request_id": "req_12345678abcdef",
        "job_id": "job_abcdef123456",
        "recording_id": "rec_fedcba654321",
        "family_id": "fam_almaty_01",
        "trace_id": "trace_12345",
        "worker_id": "worker_9999",
        "attempt": 1,
        "status_code": 200,
        "duration_ms": 145.2,
        "method": "POST",
        "route": "/v1/families/{family_id}/recordings",
        "service": "mura-api",
        "environment": "production",
        "backend": "supabase",
        "size_bytes": 1048576,
        "mime_type": "audio/webm",
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "event": "job_started",
    }
    sanitized = LogSanitizer.sanitize_dict(data)
    for key, original_val in data.items():
        assert sanitized[key] == original_val


def test_log_sanitizer_redacts_bearer_and_jwt_in_strings():
    msg = (
        "User authenticated with Bearer "
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.doNotLeakThis"
    )
    sanitized = LogSanitizer.sanitize_value(msg)
    assert "Bearer [REDACTED]" in sanitized or "[REDACTED_TOKEN]" in sanitized
    assert "doNotLeakThis" not in sanitized


def test_log_sanitizer_handles_nested_structures():
    nested = {
        "request_id": "req_safe",
        "meta": {
            "token": "leaked_token",
            "count": 42,
            "nested_list": ["clean", {"secret": "hide_me"}],
        },
    }
    sanitized = LogSanitizer.sanitize_dict(nested)
    assert sanitized["request_id"] == "req_safe"
    assert sanitized["meta"]["token"] == "[REDACTED]"
    assert sanitized["meta"]["count"] == 42
    assert sanitized["meta"]["nested_list"][0] == "clean"
    assert sanitized["meta"]["nested_list"][1]["secret"] == "[REDACTED]"


def test_structured_json_formatter_outputs_single_line_json():
    formatter = StructuredJsonFormatter(service="mura-api", environment="production")
    logger = logging.getLogger("test.json.formatter")
    record = logger.makeRecord(
        name="test.json.formatter",
        level=logging.INFO,
        fn="test_file.py",
        lno=10,
        msg="test_event_message",
        args=(),
        exc_info=None,
    )
    record.request_id = "req_123"
    record.job_id = "job_456"

    line = formatter.format(record)
    assert "\n" not in line
    data = json.loads(line)
    assert data["level"] == "info"
    assert data["message"] == "test_event_message"
    assert data["service"] == "mura-api"
    assert data["environment"] == "production"
    assert data["logger"] == "test.json.formatter"
    assert data["request_id"] == "req_123"
    assert data["job_id"] == "job_456"
    assert "timestamp" in data


def test_structured_json_formatter_sanitizes_extras():
    formatter = StructuredJsonFormatter(service="mura-worker", environment="staging")
    logger = logging.getLogger("test.json.extras")
    record = logger.makeRecord(
        name="test.json.extras",
        level=logging.ERROR,
        fn="test_file.py",
        lno=20,
        msg="job_failed",
        args=(),
        exc_info=None,
        extra={
            "token": "super_secret_token",
            "job_id": "job_safe_999",
            "prompt": "Tell me a story",
        },
    )

    line = formatter.format(record)
    data = json.loads(line)
    assert data["token"] == "[REDACTED]"
    assert data["prompt"] == "[REDACTED]"
    assert data["job_id"] == "job_safe_999"


def test_request_context_manager_lifecycle():
    assert request_id_ctx.get() is None

    with RequestContextManager("req_test_abc"):
        assert request_id_ctx.get() == "req_test_abc"

    assert request_id_ctx.get() is None


def test_request_context_manager_resets_on_exception():
    assert request_id_ctx.get() is None

    with pytest.raises(ValueError):
        with RequestContextManager("req_exception_test"):
            assert request_id_ctx.get() == "req_exception_test"
            raise ValueError("Test error inside context")

    assert request_id_ctx.get() is None


def test_worker_job_context_manager_lifecycle():
    assert job_id_ctx.get() is None
    assert recording_id_ctx.get() is None
    assert family_id_ctx.get() is None
    assert attempt_ctx.get() is None
    assert worker_id_ctx.get() is None

    with WorkerJobContextManager(
        job_id="job_001",
        recording_id="rec_001",
        family_id="fam_001",
        attempt=2,
        worker_id="worker_001",
    ):
        assert job_id_ctx.get() == "job_001"
        assert recording_id_ctx.get() == "rec_001"
        assert family_id_ctx.get() == "fam_001"
        assert attempt_ctx.get() == 2
        assert worker_id_ctx.get() == "worker_001"

    assert job_id_ctx.get() is None
    assert recording_id_ctx.get() is None
    assert family_id_ctx.get() is None
    assert attempt_ctx.get() is None
    assert worker_id_ctx.get() is None


def test_worker_job_context_manager_resets_on_exception():
    with pytest.raises(RuntimeError):
        with WorkerJobContextManager(
            job_id="job_err",
            recording_id="rec_err",
            family_id="fam_err",
            attempt=1,
            worker_id="worker_err",
        ):
            assert job_id_ctx.get() == "job_err"
            raise RuntimeError("Failure inside worker attempt")

    assert job_id_ctx.get() is None
    assert recording_id_ctx.get() is None
    assert family_id_ctx.get() is None
    assert attempt_ctx.get() is None
    assert worker_id_ctx.get() is None


def test_correlation_filter_enriches_record():
    flt = CorrelationFilter()
    logger = logging.getLogger("test.filter")
    record = logger.makeRecord(
        name="test.filter",
        level=logging.INFO,
        fn="test.py",
        lno=1,
        msg="test",
        args=(),
        exc_info=None,
    )

    with RequestContextManager("req_filter_123"):
        with WorkerJobContextManager(
            job_id="job_filter_456",
            recording_id="rec_filter_789",
            family_id="fam_filter_111",
            attempt=1,
            worker_id="worker_filter_222",
        ):
            flt.filter(record)
            assert record.request_id == "req_filter_123"
            assert record.job_id == "job_filter_456"
            assert record.recording_id == "rec_filter_789"
            assert record.family_id == "fam_filter_111"
            assert record.attempt == 1
            assert record.worker_id == "worker_filter_222"
