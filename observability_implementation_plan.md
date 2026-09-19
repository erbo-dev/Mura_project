# MURA Observability Implementation Plan: Error Tracking, Structured Logging & Correlation (Phase 1.4)

## Baseline Audit & Architecture Design

### 1. Current State Audit

#### 1.1 Python Logging
- **`apps/worker/main.py`**: Configures logging using `logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")`.
- **`apps/api/main.py`**: Has no explicit logging initialization; inherits standard root logger and uvicorn console loggers.
- **Loggers across modules**:
  - `src/mura/leases.py`: Uses `logging.getLogger(__name__)` for lease warnings.
  - `src/mura/orchestration/recordings.py`: Uses `logging.getLogger(__name__)`.
  - `src/mura/storage/deletion.py`: Uses `logging.getLogger(__name__)`.
  - `apps/worker/main.py`: Uses `logging.getLogger("mura.worker")`.
- **Missing capabilities**: No structured JSON output for Railway Log Explorer indexing, no request/job context injection into log records, no centralized sanitization.

#### 1.2 Frontend Logging (MURA-app)
- Next.js 15 App Router (`MURA-app`).
- Zero `console.log` statements in source code.
- No error boundaries (`global-error.tsx` or `error.tsx`) exist; unhandled client or server render errors fall back to Next.js default pages without error reporting.
- `@sentry/nextjs` is not yet installed or configured.

#### 1.3 Exception Boundaries
- **FastAPI Core (`apps/api/errors.py`)**:
  - Catches `StarletteHTTPException`, `RequestValidationError`, `DeepSeekError`, `ContractValidationError`.
  - Enforces canonical envelope: `{"error": {"code": ..., "message": ..., "retryable": ..., "request_id": ...}}`.
  - Does not catch unexpected generic `Exception`s at the top level, which could cause raw 500 error responses or bypass structured diagnostic logging.
- **Worker (`src/mura/orchestration/recordings.py`)**:
  - Catches `ASRClientError` (defers or fails job) and generic `Exception` (marks job failed with `pipeline_failed`).
  - Records events in database table `processing_trace_events` via `ProcessingTrace`.
  - Does not emit structured log events on stdout/stderr for Railway or capture uncaught errors in Sentry.

#### 1.4 API Middleware
- `apps/api/main.py`:
  - `assign_request_id`: Validates or generates `request_id`, sets `request.state.request_id` and response header `X-Request-ID`.
  - Does not set a concurrency-safe `ContextVar` to propagate `request_id` into downstream log messages.
  - Does not log an `http_request_completed` event with duration, normalized route template, and status code.
  - Does not quiet routine `/health` and `/ready` probes.

#### 1.5 Worker Failure Handling & Retry Behavior
- Jobs are claimed using `SELECT ... FOR UPDATE SKIP LOCKED` with heartbeats and leases (`job_lease_seconds=300`, `job_heartbeat_seconds=60`).
- If an exception occurs, the worker marks the job failed or deferred.
- If the worker process crashes abruptly, the lease expires and another worker reclaims the job.
- Heartbeats are currently quiet unless renewal fails transiently (`logger.warning("lease renewal failed transiently")`).

#### 1.6 Correlation IDs
- `request_id`: Handled by `resolve_request_id` (`req_[a-f0-9]{32}` or valid caller `X-Request-ID`).
- `job_id`: Generated at recording acceptance (`job_[a-f0-9]{32}`).
- `recording_id`: Generated at recording acceptance (`rec_[a-f0-9]{32}`).
- `trace_id`: Generated in `ProcessingTrace` (`trace_[a-f0-9]{32}`).
- `worker_id`: Generated in `new_worker_id` (`worker_[a-f0-9]{32}`).

#### 1.7 Sensitive Values Audit
- No transcripts or auth tokens are logged today.
- `src/mura/observability.py` already implements `_SENSITIVE_KEY_PARTS` for DB traces.
- However, there is no centralized filter on standard library logging to guarantee that sensitive keys (tokens, transcripts, stories, prompts) are never formatted into log strings or Sentry events.

---

## 2. Proposed Observability Architecture

