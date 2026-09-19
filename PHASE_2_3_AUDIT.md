# MURA Phase 2.3 Reliability, Security, Privacy & Abuse Hardening — Audit

## 1. Baseline Verification Status
Prior to applying Phase 2.3 hardening, all syntax collisions and baseline compilation issues across both backend and frontend were resolved and verified:
- **Backend Compilation**: `python -m compileall -q src/mura apps tests` passed with 0 errors.
- **Backend Test Suite**: `python -m pytest` passed **1,208 / 1,208 tests (100%)** in 124.93s.
- **Frontend Test Suite**: `npm test -- --run` passed **478 / 478 tests (100%)** across 42 test suites.
- **Frontend Typecheck**: `npx tsc --noEmit` passed with 0 errors.
- **Frontend Production Build**: `npm run build` compiled cleanly and generated all static and dynamic routes.

## 2. Hardening Scope & Implementation Plan
The system is now ready for comprehensive hardening across reliability, abuse protection, security, privacy, and release safety:

### Part A: Job Reliability & Central Failure Classification
- Create `src/mura/reliability/failures.py`:
  - Enums `FailureDisposition` (`RETRY`, `TERMINAL`) and `FailureCategory`.
  - Structured classification of provider HTTP errors (429, 5xx, timeouts vs 401/403/invalid request), network timeouts, storage failures, and deterministic validation failures.
  - Parsing of `Retry-After` headers and exponential backoff with jitter calculation.
- Update `src/mura/storage/book.py`:
  - Fix `claim_next_job` to filter `BookJobRow.attempts < BookJobRow.max_attempts` directly in SQL so exhausted jobs are never claimed.
  - Add `defer_job(job_id, next_attempt_at, error_code)`.
- Update `src/mura/orchestration/books.py`:
  - Integrate failure classifier into `_process_job`.
  - When retryable and attempts < max_attempts, defer job for exponential delay or `Retry-After`.
  - When terminal or attempts >= max_attempts, fail job and book permanently.

### Part B: Book Queue Monitoring & Stuck Detection
- Extend `src/mura/monitoring.py`:
  - Add `BookQueueHealth` model (queued, running, stalled, oldest queued seconds, failed in last hour).
  - Add SQL aggregate query for book queue metrics.
- Extend `apps/api/operations.py`:
  - Include book queue health in privileged `/v1/operations/monitoring` response alongside recording queue metrics.

### Part C: Stronger Evidence & Relationship Gates
- In `src/mura/domain/book_models.py`:
  - Define `ChapterRelationshipAssertion` (`subject_person_id`, `relation`, `object_person_id`, `text_span`).
  - Add `relationship_assertions: list[ChapterRelationshipAssertion]` to `ChapterDraft`.
- In `src/mura/book/chapter_gates.py`:
  - Validate assertions against snapshot relationships + normalized inverses (e.g. father <-> child, brother <-> brother).
  - Block ungrounded kinship claims with `BLOCKER`.
  - Harden evidence gate: 0 evidence coverage -> `BLOCKER`; < 0.5 coverage -> `ERROR`.

### Part D: Durable Quotas & Abuse Protection
- In `src/mura/config.py`:
  - `book_max_active_per_family = 1`
  - `book_max_created_per_family_per_day = 3`
- Create `src/mura/quotas.py`:
  - `BookQuotaService.check_and_lock_family(session, family_id)`:
  - Executes `SELECT family_id FROM families WHERE family_id = :id FOR UPDATE` to prevent concurrent creation races.
  - Enforces active book limit (409 Conflict with `book_generation_already_active`).
  - Enforces 24-hour rate limit (429 Too Many Requests with `book_daily_limit_reached`).
- Wire into `create_family_book` and `regenerate_family_book`.

### Part E: HTTP Security Headers, CORS, CSP
- Create `apps/api/security_headers.py`:
  - Security headers middleware adding `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `X-Frame-Options: DENY`, `Permissions-Policy: camera=(), geolocation=(), payment=(), usb=()`.
  - Note: Microphone is strictly allowed for self (`microphone=(self)`) on frontend since MURA is a voice recorder.
- Add `TrustedHostMiddleware` configuration in API.
- Harden Next.js security headers and CSP in `MURA-app/next.config.ts`.
- Ensure error responses never leak internal stack traces, DB keys, or storage paths.

### Part F: Privacy Deletion Lifecycle & Export
- Add `DELETE /v1/families/{family_id}/recordings/{recording_id}`:
  - Deletes audio from private storage, cascades DB records.
- Add `DELETE /v1/families/{family_id}/books/{book_id}`:
  - Cancels running jobs, deletes private PDF/EPUB artifacts from storage, deletes book records.
- Add `DELETE /v1/families/{family_id}`:
  - Requires confirmation payload `{"confirm_family_id": "fam_..."}`. Deletes all audio, artifacts, and DB records.
- Add `GET /v1/families/{family_id}/privacy/export`:
  - Exports full structured JSON of all family memories, entities, relationships, stories, and book metadata.

### Part G: Dependency Reproducibility & Migrations
- Verify `alembic heads` has exactly 1 head.
- In `MURA-app/vercel.json`, ensure `"installCommand": "npm ci"`.
- Run `uv lock` in `Mura_project` or document reproducible lockfile state.

### Part H: CI Workflows
- Create root `.github/workflows/backend.yml` and `.github/workflows/frontend.yml` running linting, typechecking, and tests.

### Part I: Production Documentation
- Author `RELIABILITY.md`, `PRIVACY_LIFECYCLE.md`, `SECURITY.md`.
- Update `MONITORING.md` and `RAILWAY_DEPLOYMENT.md`.

