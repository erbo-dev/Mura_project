"""CLI runner for MURA Staging Load & Performance Harness."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from mura.ops.load_harness import LoadHarness, benchmark_worker_throughput  # noqa: E402
from mura.storage.database import Database  # noqa: E402


async def async_main() -> int:
    parser = argparse.ArgumentParser(description="MURA Staging Load & Performance Test Runner")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("CORE_API_URL", "http://127.0.0.1:8000"),
        help="Target API base URL (default: http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--family-id",
        default="family_staging_mura",
        help="Family ID for scoped endpoint tests",
    )
    parser.add_argument(
        "--recording-id",
        default="rec_staging_001_kazakh",
        help="Recording ID for audio range tests",
    )
    parser.add_argument(
        "--auth-token",
        default=os.environ.get("TEST_USER_TOKEN"),
        help="Bearer token for family user",
    )
    parser.add_argument(
        "--operations-token",
        default=os.environ.get("OPERATIONS_API_KEY"),
        help="Bearer token for operator endpoints",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="Concurrent request semaphore limit (default: 10)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=15,
        help="Iterations per scenario (default: 15)",
    )
    parser.add_argument(
        "--benchmark-workers",
        action="store_true",
        help="Execute database worker queue throughput benchmark",
    )
    parser.add_argument(
        "--output-json",
        help="Path to write machine-readable JSON report",
    )

    args = parser.parse_args()

    print("=== MURA Staging Load & Performance Test ===")
    print(f"Target URL:            {args.base_url}")
    print(f"Family ID:             {args.family_id}")
    print(f"Concurrency:           {args.concurrency}")
    print(f"Iterations / Scenario: {args.iterations}")
    print("Profile:               TEST PROFILE (No paid AI providers invoked)")

    harness = LoadHarness(
        base_url=args.base_url,
        auth_token=args.auth_token,
        operations_token=args.operations_token,
        concurrency=args.concurrency,
    )

    report = await harness.run_profile(
        family_id=args.family_id,
        recording_id=args.recording_id,
        iterations_per_scenario=args.iterations,
        environment=os.environ.get("MURA_ENVIRONMENT", "staging"),
    )

    if args.benchmark_workers and os.environ.get("DATABASE_URL"):
        print("\n--- Running Worker Queue Throughput Benchmark ---")
        db = Database(os.environ["DATABASE_URL"])
        bench = benchmark_worker_throughput(db, iterations=50)
        report.worker_benchmark = bench
        print(f"Worker queue throughput: {bench['ops_per_second']} ops/s")

    print("\n=== LOAD TEST RESULTS SUMMARY ===")
    print(f"Overall RPS:           {report.overall_rps}")
    print(
        f"Total Requests:        {report.total_requests} "
        f"(Success: {report.total_success}, Failed: {report.total_failed})"
    )
    print(f"Overall Latency p50:   {report.overall_latencies_ms.p50_ms} ms")
    print(f"Overall Latency p95:   {report.overall_latencies_ms.p95_ms} ms")
    print(f"Overall Latency p99:   {report.overall_latencies_ms.p99_ms} ms")
    print(f"Overall Latency Max:   {report.overall_latencies_ms.max_ms} ms")
    print(f"Capacity Claim:        {report.capacity_claim}")

    print("\nScenario Details:")
    for _name, scen in report.scenarios.items():
        print(
            f"  • {scen.scenario_name:<32} "
            f"Reqs: {scen.total_requests:<3} "
            f"RPS: {scen.rps:<6} "
            f"p50: {scen.latencies_ms.p50_ms:<6}ms "
            f"p95: {scen.latencies_ms.p95_ms:<6}ms "
            f"p99: {scen.latencies_ms.p99_ms:<6}ms "
            f"Codes: {scen.status_codes}"
        )

    rep_dict = report.to_dict()
    if args.output_json:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(rep_dict, indent=2), encoding="utf-8")
        print(f"\nReport written to: {out_path}")

    return 0 if report.total_failed == 0 else 1


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())
