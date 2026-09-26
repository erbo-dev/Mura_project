# MURA Production & Staging Monitoring Specification

**Document Version**: 1.0.0  
**Scope**: Core API, Recording Worker, Book Worker, Storage Cleanup, Database Health  
**Target Audience**: On-Call SREs, Reliability Engineers, DevOps  

---

## 1. Health & Readiness Probes

MURA provides two unauthenticated probe endpoints designed for container orchestrators (Kubernetes, AWS ECS, GCP Cloud Run) and load balancers:

### Liveness Probe: `/health`
- **Purpose**: Verify that the FastAPI event loop is active and accepting requests.
- **Method**: `GET /health`
- **Expected Response**: `HTTP 200 OK`
```json
{
  "status": "ok"
}
```
- **Failure Condition**: Process hang, deadlocked event loop, out-of-memory kill.
- **Orchestrator Action**: Restart container after 3 consecutive failures (30s timeout).

### Readiness Probe: `/ready`
- **Purpose**: Verify that the database connection pool is healthy and capable of executing queries.
- **Method**: `GET /ready`
- **Expected Response**: `HTTP 200 OK`
```json
{
  "status": "ready",
  "database": "connected"
}
```
- **Failure Condition**: `HTTP 503 Service Unavailable` if database connectivity is lost or pool is exhausted.
- **Orchestrator Action**: Remove container from active traffic routing until probe recovers.

---

## 2. Operations Monitoring Summary (`/v1/operations/monitoring/summary`)

- **Purpose**: Aggregated operational health metrics, queue backlog, worker lease health, cleanup status, and 24h AI budget consumption.
- **Method**: `GET /v1/operations/monitoring/summary`
- **Authentication**: Mandatory `Authorization: Bearer <OPERATIONS_API_KEY>`. Requests with invalid or missing keys return `HTTP 401 Unauthorized` or `HTTP 403 Forbidden`.
- **Privacy Contract**: This endpoint is strictly forbidden from returning PII, audio transcripts, user quotes, story prose, email addresses, or family member names. It emits only statistical counts, job IDs, durations, and byte totals.

### Example Response Payload:
```json
{
  "status": "healthy",
  "timestamp": "2026-09-26T08:30:00Z",
  "queue": {
    "pending": 4,
    "processing": 2,
    "completed_24h": 128,
    "failed_24h": 1,
    "oldest_pending_seconds": 45,
    "expired_leases": 0
  },
  "stuck_jobs": [],
  "cleanup": {
    "pending": 0,
    "attempts_exhausted": 0
  },
  "book_queue": {
    "pending": 1,
    "processing": 1,
    "expired_leases": 0
  },
  "usage_24h": {
    "total_audio_seconds": 3840.5,
    "total_ai_input_tokens": 124500,
    "total_ai_output_tokens": 31200
  }
}
```

---

## 3. Operational Alert Thresholds & SRE Playbooks

The automated smoke tool (`scripts/ops/run_monitoring_smoke.py`) derives the following alerts directly from `/v1/operations/monitoring/summary`:

| Metric Name | Condition | Severity | Description | SRE Playbook |
|---|---|---|---|---|
| `queue.pending` | `> 100` | **WARNING** | High pending audio recording backlog. | 1. Check recording worker CPU/memory usage.<br>2. Scale recording worker replicas.<br>3. Verify external Whisper/DeepSeek rate limits. |
| `queue.oldest_pending_seconds` | `> 1200` (20m) | **CRITICAL** | Audio processing starvation. | 1. Check if recording workers are actively claiming jobs.<br>2. Verify DB connection pool for workers.<br>3. Restart dead worker pods. |
| `queue.expired_leases` | `> 0` | **WARNING** | Worker crash or lease timeout during audio processing. | 1. Inspect worker logs for OOM or unhandled exceptions.<br>2. Check network connectivity between worker and DB.<br>3. The system automatically fences the job; monitor for retry. |
| `stuck_jobs` | `count > 0` | **WARNING** | Jobs in failed state or exceeded retry budget. | 1. Fetch trace: `GET /v1/jobs/{job_id}/trace`.<br>2. Identify stage error (ASR, Diarization, LLM, or Storage).<br>3. Reset job or notify customer support. |
| `cleanup.attempts_exhausted` | `> 0` | **WARNING** | Retention audio cleanup repeatedly failed (>=3 attempts). | 1. Inspect storage bucket permissions (`mura-audio`).<br>2. Verify storage provider endpoint and credentials.<br>3. Run `python scripts/ops/reconcile_storage.py` to identify affected keys. |
| `book_queue.expired_leases` | `> 0` | **WARNING** | Book compilation worker timed out or died. | 1. Check book worker memory allocation (typst/pdf compilation).<br>2. Scale book worker pods if concurrent generation load is high. |

---

## 4. Automated Smoke Verification Tool

To verify monitoring and alerting in any staging or production environment, execute:

```bash
python scripts/ops/run_monitoring_smoke.py \
  --base-url http://127.0.0.1:8000 \
  --operations-key "$OPERATIONS_API_KEY" \
  --db-url "$DATABASE_URL" \
  --output-json .mura/reports/monitoring_smoke.json
```

The tool performs:
1. HTTP GET `/health` probe (asserts HTTP 200).
2. HTTP GET `/ready` probe (asserts HTTP 200).
3. Direct Alembic single-head migration verification against the database.
4. HTTP GET `/v1/operations/monitoring/summary` authenticated with `--operations-key`.
5. Privacy inspection of summary payload (asserts 0 forbidden keys/PII).
6. Metric threshold evaluation generating structured alert recommendations.
