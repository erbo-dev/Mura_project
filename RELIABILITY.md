# MURA (Мұра) — System Reliability, Retry Semantics & Queue Architecture
### Job Resilience · Failure Classification · Exponential Backoff with Jitter · Postgres-Only Queue

This document is the engineering reference for reliability, provider error resilience, and asynchronous job processing across the MURA platform.

---

## 1. Architectural Philosophy: Pure PostgreSQL Queue

MURA deliberately eliminates external broker dependencies (such as Celery, Redis, RabbitMQ, or SQS). All asynchronous background processing—audio pipeline extraction and Family Book compilation—is orchestrated directly inside PostgreSQL using durable row-level locks:

```text
┌────────────────────────────────────────────────────────────────────────┐
│                              PostgreSQL                                │
│                                                                        │
│   recording_jobs                                                       │
│   ├── status: 'queued' | 'running' | 'completed' | 'failed'            │
│   ├── lease_token, lease_owner, lease_expires_at                       │
│   └── attempt_count, max_retries                                       │
│                                                                        │
│   book_jobs                                                            │
│   ├── status: 'queued' | 'running' | 'completed' | 'failed'            │
│   ├── stage: 'blueprint' | 'chapter' | 'assembly' | 'export'           │
│   ├── attempts, max_attempts, next_attempt_at                          │
│   └── lease_token, lease_owner, lease_expires_at                       │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    │ SELECT ... FOR UPDATE SKIP LOCKED
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                         mura-worker Process                            │
│  - Concurrent pollers for recording jobs and book jobs                 │
│  - Atomic lease acquisition with heartbeat renewals                    │
│  - Central failure classification (Retryable vs Terminal)              │
│  - Non-blocking deferral with exponential backoff & uniform jitter     │
└────────────────────────────────────────────────────────────────────────┘
```

### Key Invariants
1. **Concurrency Safety**: Workers claim jobs via `SELECT ... FOR UPDATE SKIP LOCKED`. Multiple workers running concurrently will never claim the same job.
2. **Lease Enforced**: Every running job has an active `lease_token` and `lease_expires_at`. Workers heartbeat their leases periodically during execution.
3. **Attempt Ceiling in SQL**: `claim_next_job` queries explicitly enforce `attempts < max_attempts` in SQL, ensuring exhausted jobs cannot be re-claimed in a tight loop.
4. **No Phantom State**: Job state transitions are executed within ACID database transactions.

---

## 2. Central Failure Classification & Disposition

All external provider calls (DeepSeek LLM, OpenAI/Whisper ASR, Supabase Storage) are wrapped and classified through `mura.reliability.failures`:

### 2.1 Failure Dispositions
- `FailureDisposition.RETRY`: Transient errors suitable for delayed re-execution.
- `FailureDisposition.TERMINAL`: Deterministic or fatal errors that must immediately abort the job without re-attempting.

### 2.2 Failure Categories & Mapping

| Failure Category | HTTP Codes / Conditions | Disposition | Default Action |
| :--- | :--- | :--- | :--- |
| **`RATE_LIMIT`** | `429 Too Many Requests` | `RETRY` | Honor `Retry-After` header or exponential backoff with jitter |
| **`PROVIDER_UNAVAILABLE`** | `502 Bad Gateway`, `503 Service Unavailable`, `504 Gateway Timeout` | `RETRY` | Exponential backoff with uniform jitter |
| **`TRANSIENT_NETWORK`** | Connect timeout, read timeout, TCP reset, DNS transient error | `RETRY` | Exponential backoff with uniform jitter |
| **`STORAGE_UNAVAILABLE`** | S3 / Supabase Storage 5xx, socket timeout | `RETRY` | Defer job for retry up to max attempts |
| **`AUTHENTICATION_ERROR`** | `401 Unauthorized`, `403 Forbidden` from AI provider | `TERMINAL` | Fail job immediately, log critical error |
| **`INVALID_REQUEST`** | `400 Bad Request`, `422 Unprocessable` from AI provider | `TERMINAL` | Fail job immediately; input format is flawed |
| **`CONTENT_POLICY_VIOLATION`**| Provider safety / content moderation filter rejection | `TERMINAL` | Fail job with `content_policy_violation` |
| **`MALFORMED_OUTPUT`** | Deterministic JSON schema decoding error after repair budget | `TERMINAL` | Abort stage, mark chapter/book failed |

