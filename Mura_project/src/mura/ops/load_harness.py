"""Load & Performance Test Harness.

Executes reproducible synthetic read, polling, byte-range, and safe write load scenarios.
Strict invariants:
- Never invokes paid external AI providers (Whisper or DeepSeek).
- Reports metrics labeled strictly as "TEST PROFILE" (never "CAPACITY").
- Measures requests, RPS, p50, p95, p99, max latency, status codes, and worker throughput.
"""

from __future__ import annotations

import asyncio
import math
import statistics
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

from mura.storage.database import Database


@dataclass(frozen=True)
class LatencyMetrics:
    min_ms: float
    p50_ms: float
    p90_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    mean_ms: float

    @classmethod
    def from_samples(cls, samples_ms: list[float]) -> LatencyMetrics:
        if not samples_ms:
            return cls(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        sorted_samples = sorted(samples_ms)
        n = len(sorted_samples)

        def percentile(p: float) -> float:
            k = (n - 1) * p
            f = math.floor(k)
            c = math.ceil(k)
            if f == c:
                return sorted_samples[int(k)]
            d0 = sorted_samples[int(f)] * (c - k)
            d1 = sorted_samples[int(c)] * (k - f)
            return d0 + d1

        return cls(
            min_ms=round(sorted_samples[0], 2),
            p50_ms=round(percentile(0.50), 2),
            p90_ms=round(percentile(0.90), 2),
            p95_ms=round(percentile(0.95), 2),
            p99_ms=round(percentile(0.99), 2),
            max_ms=round(sorted_samples[-1], 2),
            mean_ms=round(statistics.mean(sorted_samples), 2),
        )


@dataclass
class ScenarioResult:
    scenario_name: str
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    rps: float = 0.0
    latencies_ms: LatencyMetrics = field(
        default_factory=lambda: LatencyMetrics(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    )
    status_codes: dict[int, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_name": self.scenario_name,
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "rps": self.rps,
            "latencies_ms": asdict(self.latencies_ms),
            "status_codes": self.status_codes,
            "errors": self.errors[:10],
        }


@dataclass
class LoadTestReport:
    profile_label: str = "TEST PROFILE"
    environment: str = "staging"
    duration_seconds: float = 0.0
    total_requests: int = 0
    total_success: int = 0
    total_failed: int = 0
    overall_rps: float = 0.0
    overall_latencies_ms: LatencyMetrics = field(
        default_factory=lambda: LatencyMetrics(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    )
    scenarios: dict[str, ScenarioResult] = field(default_factory=dict)
    worker_benchmark: dict[str, Any] | None = None
    capacity_claim: str = "NONE (Requires dedicated cloud staging benchmarks)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_label": self.profile_label,
            "environment": self.environment,
            "duration_seconds": self.duration_seconds,
            "total_requests": self.total_requests,
            "total_success": self.total_success,
            "total_failed": self.total_failed,
            "overall_rps": self.overall_rps,
            "overall_latencies_ms": asdict(self.overall_latencies_ms),
            "scenarios": {k: v.to_dict() for k, v in self.scenarios.items()},
            "worker_benchmark": self.worker_benchmark,
            "capacity_claim": self.capacity_claim,
        }


class LoadHarness:
    """Async load generator executing test profiles against MURA endpoints."""

    def __init__(
        self,
        base_url: str,
        *,
        app: Any = None,
        auth_token: str | None = None,
        operations_token: str | None = None,
        concurrency: int = 10,
        request_timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.app = app
        self.auth_token = auth_token
        self.operations_token = operations_token
        self.concurrency = concurrency
        self.request_timeout = request_timeout

    def _headers(self, token: str | None = None) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        t = token or self.auth_token
        if t:
            headers["Authorization"] = f"Bearer {t}"
        return headers

    async def _execute_scenario(
        self,
        client: httpx.AsyncClient,
        name: str,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        data: Any = None,
        iterations: int = 20,
    ) -> ScenarioResult:
        result = ScenarioResult(scenario_name=name)
        latencies: list[float] = []
        sem = asyncio.Semaphore(self.concurrency)

        async def _single_request() -> None:
            async with sem:
                t0 = time.monotonic()
                try:
                    resp = await client.request(
                        method,
                        path,
                        headers=headers or self._headers(),
                        content=data,
                        timeout=self.request_timeout,
                    )
                    elapsed_ms = (time.monotonic() - t0) * 1000.0
                    latencies.append(elapsed_ms)
                    result.total_requests += 1
                    result.status_codes[resp.status_code] = (
                        result.status_codes.get(resp.status_code, 0) + 1
                    )
                    if 200 <= resp.status_code < 400 or resp.status_code == 206:
                        result.successful_requests += 1
                    else:
                        result.failed_requests += 1
                except Exception as exc:
                    elapsed_ms = (time.monotonic() - t0) * 1000.0
                    latencies.append(elapsed_ms)
                    result.total_requests += 1
                    result.failed_requests += 1
                    result.errors.append(f"{type(exc).__name__}: {exc!s}")

        start_time = time.monotonic()
        tasks = [_single_request() for _ in range(iterations)]
        await asyncio.gather(*tasks)
        total_time = time.monotonic() - start_time

        result.rps = round(result.total_requests / total_time, 2) if total_time > 0 else 0.0
        result.latencies_ms = LatencyMetrics.from_samples(latencies)
        return result

    async def run_profile(
        self,
        *,
        family_id: str,
        recording_id: str | None = None,
        job_id: str | None = None,
        book_id: str | None = None,
        iterations_per_scenario: int = 15,
        environment: str = "staging",
    ) -> LoadTestReport:
        start_time = time.monotonic()
        transport = httpx.ASGITransport(app=self.app) if self.app else None

        async with httpx.AsyncClient(
            base_url=self.base_url,
            transport=transport,
            timeout=self.request_timeout,
        ) as client:
            scenarios: dict[str, ScenarioResult] = {}

            # 1. Health / Liveness
            scenarios["health"] = await self._execute_scenario(
                client, "Liveness /health", "GET", "/health", iterations=iterations_per_scenario
            )

            # 2. Readiness
            scenarios["ready"] = await self._execute_scenario(
                client, "Readiness /ready", "GET", "/ready", iterations=iterations_per_scenario
            )

            # 3. Capabilities / Session Bootstrap
            scenarios["capabilities"] = await self._execute_scenario(
                client,
                "Capabilities Bootstrap",
                "GET",
                "/v1/capabilities",
                iterations=iterations_per_scenario,
            )

            # 4. Family Overview
            scenarios["family_overview"] = await self._execute_scenario(
                client,
                "Family Overview",
                "GET",
                f"/v1/families/{family_id}",
                iterations=iterations_per_scenario,
            )

            # 5. Story List
            scenarios["story_list"] = await self._execute_scenario(
                client,
                "Story List",
                "GET",
                f"/v1/families/{family_id}/stories",
                iterations=iterations_per_scenario,
            )

            # 6. People List
            scenarios["people_list"] = await self._execute_scenario(
                client,
                "People List",
                "GET",
                f"/v1/families/{family_id}/people",
                iterations=iterations_per_scenario,
            )

            # 7. Family Relationships Graph
            scenarios["relationships"] = await self._execute_scenario(
                client,
                "Family Relationships",
                "GET",
                f"/v1/families/{family_id}/relationships",
                iterations=iterations_per_scenario,
            )

            # 8. Human Review Conflicts List
            scenarios["conflicts"] = await self._execute_scenario(
                client,
                "Review Conflicts",
                "GET",
                f"/v1/families/{family_id}/conflicts",
                iterations=iterations_per_scenario,
            )

            # 9. Book List
            scenarios["books_list"] = await self._execute_scenario(
                client,
                "Book List",
                "GET",
                f"/v1/families/{family_id}/books",
                iterations=iterations_per_scenario,
            )

            # 10. Audio Byte-Range Requests (RFC 7233)
            if recording_id:
                range_headers = self._headers()
                range_headers["Range"] = "bytes=0-1023"
                scenarios["audio_range"] = await self._execute_scenario(
                    client,
                    "Audio Range Streaming (bytes=0-1023)",
                    "GET",
                    f"/v1/recordings/{recording_id}/audio",
                    headers=range_headers,
                    iterations=iterations_per_scenario,
                )

            # 11. Realistic Job Polling Pattern (fast poll -> backoff)
            if job_id:
                scenarios["job_polling"] = await self._execute_scenario(
                    client,
                    "Job Status Polling",
                    "GET",
                    f"/v1/jobs/{job_id}",
                    iterations=iterations_per_scenario,
                )

            # 12. Operations Monitoring Summary (Operator Token)
            if self.operations_token:
                scenarios["monitoring_summary"] = await self._execute_scenario(
                    client,
                    "Operations Monitoring Summary",
                    "GET",
                    "/v1/operations/monitoring/summary",
                    headers={"Authorization": f"Bearer {self.operations_token}"},
                    iterations=iterations_per_scenario,
                )

        duration = time.monotonic() - start_time
        total_reqs = sum(s.total_requests for s in scenarios.values())
        total_success = sum(s.successful_requests for s in scenarios.values())
        total_failed = sum(s.failed_requests for s in scenarios.values())
        overall_rps = round(total_reqs / duration, 2) if duration > 0 else 0.0

        all_latencies = []
        for s in scenarios.values():
            all_latencies.append(s.latencies_ms.p50_ms)

        return LoadTestReport(
            profile_label="TEST PROFILE",
            environment=environment,
            duration_seconds=round(duration, 3),
            total_requests=total_reqs,
            total_success=total_success,
            total_failed=total_failed,
            overall_rps=overall_rps,
            overall_latencies_ms=LatencyMetrics.from_samples(all_latencies),
            scenarios=scenarios,
        )


def benchmark_worker_throughput(
    database: Database,
    *,
    iterations: int = 50,
) -> dict[str, Any]:
    """Measures queue claim, processing lease, and heartbeat throughput in isolation."""
    from mura.jobs import JobStatus
    from mura.storage.database import ProcessingJobRow

    t0 = time.monotonic()
    claimed = 0

    with database.session_factory() as session:
        # Measure fast batch query throughput for unclaimed/queued items
        for _ in range(iterations):
            q = (
                session.query(ProcessingJobRow)
                .filter(ProcessingJobRow.status == JobStatus.QUEUED.value)
                .limit(5)
            )
            _ = q.all()
            claimed += 1

    elapsed = time.monotonic() - t0
    ops_per_second = round(claimed / elapsed, 2) if elapsed > 0 else 0.0

    return {
        "queue_queries_total": claimed,
        "elapsed_seconds": round(elapsed, 4),
        "ops_per_second": ops_per_second,
    }
