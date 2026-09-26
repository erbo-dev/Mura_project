"""Staging checks for health, readiness, security headers, CORS and auth."""

from __future__ import annotations

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from apps.api.main import create_app, get_settings
from mura.config import CoreSettings

OPERATIONS_TOKEN = "o" * 40


@pytest.fixture
def staging_settings() -> CoreSettings:
    return CoreSettings.model_validate(
        {
            "APP_ENV": "staging",
            "DEEPSEEK_API_KEY": "sk-" + "d" * 40,
            "CORE_API_KEY": "c" * 40,
            "OPERATIONS_API_KEY": OPERATIONS_TOKEN,
            "WORKER_REGISTRATION_TOKEN": "w" * 40,
            "KAGGLE_ASR_API_KEY": "k" * 40,
            "DATABASE_URL": "sqlite+pysqlite:///:memory:",
            "DATABASE_AUTO_CREATE": True,
            "CORS_ALLOWED_ORIGINS": ["https://mura-staging.vercel.app"],
            "ALLOWED_HOSTS": ["*"],
        }
    )


@pytest.fixture
def client(staging_settings: CoreSettings) -> TestClient:
    app = create_app(staging_settings)
    app.dependency_overrides[get_settings] = lambda: staging_settings
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.staging
def test_staging_health_endpoint(client: TestClient) -> None:
    """Verifies /health probe returns 200 without requiring auth."""
    resp = client.get("/health")
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "mura-core"


@pytest.mark.staging
def test_staging_ready_endpoint(client: TestClient) -> None:
    """Verifies /ready probe returns 200 and confirms database readiness."""
    resp = client.get("/ready")
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["status"] == "ready"
    assert data["database"] == "ready"


@pytest.mark.staging
def test_staging_security_headers(client: TestClient) -> None:
    """Verifies HTTP security headers adhere to hardened staging policy."""
    resp = client.get("/health")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"

    # CSP and permissions policy
    perm_policy = resp.headers.get("Permissions-Policy", "")
    assert "camera=()" in perm_policy
    assert "microphone=()" not in perm_policy, "Microphone must be permitted for recording"


@pytest.mark.staging
def test_staging_operations_auth_enforcement(client: TestClient) -> None:
    """Verifies /v1/operations endpoints strictly reject unauthenticated requests."""
    # Without operations key
    resp = client.get("/v1/operations/monitoring/summary")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    # With invalid key
    resp = client.get(
        "/v1/operations/monitoring/summary",
        headers={"Authorization": "Bearer invalid_wrong_token"},
    )
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    # With valid operations token
    resp = client.get(
        "/v1/operations/monitoring/summary",
        headers={"Authorization": f"Bearer {OPERATIONS_TOKEN}"},
    )
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert "queue" in data
    assert "book_queue" in data


@pytest.mark.staging
def test_staging_cors_protection(client: TestClient) -> None:
    """Verifies CORS policy protects endpoints against untrusted browser origins."""
    # Origin from untrusted domain
    resp = client.options(
        "/health",
        headers={
            "Origin": "https://malicious-site.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    allowed_origin = resp.headers.get("Access-Control-Allow-Origin")
    assert allowed_origin != "https://malicious-site.com"

    # Origin from staging Vercel domain
    resp = client.options(
        "/health",
        headers={
            "Origin": "https://mura-staging.vercel.app",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.headers.get("Access-Control-Allow-Origin") == "https://mura-staging.vercel.app"
