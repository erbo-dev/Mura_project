"""Tests for HTTP security headers, CORS methods, and defensive defaults."""

from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.main import create_app, get_settings
from apps.api.security_headers import SECURITY_HEADERS
from mura.config import CoreSettings


def _settings() -> CoreSettings:
    return CoreSettings.model_validate(
        {
            "DEEPSEEK_API_KEY": "sk-" + "d" * 40,
            "CORE_API_KEY": "c" * 40,
            "OPERATIONS_API_KEY": "o" * 40,
            "WORKER_REGISTRATION_TOKEN": "w" * 40,
            "KAGGLE_ASR_API_KEY": "k" * 40,
            "DATABASE_URL": "sqlite+pysqlite:///:memory:",
            "DATABASE_AUTO_CREATE": True,
            "CORS_ALLOWED_ORIGINS": ["https://mura.example.com"],
        }
    )


def test_api_security_headers_present() -> None:
    settings = _settings()
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    response = client.get("/health")
    assert response.status_code == 200

    # Verify standard defensive security headers
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"

    # Verify permissions policy preserves microphone for audio recording
    perm_policy = response.headers.get("Permissions-Policy", "")
    assert "camera=()" in perm_policy
    assert "geolocation=()" in perm_policy
    assert "payment=()" in perm_policy
    assert "usb=()" in perm_policy
    # Microphone MUST NOT be disabled with ()
    assert "microphone=()" not in perm_policy


def test_cors_allows_delete_and_options() -> None:
    settings = _settings()
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    response = client.options(
        "/v1/families/fam_test/recordings/rec_1",
        headers={
            "Origin": "https://mura.example.com",
            "Access-Control-Request-Method": "DELETE",
        },
    )
    # Status code 200 for CORS preflight
    assert response.status_code == 200
    allow_methods = response.headers.get("Access-Control-Allow-Methods", "")
    assert "DELETE" in allow_methods

