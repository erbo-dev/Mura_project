"""CLI runner for MURA Staging Security Smoke Suite."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from mura.ops.security_smoke import SecuritySmokeRunner  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="MURA Staging Security Smoke Runner")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("CORE_API_URL", "http://127.0.0.1:8000"),
        help="Core API base URL",
    )
    parser.add_argument(
        "--family-a-token",
        default=os.environ.get("FAMILY_A_USER_TOKEN"),
        help="Bearer token for Family A user (to test cross-family isolation against Family B)",
    )
    parser.add_argument(
        "--family-b-id",
        default="family_staging_forbidden",
        help="Target forbidden Family ID",
    )
    parser.add_argument(
        "--family-b-recording-id",
        default="rec_staging_forbidden_01",
        help="Target forbidden Recording ID",
    )
    parser.add_argument(
        "--output-json",
        help="Path to write machine-readable JSON report",
    )

    args = parser.parse_args()

    token_status = "[SET]" if args.family_a_token else "[UNSET - BOLA tests will be marked BLOCKED]"
    print("=== MURA Staging Security Smoke ===")
    print(f"Base URL:         {args.base_url}")
    print(f"Family A Token:   {token_status}")
    print(f"Target Family B:  {args.family_b_id}")

    runner = SecuritySmokeRunner(base_url=args.base_url)
    report = runner.run(
        family_a_token=args.family_a_token,
        family_b_id=args.family_b_id,
        family_b_recording_id=args.family_b_recording_id,
    )

    rep_dict = report.to_dict()

    print("\n=== SECURITY SMOKE RESULTS ===")
    print(f"Overall Status:   {report.overall_status}")
    print(f"Total Checks:     {report.total_checks}")
    print(f"Passed:           {report.passed}")
    print(f"Failed:           {report.failed}")
    print(f"Blocked:          {report.blocked}")
    print(f"Duration:         {report.duration_seconds}s")

    print("\nCheck Details:")
    for c in report.checks:
        icon = (
            "[PASS]" if c.status == "PASS" else ("[BLOCKED]" if c.status == "BLOCKED" else "[FAIL]")
        )
        print(f"  {icon:<9} [{c.category.upper()}] {c.name}: {c.details}")

    if args.output_json:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(rep_dict, indent=2), encoding="utf-8")
        print(f"\nReport written to: {out_path}")

    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
