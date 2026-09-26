# MURA Staging & Production Deployment Runbooks

**Document Version**: 1.0.0  
**Scope**: Core API, Recording Worker, Book Worker, Cleanup Worker, PostgreSQL & Storage  

---

## 1. System Topology & Worker Architecture

MURA adopts an asynchronous worker architecture centered around PostgreSQL-backed transactional queues:

```text
[ Browser / Client ]
         │
         ▼
┌──────────────────┐       ┌────────────────────────┐
│  Core API (FastAPI)│──────▶│ PostgreSQL Database    │
└──────────────────┘       │  • families, users     │
                           │  • recordings, claims  │
┌──────────────────┐       │  • processing_jobs     │
│ Recording Worker │◀─────▶│  • book_jobs           │
└──────────────────┘       │  • storage_cleanup     │
                           └────────────────────────┘
┌──────────────────┐                   ▲
│   Book Worker    │◀──────────────────┤
└──────────────────┘                   │
┌──────────────────┐                   │
│  Cleanup Worker  │◀──────────────────┘
└──────────────────┘
```

Each worker process runs independently to isolate resource contention:
- **Core API**: Stateless, handling CRUD, authentication, audio streaming, and job enqueueing.
- **Recording Worker**: High-compute process pulling audio processing jobs (`asr`, `diarization`, `entity_resolution`, `archive_materialization`).
- **Book Worker**: Memory-intensive process compiling genealogical books, chapters, and PDF exports via Typst.
- **Cleanup Worker**: Periodic process executing data retention policies and orphan audio cleanup.

---

## 2. Environment Configuration Contract

### Common Database & Storage Configuration
| Variable | Required | Description | Example |
|---|---|---|---|
| `DATABASE_URL` | Yes | SQLAlchemy connection string | `postgresql+psycopg://user:pass@host:5432/mura` |
| `STORAGE_DRIVER` | Yes | Storage provider (`local` or `supabase`) | `local` / `supabase` |
| `AUDIO_STORAGE_DIR` | If local | Directory for raw and processed audio | `/var/mura/audio` |
| `BOOK_STORAGE_DIR` | If local | Directory for book compilation artifacts | `/var/mura/books` |
| `SUPABASE_URL` | If supabase | Supabase project URL | `https://xxxx.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | If supabase | Server-side storage key (never expose to client) | `eyJ...` |

### Core API Server Configuration
| Variable | Required | Description | Example |
|---|---|---|---|
| `CORE_API_KEY` | Yes | Pre-shared key for service-to-service communication | `sec_...` |
| `OPERATIONS_API_KEY` | Yes | Bearer token for `/v1/operations/*` endpoints | `ops_...` |
| `CLERK_ISSUER` | Yes | Clerk authentication issuer URL | `https://clerk.mura.kz` |
| `CLERK_JWKS_URL` | Yes | Clerk JWKS public keys URL | `https://clerk.mura.kz/.well-known/jwks.json` |
| `PORT` | No | API port (default: 8000) | `8000` |

### Worker Configurations
| Variable | Required | Description | Example |
|---|---|---|---|
| `WORKER_POLL_INTERVAL_SECONDS` | No | Polling backoff interval when queue is idle | `2.0` |
| `WORKER_LEASE_SECONDS` | No | Heartbeat expiration duration for worker fencing | `300` |
| `MAX_RETRIES_PER_JOB` | No | Maximum retry attempts before marking job stuck | `3` |

---

## 3. Container Deployment (Docker)

### Core API Container:
```bash
# Build API image
docker build -f Dockerfile -t mura-core-api:staging .

# Run API container
docker run -d --name mura-api \
  -p 8000:8000 \
  -e DATABASE_URL="$DATABASE_URL" \
  -e OPERATIONS_API_KEY="$OPERATIONS_API_KEY" \
  -e STORAGE_DRIVER="local" \
  -v /var/mura/audio:/var/mura/audio \
  mura-core-api:staging
```

### Worker Container:
```bash
# Build Worker image
docker build -f Dockerfile.worker -t mura-worker:staging .

# Run Recording Worker
docker run -d --name mura-recording-worker \
  -e DATABASE_URL="$DATABASE_URL" \
  -e WORKER_ROLE="recording" \
  mura-worker:staging

# Run Book Worker
docker run -d --name mura-book-worker \
  -e DATABASE_URL="$DATABASE_URL" \
  -e WORKER_ROLE="book" \
  mura-worker:staging
```

---

## 4. Zero-Downtime Migration & Rolling Deployment Protocol

1. **Pre-Deployment Backup**:
   Execute the backup drill before rolling updates:
   ```bash
   python scripts/ops/backup_restore_drill.py --source-db-url "$DATABASE_URL" --target-db-url "$VERIFY_DB_URL"
   ```
2. **Apply Database Migrations**:
   Run schema upgrade:
   ```bash
   alembic upgrade head
   ```
3. **Rolling Update Core API**:
   Deploy new API replicas. New replicas verify readiness via `/ready` before receiving ingress traffic.
4. **Restart Workers Gracefully**:
   Send `SIGTERM` to existing workers. Workers complete their current lease before exiting. New workers start and claim pending jobs from the queue.
5. **Post-Deployment Verification**:
   Execute monitoring smoke to confirm zero alerts and healthy queues:
   ```bash
   python scripts/ops/run_monitoring_smoke.py --base-url http://api.staging:8000 --operations-key "$OPERATIONS_API_KEY"
   ```
