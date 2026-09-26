"""Tests for load & performance test harness."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, Response, status

from mura.ops.load_harness import (
    LatencyMetrics,
    LoadHarness,
    benchmark_worker_throughput,
)
from mura.storage.database import Database


def test_latency_percentiles() -> None:
    samples = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
    metrics = LatencyMetrics.from_samples(samples)

    assert metrics.min_ms == 10.0
    assert metrics.max_ms == 100.0
    assert metrics.mean_ms == 55.0
    assert metrics.p50_ms == 55.0  # Median
    assert metrics.p95_ms > 90.0
    assert metrics.p99_ms > 95.0

    # Empty list
    empty = LatencyMetrics.from_samples([])
    assert empty.min_ms == 0.0
    assert empty.p50_ms == 0.0


def test_load_harness_run() -> None:
    async def _async_test() -> None:
        app = FastAPI()

        @app.get("/health")
        def health() -> dict[str, str]:
            return {"status": "ok"}

        @app.get("/ready")
        def ready() -> dict[str, str]:
            return {"status": "ready"}

        @app.get("/v1/capabilities")
        def capabilities() -> dict[str, str]:
            return {"capabilities": "synthetic"}

        @app.get("/v1/families/{family_id}")
        def family(family_id: str) -> dict[str, str]:
            return {"family_id": family_id, "name": "Test"}

        @app.get("/v1/families/{family_id}/stories")
        def stories(family_id: str) -> list[dict[str, str]]:
            return [{"story_id": "st_1"}]

        @app.get("/v1/families/{family_id}/people")
        def people(family_id: str) -> list[dict[str, str]]:
            return [{"person_id": "p_1"}]

        @app.get("/v1/families/{family_id}/relationships")
        def relationships(family_id: str) -> list[dict[str, str]]:
            return [{"rel_id": "r_1"}]

        @app.get("/v1/families/{family_id}/conflicts")
        def conflicts(family_id: str) -> list[dict[str, str]]:
            return []

        @app.get("/v1/families/{family_id}/books")
        def books(family_id: str) -> list[dict[str, str]]:
            return []

        @app.get("/v1/recordings/{recording_id}/audio")
        def audio_range(recording_id: str) -> Response:
            return Response(
                content=b"partial-audio-content",
                status_code=status.HTTP_206_PARTIAL_CONTENT,
                headers={
                    "Content-Range": "bytes 0-1023/2048",
                    "Content-Type": "audio/wav",
                },
            )

        @app.get("/v1/operations/monitoring/summary")
        def monitoring_summary() -> dict[str, str]:
            return {"queue": "healthy"}

        harness = LoadHarness(
            base_url="http://testserver",
            app=app,
            auth_token="token_load_bench",
            operations_token="operator_token",
            concurrency=5,
        )

        report = await harness.run_profile(
            family_id="fam_123",
            recording_id="rec_456",
            iterations_per_scenario=5,
        )

        assert report.profile_label == "TEST PROFILE"
        assert report.total_requests > 0
        assert report.total_failed == 0
        assert report.total_success == report.total_requests
        assert report.overall_rps > 0
        assert "health" in report.scenarios
        assert "audio_range" in report.scenarios
        assert report.scenarios["audio_range"].successful_requests == 5
        assert report.scenarios["audio_range"].status_codes[206] == 5
        assert "capacity_claim" in report.to_dict()

    asyncio.run(_async_test())


def test_worker_throughput(tmp_path: Path) -> None:
    db_file = tmp_path / "worker_bench.db"
    db = Database(f"sqlite+pysqlite:///{db_file}")
    db.create_schema()

    bench = benchmark_worker_throughput(db, iterations=20)
    assert bench["queue_queries_total"] == 20
    assert bench["elapsed_seconds"] >= 0
    assert bench["ops_per_second"] >= 0