### 2.1 Minimal Structured JSON Logging (Stdlib)
- Implement `src/mura/logging.py`:
  - `StructuredJsonFormatter(logging.Formatter)`: Emits single-line JSON records formatted for Railway Log Explorer indexing:
    ```json
    {
      "timestamp": "2026-09-17T14:55:00.123Z",
      "level": "info",
      "message": "job_started",
      "service": "mura-worker",
      "environment": "production",
      "logger": "mura.worker",
      "job_id": "job_12345",
      "recording_id": "rec_67890",
      "attempt": 1
    }
    ```
  - In development (`Environment.LOCAL`, `Environment.TEST` or `LOG_FORMAT=text`), outputs clean human-readable console format.
  - In production / staging (`Environment.PRODUCTION`, `Environment.STAGING` or `LOG_FORMAT=json`), outputs single-line JSON.
  - Avoids adding `structlog` by leveraging standard library `logging` with context extraction and filter hooks.

### 2.2 Centralized Log Sanitization (`LogSanitizer`)
- `src/mura/logging.py`:
  - `LogSanitizer`: Recursively scrubs dictionaries, lists, and strings.
  - Matches sensitive keys: `authorization`, `cookie`, `set-cookie`, `token`, `access_token`, `refresh_token`, `password`, `secret`, `api_key`, `service_role_key`, `signed_url`, `transcript`, `prompt`, `response_content`, `body`, `audio_bytes`, `email`, `story`, `quote`.
  - Redacts sensitive values to `"[REDACTED]"`.
  - Replaces potential JWTs (`Bearer eyJ...`, `eyJ...`) with `[REDACTED_TOKEN]`.
  - Strips Pydantic models to safe metadata dictionaries.
  - Plugged directly into `StructuredJsonFormatter` and Sentry's `before_send`.

### 2.3 Contextual Correlation via `ContextVar`
- Concurrency-safe context variables:
  - `request_id_ctx: ContextVar[str | None]`
  - `job_id_ctx: ContextVar[str | None]`
  - `recording_id_ctx: ContextVar[str | None]`
  - `attempt_ctx: ContextVar[int | None]`
  - `worker_id_ctx: ContextVar[str | None]`
- `CorrelationFilter(logging.Filter)`: Automatically enriches all standard `logging` records with active context variables.

### 2.4 FastAPI Request Correlation & Quiet Probes
- In `apps/api/main.py`:
  - Update `assign_request_id` middleware:
    - Sets `request_id_ctx`.
    - Resolves normalized route template (e.g. `/v1/families/{family_id}/recordings/{recording_id}`) using FastAPI route matching rather than logging raw URL paths.
    - Measures duration `duration_ms`.
    - Emits structured `http_request_completed` log.
    - Probes `/health` and `/ready`:
      - Suppresses routine 200 OK access logs (logged at `DEBUG` only).
      - Logs non-200 responses as `WARNING`/`ERROR` with diagnostic context.
    - Ensures `X-Request-ID` is returned in response headers and canonical error envelopes.

### 2.5 Worker Job Correlation
- In `apps/worker/main.py` and `src/mura/orchestration/recordings.py`:
  - Set `job_id_ctx`, `recording_id_ctx`, `attempt_ctx`, `worker_id_ctx` on job claim.
  - Emit structured events:
    - `job_claimed`
    - `job_started`
    - `job_completed`
    - `job_failed`
    - `job_retry_scheduled`
  - Reset context variables on job completion or release.
  - Heartbeat remains silent (`DEBUG`), logging only abnormal lease renewals at `WARNING`.

### 2.6 Sentry Integration (API & Worker)
- Install `sentry-sdk>=2.0,<3` in `Mura_project`.
- Implement `src/mura/sentry.py`:
  - `init_sentry(service: str, settings: CoreSettings)`:
    - Checks `settings.sentry_dsn`. If unset, disabled cleanly without errors.
    - `send_default_pii = False`.
    - Sets environment, release (from `GIT_COMMIT_SHA` / `RAILWAY_GIT_COMMIT_SHA`).
    - Sets sample rate from `settings.sentry_traces_sample_rate` (default `0.05`).
    - Configures `traces_sampler` to drop traces for `/health` and `/ready`.
    - Configures `before_send` hook using `LogSanitizer` to scrub breadcrumbs, request data, and extra tags.
    - Sets safe tags: `service`, `environment`, `request_id`, `job_id`.
  - In `apps/api/main.py`: Initialize Sentry on startup with FastAPI integration.
  - In `apps/worker/main.py`: Initialize Sentry on startup with standalone worker context. Flush on shutdown (`sentry_sdk.flush()`).

