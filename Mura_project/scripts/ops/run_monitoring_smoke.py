"""CLI runner for MURA Monitoring & Operations Smoke Tool."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from mura.ops.backup_restore import redact_connection_url  # noqa: E402
from mura.ops.monitoring_smoke import MonitoringSmokeRunner  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="MURA Monitoring & Operations Smoke Verifier")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("CORE_API_URL", "http://127.0.0.1:8000"),
        help="Core API base URL",
    )
    parser.add_argument(
        "--operations-key",
        default=os.environ.get("OPERATIONS_API_KEY"),
        help="Operations API Bearer token",
    )
    parser.add_argument(
        "--db-url",
        default=os.environ.get("DATABASE_URL"),
        help="PostgreSQL connection URL for schema verification",
    )
    parser.add_argument(
        "--output-json",
        help="Path to write machine-readable JSON report",
    )

    args = parser.parse_args()

    print("=== MURA Monitoring & Operations Smoke ===")
    print(f"Base URL:       {args.base_url}")
    print(f"Database:       {redact_connection_url(args.db_url)}")
    print(f"Operations Key: {'[SET]' if args.operations_key else '[UNSET]'}")

    runner = MonitoringSmokeRunner(
        base_url=args.base_url,
        operations_api_key=args.operations_key,
        database_url=args.db_url,
        project_root=ROOT,
    )

    report = runner.run()
    rep_dict = report.to_dict()

    print("\n=== SMOKE RESULTS ===")
    print(f"Overall Status:     {report.overall_status}")
    print(f"Health Probe:       {report.health_status}")
    print(f"Readiness Probe:    {report.ready_status}")
    print(f"Schema Status:      {report.schema_status}")
    print(f"Operations Auth:    {report.operations_auth_status}")
    print(f"Privacy Redaction:  {report.privacy_status}")
    print(f"Duration:           {report.duration_seconds}s")

    if report.alerts:
        print(f"\nDerived Operational Alerts ({len(report.alerts)}):")
        for a in report.alerts:
            print(
                f"  • [{a.severity}] {a.metric}: {a.condition} "
                f"(current: {a.current_value}) - {a.recommendation}"
            )

    if report.errors:
        print(f"\nErrors ({len(report.errors)}):")
        for e in report.errors:
            print(f"  ! {e}")

    if args.output_json:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(rep_dict, indent=2), encoding="utf-8")
        print(f"\nReport written to: {out_path}")

    return 0 if report.overall_status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
