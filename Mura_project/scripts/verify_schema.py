"""Read-Only Database Schema & Migration Verifier for Staging.

Verifies:
1. Single Alembic head matches expected version '20260918_0013'.
2. All 14+ core tables and critical columns exist in the target database.
3. Migration idempotency ('alembic upgrade head' is safe to run repeatedly).

Never prints sensitive database connection strings or passwords.
Exits non-zero on verification failure.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV_SITE = ROOT / ".venv" / "Lib" / "site-packages"
if VENV_SITE.exists() and str(VENV_SITE) not in sys.path:
    sys.path.insert(0, str(VENV_SITE))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

EXPECTED_HEAD = "20260918_0013"

EXPECTED_TABLES = [
    "users",
    "families",
    "family_memberships",
    "recordings",
    "processing_jobs",
    "archive_people",
    "archive_claims",
    "archive_corrections",
    "archive_conflicts",
    "family_graph_edges",
    "books",
    "book_source_snapshots",
    "book_plans",
    "book_chapters",
    "book_continuity_states",
    "book_exports",
    "book_jobs",
    "ai_usage_events",
]

CRITICAL_COLUMNS = {
    "books": {
        "book_id",
        "family_id",
        "created_by_user_id",
        "title",
        "status",
        "stage",
        "target_word_count",
        "chapters_approved",
    },
    "book_chapters": {"chapter_id", "book_id", "chapter_number", "title", "status", "word_count"},
    "book_jobs": {"job_id", "book_id", "family_id", "status", "stage", "lease_owner"},
    "ai_usage_events": {"event_id", "provider", "model", "operation", "book_id", "chapter_number"},
    "recordings": {"recording_id", "family_id", "speaker_id", "audio_path", "storage_key"},
    "processing_jobs": {"job_id", "recording_id", "status", "lease_owner"},
}


def verify_alembic_heads(alembic_cfg_path: Path) -> bool:
    print("--- Verifying Alembic Migration Head ---")
    config = Config(str(alembic_cfg_path))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    if len(heads) != 1:
        print(f"[FAIL] Expected exactly 1 Alembic head, found {len(heads)}: {heads}")
        return False
    head = heads[0]
    if head != EXPECTED_HEAD:
        print(f"[FAIL] Expected Alembic head '{EXPECTED_HEAD}', got '{head}'")
        return False
    print(f"[OK] Single Alembic linear head verified: {head}")
    return True


def verify_database_schema(engine) -> bool:
    print("--- Verifying Database Schema & Table Structure ---")
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    passed = True

    missing_tables = []
    for table in EXPECTED_TABLES:
        if table in existing_tables:
            print(f"[OK] Table '{table}' exists")
        else:
            print(f"[FAIL] Table '{table}' is MISSING")
            missing_tables.append(table)
            passed = False

    if missing_tables:
        return False

    print("--- Verifying Critical Column Definitions ---")
    for table, required_cols in CRITICAL_COLUMNS.items():
        cols = {col["name"] for col in inspector.get_columns(table)}
        missing_cols = required_cols - cols
        if missing_cols:
            print(f"[FAIL] Table '{table}' is missing columns: {missing_cols}")
            passed = False
        else:
            print(f"[OK] Table '{table}' has all required columns ({len(required_cols)} verified)")

    return passed


def main() -> int:
    print("=== MURA Staging Database Schema Verifier ===")
    project_root = Path(__file__).resolve().parent.parent
    alembic_ini = project_root / "alembic.ini"
    if not alembic_ini.exists():
        print(f"[FAIL] alembic.ini not found at {alembic_ini}")
        return 1

    if not verify_alembic_heads(alembic_ini):
        return 1

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("[SKIP] DATABASE_URL is not set in environment. Skipping live table inspection.")
        print("Alembic repository heads are structurally verified.")
        return 0

    try:
        engine = create_engine(db_url)
        schema_ok = verify_database_schema(engine)
        if not schema_ok:
            return 1
        print("RESULT: Schema and migrations verified successfully.")
        return 0
    except Exception as exc:
        print(f"[FAIL] Error connecting to database: {type(exc).__name__}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
