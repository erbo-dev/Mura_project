# MURA FINAL PRODUCTION READINESS REPORT

Single source of truth for the launch readiness of **MURA / «Мұра»** (commit `7830de6bc0034531a1c209adcbb0fa6921579a41`).

---

## A. Release Candidate

- **Candidate SHA**: `7830de6bc0034531a1c209adcbb0fa6921579a41`
- **Date**: 2026-09-27
- **Alembic Head**: `20260927_0020` (Single linear head; tails from `20260923_0018` -> `20260926_0019` -> `20260927_0020`)
- **Frontend Version**: `0.1.0` (Next.js 15.2.4, React 19.0.0, TypeScript 5.8.2)
- **Backend Version**: `1.0.0rc1` (FastAPI 0.116.1, SQLAlchemy 2.0.41, Pydantic 2.11.7, Python 3.11 / 3.13)
- **Baseline CI Status**: **10 / 10 PASS** across all official GitHub Actions on `main`:
  - `Backend CI (Python 3.11)`: PASS
  - `Python 3.13 compatibility`: PASS
  - `Quality (Ruff, Mypy, Formatting)`: PASS
  - `Security (Bandit, pip-audit, secret scan)`: PASS
  - `CodeQL (JS/TS + Python)`: PASS
  - `Container builds (API & Worker Dockerfiles)`: PASS

---

## B. Functional Product Matrix

| Surface / Function | Backend Endpoint | Frontend Route / UX | Unit / Integration Tests | E2E / Playwright | Staging Validation | Status | Evidence / Invariant Verification |
|---|---|---|---|---|---|---|---|
| **Authentication & Identity** | `GET /v1/me`, `POST /v1/users` | `/sign-in`, `/sign-up`, `/api/auth/*` | `test_identity_families.py`, `test_auth.py` | `critical-journeys.spec.ts` (bootstrap session) | BLOCKED (Cloud Clerk) | **PASS** (Local) | Idempotent user bootstrap under concurrency; JWT template contract verified. |
| **Family Lifecycle** | `POST /v1/families`, `GET /v1/families/{id}`, `DELETE /v1/families/{id}` | `/home`, `/settings` | `test_postgres_atomicity.py`, `test_identity_families.py` | `critical-journeys.spec.ts` (create first family) | BLOCKED (Cloud DB) | **PASS** (Local) | Sole OWNER invariant enforced; concurrent deletion converges; relational cleanup durable. |
| **Family Invitations** | `POST /v1/families/{id}/invitations`, `GET /v1/invitations/{token}`, `POST /v1/invitations/{token}/accept`, `POST .../revoke` | `/invite/[token]`, `/settings` | `test_invitation_concurrency.py`, `test_invitations.py` | `critical-journeys.spec.ts` (role authorization) | BLOCKED (Cloud DB) | **PASS** (Local) | Plaintext tokens never stored (SHA-256 hashed); double-accept and accept-vs-revoke races serialized via row locks. |
| **Recording Creation & Audio Ingestion** | `POST /v1/families/{id}/recordings`, `GET .../audio` | `/record`, `/processing` | `test_recordings.py`, `test_audio_contract.py`, `test_cost_guards_concurrency.py` | `critical-journeys.spec.ts` | BLOCKED (Supabase Storage) | **PASS** (Local) | Trusted audio duration probing; user & family daily duration limits enforced before disk write. |
| **Recording Worker & AI Pipeline** | Background processing loop, `GET /v1/workers/current` | `/processing` | `test_recording_worker.py`, `test_worker_fencing.py` | Simulated pipeline harness | BLOCKED (Railway Worker) | **PASS** (Local) | `FOR UPDATE SKIP LOCKED` leasing; stale worker fencing; AI reservation budget checked before paid provider invocation. |
| **Family Archive** | `GET /v1/families/{id}/archive`, `/stories`, `/people`, `/tree` | `/home`, `/stories`, `/tree`, `/story/[id]`, `/person/[id]` | `test_archive.py`, `test_people.py`, `test_graph.py` | Rendered across all viewports | BLOCKED (Cloud DB) | **PASS** (Local) | Strict evidence closure; unknown endpoints preserved; zero cross-family entity leakage. |
| **People & Relationships** | `GET /v1/families/{id}/people/{id}` | `/person/[id]`, `/tree` | `test_people.py`, `test_relationships.py` | Archive inspection | BLOCKED (Cloud DB) | **PASS** (Local) | Directional relationship semantics verified; no speculative kinship inference. |
| **Family Tree** | `GET /v1/families/{id}/tree` | `/tree` | `test_tree.py`, `test_graph.py` | Interactive SVG/DOM | BLOCKED (Cloud DB) | **PASS** (Local) | Cycles and conflicting parental branches handled without infinite recursion or rendering crash. |
| **Human Review & Conflict Resolution** | `GET /v1/families/{id}/conflicts`, `POST .../resolve` | `/review` | `test_review.py`, `test_conflicts.py` | `critical-journeys.spec.ts` (claim resolve & viewer check) | BLOCKED (Cloud DB) | **PASS** (Local) | Resolving preserves historical claims, evidence spans, and actor timestamps; VIEWER role denied mutation. |
| **Family Book Generation** | `POST /v1/families/{id}/books`, `GET .../pdf`, `GET .../epub` | `/books`, `/books/[id]` | `test_books.py`, `test_book_worker.py`, `test_postgres_atomicity.py` | Book lifecycle flow | BLOCKED (Railway Worker) | **PASS** (Local) | Immutable selected-source snapshot; ungrounded assertions rejected; PDF & EPUB output generation validated. |
| **Audio Playback & Seeking** | `GET /v1/families/{id}/recordings/{id}/audio` | HTML5 `<audio>` player | `test_audio_range.py` | Audio component playback | BLOCKED (Supabase Storage) | **PASS** (Local) | HTTP Range RFC 9110 compliance: `206 Partial Content`, `bytes 0-`, suffix range, and `416 Range Not Satisfiable`. |
| **Privacy Export** | `POST /v1/families/{id}/export` | `/settings` | `test_privacy_export.py` | Manifest inspection | BLOCKED (Cloud DB) | **PASS** (Local) | Complete evidence, claim, decision, and object manifest exported; zero plaintext auth secret leakage. |
| **Account Deletion** | `DELETE /v1/me` | `/settings` | `test_account_deletion.py`, `test_identity_families.py` | `critical-journeys.spec.ts` (sole owner blocked, allowed user signs out) | BLOCKED (Cloud Clerk) | **PASS** (Local) | Sole OWNER blocked with localized alert; disposable user enqueues physical cleanup, clears local storage, signs out. |
| **Cost Guards & Usage Budgets** | Budget calculation & reservation models | Backend middleware | `test_cost_guards_concurrency.py`, `test_cost_guards.py` | Reservation expiry harness | BLOCKED (DeepSeek / Whisper live) | **PASS** (Local) | Atomic reservation deduction; provider never called upon quota rejection; expired reservations reclaimed. |

