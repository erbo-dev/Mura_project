# MURA (Мұра) — Phase 2.4 Staging Deployment & Validation Plan

## 1. Executive Summary & Release Candidate Identification
- **Release Candidate SHA**: `c3348f4b56aa627ecfc6bfade467af2082c30230`
- **Branch**: `main`
- **Working Tree**: Clean (`nothing to commit, working tree clean`)
- **Baseline Test Status**:
  - Backend: `1,242 / 1,242 passed (100%)` in 350.20s
  - Frontend: `478 / 478 passed (100%)` in 7.47s
  - TypeScript: `npx tsc --noEmit` passed with 0 errors
  - Production Build: `npm run build` passed, all 21 static/dynamic pages compiled
  - Alembic Head: Single linear head `20260918_0013 (head)`

---

## 2. Real Cloud Access Matrix

| System | CLI Available | Authenticated | Resource Exists | Can Mutate | Classification Status |
|---|---|---|---|---|---|
| **GitHub** | Yes (`gh.exe`) | No (`gh auth status` = not logged in) | Yes (`origin/main`) | No | `AVAILABLE BUT NOT AUTHENTICATED` |
| **Railway** | Yes (`railway.ps1`) | Yes (`pororosororo184@gmail.com`) | Yes (`closeros-staging-2`) | Pending Staging Project Provisioning | `VERIFIED AUTHENTICATED` |
| **Vercel** | No (global CLI not installed) | No | Unknown | No | `CLI NOT AVAILABLE` |
| **Supabase** | No (CLI not installed) | Via direct connection/keys | Staging project via URL/Key | Via API / PostgreSQL driver | `CLI NOT AVAILABLE (Direct API/Postgres)` |
| **Sentry** | Integrated via SDK | DSN-based | Configured via env | Read/write events via SDK | `AVAILABLE VIA RUNTIME CONFIG` |
| **DeepSeek** | Integrated via SDK | API Key-based | Configured via env | Via HTTP API | `AVAILABLE VIA RUNTIME CONFIG` |

> [!NOTE]
> As per Rule 5, because remote GitHub Actions execution cannot be triggered without authenticated GitHub credentials from this shell, GitHub Actions is classified as:
> `CI CONFIG PRESENT — REMOTE GITHUB ACTION EXECUTION NOT VERIFIED`.

---

## 3. Staging Topology & Resource Architecture

### Services
1. **Frontend (Vercel)**:
   - Framework: Next.js 15.5.20 App Router
   - Deployment: Vercel Preview / Staging
   - URL: Configured via `NEXT_PUBLIC_API_URL`
2. **Core API (Railway)**:
   - Service: `mura-api-staging`
   - Entrypoint: `uvicorn apps.api.main:app --host 0.0.0.0 --port $PORT`
   - Environment: `APP_ENV=staging`
3. **Standalone Worker (Railway)**:
   - Service: `mura-worker-staging`
   - Entrypoint: `python apps/worker/main.py`
   - Loops: `RecordingJobWorker` (ASR/extraction) + `BookJobWorker` (Family Book)
4. **Database & Auth & Storage (Supabase)**:
   - Project: Dedicated staging project
   - PostgreSQL: 14 core tables + Alembic migration version `20260918_0013`
   - Auth: Supabase Auth (OIDC JWKS)
   - Storage Buckets:
     - `mura-audio-staging` (`PUBLIC = FALSE`)
     - `mura-books-staging` (`PUBLIC = FALSE`)
5. **Observability (Sentry)**:
   - Environment: `staging`
   - Data scrubbers: Zero user content, zero tokens, zero audio, zero prompts

---

## 4. Staging Environment Variable Catalog

