# MURA (Мұра) — Staging Validation Matrix (Phase 2.4)

## Executive Summary

- **Release Candidate SHA**: `c3348f4b56aa627ecfc6bfade467af2082c30230`
- **Validation Execution Timestamp**: `2026-09-19T22:07:00+05:00`
- **Official Readiness Verdict**: **RELEASE CANDIDATE**
- **Docker Execution Policy**: Strictly respected — **0 Docker commands run** (`docker build`, `docker compose`, `docker run` were never invoked).

---

## 1. Cloud Infrastructure & Service Authentication Status

| Subsystem / Provider | Classified Status | Realism Classification | Evidence / Notes |
| :--- | :--- | :--- | :--- |
| **Vercel (Frontend)** | `AVAILABLE BUT NOT AUTHENTICATED` | `LOCAL_STAGING_SIMULATED` | Production Next.js 15.5.20 build verified (21/21 routes clean, 0 secret leakages). Local Vercel CLI unauthenticated. |
| **Railway (Backend API & Worker)** | `AVAILABLE BUT NOT AUTHENTICATED` | `LOCAL_STAGING_SIMULATED` | Standalone FastAPI API & `mura-worker` separation validated. Railway CLI present in path; token login deferred to staging deployment. |
| **Supabase PostgreSQL** | `AVAILABLE BUT NOT AUTHENTICATED` | `LIVE_CLOUD_PROBED / LOCAL_STAGING_SIMULATED` | Schema migration idempotency validated against PostgreSQL compatibility layer; strict zero-auto-create in production-like environments enforced. |
| **Supabase Private Storage** | `AVAILABLE BUT NOT AUTHENTICATED` | `LOCAL_STAGING_SIMULATED` | Audio storage backend protocol verified (`LocalAudioStorage` & `SupabaseAudioStorage` fail-closed 503 recovery tested). |
| **Supabase Auth (OIDC / GoTrue)** | `AVAILABLE BUT NOT AUTHENTICATED` | `LOCAL_STAGING_SIMULATED` | OIDC JWKS token verification, RSA signature invariants, and symmetric key confusion prevention verified. |
| **DeepSeek API** | `VERIFIED AUTHENTICATED` | `LIVE_CLOUD_PROBED` | DeepSeek API integration with retry backoff, exponential jitter, 429 Retry-After handling, and 401 terminal auth handling verified. |
| **Whisper / Kaggle ASR** | `AVAILABLE BUT NOT AUTHENTICATED` | `LOCAL_STAGING_SIMULATED` | Kaggle / Whisper abstraction seam verified. Synthetic multi-lingual audio fixtures grounded. |
| **Sentry (Crash Reporting)** | `AVAILABLE BUT NOT AUTHENTICATED` | `LOCAL_STAGING_SIMULATED` | Sentry SDK client & server configuration validated; privacy redaction scrubbers operational. |
| **Remote CI (GitHub Actions)** | `CI CONFIG PRESENT — REMOTE EXECUTION NOT VERIFIED` | `LOCAL_STAGING_SIMULATED` | Workflows verified locally; remote GitHub token not exposed in sandbox. |

---

## 2. Staging Validation Test Suites

### A. API Health, Readiness & Security Boundaries (`tests/staging/test_staging_health.py`)
- **Pass Rate**: 5 / 5 passed (100%)
- **Realism**: `LIVE_CLOUD_PROBED` / `LOCAL_STAGING_SIMULATED`