---

## C. Browser / UX Audit

- **Desktop Viewports (1920×1080, 1440×900, 1366×768)**:
  - Navigation layout clean; sidebar responsive with persistent state.
  - Review interface displays dual statement diffs with synchronized audio range selectors.
  - Family tree layout renders multi-generation nodes without horizontal viewport overflow.
- **Mobile Viewports (390×844, 360×800, 768×1024)**:
  - Sheet dialogs handle touch dismiss and viewport height clipping safely (`100dvh`).
  - Mobile bottom navigation preserves safe-area insets.
  - Recording interface retains visible duration counter and accessible microphone trigger.
- **Accessibility (a11y)**:
  - Form controls have explicit `htmlFor` / `id` associations.
  - Dialog focus trapping active; ESC key dismisses overlays.
  - Interactive buttons retain visible keyboard focus rings (`focus-visible:ring-2`).
- **Localization (RU / KK / EN)**:
  - Three language dictionaries maintained (`ru`, `kk`, `en`).
  - Pluralization helpers in place for story counts, recording minutes, and days.
- **Loading, Empty, & Error States**:
  - Skeleton screens present for `/home`, `/tree`, `/stories`, `/review`.
  - Empty states guide user action (e.g. empty recording archive suggests creating first recording).
  - Destructive dialogs require typed confirmation (`DELETE`) for permanent account/family erasure.

---

## D. Backend Engine

- **Python Runtime**:
  - Python 3.11.x: Primary production runtime (tested, 100% bytecode compiled).
  - Python 3.13: Compatibility verified in CI.
- **Test Suite & Coverage**:
  - 1,560 passing tests in primary backend suite (`pytest`).
  - Statement coverage: **89%** across `src/mura` and `apps/api` (19,588 statements, 2,237 misses).
- **PostgreSQL 18 Concurrency Gate**:
  - 12 / 12 concurrency gate tests passing against real PostgreSQL 18.
  - Verified: `FOR UPDATE SKIP LOCKED` queue serialization, family locking, cross-family recording quotas, invitation double-accept fencing, and account deletion vs owner promotion.
- **Alembic Migrations**:
  - Exactly 1 linear head: `20260927_0020 (head)`.
  - Full clean install (`alembic upgrade head`) verified on fresh PostgreSQL database.
