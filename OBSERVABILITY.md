# MURA (Мұра) — Production Observability, Structured Logging & Error Tracking
### Sentry · Railway JSON Logging · Request & Worker Job Correlation

This document serves as the operational guide for MURA's observability infrastructure, runtime diagnostics, and privacy-first error tracking.

---

## 1. Architecture Overview

```text
[ Client / Browser ]
        │
        ▼ (1) HTTP Request (with optional X-Request-ID)
[ Next.js 15 (Vercel) ]
        │ Sentry Client & Server Tracking (sendDefaultPii=false, no Replay)
        │
        ▼ (2) Server-side Proxy (propagates X-Request-ID)
[ FastAPI Core API (Railway) ]
        │
        ├─▶ ContextVar: request_id_ctx
        ├─▶ Emits: Single-line JSON log (indexed by Railway Log Explorer)
        ├─▶ Generates: recording_id, job_id
        │
        ▼ (3) Inserts Job (SELECT ... FOR UPDATE SKIP LOCKED)
[ Supabase PostgreSQL ]
        ▲
        │ (4) Claims Job (with lease & heartbeat)
[ mura-worker (Railway) ]
        │
        ├─▶ ContextVar: job_id_ctx, recording_id_ctx, attempt_ctx, worker_id_ctx
        ├─▶ Emits: Single-line JSON logs (job_claimed, job_started, job_completed)
        └─▶ Sentry Worker Tracking (captures terminal job failures with safe tags)
```

### Key Pillars
1. **Runtime Diagnostics (Railway Log Explorer)**: Pure Python standard library structured JSON logging emitting single-line JSON records indexed by `@request_id`, `@job_id`, `@level`, `@service`.
2. **Error Tracking & Grouping (Sentry)**: Captures unhandled backend and worker exceptions, and Next.js client/server errors with stack traces, breadcrumbs, and safe tags.
3. **Correlation**: `request_id` correlates API operations, while `job_id`, `recording_id`, `attempt`, and `worker_id` correlate Worker operations.
4. **Strict Privacy Boundary**: Transcripts, audio files, prompts, LLM completion outputs, person names, and secrets are strictly redacted and never logged or forwarded to Sentry.

---

## 2. Structured JSON Logging (Railway Log Explorer)

In production and staging (`MURA_ENVIRONMENT=production` or `LOG_FORMAT=json`), all logs are emitted to standard output as single-line JSON.

### Log Format
```json
{
  "timestamp": "2026-09-17T15:10:00.123456+00:00",
  "level": "info",
  "message": "http_request_completed",
  "logger": "mura.api",
  "service": "mura-api",
  "environment": "production",
  "request_id": "req_88f912c759084ef7a3c75ab3b4e69b2d",
  "method": "POST",
  "route": "/v1/families/{family_id}/recordings",
  "status_code": 202,
  "duration_ms": 142.5
}
```

### Worker Log Example
```json
{
  "timestamp": "2026-09-17T15:10:01.987654+00:00",
  "level": "info",
  "message": "job_started",
  "logger": "mura.orchestration.recordings",
  "service": "mura-worker",
  "environment": "production",
  "job_id": "job_b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6",
  "recording_id": "rec_1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d",
  "family_id": "fam_a1b2c3d4e5f6",
  "attempt": 1,
  "worker_id": "worker_999988887777",
  "event": "job_started"
}
```

### Querying in Railway Log Explorer
Railway indexes structured JSON fields on stdout. You can search directly using:
- Search by Request ID: `@request_id:"req_..."`
- Search by Job ID: `@job_id:"job_..."`
- Search by Recording ID: `@recording_id:"rec_..."`
- Search by Service: `@service:"mura-worker"`
- Search by Error Level: `@level:"error"`
- Search by Event: `@event:"job_failed"`

---

## 3. Request & Worker Job Correlation

### 3.1 Request Correlation (FastAPI)
1. **Validation & Resolution**:
   - The API inspects the incoming `X-Request-ID` header.
   - If present, it must strictly match `\A[A-Za-z0-9_-]{8,64}\Z`.
   - If missing, invalid, or oversized, a cryptographically random identifier `req_{uuid4().hex}` is generated.