---

## 3. Backoff, Jitter & `Retry-After` Semantics

To prevent "thundering herds" or retry storms against third-party providers during partial outages, MURA implements **Full Jitter Exponential Backoff**:

### 3.1 Delay Calculation Formula
For attempt number \(k\) (where \(k \ge 1\)):
\[
t_{\text{raw}} = \min\left(t_{\text{max}}, t_{\text{base}} \times 2^{k - 1}\right)
\]
\[
t_{\text{delay}} = \max\left(t_{\text{base}}, t_{\text{raw}} + \text{Uniform}(-j, j)\right)
\]
Where:
- \(t_{\text{base}} = 5.0\text{ seconds}\)
- \(t_{\text{max}} = 300.0\text{ seconds}\)
- \(j = 0.25 \times t_{\text{raw}}\) (uniform jitter)

### 3.2 Handling `Retry-After` Headers
When an upstream provider returns HTTP 429 or 503 with a `Retry-After` header, the classifier parses:
1. **Delta-seconds**: Integer seconds (e.g. `Retry-After: 30`).
2. **HTTP Date**: RFC 1123 / RFC 2822 timestamps (e.g. `Retry-After: Fri, 31 Dec 2026 23:59:59 GMT`).
The system calculates the delay from current UTC time, clamps it to \([t_{\text{base}}, t_{\text{max}}]\), and adds uniform jitter.

---

## 4. Job Deferral vs Terminal Failure

When an error occurs during execution in `_process_job`:

```python
classification = classify_exception(exc)

if classification.disposition == FailureDisposition.RETRY and job.attempts < job.max_attempts:
    delay_seconds = compute_backoff_delay(
        attempt=job.attempts,
        retry_after=classification.retry_after_seconds,
    )
    next_attempt_at = utcnow() + timedelta(seconds=delay_seconds)
    book_repo.defer_job(
        job_id=job.job_id,
        next_attempt_at=next_attempt_at,
        error_code=classification.category.value,
    )
else:
    # Terminal failure or retries exhausted
    book_repo.fail_job(
        job_id=job.job_id,
        error_code=classification.category.value,
        error_detail=classification.message,
    )
    book_repo.fail_book(
        book_id=job.book_id,
        error_code=classification.category.value,
        error_detail=classification.message,
    )
```

### Deferral Invariants
- `defer_job` releases the active lease (`lease_token = None`, `lease_expires_at = None`, `lease_owner = None`).
- Resets job status from `running` back to `queued`.
- Records `next_attempt_at` timestamp.
- The poller query `claim_next_job` includes `(next_attempt_at IS NULL OR next_attempt_at <= now())`, guaranteeing the job will not be polled before its backoff expires.

---

## 5. Stuck Job Detection & Lease Recovery

If a worker container crashes, runs out of memory (OOM), or experiences an ungraceful shutdown while holding a job:

1. **Lease Expiration**: The job's `lease_expires_at` is fixed upon claim (default 120s) and refreshed via background heartbeats.
2. **Reclamation**: When `lease_expires_at < now()`, the job is considered expired.
3. **Queue Health Monitoring**: The operator endpoint `GET /v1/operations/monitoring/summary` detects:
   - `book_stuck_jobs`: Jobs pending longer than `stuck_book_pending_seconds` (1200s) or running past lease expiration plus grace period (`stuck_book_lease_grace_seconds` = 60s).
   - `expired_lease_jobs`: Real-time count of unheartbeated running jobs.
4. **Automatic Reclaim**: Upon next claim cycle or worker restart, expired leases are automatically re-eligible for claim by available workers.