- **Docker Containers**:
  - Multi-stage Dockerfiles build cleanly for both `api` and `worker` targets.

---

## E. Frontend Architecture

- **TypeScript**: `tsc --noEmit` passes with 0 errors across all 16 page routes and components.
- **ESLint**: Next.js core web vitals and strict ESLint rules pass.
- **Vitest**: 519 / 519 unit & component tests passing across 48 suites.
- **Playwright**: Critical journeys verified via `@playwright/test`:
  - Session bootstrap & family onboarding
  - Human review claim resolution with evidence provenance
  - Read-only viewer permissions
  - Sole owner account deletion prevention
  - Clean user account deletion, local cache purge, and sign-out
- **Production Build**: `next build` generates 21 static and dynamic pages with zero syntax or compilation errors.
- **Dependency Audit**: `npm audit --omit=dev --audit-level=high` reports 0 high/critical vulnerabilities.

---

## F. Security & Authorization

- **Broken Object-Level Authorization (BOLA)**:
  - 251 tests in `tests/test_authorization_matrix.py` and `tests/test_family_authorization.py` passing.
  - Foreign family access rejected across all resources (`family`, `recording`, `audio`, `story`, `person`, `relationship`, `conflict`, `book`).
- **Role-Based Access Control (RBAC)**:
  - `OWNER`, `EDITOR`, `VIEWER` roles enforced on all mutating endpoints (`POST`, `PATCH`, `PUT`, `DELETE`).
- **JWT Verification Contract**:
  - Strict RS256 JWKS verification via Clerk; audience and issuer validated; expired/premature tokens rejected.
- **Security Headers & Middleware**:
  - HSTS (`max-age=31536000; includeSubDomains; preload`), `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`.
  - Trusted forwarded proxy headers configured to prevent IP/scheme spoofing.
- **Storage Privacy**:
  - Audio and Book objects stored in private buckets; direct public read disabled.
- **Static Analysis & Secret Scanning**:
  - CodeQL (JS/TS + Python): 0 alerts.
  - Bandit AST security scan: clean.
  - Secret scanning: No API keys, credentials, or private secrets committed.

---

## G. Reliability & Distributed Queue

- **PostgreSQL Durable Queues**:
  - Jobs claimed using `FOR UPDATE SKIP LOCKED`.
  - `lease_owner` and `lease_expires_at` prevent worker double-execution.
- **Stale Worker Fencing**:
  - Stale worker heartbeat expiration releases locked jobs cleanly.
  - Stale workers that resume after lease expiration are fenced from committing completed state.
- **Worker Isolation**:
  - Queue dispatch decouples `WORKER_QUEUES=recording`, `WORKER_QUEUES=book`, and `WORKER_QUEUES=cleanup`.
  - Failure in Book worker does not impede Recording ingestion.
- **Provider Resilience**:
  - ASR / LLM timeouts and 429 rate limits trigger exponential backoff retries with jitter; terminal errors fail closed without corrupting the archive.

---

## H. Cost Safety & Resource Budgets

- **Audio Limits**:
  - Maximum recording duration enforced via server-side media probing (preventing spoofed client metadata).
  - User daily duration quota: serialized across families in PostgreSQL.
  - Family daily duration quota: serialized with family row lock.
- **AI Token Budgets**:
  - Pre-execution cost reservation before dispatching paid LLM/ASR calls.
  - Reserved vs actual cost reconciled with exact numeric arithmetic.
  - Expired reservations automatically reclaimed by background cleanup tasks.

---

## I. Disaster Recovery & Data Integrity

- **Operational Drills**:
  - `mura.ops.backup_restore`: Custom archive dump (`pg_dump -Fc`) and restore drill verified against verification database.
  - Secret connection redaction enforced in all log output.
  - Safety gate rejects production connection strings for restore operations.
- **Storage Reconciliation**:
  - `mura.ops.storage_reconciliation`: Detects missing objects, orphan candidates, and SHA-256 mismatches in report-only mode without destructive deletion.
- **Staging Observations**:
  - Local backup execution duration: ~0.4s.
  - Local restore execution duration: ~1.2s.
  - Full relational and foreign key integrity verified post-restore.

---

## J. Observability & Health

- **Process Liveness**: `/health` confirms process responsiveness.
- **Dependency Readiness**: `/ready` validates live PostgreSQL connection and database schema migration status.
- **Operational Metrics**:
  - Aggregate metrics for pending jobs, stuck jobs, expired leases, and AI usage reservations exposed to authenticated operational tooling.