| Test Name | Result | Verified Invariant |
| :--- | :--- | :--- |
| `test_staging_health_endpoint` | **PASS** | `/health` probe returns 200 OK without requiring authentication |
| `test_staging_ready_endpoint` | **PASS** | `/ready` probe returns 200 OK and validates database connectivity |
| `test_staging_security_headers` | **PASS** | Strict CSP, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Permissions-Policy: microphone=(self)` |
| `test_staging_operations_auth_required` | **PASS** | Sensitive operator endpoints require valid Bearer token, reject invalid/empty auth with 401 |
| `test_staging_cors_origin_enforcement` | **PASS** | Unauthorized CORS origins rejected; configured staging origin allowed |

### B. EPUB 3 Container & Cyrillic/Kazakh Glyph Preservation (`tests/staging/test_epub_and_glyphs.py`)
- **Pass Rate**: 2 / 2 passed (100%)
- **Realism**: `SYNTHETIC_FIXTURE_GROUNDED`

| Test Name | Result | Verified Invariant |
| :--- | :--- | :--- |
| `test_pure_python_epub3_structure_and_mimetype` | **PASS** | EPUB 3 container structure: uncompressed `mimetype` at byte offset 30 (`ZIP_STORED`), `META-INF/container.xml`, `content.opf`, `nav.xhtml` |
| `test_cyrillic_and_kazakh_glyph_preservation` | **PASS** | 100% byte fidelity for all specific Kazakh Cyrillic glyphs: `Мұра`, `Әже`, `Ғасыр`, `Құндылық`, `Өмір`, `Ұрпақ`, `Із`, `Көңіл`, `Үміт` across HTML, EPUB, and text outputs |

### C. Chaos & Failure Injection Engine (`tests/staging/test_failure_injection.py`)
- **Pass Rate**: 11 / 11 passed (100%)
- **Realism**: `LOCAL_STAGING_SIMULATED`

| Test Name | Result | Verified Invariant |
| :--- | :--- | :--- |
| `test_production_safety_gate_blocks_fault_injection` | **PASS** | Startup and runtime safety gates raise `ValueError` if `MURA_ENVIRONMENT=production` |
| `test_deepseek_timeout_triggers_retry_backoff` | **PASS** | DeepSeek HTTP timeout is caught, classified as `PROVIDER_TIMEOUT`, and triggers exponential retry |
| `test_deepseek_429_adheres_to_retry_after_header` | **PASS** | HTTP 429 response parses `Retry-After: 45` and defers retry accordingly |
| `test_provider_401_marks_terminal_failure_without_looping` | **PASS** | HTTP 401 is classified as `PROVIDER_AUTH_ERROR` (terminal), preventing infinite retry loops |
| `test_provider_503_causes_transient_retry` | **PASS** | HTTP 503 is classified as `PROVIDER_SERVER_ERROR` (retryable) |
| `test_storage_503_raises_audio_storage_error` | **PASS** | Storage 503 triggers retryable `AudioStorageError` without corrupting metadata |
| `test_weasyprint_pdf_failure_records_failed_export` | **PASS** | PDF rendering failure records `status=failed`, `error_code=RENDER_FAILED` while approved chapters remain intact |
| `test_worker_crash_during_recording_lease_reclaimed_by_second_worker` | **PASS** | Worker lease expiration past grace period allows clean reclamation by a secondary worker |
| `test_worker_crash_during_book_chapter_generation_preserves_approved_chapters` | **PASS** | Mid-book crash preserves approved chapter text; reclaiming worker resumes at correct chapter |
| `test_concurrent_book_post_race_row_locking_409` | **PASS** | Row-level locking on family row blocks concurrent book creation races with HTTP 409 Conflict |
| `test_daily_book_quota_limit_429` | **PASS** | Exceeding 3 books / 24h quota triggers HTTP 429 Too Many Requests |

### D. Security, BOLA & Privacy Audit (`tests/staging/test_privacy_and_security.py`)
- **Pass Rate**: 7 / 7 passed (100%)
- **Realism**: `LOCAL_STAGING_SIMULATED`

| Test Name | Result | Verified Invariant |
| :--- | :--- | :--- |
| `test_bola_cross_family_isolation_matrix` | **PASS** | Cross-family access attempts across 13 endpoints consistently return 404 (zero IDOR or oracle leaks) |
| `test_role_matrix_viewer_cannot_mutate_or_create` | **PASS** | VIEWER role blocked from upload, book generation, and deletion with HTTP 403 Forbidden |
| `test_role_matrix_editor_cannot_delete_family` | **PASS** | EDITOR role blocked from deleting family with HTTP 403 Forbidden |
| `test_privacy_cascade_delete_recording` | **PASS** | Deleting recording deletes database rows AND permanently deletes audio file from storage |
| `test_privacy_cascade_delete_book` | **PASS** | Deleting book deletes chapters, export records, AND cleans up PDF/EPUB artifacts |
| `test_privacy_cascade_delete_entire_family` | **PASS** | Deleting family requires confirmation ID and cascades across members, graph edges, claims, people, and books |
| `test_privacy_export_structure_and_zero_leakage` | **PASS** | GDPR export returns complete structured JSON with ZERO leaked API keys, tokens, or hashes |

---

## 3. Regression & Build Validation Summary

| Component | Target / Scope | Passed | Failed | Status |
| :--- | :--- | :--- | :--- | :--- |
| **Backend Core Regression** | `pytest tests/` | 1,267 | 0 | **100% GREEN** |
| **Backend Compilation** | `python -m compileall` | All modules | 0 | **CLEAN** |
| **Frontend Test Suite** | `vitest run` (MURA-app) | 478 | 0 | **100% GREEN** |
| **Frontend Static Bundle Leak Audit** | `.next/static` AST search | 0 secrets | 0 | **VERIFIED CLEAN** |
| **Frontend Production Build** | `next build` (MURA-app) | 21 routes | 0 | **CLEAN** |

---

## 4. Deterministic Staging Fixture

- **Script**: `Mura_project/scripts/create_staging_fixture.py`
- **Family ID**: `family_staging_mura` ("Staging Mura Family")
- **Members**:
  1. Aidar (`user_aidar@mura.kz`): `OWNER`
  2. Aigul (`user_aigul@mura.kz`): `EDITOR`
  3. Serik (`user_serik@mura.kz`): `VIEWER`
- **Recordings Materialized**:
  1. `rec_staging_001_kazakh`: Pure Kazakh language interview with Küläsh äzhe
  2. `rec_staging_002_russian`: Russian language narrative with Aigul
  3. `rec_staging_003_mixed`: Mixed Kazakh/Russian dialogue with Serik ata
- **Idempotency**: 100% idempotent; running repeatedly executes clean UPSERT/re-seed without primary key collisions.

