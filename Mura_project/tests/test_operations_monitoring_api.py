from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from apps.api.main import create_app, get_settings
from mura.config import CoreSettings
from mura.jobs import JobStatus
from mura.storage.ai_usage import AIUsageLedger
from mura.storage.database import Database, ProcessingJobRow, RecordingRow

CORE_TOKEN = "c" * 40
OPERATIONS_TOKEN = "o" * 40


def _settings(**overrides: Any) -> CoreSettings:
    payload: dict[str, Any] = {
        "DEEPSEEK_API_KEY": "sk-" + "d" * 40,
        "CORE_API_KEY": CORE_TOKEN,
        "OPERATIONS_API_KEY": OPERATIONS_TOKEN,
        "WORKER_REGISTRATION_TOKEN": "w" * 40,
        "KAGGLE_ASR_API_KEY": "a" * 40,
        "DATABASE_URL": "sqlite+pysqlite:///:memory:",
        "DATABASE_AUTO_CREATE": True,
    }
    payload.update(overrides)
    return CoreSettings.model_validate(payload)


def test_operations_monitoring_summary_authorized_response() -> None:
    settings = _settings()
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app, raise_server_exceptions=False)

    # 1. Unauthenticated -> 401
    unauth_resp = client.get("/v1/operations/monitoring/summary")
    assert unauth_resp.status_code == 401
    assert unauth_resp.json()["error"]["code"] == "unauthorized"

    # 2. Application Core Token -> 401 (requires operator key)
    core_auth_resp = client.get(
        "/v1/operations/monitoring/summary",
        headers={"Authorization": f"Bearer {CORE_TOKEN}"},
    )
    assert core_auth_resp.status_code == 401
    assert core_auth_resp.json()["error"]["code"] == "unauthorized"

    # 3. Valid Operations Token -> 200
    resp = client.get(
        "/v1/operations/monitoring/summary",
        headers={"Authorization": f"Bearer {OPERATIONS_TOKEN}"},
    )
    assert resp.status_code == 200
    data = resp.json()

    # Check top-level envelope fields
    assert "timestamp" in data
    assert "queue" in data
    assert "pipeline_24h" in data
    assert "ai_24h" in data
    assert "stuck_jobs" in data
    assert "book_queue" in data
    assert "book_stuck_jobs" in data

    # Check queue structure
    queue = data["queue"]
    assert "pending" in queue
    assert "running" in queue
    assert "failed" in queue
    assert "expired_leases" in queue
    assert "oldest_pending_seconds" in queue

    # Check pipeline_24h structure
    pipeline = data["pipeline_24h"]
    assert "completed" in pipeline
    assert "failed" in pipeline

    # Check ai_24h structure
    ai = data["ai_24h"]
    assert "requests_count" in ai
    assert "input_tokens" in ai
    assert "output_tokens" in ai
    assert "cached_input_tokens" in ai
    assert "audio_seconds" in ai
    assert "estimated_cost_usd" in ai

    # Check stuck_jobs list
    assert isinstance(data["stuck_jobs"], list)


def test_operations_monitoring_summary_privacy_no_content_leakage() -> None:
    settings = _settings()
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app, raise_server_exceptions=False)

    # Access runtime database to seed a recording and usage event
    runtime = app.state._test_runtime if hasattr(app.state, "_test_runtime") else None
    from apps.api.main import _runtime
    assert _runtime is not None

    # Seed job and AI usage
    with _runtime.database.session_factory.begin() as session:
        session.add(
            RecordingRow(
                recording_id="rec_private_99",
                family_id="fam_private_99",
                speaker_id="spk_private_99",
                speaker_name="Secret Grandmother Name",
                original_filename="secret_audio.wav",
                audio_path="/data/secret_audio.wav",
            )
        )
        session.flush()
        session.add(
            ProcessingJobRow(
                job_id="job_private_99",
                recording_id="rec_private_99",
                status=JobStatus.COMPLETED.value,
                stage="completed",
            )
        )

    ledger = AIUsageLedger(_runtime.database)
    ledger.record_usage(
        provider="deepseek",
        model="deepseek-v4-flash",
        operation="cleaner",
        latency_ms=250,
        success=True,
        input_tokens=1500,
        output_tokens=200,
        cached_input_tokens=800,
        job_id="job_private_99",
        recording_id="rec_private_99",
        family_id="fam_private_99",
    )

    resp = client.get(
        "/v1/operations/monitoring/summary",
        headers={"Authorization": f"Bearer {OPERATIONS_TOKEN}"},
    )
    assert resp.status_code == 200
    body_text = resp.text

    # Privacy verification: strictly NO family content, names, audio paths or secrets
    assert "Secret Grandmother Name" not in body_text
    assert "secret_audio.wav" not in body_text
    assert "/data/secret_audio.wav" not in body_text
    assert OPERATIONS_TOKEN not in body_text
    assert CORE_TOKEN not in body_text