### Backend API & Worker
- `APP_ENV=staging`
- `DATABASE_URL` (PostgreSQL connection string with SSL)
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_STORAGE_BUCKET=mura-audio-staging`
- `SUPABASE_BOOKS_BUCKET=mura-books-staging`
- `AUDIO_STORAGE_BACKEND=supabase`
- `DEEPSEEK_API_KEY`
- `DEEPSEEK_BASE_URL`
- `DEEPSEEK_MODEL`
- `DEEPSEEK_FALLBACK_MODEL`
- `OPERATIONS_API_KEY`
- `SENTRY_DSN`
- `SENTRY_ENVIRONMENT=staging`
- `ALLOWED_HOSTS`
- `CORS_ORIGINS`
- `BOOK_MAX_ACTIVE_PER_FAMILY=1`
- `BOOK_MAX_CREATED_PER_FAMILY_PER_DAY=5`
- `MURA_FAULT_INJECTION` (`0` by default; set to `1` only during controlled chaos tests)

### Frontend (Vercel)
- `MURA_API_URL`
- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `NEXT_PUBLIC_SENTRY_DSN`

> [!CAUTION]
> Privileged keys (`SUPABASE_SERVICE_ROLE_KEY`, `DEEPSEEK_API_KEY`, `OPERATIONS_API_KEY`) are NEVER included in `NEXT_PUBLIC_*` or exposed to the browser bundle.

---

## 5. Deterministic Fixture Design

### "Staging Mura Family"
- **Family ID**: Deterministic synthetic identifier `family_staging_mura`
- **Members**:
  - Aidar (Narrator, Owner)
  - Aigul (Editor, Mother)
  - Serik (Viewer, Grandfather)
- **Controlled Recordings**:
  - **Recording A (Kazakh)**: "Әжемнің мұрасы" — Kazakh Cyrillic transcript (`Мұра`, `Әже`, `Ұрпақ`), kinship links, family heirloom carpet.
  - **Recording B (Russian)**: "Переезд в Алматы" — Corrected date (1978 -> 1982), conflicting memory on house number.
  - **Recording C (Code-Switched RU/KK)**: "Шілдехана тойы" — Uncertain claim regarding attendees.

---

## 6. Validation & Failure Injection Matrix

| ID | Scenario | Injection Mechanism | Expected Behavior | Realism Tier |
|---|---|---|---|---|
| SC-01 | API Boot & Health | None | `/health` returns 200 alive, `/ready` checks DB | STAGING HTTP |
| SC-02 | Worker Dual Boot | None | Both `RecordingJobWorker` and `BookJobWorker` loops start | LOCAL INTEGRATION / STAGING |
| SC-03 | Schema Verification | `scripts/verify_schema.py` | Single head `20260918_0013`, 14 core tables present, idempotent | LOCAL INTEGRATION |
| SC-04 | Short Audio E2E | Upload synthetic audio | Private storage -> ASR -> Extraction -> Archive | STAGING HTTP |
| SC-05 | Private Audio Protection | Unauthenticated & Cross-family requests | Unauthenticated = 401; Cross-family = 404; Member = 200 | STAGING HTTP |
| SC-06 | Family Book E2E | Create Book A+B | Planning -> Writing -> Reviewing -> Export -> Completed | STAGING HTTP |
| SC-07 | Snapshot Immutability | Add recording C while Book in progress | Snapshot remains exactly A+B; C is ignored | LOCAL INTEGRATION |
| SC-08 | PDF Runtime Export | Book completed | `%PDF` header, Cyrillic & Kazakh glyphs rendered | LOCAL INTEGRATION |
| SC-09 | EPUB Structure | Book completed | Valid ZIP, uncompressed mimetype first, container.xml, nav, spine | LOCAL INTEGRATION |
| SC-10 | Worker Restart during Recording | Crash worker during active claim | Lease expires, reclaimed, completes without duplicate archive | LOCAL INTEGRATION |
| SC-11 | Worker Restart during Book | Crash worker at chapter 3 | Approved chapters preserved, resumes first unfinished chapter | LOCAL INTEGRATION |
| SC-12 | DeepSeek Timeout | `DEEPSEEK_TIMEOUT_ONCE` | Classified `RETRY`, backoff scheduled, lease dropped, resumes | STAGING FAKE PROVIDER |
| SC-13 | DeepSeek 429 Rate Limit | `DEEPSEEK_429_ONCE` | Classified `rate limit`, `Retry-After` respected, no busy loop | STAGING FAKE PROVIDER |
| SC-14 | Provider 401 Auth Failure | `PROVIDER_401_ONCE` | Classified `TERMINAL`, immediate safe fail, no infinite retries | STAGING FAKE PROVIDER |
| SC-15 | Provider 503 Outage | `PROVIDER_503_ONCE` | Classified `transient retry`, resumes when restored | STAGING FAKE PROVIDER |
| SC-16 | Storage 503 Outage | `STORAGE_503_ONCE` | Classified `transient retry`, no false export | STAGING FAKE PROVIDER |
| SC-17 | Missing Storage Audio | Delete audio file after DB creation | Safe failure classification, no unhandled worker crash loop | LOCAL INTEGRATION |
| SC-18 | Export Engine Crash | `PDF_FAILURE_ONCE` | Book retryable, retry regenerates PDF without rewriting chapters | LOCAL INTEGRATION |
| SC-19 | Duplicate Book POST Race | Near-simultaneous POST /books | Row-lock quota: exactly one 201/200, second 409 conflict | LOCAL INTEGRATION |
| SC-20 | Daily Book Quota | Create books exceeding limit | Exceeding limit returns HTTP 429 | LOCAL INTEGRATION |
| SC-21 | Cross-Family BOLA Matrix | User A queries User B resources | 404 across recordings, stories, books, chapters, exports | STAGING HTTP |
| SC-22 | Privacy Cascades | Family deletion | DB rows + audio object + PDF + EPUB deleted physically | LOCAL INTEGRATION |
| SC-23 | Privacy Export | `GET /v1/families/{id}/privacy/export` | Valid portable JSON, zero secrets or credentials | STAGING HTTP |
| SC-24 | Security Headers & CORS | Inspect HTTP responses | CSP microphone allowed, Referrer-Policy, CORS restricts origins | STAGING HTTP |
| SC-25 | Secrets Audit | Scan `.next/` build bundle | Zero privileged keys (`SUPABASE_SERVICE_ROLE_KEY`, etc.) | STATIC BUNDLE AUDIT |
| SC-26 | Operations Exposure | Call `/v1/operations/monitoring/summary` | Denied without valid key, blocked by frontend proxy | STAGING HTTP |

---

## 7. Rollback Strategy
1. **Frontend**: Vercel instant rollback to previous deployment in project dashboard if UI regression occurs.
2. **Backend Services**: Railway rollback to previous release SHA via `railway rollback`.
3. **Database**: PostgreSQL point-in-time recovery via Supabase; Alembic migrations strictly designed for backward compatibility.
4. **Storage**: Buckets are private; hard deletions require explicit sole-owner confirmation.