2. **Context Propagation**:
   - Stored in a concurrency-safe `request_id_ctx: ContextVar[str | None]`.
   - Bounded via `try ... finally` block ensuring `request_id_ctx.reset(token)` is always executed on request completion.
   - Returned in response header `X-Request-ID` and in all canonical error envelopes (`{"error": {"request_id": ...}}`).
3. **Normalized Route Template**:
   - Logs the parameterized FastAPI route (e.g. `/v1/families/{family_id}/recordings/{recording_id}`) rather than raw URLs, preventing route parameter leakage.
4. **Quiet Health Probes**:
   - Routine successful (`< 400`) requests to `/health` and `/ready` are logged at `DEBUG` level and excluded from Sentry tracing, preventing log spam.

### 3.2 Worker Job Correlation (`mura-worker`)
1. **Job Claim & Scoping**:
   - When a worker claims a job from PostgreSQL, it binds:
     - `job_id_ctx`
     - `recording_id_ctx`
     - `family_id_ctx`
     - `attempt_ctx`
     - `worker_id_ctx`
   - Using `WorkerJobContextManager`, all context variables are strictly reset in `finally` blocks, preventing cross-operation context pollution.
2. **Lifecycle Events Emitted**:
   - `job_claimed`: Immediately upon lease acquisition.
   - `job_started`: Before ASR transcription begins.
   - `job_completed`: After database persistence and finalization.
   - `job_failed`: On unrecoverable error.
   - `job_retry_scheduled`: When deferred due to retryable ASR unavailability.
3. **Quiet Heartbeats**:
   - Periodic lease heartbeats run quietly in the background, logging only at `WARNING` if a transient database renewal fails.

---

## 4. Privacy & Redaction Policy

> [!CAUTION]
> **Strict Privacy Guarantee**: MURA processes private family memories and voices. Observability data must never become a secondary repository of family stories or credentials.

### Centralized Sanitization (`LogSanitizer`)
Both structured logs and Sentry payloads pass through `mura.logging.LogSanitizer`:

| Classification | Keys / Attributes | Action |
| :--- | :--- | :--- |
| **Credentials & Secrets** | `authorization`, `cookie`, `set-cookie`, `token`, `access_token`, `refresh_token`, `password`, `secret`, `api_key`, `service_role_key`, `signed_url` | Redacted to `"[REDACTED]"` |
| **Private Family Data** | `transcript`, `prompt`, `response_content`, `story_content`, `body`, `audio_bytes`, `email`, `first_name`, `last_name`, `speaker_name`, `story`, `quote`, `text`, `content` | Redacted to `"[REDACTED]"` |
| **Tokens in Strings** | `Bearer ...`, `eyJ...` (JWT patterns) | Replaced with `Bearer [REDACTED]` or `[REDACTED_TOKEN]` |
| **Raw Objects / Bodies** | Request bodies, Pydantic story models, arbitrary instances | Replaced with `<ClassName>` |
| **Safe Opaque Identifiers** | `request_id`, `job_id`, `recording_id`, `family_id`, `trace_id`, `worker_id`, `attempt`, `status_code`, `duration_ms`, `method`, `route`, `service`, `level`, `timestamp`, `backend`, `size_bytes`, `mime_type`, `provider`, `model`, `error_code` | **Preserved intact** |

### Sentry Privacy Settings
- `send_default_pii = False` on all Sentry clients.
- Request body and data are explicitly stripped in `before_send`.
- Session Replay is **disabled** (`replaysSessionSampleRate: 0`, `replaysOnErrorSampleRate: 0`).

---

## 5. Sentry Configuration

Sentry is **fully optional**. If `SENTRY_DSN` or `NEXT_PUBLIC_SENTRY_DSN` is unset, the system functions normally with zero errors and error tracking disabled.

### 5.1 Backend API (`mura-api`)
- Initialized in `apps/api/main.py` via `mura.sentry.init_sentry("mura-api", ...)`.
- Automatically tags `service="mura-api"`, `request_id`, `environment`.
- Unhandled 500 errors are caught by `apps/api/errors.py` and sent to Sentry without exposing stack traces to clients.

