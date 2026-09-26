"""Tests for monitoring smoke tool and privacy leakage safeguards."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from mura.ops.monitoring_smoke import (
    MonitoringSmokeRunner,
    assert_zero_privacy_leakage,
    evaluate_monitoring_thresholds,
)


def test_privacy_leakage_safeguard_clean() -> None:
    clean_summary = {
        "queue": {
            "pending": 5,
            "running": 2,
            "failed": 0,
            "oldest_pending_seconds": 12.4,
            "expired_leases": 0,
        },
        "cleanup": {"queued": 1, "attempts_exhausted": 0},
        "ai_usage": {"total_tokens": 15000, "audio_seconds": 34.5},
        "stuck_jobs": [],
    }
    violations = assert_zero_privacy_leakage(clean_summary)
    assert len(violations) == 0


def test_privacy_leakage_safeguard_flags_prose_and_keys() -> None:
    leaky_summary = {
        "queue": {"pending": 1},
        "leak_1": {"transcript": "Some private speech from recording"},
        "leak_2": {"story_text": "Story about grandma"},
        "leak_3": (
            "Very long family memory narrative that contains sensitive personal stories told by "
            "relatives during winter in Almaty about their ancestors and family heirlooms."
        ),
    }
    violations = assert_zero_privacy_leakage(leaky_summary)
    assert len(violations) >= 3
    assert any("transcript" in v for v in violations)
    assert any("story_text" in v for v in violations)
    assert any("prose" in v for v in violations)


def test_evaluate_monitoring_thresholds() -> None:
    bad_summary = {
        "queue": {
            "pending": 100,
            "oldest_pending_seconds": 1500.0,  # > 1200s
            "expired_leases": 2,  # > 0
        },
        "stuck_jobs": [{"job_id": "job_1", "reason": "pending_too_long"}],
        "cleanup": {"attempts_exhausted": 3},  # > 0
        "book_queue": {"expired_leases": 1},
    }
    alerts = evaluate_monitoring_thresholds(bad_summary)
    assert len(alerts) == 5

    critical = [a for a in alerts if a.severity == "CRITICAL"]
    assert len(critical) == 1
    assert critical[0].metric == "queue.oldest_pending_seconds"

    warnings = [a for a in alerts if a.severity == "WARNING"]
    assert len(warnings) == 4


def test_monitoring_smoke_runner_mocked() -> None:
    with patch("requests.get") as mock_get:

        def _fake_get(url, **kwargs):
            resp = MagicMock()
            if url.endswith("/health"):
                resp.status_code = 200
                resp.json.return_value = {"status": "ok", "service": "mura-core"}
            elif url.endswith("/ready"):
                resp.status_code = 200
                resp.json.return_value = {"status": "ready", "database": "ready"}
            elif "/v1/operations/monitoring/summary" in url:
                auth = kwargs.get("headers", {}).get("Authorization")
                if auth == "Bearer valid_ops_key":
                    resp.status_code = 200
                    resp.json.return_value = {
                        "queue": {"pending": 0, "expired_leases": 0},
                        "stuck_jobs": [],
                    }
                else:
                    resp.status_code = 401
                    resp.json.return_value = {"detail": "unauthorized"}
            return resp

        mock_get.side_effect = _fake_get

        runner = MonitoringSmokeRunner(
            base_url="http://test-mura:8000",
            operations_api_key="valid_ops_key",
        )
        report = runner.run()

        assert report.overall_status == "PASS"
        assert report.health_status == "PASS"
        assert report.ready_status == "PASS"
        assert report.operations_auth_status == "PASS"
        assert report.privacy_status == "PASS"
