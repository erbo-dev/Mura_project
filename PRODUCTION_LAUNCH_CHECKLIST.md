# MURA (Мұра) — Production Launch Checklist (Phase 2.5 Pre-Flight)

This pre-flight checklist establishes the required operational and security validations before promoting the system from `RELEASE CANDIDATE` to `PRODUCTION READY` in Phase 2.5.

---

## 1. Secrets & Environment Configuration

- [ ] **Environment Setting**: `MURA_ENVIRONMENT=production` set across all services.
- [ ] **Fault Injection Gate**: Verify `MURA_FAULT_INJECTION` is completely unset or `false`. (Application startup will raise `ValueError` if set to true).
- [ ] **Database Schema Invariant**: Verify `DATABASE_AUTO_CREATE=false`. Database tables must ONLY be updated via Alembic migrations.
- [ ] **PostgreSQL Scheme**: Verify `DATABASE_URL` targets `postgresql://` or `postgresql+psycopg://` with SSL mode enforced. SQLite URLs fail closed on startup.
- [ ] **Credential Separation**:
  - `CORE_API_KEY` (minimum 32 chars) generated uniquely.
  - `OPERATIONS_API_KEY` (minimum 32 chars) generated uniquely and differs from `CORE_API_KEY`.
  - `WORKER_REGISTRATION_TOKEN` (minimum 32 chars) generated uniquely.
  - `DEEPSEEK_API_KEY` loaded securely via platform secrets (never committed).
- [ ] **OIDC & Auth Boundaries**:
  - `AUTH_MODE=oidc`
  - `AUTH_ISSUER` targets official Supabase Auth URL (`https://<project-id>.supabase.co/auth/v1`).
  - `AUTH_JWKS_URL` targets HTTPS JWKS endpoint (`https://<project-id>.supabase.co/auth/v1/.well-known/jwks.json`).
  - `AUTH_ALLOWED_ALGORITHMS` restricted to asymmetric keys (`RS256`, `ES256`). Symmetric `HS*` algorithms strictly forbidden.

---

## 2. Infrastructure & Service Deployment

- [ ] **Railway API Service**:
  - Service: `mura-api` running `uvicorn apps.api.main:app`.
  - Health check probe: `GET /health` (interval: 15s).
  - Readiness check probe: `GET /ready` (interval: 15s).
- [ ] **Railway Worker Service**:
  - Service: `mura-worker` running `python -m mura.worker`.
  - Scaled independently of web API.
  - Verified lease parameters: `JOB_LEASE_SECONDS=300`, `JOB_HEARTBEAT_SECONDS=30`.
- [ ] **Vercel Frontend Service**:
  - Production deployment pointing to `MURA-app`.
  - Environment variables: `NEXT_PUBLIC_MURA_API_URL`, `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`.
  - Verified: Zero private keys (`DEEPSEEK_API_KEY`, `SUPABASE_SERVICE_ROLE_KEY`) present in frontend environment.
- [ ] **Supabase PostgreSQL**:
  - Point-in-time recovery (PITR) enabled.
  - Daily automatic snapshots enabled.
  - Connection pool limits configured (max 20 connections per API instance).
- [ ] **Supabase Storage**:
  - Bucket `mura-audio` marked as PRIVATE (public access disabled).
  - Bucket `mura-artifacts` marked as PRIVATE.
  - Storage RLS policies enforce access solely via backend service role key.

---

## 3. Security, Privacy & Compliance Validation

- [ ] **Security Headers**:
  - Verify `X-Content-Type-Options: nosniff`.
  - Verify `X-Frame-Options: DENY`.
  - Verify `Referrer-Policy: strict-origin-when-cross-origin`.
  - Verify `Permissions-Policy: microphone=(self), camera=(), geolocation=()`.
- [ ] **CORS Configuration**:
  - `CORS_ALLOWED_ORIGINS` explicitly set to production domains (`https://mura.kz`, `https://app.mura.kz`). Wildcards (`*`) strictly forbidden.
- [ ] **BOLA / IDOR Verification**:
  - Cross-family request matrix verified: returns 404 on foreign resource IDs.
- [ ] **Privacy Cascades**:
  - Deleting recording wipes audio file from private storage.
  - Deleting book wipes export artifacts from storage.
  - Deleting family cascades across all associated tables and storage buckets.
- [ ] **GDPR Export**:
  - `GET /v1/families/{id}/privacy/export` validated with zero secret tokens leaked.

---

## 4. Observability, Telemetry & Alerts

- [ ] **Sentry DSN**: Configured for both backend (`apps/api`, `mura-worker`) and frontend (`MURA-app`).
- [ ] **PII Scrubbing**: Sentry scrubbers active — raw customer audio transcripts, family names, and private keys never sent to Sentry.
- [ ] **Logging Format**: `LOG_FORMAT=json` for structured log ingestion.
- [ ] **Alerts Configured**:
  - Provider rate limit spikes (HTTP 429 > 5 per minute).
  - Provider auth failures (HTTP 401 alerts on-call immediately).
  - Worker lease reclamation spikes (indicating worker instability).
  - High memory usage (> 80% container RAM).