### 5.2 Standalone Worker (`mura-worker`)
- Initialized in `apps/worker/main.py` via `mura.sentry.init_sentry("mura-worker", ...)`.
- Captures terminal job failures at the top-level boundary in `src/mura/orchestration/recordings.py`.
- No duplicate events: nested exceptions caught and handled are not reported repeatedly.
- Calls `flush_sentry()` upon graceful process shutdown.

### 5.3 Frontend (`MURA-app`)
- Integrated via Next.js 15 App Router conventions:
  - `sentry.client.config.ts`: Client-side error tracking.
  - `sentry.server.config.ts`: Server-side error tracking.
  - `sentry.edge.config.ts`: Edge/middleware error tracking.
  - `src/instrumentation.ts`: Next.js 15 instrumentation hook with `onRequestError`.
  - `src/app/error.tsx`: Route error boundary with localized message.
  - `src/app/global-error.tsx`: Root fatal error boundary.

---

## 6. How to Diagnose a Failed Recording (End-to-End Walkthrough)

When an issue occurs with a recording, follow these steps:

### Scenario: User reports upload or processing failed

1. **Find the Request in API Logs**:
   - In Railway Log Explorer for `mura-api`:
     ```text
     @route:"/v1/families/{family_id}/recordings" @status_code:>=400
     ```
   - Locate the log entry and note the `request_id`.
   - Check the response payload returned to the client (or the error log) for `recording_id` and `job_id`.

2. **Correlate with Worker Execution**:
   - In Railway Log Explorer for `mura-worker`:
     ```text
     @job_id:"job_..."
     ```
   - You will see the complete event trail:
     - `job_claimed`
     - `job_started`
     - `job_completed` OR `job_failed` / `job_retry_scheduled`

3. **Inspect the Error in Sentry**:
   - Search Sentry by tag: `job_id:job_...` or `request_id:req_...`.
   - The Sentry issue provides the exact stack trace and exception class without leaking private audio or transcripts.

4. **Verify Database Trace**:
   - Query the `processing_trace_events` table for granular stage timings:
     ```sql
     SELECT stage, event_name, outcome, duration_ms, attributes
     FROM processing_trace_events
     WHERE job_id = 'job_...'
     ORDER BY attempt, sequence;
     ```

---

## 7. Environment Variables Reference

| Variable | Service | Default | Description |
| :--- | :--- | :--- | :--- |
| `LOG_FORMAT` | API & Worker | `auto` | `auto` (json in prod/staging, text in local), `json`, or `text` |
| `LOG_LEVEL` | API & Worker | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `SENTRY_DSN` | API & Worker | *(unset)* | Sentry DSN for backend error tracking |
| `SENTRY_ENVIRONMENT` | API & Worker | *(MURA_ENVIRONMENT)* | Deployment environment tag |
| `SENTRY_TRACES_SAMPLE_RATE`| API & Worker | `0.05` | Transaction sampling rate (0.0 to 1.0) |
| `NEXT_PUBLIC_SENTRY_DSN` | Frontend | *(unset)* | Sentry DSN for Next.js client & server |
| `SENTRY_AUTH_TOKEN` | Frontend Build | *(unset)* | Build-time secret for source map upload (never client-facing) |

---

## 8. Phase 1.5 Monitoring & Cost Tracking

Operational queue health, AI provider usage, cost tracking, and incident playbooks are implemented in Phase 1.5. See [MONITORING.md](file:///d:/Mura_production/MONITORING.md) for:
- Three-tier monitoring architecture (Railway native metrics, Sentry alerts, Supabase PostgreSQL durable queue health)
- Queue health and stuck-job detection engine (`QueueHealthService`)
- AI usage ledger (`ai_usage_events`) and deterministic `Decimal` cost calculations
- Operator-only monitoring endpoint: `GET /v1/operations/monitoring/summary`
- Operational incident playbooks ("Stuck recording", "AI cost spike", "Worker OOM/crash recovery")


