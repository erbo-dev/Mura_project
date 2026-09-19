# MURA (Мұра) — Production Monitoring, AI Usage, Cost Tracking & Alerts
### Three-Tier Observability · Queue Health · AI Cost Ledger · Operational Playbooks

This document is the operational guide and runbook for monitoring MURA in production.

---

## 1. Monitoring Architecture (Three-Tier Strategy)

MURA achieves production-grade monitoring without introducing heavy observability infrastructure (no Prometheus, Grafana, Datadog, Redis, or Celery). Instead, observability is built upon three complementary, zero-maintenance tiers:

```text
┌───────────────────────────────────────────────────────────────────────────────────┐
│                           TIER 1: RAILWAY NATIVE METRICS                          │
│  - Container CPU utilization (%)          - Disk write / ephemeral usage          │
│  - Memory / RSS saturation (MB, %)        - Container restarts / OOM kills        │
│  - Network ingress / egress bandwidth     - Deployment status & rollout health    │
└────────────────────────────────────────┬──────────────────────────────────────────┘
                                         │
┌────────────────────────────────────────▼──────────────────────────────────────────┐
│                            TIER 2: SENTRY APM & ALERTS                            │
│  - Unhandled 5xx exceptions & crashes     - Sentry Performance (p50, p95, p99)    │
│  - Terminal worker failures (`job_failed`)- External HTTP client latency/errors   │
│  - React Error Boundaries (Next.js)       - Correlated tags: job_id, recording_id │
└────────────────────────────────────────┬──────────────────────────────────────────┘
                                         │
┌────────────────────────────────────────▼──────────────────────────────────────────┐
│                  TIER 3: SUPABASE POSTGRESQL DURABLE HEALTH & LEDGER              │
│  - Queue Health: pending, running, failed, oldest pending seconds, expired leases │
│  - AI Usage Ledger (`ai_usage_events`): tokens, duration, Decimal USD cost        │
│  - Stuck-Job Engine: pending_too_long, lease_expired, max_retries_exceeded        │
│  - Privileged Operator Endpoint: GET /v1/operations/monitoring/summary            │
└───────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Tier 1: Railway Native Container Metrics

Railway provides built-in metrics collection and alerting directly in the project dashboard (`Settings -> Metrics`).

### 2.1 Monitored Metrics
- **CPU Saturation (%)**: Tracked per container (`mura-api` and `mura-worker`).
- **Memory RSS (MB / %)**: Tracked against provisioned container RAM.
- **Restarts / CrashLoopBackOff**: Tracked per replica.
- **Network Egress**: Tracked for audio download/upload and external API calls.

### 2.2 Railway Alert Configuration
Configure notifications via Slack, PagerDuty, or Webhook in Railway dashboard:

| Alert Name | Service | Metric & Condition | Evaluation Window | Severity | Action |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Worker High Memory** | `mura-worker` | Memory > 85% of limit | Sustained 5m | Warning | Check for huge audio files or memory leak |
| **Worker OOM / Restarts** | `mura-worker` | Restart count > 2 | 10m window | Critical | Check worker logs `@event:"job_started"` before crash |
| **API High Latency / CPU** | `mura-api` | CPU > 90% | Sustained 5m | Warning | Evaluate horizontal scaling or slow upstream requests |
| **Container Disk Warning** | `mura-worker` | Ephemeral disk > 80% | Sustained 5m | Warning | Check `/tmp` audio materialization cleanup |

---

## 3. Tier 2: Sentry Error Tracking & Alerts

Sentry captures operational exceptions and tracks transaction durations across backend and worker without capturing user audio or transcripts.

### 3.1 Alert Rules in Sentry

1. **Production 5xx Surge (API)**:
   - **Condition**: Number of unhandled server exceptions (`level:error`) on service `mura-api` > 5 within 1 minute.
   - **Action**: High-priority alert to on-call channel.
   - **Triage**: Filter by `@route` and inspect the correlated `@request_id`.

2. **Terminal Worker Failure (`job_failed`)**:
   - **Condition**: Event tagged with `event:job_failed` occurs on `mura-worker`.
   - **Action**: Alert to engineering on-call channel.
   - **Triage**: Extract `job_id`, `recording_id`, and `attempt` from Sentry tags and query `/v1/jobs/{job_id}/trace`.

3. **AI Provider Outage / Error Spike**:
   - **Condition**: Failures tagged with `error_code:deepseek_error`, `error_code:asr_unavailable`, or `error_code:whisper_timeout` > 5 within 10 minutes.
   - **Action**: Alert to AI infrastructure channel.

---

## 4. Tier 3: Supabase PostgreSQL Queue Health & Stuck-Job Diagnostics

MURA preserves its durable `SELECT ... FOR UPDATE SKIP LOCKED` PostgreSQL queue with worker leases and heartbeats. Queue health and stuck-job detection are built into the core codebase (`mura.monitoring.QueueHealthService`).

### 4.1 Derived Stuck-Job Thresholds
Stuck-job detection does not use arbitrary magic constants. Thresholds are derived mathematically from active configuration (`CoreSettings`):

```python
thresholds = MonitoringThresholds.from_settings(settings)
# 1. pending_threshold_seconds = job_lease_seconds + asr_request_timeout_seconds (default: 300s + 900s = 1200s)
# 2. lease_grace_seconds = job_heartbeat_seconds * 2 (default: 60s * 2 = 120s)
# 3. max_retries = 3
```

A job is classified as **stuck** if:
1. `pending_too_long`: Status is `queued`, and wait time exceeds `pending_threshold_seconds` (1200s).
2. `lease_expired`: Status is non-terminal (`transcribing` or `extracting`), and `lease_expires_at + lease_grace_seconds` is in the past.
3. `max_retries_exceeded`: Status is `queued`, and `attempt_count >= max_retries` (3).

> [!NOTE]
> Diagnostic queries are strictly read-only and **never mutate or terminate jobs**. The existing worker lease-expiry mechanism automatically reclaims orphaned jobs safely when leases expire.

---

## 5. Tier 3: AI Provider Usage & Cost Accounting Ledger

MURA includes a durable, append-only AI usage ledger (`ai_usage_events`) backed by PostgreSQL.

### 5.1 Privacy Guarantees
The AI usage ledger records operational metadata only:
- **Recorded**: `provider`, `model`, `operation` (e.g. `cleaner`, `extractor`, `asr_transcribe`), `status` (`success`/`failed`), `tokens` (`prompt_tokens`, `completion_tokens`, `prompt_cache_hit_tokens`, `prompt_cache_miss_tokens`), `audio_duration_seconds`, `request_seconds`, `estimated_cost_usd`, `pricing_version`.
- **Correlated**: `job_id`, `recording_id`, `request_id`, `family_id`, `attempt`.
- **STRICTLY EXCLUDED**: Prompts, completion text, transcripts, audio bytes, family member names, relationships, quotes, cookies, tokens.

### 5.2 Deterministic Financial Arithmetic
Cost calculation is implemented in `mura.cost.calculate_ai_cost` using Python `Decimal` arithmetic to eliminate floating-point rounding errors:

- **DeepSeek Cache-Aware Pricing**:
  - `deepseek-v4-flash`, `deepseek-chat`, `deepseek-v3`:
    - Cache hit prompt: \$0.07 / 1,000,000 tokens
    - Cache miss prompt: \$0.27 / 1,000,000 tokens
    - Output completion: \$1.10 / 1,000,000 tokens
  - `deepseek-v4-pro`, `deepseek-reasoner`:
    - Cache hit prompt: \$0.14 / 1,000,000 tokens
    - Cache miss prompt: \$0.55 / 1,000,000 tokens
    - Output completion: \$2.19 / 1,000,000 tokens
- **Whisper ASR Pricing**:
  - `whisper-1`: \$0.006 / minute (\$0.0001 / second of audio)
- **Unknown Models**:
  - If a model or provider is unrecognized, `calculate_ai_cost` returns `(None, None)`. **It never fabricates or guesses costs.**

---

## 6. Privileged Operator Monitoring Endpoint

MURA exposes an operator-only monitoring summary endpoint:
```http
GET /v1/operations/monitoring/summary
```

### 6.1 Authentication & Security Controls
- **Guarded by `OPERATIONS_API_KEY`**: Requires HTTP header `Authorization: Bearer <OPERATIONS_API_KEY>` or `X-Operations-Token: <OPERATIONS_API_KEY>`.
- **401/403 Enforced**: Rejects missing or invalid tokens with canonical error envelopes.
- **Frontend Proxy Shield**: The Next.js API proxy (`/api/mura/[...path]`) explicitly excludes `/v1/operations/*` from its route allowlist. It is completely unreachable from public web browsers.

### 6.2 Sample Response
```json
{
  "queue": {
    "pending_jobs": 0,
    "running_jobs": 1,
    "failed_jobs": 2,
    "oldest_pending_seconds": null,
    "expired_lease_jobs": 0
  },
  "book_queue": {
    "pending_jobs": 0,
    "running_jobs": 1,
    "failed_jobs": 0,
    "oldest_pending_seconds": null,
    "expired_lease_jobs": 0
  },
  "pipeline_24h": {
    "completed_24h": 42,
    "failed_24h": 1,
    "avg_duration_seconds": 18.45
  },
  "ai_24h": {
    "total_prompt_tokens": 128500,
    "total_completion_tokens": 34200,
    "total_audio_seconds": 960.0,
    "total_estimated_cost_usd": "0.142580",
    "by_provider": {
      "deepseek": {
        "calls": 84,
        "prompt_tokens": 128500,
        "completion_tokens": 34200,
        "estimated_cost_usd": "0.046580"
      },
      "whisper": {
        "calls": 14,
        "audio_seconds": 960.0,
        "estimated_cost_usd": "0.096000"
      }
    },
    "by_operation": {
      "cleaner": { "calls": 42, "cost_usd": "0.012500" },
      "extractor": { "calls": 42, "cost_usd": "0.034080" },
      "asr_transcribe": { "calls": 14, "cost_usd": "0.096000" }
    }
  },
  "stuck_jobs": [],
  "book_stuck_jobs": [],
  "thresholds": {
    "pending_threshold_seconds": 1200.0,
    "lease_grace_seconds": 120.0,
    "max_retries": 3
    "max_retries": 3,
    "stuck_book_pending_seconds": 1200.0,
    "stuck_book_lease_grace_seconds": 60.0
  }
}
```

---

## 7. Incident Playbooks

### Playbook 1: "User reports recording is stuck in processing"

1. **Obtain Identifiers**:
   Ask user or inspect frontend for `recording_id` (e.g. `rec_...`).
2. **Query Operator Summary**:
   ```bash
   curl -H "Authorization: Bearer $OPERATIONS_API_KEY" \
     https://api.mura.kz/v1/operations/monitoring/summary | jq .stuck_jobs
   ```
3. **Inspect Database Job State**:
   ```sql
   SELECT job_id, recording_id, status, attempt_count, lease_owner, lease_expires_at, updated_at
   FROM recording_jobs
   WHERE recording_id = 'rec_...';
   ```
4. **Search Railway Logs**:
   Query Railway Log Explorer:
   ```text
   @recording_id:"rec_..."
   ```
   Check the sequence of events:
   - `job_claimed` -> Did worker acquire the lease?
   - `job_started` -> Did transcription begin?
   - Look for unhandled exceptions or ASR timeouts.
5. **Inspect Detailed Trace**:
   ```bash
   curl -H "Authorization: Bearer $CORE_API_KEY" \
     https://api.mura.kz/v1/jobs/{job_id}/trace
   ```
   Check which stage timed out (`asr_transcription`, `cleaner_pass`, `extraction_pass`).
6. **Resolution**:
   - If `lease_expires_at` is expired, the next worker poll cycle will automatically reclaim the job and retry (up to `max_retries = 3`).
   - If attempts reached 3, the job moves to `failed` and creates a conflict review item so family data is preserved.

---

### Playbook 2: "AI bill unexpectedly increases / runaway cost"

1. **Inspect 24h Cost Breakdown**:
   ```bash
   curl -H "Authorization: Bearer $OPERATIONS_API_KEY" \
     https://api.mura.kz/v1/operations/monitoring/summary | jq .ai_24h
   ```
2. **Identify Top Cost Driver**:
   Check whether cost increase is driven by `deepseek` token volume or `whisper` audio duration.
3. **Analyze Cache Hit Ratio**:
   ```sql
   SELECT
     model,
     SUM(prompt_cache_hit_tokens) AS cache_hits,
     SUM(prompt_cache_miss_tokens) AS cache_misses,
     ROUND(SUM(prompt_cache_hit_tokens)::numeric / NULLIF(SUM(prompt_cache_hit_tokens + prompt_cache_miss_tokens), 0) * 100, 2) AS hit_ratio_pct
   FROM ai_usage_events
   WHERE provider = 'deepseek' AND created_at > NOW() - INTERVAL '24 hours'
   GROUP BY model;
   ```
   *Expected*: Structured anchor prompts achieve >80% prompt cache hit ratio on DeepSeek. If hit ratio dropped, verify prompt prefixes were not altered.
4. **Find Outlier Jobs**:
   ```sql
   SELECT job_id, recording_id, SUM(prompt_tokens + completion_tokens) AS total_tokens, SUM(estimated_cost_usd) AS cost
   FROM ai_usage_events
   WHERE created_at > NOW() - INTERVAL '24 hours'
   GROUP BY job_id, recording_id
   ORDER BY cost DESC
   LIMIT 10;
   ```

---

### Playbook 3: "Worker crashes or enters OOM crash loop"

1. **Check Railway Metrics**:
   Open Railway Dashboard -> `mura-worker` -> Metrics tab. Check Memory graph to see if memory spikes to 100% (OOM).
2. **Identify Offending Recording**:
   Look at the last log lines before the crash:
   ```text
   @service:"mura-worker" @event:"job_started"
   ```
   Check the `recording_id` of the last claimed job.
3. **Verify Audio Duration & Size**:
   ```sql
   SELECT recording_id, content_type, file_size_bytes, audio_duration_seconds
   FROM family_recordings
   WHERE recording_id = 'rec_...';
   ```
   Check if a user uploaded an abnormally large file exceeding memory bounds.
4. **Crash Recovery**:
   When the worker restarts, PostgreSQL automatically releases the expired lease after `job_lease_seconds` (300s). The job will retry up to attempt 3. If it consistently crashes, it moves to `failed` without crashing future jobs.

---

### Playbook 4: "Family Book generation is stuck in queued or running"

1. **Query Operator Summary for Stuck Book Jobs**:
   ```bash
   curl -H "Authorization: Bearer $OPERATIONS_API_KEY" \
     https://api.mura.kz/v1/operations/monitoring/summary | jq .book_stuck_jobs
   ```
2. **Inspect Database Book Job Row**:
   ```sql
   SELECT job_id, book_id, stage, status, attempts, max_attempts, next_attempt_at, lease_owner, lease_expires_at, updated_at
   FROM book_jobs
   WHERE book_id = 'book_...';
   ```
3. **Verify Chapter Progress**:
   ```sql
   SELECT chapter_id, chapter_number, status, word_count, repair_attempts, error_code, updated_at
   FROM book_chapters
   WHERE book_id = 'book_...'
   ORDER BY chapter_number ASC;
   ```
4. **Determine Stuck Cause**:
   - `next_attempt_at > NOW()`: Job is under deliberate exponential backoff / `Retry-After` delay due to transient provider rate limit. No action required unless delayed for >30 minutes.
   - `status = 'running'` and `lease_expires_at < NOW()`: Worker holding lease terminated ungracefully. Lease will automatically be reclaimed on next claim cycle.
   - `status = 'failed'`: Book was terminally rejected (e.g. content policy, quota violation, or kinship grounding blocker). Inspect `error_code` and `error_detail` on `books` table.


