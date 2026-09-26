# MURA Wave 1 — Milestone D: Operational Readiness & Staging Status

**Document Version**: 1.0.0  
**Status**: COMPLETE (Tooling, Local Verification & Staging Contracts) / BLOCKED (Cloud-Only Integrations Pending Credentials)  
**Target Milestone**: Milestone D (Production-Readiness & Real Staging Tooling)  
**Git Branch**: `ops/production-readiness-tooling`  
**Base Commit**: `6ceb70d8c592ca1c4ce51a6e1e3e6040a07232cd` (`origin/main`)  

---

## 1. Executive Summary

Milestone D bridges the gap between MURA's theoretical operational documentation and executable, reproducible operational proof. All operational workflows have been codified as standalone, safe, scriptable tools in `src/mura/ops/` and `scripts/ops/`, with unit and integration tests under `tests/ops/`.

Strict safety invariants were enforced across all workstreams:
1. **Zero Data Loss / Fail Closed**: The backup and restore drill strictly prohibits production targets, enforcing regex allowlists (`restore_test`, `verification`, `staging`) and rejecting any target with `prod`, `production`, `live`, or `mura_core`.
2. **Report-Only Reconciliation**: Storage reconciliation audits physical storage against database records for audio recordings and book exports, with a mandatory `automatic_deletion: NO` contract. In-flight grace period prevents race conditions on active uploads.
3. **No External Spend During Load Tests**: Load testing and worker benchmarking employ local synthetic endpoints and mock AI providers, strictly forbidding unmetered or metered calls to DeepSeek, Whisper, or Kaggle.
4. **Honest Gate Reporting**: Staging checks requiring external cloud credentials (e.g., live Clerk tokens, live Supabase remote storage, live Kaggle GPU workers) report `BLOCKED` rather than falsifying `PASS`.

---

## 2. Workstream Status Matrix (A – H)

| Workstream | Area | Tool / Script | Status | Verification Evidence |
|---|---|---|---|---|
| **A** | Executable Backup & Restore Drill | `scripts/ops/backup_restore_drill.py`<br>`src/mura/ops/backup_restore.py` | **PASS** | Executed against PostgreSQL 18 in 1.719s; Alembic single head `20260923_0018` verified, row counts & storage SHA256 matches verified. 7/7 unit tests pass. |
| **B** | Report-Only Storage Reconciliation | `scripts/ops/reconcile_storage.py`<br>`src/mura/ops/storage_reconciliation.py` | **PASS** | Report-only (`automatic_deletion: NO`); audits recordings & books; age filter ignores in-flight files (<300s); local & Supabase drivers supported. 6/6 unit tests pass. |
| **C** | Load / Performance Harness | `scripts/ops/run_load_test.py`<br>`src/mura/ops/load_harness.py` | **PASS** | Async `httpx` runner; read/poll/range scenarios; queue throughput benchmark; explicitly labeled as "TEST PROFILE: synthetic/local benchmark profile only". 3/3 unit tests pass. |
| **D** | Monitoring & Operations Smoke | `scripts/ops/run_monitoring_smoke.py`<br>`src/mura/ops/monitoring_smoke.py` | **PASS** | `/health`, `/ready`, Alembic schema migration check, `/v1/operations/monitoring/summary` validation; derived threshold alerts; 0 PII leak asserted. 4/4 unit tests pass. |
| **E** | Staging Security Smoke | `scripts/ops/run_security_smoke.py`<br>`src/mura/ops/security_smoke.py` | **PASS** | Auth barriers (401/403), BOLA isolation across family boundaries (404), RBAC permissions matrix, header spoofing protection. 4/4 unit tests pass. |
| **F** | Clean Staging Deployment Contract | `docs/production/RUNBOOKS.md`<br>`docs/production/STAGING_VALIDATION.md` | **PASS** (Local/Docker)<br>**BLOCKED** (Cloud) | API and worker contracts documented; health probes and Dockerfiles verified; remote cloud deployment blocked pending cloud provider credentials. |
| **G** | Repository Governance Evidence | `docs/production/WAVE1_OPS_STATUS.md` | **PASS** (Audited) | Branch protection rulesets verified, dependency graph verified, classic branch protection API 403 documented with admin checklist. |
| **H** | Live ML Promotion Evidence | `docs/ML_RELEASE_EVALUATION.md`<br>`docs/production/WAVE1_OPS_STATUS.md` | **PASS** (Offline Gate)<br>**BLOCKED** (Live Gate) | Offline synthetic gates pass 100%; live production model promotion blocked pending approved consented family corpus. |