### 2.7 Storage & AI Observability
- Audio storage operations log:
  - `audio_upload_started`, `audio_upload_completed`, `audio_materialize_started`, `audio_materialize_completed`, `audio_delete_completed`
  - Safe metadata: `backend`, `size_bytes`, `duration_ms`, `mime_type`.
  - Forbidden: object URLs, signed URLs, audio binary data.
- AI operations log:
  - DeepSeek & Whisper: `provider`, `model`, `operation`, `duration_ms`, `success`, token counts (if returned by provider).
  - Forbidden: prompts, full transcripts, generated stories, evidence quotes.

### 2.8 Next.js 15 Sentry Integration (`MURA-app`)
- Install `@sentry/nextjs`.
- Add configuration files:
  - `sentry.client.config.ts`: Client-side error tracking with `sendDefaultPii: false`, Replay disabled (`replaysSessionSampleRate: 0`, `replaysOnErrorSampleRate: 0`).
  - `sentry.server.config.ts`: Server-side error tracking with sanitized `beforeSend`.
  - `sentry.edge.config.ts`: Edge/middleware error tracking.
  - `instrumentation.ts`: Next.js 15 instrumentation hook.
  - `next.config.ts`: Wrap with `withSentryConfig` preserving `output: "standalone"`.
- Add error boundary UI:
  - `src/app/global-error.tsx` & `src/app/error.tsx`: Capture errors to Sentry, display friendly multilingual message (KZ/RU/EN) without breaking styling.

---

## 3. Exact Files to Modify & Create

### New Files
1. `Mura_project/src/mura/logging.py`: Structured JSON formatter, log sanitizer, correlation filter, and logger setup.
2. `Mura_project/src/mura/sentry.py`: Sentry initialization, safe `before_send` filtering, trace sampling.
3. `Mura_project/tests/test_logging_sanitization.py`: Unit tests for JSON log shape, secret redaction, transcript redaction, context propagation.
4. `Mura_project/tests/test_sentry_integration.py`: Unit tests for Sentry initialization, disabled state, and PII filtering.
5. `MURA-app/sentry.client.config.ts`: Sentry client config.
6. `MURA-app/sentry.server.config.ts`: Sentry server config.
7. `MURA-app/sentry.edge.config.ts`: Sentry edge config.
8. `MURA-app/src/instrumentation.ts`: Next.js instrumentation.
9. `MURA-app/src/app/error.tsx`: Next.js route error boundary.
10. `MURA-app/src/app/global-error.tsx`: Next.js root error boundary.
11. `OBSERVABILITY.md`: Production observability guide.

### Modified Files
1. `Mura_project/pyproject.toml`: Add `sentry-sdk>=2.0,<3` dependency.
2. `Mura_project/src/mura/config.py`: Add `sentry_dsn`, `sentry_environment`, `sentry_traces_sample_rate`, `log_level`, `log_format`.
3. `Mura_project/apps/api/main.py`: Setup structured logging, Sentry initialization, context-aware `assign_request_id` middleware, quiet healthcheck access logs.
4. `Mura_project/apps/api/errors.py`: Add global unhandled `Exception` handler that reports to Sentry, logs structured error, and returns canonical 500 error envelope.
5. `Mura_project/apps/worker/main.py`: Setup structured logging, Sentry initialization, shutdown flush.
6. `Mura_project/src/mura/orchestration/recordings.py`: Attach worker correlation context, emit structured lifecycle logs, report uncaught pipeline errors to Sentry.
7. `MURA-app/package.json`: Add `@sentry/nextjs`.
8. `MURA-app/next.config.ts`: Wrap config with `withSentryConfig`.
9. `MURA-app/.env.example`, `Mura_project/.env.example`, `.env.example`: Add Sentry environment variables.
10. `RAILWAY_DEPLOYMENT.md`: Update environment variables table with Sentry options.

---

## 4. Verification Plan

1. **Backend Tests**:
   - `python -m pytest tests/test_logging_sanitization.py tests/test_sentry_integration.py tests/test_family_recording_api.py tests/test_standalone_worker.py tests/test_audio_storage.py tests/test_supabase_storage.py`
   - Verify 0 regressions on existing 92 tests + new tests.
2. **Frontend Tests & Build**:
   - `npm test` (`vitest run`) in `MURA-app`.
   - `npm run build` in `MURA-app` to verify standalone Next.js compilation with Sentry wrapping.
3. **Static & Security Verification**:
   - Verify no PII, secret keys, or transcripts can leak through structured logs or Sentry events.

