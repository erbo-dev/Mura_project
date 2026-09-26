# Wave 1 Milestone C browser/product status

## Baseline
- Main at branch creation: `8f16e1a22e044ade948929d033f958c5779115bc` (Milestone A was merged by the user). Milestone B is independently open as PR #59; its final-head Backend, Frontend, Quality, Security and CodeQL workflows PASS. GitHub dependency review remains BLOCKED because dependency graph is disabled.
- Branch: `fix/frontend-production-correctness`, independent of B; PR NOT EXECUTED. No production deployment or merge.

## Milestone C
- Status: COMPLETE; full milestone exit validation EXECUTED and PASS. Independent PR to be opened against `main`; no merge or deployment.
- Confirmed & Implemented:
  - Human Review UI: Implemented conflict cards with claim text, timestamp, certainty, evidence quotes extracted from recording pipeline spans, and playback of source audio via `/v1/recordings/{id}/audio`. Full resolve, dismiss, and reopen actions with optimistic updates and error recovery. Non-editor roles (e.g. viewer) are strictly disabled from decision actions.
  - Safe Account Deletion: Enabled `DELETE /v1/me` proxy forwarding. Implemented destructive modal requiring confirmation keyword, sole-owner transfer alert / block, sign-out orchestration via Clerk adapter hook, and comprehensive client-side state purge (`purgeLocalRecordingsFor`, `localStorage.removeItem`).
  - Audio Range & Binary Transport: Range byte streaming implemented in Core with HTTP 206/416, `Content-Range`, and `Accept-Ranges: bytes`. Proxy forwards Range headers, applies route-specific timeouts (header-only deadline for binary streams), safely handles binary responses, and preserves `Content-Disposition`.
  - Playwright E2E Suite: Deterministic critical journey suite covering session/family creation, Human Review resolve, viewer read-only role enforcement, sole-owner account deletion block, and account deletion with sign-out.
- Validation Evidence:
  - Frontend Vitest: 512 passed across 48 test files.
  - Frontend ESLint & TypeScript: 0 errors (`npm run lint`, `npx tsc --noEmit`).
  - Frontend Production Build: PASS (`next build` compiled 21 static and dynamic pages).
  - Playwright Critical Journeys: 5 passed (`critical-journeys.spec.ts`).
  - Backend Migrations: Upgraded test database to Alembic head `20260923_0018`.
  - Backend Targeted Pytest: 285 passed (`test_audio_storage.py`, `test_authorization_matrix.py`, `test_family_authorization.py`, `test_conflict_resolution.py`).
  - Python 3.13 Backend Pytest: 5 passed (`test_conflict_resolution.py`).
  - PostgreSQL Concurrency & Atomicity Gate: 7 passed (`test_postgres_atomicity.py`).
  - Code Quality & Security: Ruff check (PASS), Ruff format (PASS), mypy (PASS), Bandit (0 issues).
  - Dependency Audits: npm audit (0 prod vulnerabilities), pip-audit (0 known vulnerabilities).
  - Docker Containers: `mura-wave1-api:local` and `mura-wave1-worker:local` built and tagged successfully.

## External blockers
- Real Clerk/Supabase staging and protected browser journeys: BLOCKED without staging credentials (synthetic mock and isolated local Postgres/Next harness executed).
- Dependency-review and branch-protection evidence: BLOCKED as recorded in B.

## Decisions
- Core remains the authorization boundary. Binary response headers are selected by explicit allowlist; private storage keys never go to browser. No provider deletion claim without provider confirmation.

## Remaining roadmap
- Push `fix/frontend-production-correctness` and open independent PR without merging. Proceed to Milestone D staging tooling and local integration validation.
