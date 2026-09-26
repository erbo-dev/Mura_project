"""CLI runner for MURA Storage Reconciliation."""

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
from mura.ops.storage_reconciliation import StorageReconciler  # noqa: E402
from mura.storage.database import Database  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="MURA Report-Only Storage Reconciliation")
    parser.add_argument(
        "--db-url",
        default=os.environ.get("DATABASE_URL"),
        help="Database connection URL (defaults to DATABASE_URL)",
    )
    parser.add_argument(
        "--audio-dir",
        default=str(ROOT / ".mura" / "audio"),
        help="Local audio storage directory",
    )
    parser.add_argument(
        "--book-dir",
        default=str(ROOT / ".mura" / "books"),
        help="Local book storage directory",
    )
    parser.add_argument(
        "--min-orphan-age-seconds",
        type=float,
        default=300.0,
        help="Minimum object age before reporting as orphan candidate (default: 300s)",
    )
    parser.add_argument(
        "--output-json",
        help="Path to write machine-readable JSON report",
    )
    parser.add_argument(
        "--fail-on-inconsistencies",
        action="store_true",
        help="Exit non-zero if any missing, orphan, or corrupted objects are detected",
    )

    args = parser.parse_args()

    if not args.db_url:
        print("[FAIL] Missing --db-url or DATABASE_URL in environment.", file=sys.stderr)
        return 1

    print("=== MURA Storage Reconciliation (Report-Only) ===")
    print(f"Database:              {redact_connection_url(args.db_url)}")
    print(f"Audio Directory:       {args.audio_dir}")
    print(f"Book Directory:        {args.book_dir}")
    print(f"Min Orphan Age:        {args.min_orphan_age_seconds}s")
    print("Automatic Deletion:    STRICTLY DISABLED (REPORT ONLY)")

    db = Database(args.db_url)
    reconciler = StorageReconciler(
        database=db,
        audio_dir=Path(args.audio_dir) if args.audio_dir else None,
        book_dir=Path(args.book_dir) if args.book_dir else None,
        supabase_url=os.environ.get("SUPABASE_URL"),
        supabase_service_role_key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY"),
        min_orphan_age_seconds=args.min_orphan_age_seconds,
    )

    report = reconciler.reconcile()
    rep_dict = report.to_dict()

    print("\n=== RECONCILIATION SUMMARY ===")
    print(f"Status:                {report.status}")
    print(f"Checked DB Records:    {report.checked_db_records}")
    print(f"Checked Storage Objs:  {report.checked_storage_objects}")
    print(f"Healthy Matches:       {report.healthy}")
    print(f"Missing in Storage:    {report.missing}")
    print(f"Orphaned in Storage:   {report.orphaned}")
    print(f"Size Mismatches:       {report.size_mismatch}")
    print(f"Hash Mismatches:       {report.hash_mismatch}")
    print(f"Invalid Keys:          {report.invalid_keys}")
    print(f"Storage Errors:        {report.storage_errors}")
    print(f"Duration:              {report.duration_seconds}s")

    if report.issues:
        print(f"\nDetected {len(report.issues)} Issue(s):")
        for idx, issue in enumerate(report.issues[:10], 1):
            print(
                f"  {idx}. [{issue.domain.upper()}:{issue.issue_type}] "
                f"key='{issue.storage_key}': {issue.details}"
            )
        if len(report.issues) > 10:
            print(f"  ... and {len(report.issues) - 10} more issues.")

    if args.output_json:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(rep_dict, indent=2), encoding="utf-8")
        print(f"\nFull report written to: {out_path}")

    if args.fail_on_inconsistencies and report.status != "PASS":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