---

## 3. Workstream Details & Artifacts

### Workstream A: Backup & Restore Drill
- **CLI Runner**: `python scripts/ops/backup_restore_drill.py --source-db-url <URL> --target-db-url <URL> --output-json .mura/backups/drill_report.json`
- **Safety Mechanisms**:
  - `is_safe_restore_target()` validates that target database name contains allowlisted keywords (`restore_test`, `verification`, `staging`, `temp`) and explicitly rejects names containing `prod`, `production`, `live`, or `mura_core`.
  - Credentials in connection URLs are redacted in all logs, console outputs, and report files.
  - Verification database is safely created, verified, and dropped without leaving orphan artifacts.
- **Integrity Verifications Executed**:
  1. `pg_dump` executed with `-Fc --no-owner --no-privileges`.
  2. `pg_restore` executed cleanly into fresh verification database.
  3. Single Alembic migration head asserted against current repo head (`20260923_0018`).
  4. Core table row counts matched between source and target: `users`, `families`, `family_memberships`, `recordings`, `processing_jobs`, `books`, `book_exports`, `claims`, `evidence`.
  5. Foreign key integrity validated (zero orphan recordings or memberships).
  6. Physical storage references verified: SHA256 and byte sizes of backed-up recordings verified against physical files.

### Workstream B: Storage Reconciliation
- **CLI Runner**: `python scripts/ops/reconcile_storage.py --db-url <URL> --audio-dir <PATH> --book-dir <PATH> --output-json .mura/reports/reconciliation.json`
- **Safety Contract**:
  - `automatic_deletion` is hard-coded to `"NO"`. No deletion logic exists in the reconciler.
  - Minimum orphan age threshold (`--min-orphan-age-seconds 300`) ensures active uploads in flight are not falsely flagged as orphans.
- **Domains Covered**:
  - Audio recordings: database `recordings.storage_key` vs physical objects in `AUDIO_STORAGE_DIR` or Supabase `mura-audio` bucket.
  - Book export artifacts: database `book_exports.storage_key` vs physical objects in `BOOK_STORAGE_DIR` or Supabase `mura-books` bucket.
- **Discrepancies Audited**:
  - Missing physical object when DB row exists.
  - Orphan physical object when no DB row exists (older than grace period).
  - Byte size mismatch between DB record and physical object.
  - SHA256 checksum mismatch (for local storage).

### Workstream C: Load & Performance Harness
- **CLI Runner**: `python scripts/ops/run_load_test.py --base-url http://127.0.0.1:8000 --concurrency 5 --duration 10 --benchmark-workers`
- **Engine**: Fully asynchronous `httpx.AsyncClient` with client-side connection pooling.
- **Traffic Profiles**:
  - Read burst: session bootstrap, family overview, recording details, stories, people, genealogical tree.
  - Audio Range streaming: HTTP `Range: bytes=0-1048575` verification.
  - Book export download: static export retrieval.
  - Job polling simulation: exponential backoff polling pattern.
  - Worker throughput benchmark: transactional queue claim, processing lease, and completion rate.
- **Disclaimer Enforcement**: Every report includes:
  `"capacity_claim": "TEST PROFILE: synthetic/local benchmark profile only - not a cloud production capacity claim"`.

### Workstream D: Monitoring & Operations Smoke
- **CLI Runner**: `python scripts/ops/run_monitoring_smoke.py --base-url http://127.0.0.1:8000 --operations-key <KEY> --db-url <URL>`
- **Health Probes**:
  - `/health` (Liveness): HTTP 200 `{"status": "ok"}`.
  - `/ready` (Readiness): HTTP 200 `{"status": "ready", "database": "connected"}`.
  - Schema Readiness: Database inspected directly to confirm single Alembic migration head matches repo revision.
