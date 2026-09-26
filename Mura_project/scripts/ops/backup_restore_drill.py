"""CLI runner for MURA Backup & Restore Drill."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Add project root and src to path
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from mura.ops.backup_restore import (  # noqa: E402
    BackupRestoreError,
    UnsafeRestoreTargetError,
    execute_backup_restore_drill,
    redact_connection_url,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="MURA Backup & Restore Integrity Drill")
    parser.add_argument(
        "--source-url",
        default=os.environ.get("DATABASE_URL"),
        help="Source PostgreSQL connection URL (defaults to DATABASE_URL)",
    )
    parser.add_argument(
        "--target-url",
        default=os.environ.get("VERIFICATION_DATABASE_URL"),
        help="Target isolated verification database URL (defaults to VERIFICATION_DATABASE_URL)",
    )
    parser.add_argument(
        "--environment",
        default=os.environ.get("MURA_ENVIRONMENT", "staging"),
        help="Current environment (default: staging; production is strictly rejected)",
    )
    parser.add_argument(
        "--dump-dir",
        default=str(ROOT / ".mura" / "backups"),
        help="Directory to place temporary dump file",
    )
    parser.add_argument(
        "--audio-dir",
        default=str(ROOT / ".mura" / "audio"),
        help="Directory where test audio fixtures reside",
    )
    parser.add_argument(
        "--output-json",
        help="Path to write machine-readable JSON result",
    )

    args = parser.parse_args()

    if not args.source_url:
        print("[FAIL] Missing --source-url or DATABASE_URL in environment.", file=sys.stderr)
        return 1

    if not args.target_url:
        print(
            "[FAIL] Missing --target-url or VERIFICATION_DATABASE_URL in environment.",
            file=sys.stderr,
        )
        return 1

    print("=== MURA PostgreSQL Backup & Restore Drill ===")
    print(f"Source database: {redact_connection_url(args.source_url)}")
    print(f"Target database: {redact_connection_url(args.target_url)}")
    print(f"Environment:     {args.environment}")

    try:
        result = execute_backup_restore_drill(
            source_url=args.source_url,
            target_url=args.target_url,
            project_root=ROOT,
            dump_dir=Path(args.dump_dir),
            audio_dir=Path(args.audio_dir),
            environment=args.environment,
        )
        print("\n=== DRILL COMPLETED SUCCESSFULLY ===")
        print(f"Backup Status:   {result.backup}")
        print(f"Restore Status:  {result.restore}")
        print(f"Migration Head:  {result.migration_head}")
        print(f"Row Checks:      {result.row_checks}")
        print(f"Storage Checks:  {result.storage_checks}")
        print(f"Duration:        {result.duration_seconds}s")

        res_dict = result.to_dict()
        if args.output_json:
            out_path = Path(args.output_json)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(res_dict, indent=2), encoding="utf-8")
            print(f"Report written to: {out_path}")
        else:
            print(json.dumps(res_dict, indent=2))

        return 0

    except UnsafeRestoreTargetError as exc:
        print(f"\n[SAFETY BLOCKED] {exc}", file=sys.stderr)
        return 2
    except BackupRestoreError as exc:
        print(f"\n[DRILL FAILED] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"\n[UNEXPECTED ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