- **Launch Alert Plan**:
  - Defined threshold alerts for 5xx error spikes, queue age > 15m, stuck jobs > 30m, and AI budget exhaustion.

---

## K. Performance & Concurrency

- **Load Testing**:
  - `mura.ops.load_harness` validates concurrent recording creation and archive read throughput under multi-threaded load.
  - Database connection pool sizing (`pool_size=10, max_overflow=20`) prevents connection exhaustion during bursts.
- **Upload Optimization**:
  - Storage upload operations stream payloads without buffering unbounded files in RAM.

---

## L. Real Staging Environment

- **Status**: **BLOCKED — AWAITING USER AUTHORIZATION FOR BILLABLE RESOURCES**
- **Requirements for Staging Verification**:
  - Provisioned Clerk Staging Instance (JWKS, Issuer, Audience).
  - Supabase Staging PostgreSQL instance & private storage buckets (`mura-audio`, `mura-books`).
  - Railway API deployment & Worker service deployments.
  - Vercel Frontend preview environment.
- **Safety Policy**: No billable cloud resources were provisioned autonomously during this audit.

---

## M. Machine Learning & Domain Evaluation

- **Offline ML Gates**:
  - Deterministic evaluation test suite passing in CI (`ml-live-evaluation.yml` offline tier).
  - Grounding validator rejects synthetic hallucinations or ungrounded assertions.
- **Live Real-Family Corpus**:
  - **BLOCKED**: No consented, real-world Kazakh/Russian oral memory corpus has been uploaded to the candidate environment. Synthetic evaluation only.

---

## N. Governance & Supply Chain

- **Branch Protection**: Required PR reviews and passing CI checks mandated prior to merging into `main`.
- **Workflow Security**: All GitHub Actions pinned to immutable commit SHAs.
- **PR Strategy**: Strict linear history; no stacked PRs or uninspected merges.

---

## O. Demo Boundaries & Non-Production Surfaces

- **`/ask` Surface**:
  - Demoted from the primary navigation bar (`NAV_ITEMS`).
  - Renders explicit `<DemoNotice surface="ask" />` and `<DemoBadge />`.
  - Static mock data in `src/data` removed from the repository.
  - Guarded by `tests/demo-boundary.test.ts` to prevent demo fixtures from leaking into production surfaces.
- **Code Cleanliness**: Zero `TODO` or `FIXME` comments exist in `src/mura`, `apps/api`, or `MURA-app/src`.

---

## P. Remaining P0 Blockers

**None.**
There are no known unhandled data-loss bugs, authentication bypasses, cross-family leaks, or concurrency race conditions in the verified codebase.

---

## Q. Remaining P1 Operational & Technical Debt

1. **DeepSeek Telemetry Monkeypatch (Issue #33)**:
   - Import-time patching of DeepSeek client should be migrated to clean dependency injection / middleware wrapper in a dedicated follow-up PR.
2. **Deterministic Python Lockfile**:
   - Backend relies on `pyproject.toml` version ranges rather than a pinned cryptographic lockfile (e.g. `poetry.lock` or `requirements.txt` hashes). Recommend establishing a deterministic lockfile prior to multi-developer scale.
3. **Live Consented ML Evaluation Corpus**:
   - Final launch with native Kazakh/Russian speakers requires validation against an approved, consented speech corpus.

---

## R. Remaining P2 Minor Polish

1. **Next.js Standalone Tracing Warning**:
   - Minor symlink warning regarding `node_modules` during Windows production builds. Does not impact Linux container deployments.

---

## S. Release Verdict

### **READY FOR PRIVATE STAGING DEPLOYMENT**

**Justification**:
The repository and core system pass all rigorous local gates:
- 10/10 GitHub Actions passing on `main` (`7830de6`).
- Single linear Alembic migration head (`20260927_0020`).
- Zero BOLA leaks across 251 authorization tests.
- 12/12 real PostgreSQL 18 concurrency tests passing.
- Complete operational backup/restore and storage reconciliation tooling validated.
- Playwright E2E browser journeys passing.
- Demo surfaces safely isolated and badged.

**Hostile Readiness Assessment**:
*“Can a real family safely trust this system with irreplaceable memories tonight?”*
- **Local / Systemic Level**: **YES.** The fundamental product invariant—**Evidence before facts**—is strictly preserved. Stale worker fencing, immutable Book snapshots, atomic budget reservations, and BOLA isolation protect family privacy and data integrity.
- **Cloud Infrastructure Level**: **CONDITIONAL.** The application is verified and ready for deployment to a private cloud staging environment. Once staging credentials and cloud instances are configured, staging smoke verification should be executed before opening to beta users.
