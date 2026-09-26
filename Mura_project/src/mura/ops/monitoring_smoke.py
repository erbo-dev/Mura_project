"""Monitoring & Operations Smoke Verification Tool.

Verifies:
1. API Liveness (/health probe)
2. API Readiness (/ready probe confirming DB connection)
3. Schema & Alembic linear head integrity
4. Operations authentication boundary (requires distinct OPERATIONS_API_KEY)
5. Queue health & stuck-job metrics (/v1/operations/monitoring/summary)
6. Privacy redaction: strictly asserts zero private prose or family transcripts
7. Derived alert threshold evaluations
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import requests
from sqlalchemy import text

from mura.ops.backup_restore import get_expected_alembic_head
from mura.storage.database import Database


@dataclass(frozen=True)
class ThresholdAlert:
    metric: str
    condition: str
    current_value: Any
    severity: str  # "INFO" | "WARNING" | "CRITICAL"
    recommendation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MonitoringSmokeReport:
    overall_status: str = "PASS"
    health_status: str = "PASS"
    ready_status: str = "PASS"
    schema_status: str = "PASS"
    operations_auth_status: str = "PASS"
    privacy_status: str = "PASS"
    duration_seconds: float = 0.0
    monitoring_summary: dict[str, Any] = field(default_factory=dict)
    alerts: list[ThresholdAlert] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["alerts"] = [a.to_dict() for a in self.alerts]
        return data


PRIVATE_KEY_PATTERNS = {
    "transcript",
    "readable_text",
    "evidence_quote",
    "quote",
    "story_text",
    "prose",
    "chapter_content",
}


def assert_zero_privacy_leakage(obj: Any, path: str = "") -> list[str]:
    """Recursively validates that monitoring payload contains NO private prose or transcripts."""
    violations: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            current_path = f"{path}.{k}" if path else k
            lower_k = k.lower()
            if any(p in lower_k for p in PRIVATE_KEY_PATTERNS):
                violations.append(f"Private key detected at '{current_path}'")
            violations.extend(assert_zero_privacy_leakage(v, current_path))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            violations.extend(assert_zero_privacy_leakage(item, f"{path}[{idx}]"))
    elif isinstance(obj, str):
        # Alert if text looks like private story prose (> 100 characters of natural text)
        if (
            len(obj) > 150
            and " " in obj
            and not obj.startswith("http")
            and not re.match(r"^[0-9a-fA-F-]+$", obj)
        ):
            violations.append(f"Potential private prose at '{path}' (length {len(obj)})")
    return violations


def evaluate_monitoring_thresholds(summary: dict[str, Any]) -> list[ThresholdAlert]:
    """Evaluates monitoring summary against operational alert thresholds."""
    alerts: list[ThresholdAlert] = []

    # Queue checks
    queue = summary.get("queue", {})
    pending = queue.get("pending", 0)
    oldest_pending = queue.get("oldest_pending_seconds")
    expired_leases = queue.get("expired_leases", 0)

    if pending > 100:
        alerts.append(
            ThresholdAlert(
                metric="queue.pending",
                condition="pending > 100",
                current_value=pending,
                severity="WARNING",
                recommendation="Queue backlog growing; scale recording workers.",
            )
        )

    if expired_leases > 0:
        alerts.append(
            ThresholdAlert(
                metric="queue.expired_leases",
                condition="expired_leases > 0",
                current_value=expired_leases,
                severity="WARNING",
                recommendation="Investigate dead worker or network timeout; worker fencing active.",
            )
        )

    if oldest_pending is not None and oldest_pending > 1200:
        alerts.append(
            ThresholdAlert(
                metric="queue.oldest_pending_seconds",
                condition="oldest_pending > 1200s",
                current_value=oldest_pending,
                severity="CRITICAL",
                recommendation="Queue latency exceeds 20 minutes; scale recording workers.",
            )
        )

    # Stuck jobs
    stuck_jobs = summary.get("stuck_jobs", [])
    if len(stuck_jobs) > 0:
        alerts.append(
            ThresholdAlert(
                metric="stuck_jobs",
                condition="count > 0",
                current_value=len(stuck_jobs),
                severity="WARNING",
                recommendation="Inspect job traces via /v1/jobs/{job_id}/trace for root cause.",
            )
        )

    # Storage cleanup
    cleanup = summary.get("cleanup", {})
    exhausted = cleanup.get("attempts_exhausted", 0)
    if exhausted > 0:
        alerts.append(
            ThresholdAlert(
                metric="cleanup.attempts_exhausted",
                condition="attempts_exhausted > 0",
                current_value=exhausted,
                severity="WARNING",
                recommendation="Cleanup jobs failed; check storage credentials and network.",
            )
        )

    # Book queue
    book_queue = summary.get("book_queue", {})
    book_expired = book_queue.get("expired_leases", 0)
    if book_expired > 0:
        alerts.append(
            ThresholdAlert(
                metric="book_queue.expired_leases",
                condition="expired_leases > 0",
                current_value=book_expired,
                severity="WARNING",
                recommendation="Book worker lease expired; check book worker health.",
            )
        )

    return alerts


class MonitoringSmokeRunner:
    """Probes operations and health endpoints and validates observability invariants."""

    def __init__(
        self,
        base_url: str,
        *,
        operations_api_key: str | None = None,
        database_url: str | None = None,
        project_root: Path | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.operations_api_key = operations_api_key
        self.database_url = database_url
        self.project_root = project_root
        self.timeout_seconds = timeout_seconds

    def run(self) -> MonitoringSmokeReport:
        t0 = time.monotonic()
        report = MonitoringSmokeReport()

        # 1. Health Probe
        try:
            h_resp = requests.get(f"{self.base_url}/health", timeout=self.timeout_seconds)
            if h_resp.status_code != 200 or h_resp.json().get("status") != "ok":
                report.health_status = "FAIL"
                report.errors.append(f"/health returned HTTP {h_resp.status_code}: {h_resp.text}")
        except Exception as exc:
            report.health_status = "FAIL"
            report.errors.append(f"/health connection failed: {exc}")

        # 2. Readiness Probe
        try:
            r_resp = requests.get(f"{self.base_url}/ready", timeout=self.timeout_seconds)
            if r_resp.status_code != 200 or r_resp.json().get("database") != "ready":
                report.ready_status = "FAIL"
                report.errors.append(f"/ready returned HTTP {r_resp.status_code}: {r_resp.text}")
        except Exception as exc:
            report.ready_status = "FAIL"
            report.errors.append(f"/ready connection failed: {exc}")

        # 3. Schema & Linear Head Verification
        if self.database_url and self.project_root:
            try:
                expected_head = get_expected_alembic_head(self.project_root)
                db = Database(self.database_url)
                with db.session_factory() as session:
                    res = session.execute(text("SELECT version_num FROM alembic_version")).scalar()
                    if res != expected_head:
                        report.schema_status = "FAIL"
                        report.errors.append(
                            f"Alembic linear head mismatch: expected {expected_head}, got {res}"
                        )
            except Exception as exc:
                report.schema_status = "FAIL"
                report.errors.append(f"Database schema inspection failed: {exc}")

        # 4. Operations Auth Boundary & Summary
        if self.operations_api_key:
            # Check 401 without auth
            try:
                no_auth = requests.get(
                    f"{self.base_url}/v1/operations/monitoring/summary",
                    timeout=self.timeout_seconds,
                )
                if no_auth.status_code != 401:
                    report.operations_auth_status = "FAIL"
                    report.errors.append(
                        f"Expected 401 without operations token, got {no_auth.status_code}"
                    )
            except Exception as exc:
                report.operations_auth_status = "FAIL"
                report.errors.append(f"Operations auth probe failed: {exc}")

            # Check 200 with valid operations token
            try:
                auth_resp = requests.get(
                    f"{self.base_url}/v1/operations/monitoring/summary",
                    headers={"Authorization": f"Bearer {self.operations_api_key}"},
                    timeout=self.timeout_seconds,
                )
                if auth_resp.status_code == 200:
                    summary_data = auth_resp.json()
                    report.monitoring_summary = summary_data

                    # Privacy Leakage Check
                    violations = assert_zero_privacy_leakage(summary_data)
                    if violations:
                        report.privacy_status = "FAIL"
                        report.errors.extend(violations)

                    # Threshold Evaluation
                    report.alerts = evaluate_monitoring_thresholds(summary_data)
                else:
                    report.operations_auth_status = "FAIL"
                    report.errors.append(
                        f"/v1/operations/monitoring/summary returned HTTP "
                        f"{auth_resp.status_code}: {auth_resp.text}"
                    )
            except Exception as exc:
                report.operations_auth_status = "FAIL"
                report.errors.append(f"Operations summary request failed: {exc}")
        else:
            report.operations_auth_status = "SKIPPED (No OPERATIONS_API_KEY provided)"

        report.duration_seconds = round(time.monotonic() - t0, 3)
        if (
            report.health_status == "FAIL"
            or report.ready_status == "FAIL"
            or report.schema_status == "FAIL"
            or report.operations_auth_status == "FAIL"
            or report.privacy_status == "FAIL"
        ):
            report.overall_status = "FAIL"
        else:
            report.overall_status = "PASS"

        return report