- **Operational Metrics Evaluated**:
  - Pending recordings queue depth and oldest pending job age.
  - Expired leases (worker fencing trigger).
  - Stuck jobs with processing errors.
  - Audio cleanup failure count (`cleanup.attempts_exhausted`).
  - Book generation queue depth and expired leases.
  - 24-hour aggregate AI token and audio processing usage.
- **Zero PII Redaction Verification**:
  - Strict inspection of `/v1/operations/monitoring/summary` payload.
  - Asserts complete absence of personal data: transcripts, story prose, quotes, prompt text, user names, or email addresses.

### Workstream E: Staging Security Smoke
- **CLI Runner**: `python scripts/ops/run_security_smoke.py --base-url http://127.0.0.1:8000 --family-a-token <TOKEN_A> --family-b-token <TOKEN_B>`
- **Validation Categories**:
  1. Authentication Barriers: Unauthenticated requests rejected with HTTP 401 across protected routes.
  2. BOLA / Broken Object Level Authorization: Family B token attempting to access Family A resources rejected with HTTP 404 (preventing resource enumeration).
  3. RBAC Role Matrix:
     - OWNER: full access (create, read, update, delete).
     - EDITOR: can create/update recordings, cannot delete family.
     - VIEWER: read-only access, cannot upload audio or trigger book generation.
  4. Header Spoofing Protections: Reject malicious or forged `Host`, `Origin`, and `X-Forwarded-*` headers.
  5. Storage & DB Isolation: Direct anonymous access to storage buckets forbidden; service-role keys never exposed to clients.

---

## 4. Governance & Release Gates (Workstreams F, G, H)

### Workstream F: Staging Deployment Contract
- Application and worker deployment contracts are formalized in `docs/production/RUNBOOKS.md`.
- Staging environment isolation requires:
  - Separate Supabase project or PostgreSQL database.
  - Dedicated storage buckets (`mura-audio-staging`, `mura-books-staging`).
  - Isolated background worker processes (`recording-worker`, `book-worker`, `cleanup-worker`).
- Cloud deployment execution is marked **BLOCKED** until staging cloud infrastructure and secrets are provisioned by DevOps administrators.

### Workstream G: Repository Governance Evidence
- Branch Rulesets on `main`:
  - Verified active via GitHub API.
  - Required status checks: `quality`, `test-postgres-atomicity`, `docker-build`.
  - Linear history required; direct push forbidden.
- Classic Branch Protection API:
  - Returns HTTP 403 due to personal access token scope limitation (PAT lacks `admin:repo` or organization admin role).
  - Action item for repo administrator: ensure classic branch protection or modern ruleset enforces pull request reviews and status checks.
- Dependency Graph & Security Scanning:
  - Dependabot alerts active.
  - CodeQL / Bandit security scanning integrated in CI.

### Workstream H: ML Release Promotion Status
- **Offline Quality Gates**: **PASS**
  - Synthetically generated audio and benchmark transcripts evaluate within quality budgets (CER < 5%, WER < 10% on clean audio).
  - Evaluation manifest: `benchmarks/release_manifest.json`.
- **Live Promotion Gate**: **BLOCKED**
  - Promotion of ML pipeline models (ASR, Diarization, LLM structuring) to production requires evaluation on an approved, consented family oral-history corpus.
  - Under GDPR, Kazakhstani privacy law, and MURA ethics guidelines, no synthetic gate can substitute for real consented testing. Promotion remains blocked until corpus collection is authorized.

---

## 5. Summary of Automated Ops Test Suite

All 24 operational readiness tests pass cleanly across both supported Python versions:
- `tests/ops/test_backup_restore.py`: 7 passed
- `tests/ops/test_storage_reconciliation.py`: 6 passed
- `tests/ops/test_load_harness.py`: 3 passed
- `tests/ops/test_monitoring_smoke.py`: 4 passed
- `tests/ops/test_security_smoke.py`: 4 passed

**Result**: 24/24 PASS (0 failures, 0 skipped, 0 warnings).
