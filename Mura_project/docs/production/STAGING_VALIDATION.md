# MURA Staging Validation & Verification Runbook

**Document Version**: 1.0.0  
**Target Environment**: Staging / Pre-Production  
**Prerequisites**: Python 3.11+, PostgreSQL 17+, Docker (optional)  

---

## 1. Overview & Verification Philosophy

MURA enforces strict staging validation before any code reaches production. The validation pipeline combines automated contract tests, security boundaries, backup/recovery drills, storage consistency checks, and load simulation.

All staging verification tools are designed with **fail-closed safety invariants**:
- They will never overwrite production data.
- They will never automatically delete physical storage objects.
- They will never run unmetered load tests against paid external AI providers.
- They will report `BLOCKED` rather than false `PASS` when external cloud credentials are unconfigured.

---

## 2. Staging Validation Checklist

| Step | Operation | Tool / Command | Verification Criteria | Status |
|---|---|---|---|---|
| **1** | Database Migration & Head Check | `alembic upgrade head` | Single migration head matches expected revision (`20260923_0018`). | REQUIRED |
| **2** | Safe Backup & Restore Drill | `python scripts/ops/backup_restore_drill.py` | Full `pg_dump`/`pg_restore` cycle into safe target database; table counts and SHA256 hashes match. | REQUIRED |
| **3** | Report-Only Storage Reconciliation | `python scripts/ops/reconcile_storage.py` | Zero missing files; active orphans within grace period; `automatic_deletion: NO`. | REQUIRED |
| **4** | Monitoring & Operations Smoke | `python scripts/ops/run_monitoring_smoke.py` | `/health`, `/ready` return 200; operations auth works; zero PII leak in summary. | REQUIRED |
| **5** | Staging Security Smoke | `python scripts/ops/run_security_smoke.py` | Auth 401 on unauthenticated requests; BOLA 404 across family boundaries; role matrix enforced. | REQUIRED |
| **6** | Load & Worker Throughput Test | `python scripts/ops/run_load_test.py` | Concurrency test with synthetic providers; worker queue claim & processing verified. | REQUIRED |

---

## 3. Step-by-Step Execution Guide

### Step 1: Database Migration
Ensure the staging database has all migrations applied:
```bash
export DATABASE_URL="postgresql+psycopg://mura_user:secret@staging-db.internal:5432/mura_staging"
alembic current
alembic upgrade head
alembic check
```

### Step 2: Backup & Restore Drill
Run the safe backup and restore drill against an isolated verification database. The tool creates a temporary database, verifies the restore, and tears it down:
```bash
python scripts/ops/backup_restore_drill.py \
  --source-db-url "$DATABASE_URL" \
  --target-db-url "postgresql+psycopg://mura_user:secret@staging-db.internal:5432/mura_restore_verification_test" \
  --audio-dir /var/mura/audio \
  --output-json .mura/backups/drill_report.json
```
**Expected Output**:
- Exit code: `0`
- `backup_status: PASS`, `restore_status: PASS`, `schema_verified: True`
- Table count matches: `100%`

### Step 3: Storage Reconciliation
Audit storage for orphaned files or missing database references:
```bash
python scripts/ops/reconcile_storage.py \
  --db-url "$DATABASE_URL" \
  --audio-dir /var/mura/audio \
  --book-dir /var/mura/books \
  --min-orphan-age-seconds 300 \
  --output-json .mura/reports/storage_reconciliation.json
```
**Expected Output**:
- Exit code: `0`
- `automatic_deletion: NO`
- Zero `missing` issues for active recordings.

### Step 4: Monitoring Smoke
Verify health probes, schema status, and operational metrics:
```bash
python scripts/ops/run_monitoring_smoke.py \
  --base-url "http://staging-api.internal:8000" \
  --operations-key "$OPERATIONS_API_KEY" \
  --db-url "$DATABASE_URL" \
  --output-json .mura/reports/monitoring_smoke.json
```
**Expected Output**:
- Exit code: `0`
- `Overall Status: PASS`
- `Privacy Redaction: PASS` (0 private tokens or content exposed)

### Step 5: Security & BOLA Smoke
Verify authorization boundaries and tenant isolation:
```bash
python scripts/ops/run_security_smoke.py \
  --base-url "http://staging-api.internal:8000" \
  --family-a-token "$STAGING_FAMILY_A_JWT" \
  --family-b-token "$STAGING_FAMILY_B_JWT" \
  --output-json .mura/reports/security_smoke.json
```
**Expected Output**:
- Exit code: `0`
- Unauthenticated requests rejected (`PASS`)
- Cross-family BOLA access returns 404 (`PASS`)
- Host/Origin spoofing rejected (`PASS`)

### Step 6: Load & Worker Benchmark
Run the staging load harness:
```bash
python scripts/ops/run_load_test.py \
  --base-url "http://staging-api.internal:8000" \
  --concurrency 10 \
  --duration 30 \
  --benchmark-workers \
  --db-url "$DATABASE_URL" \
  --output-json .mura/reports/load_test.json
```
**Expected Output**:
- Exit code: `0`
- Zero 5xx server errors
- Capacity claim labeled as `TEST PROFILE`

---

## 4. Handling Blocked External Dependencies

If staging cloud accounts are not yet provisioned:
1. **Clerk Authentication**: When live Clerk JWTs are unavailable, run security smoke without `--family-a-token`. The tool will test unauthenticated and malformed token rejections and record BOLA as `BLOCKED (No credentials provided)` without failing the pipeline.
2. **Supabase Storage**: If remote Supabase credentials are missing, specify `--audio-dir` pointing to the local staging volume.
3. **Kaggle GPU Worker**: Keep Kaggle worker in mock/synthetic mode for staging validation. Live promotion remains blocked until dedicated GPU credentials are supplied.
