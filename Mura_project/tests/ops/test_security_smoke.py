"""Tests for staging security smoke runner."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from mura.ops.security_smoke import SecuritySmokeReport, SecuritySmokeRunner


def test_security_smoke_runner_auth_and_headers() -> None:
    with patch("requests.get") as mock_get:

        def _fake_get(url, **kwargs):
            resp = MagicMock()
            if url.endswith("/health"):
                resp.status_code = 200
                resp.headers = {
                    "X-Content-Type-Options": "nosniff",
                    "X-Frame-Options": "DENY",
                    "Permissions-Policy": "camera=(), microphone=(self)",
                }
                resp.text = '{"status": "ok"}'
            elif url.endswith("/v1/capabilities"):
                auth = kwargs.get("headers", {}).get("Authorization")
                if not auth or "not-a-valid" in auth or "invalid_signature" in auth:
                    resp.status_code = 401
                    resp.text = '{"detail": "unauthorized"}'
                else:
                    resp.status_code = 200
                    resp.text = '{"capabilities": "clean"}'
            return resp

        mock_get.side_effect = _fake_get

        runner = SecuritySmokeRunner(base_url="http://test-security:8000")
        report = SecuritySmokeReport()
        runner.run_auth_checks(report)
        runner.run_security_headers_checks(report)
        runner.run_storage_credentials_checks(report)

        assert report.overall_status == "PASS"
        assert report.passed >= 6
        assert report.failed == 0


def test_bola_isolation_blocked_without_token() -> None:
    runner = SecuritySmokeRunner(base_url="http://test-security:8000")
    report = SecuritySmokeReport()
    runner.run_bola_isolation_checks(report, family_a_token=None, family_b_id="fam_b")

    assert report.blocked == 1
    assert report.checks[0].status == "BLOCKED"


def test_bola_isolation_passes_on_404() -> None:
    with patch("requests.get") as mock_get:
        resp = MagicMock()
        resp.status_code = 404
        resp.text = '{"detail": "family not found"}'
        mock_get.return_value = resp

        runner = SecuritySmokeRunner(base_url="http://test-security:8000")
        report = SecuritySmokeReport()
        runner.run_bola_isolation_checks(
            report,
            family_a_token="token_a",
            family_b_id="fam_b",
            family_b_recording_id="rec_b",
        )

        assert report.failed == 0
        assert report.passed == 4  # profile, recording, audio, conflicts
        assert all(c.status == "PASS" for c in report.checks)


def test_storage_credentials_leaked_detection() -> None:
    with patch("requests.get") as mock_get:
        resp = MagicMock()
        resp.status_code = 200
        resp.text = (
            '{"error": "Failed to connect to postgresql://user:pass@host/db with service_role"}'
        )
        mock_get.return_value = resp

        runner = SecuritySmokeRunner(base_url="http://test-security:8000")
        report = SecuritySmokeReport()
        runner.run_storage_credentials_checks(report)

        assert report.failed == 1
        assert "service_role" in report.checks[0].details
