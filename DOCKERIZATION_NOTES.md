# MURA (Мұра) — Production Containerization & Platform Notes

This document captures key architectural decisions, platform differences, and production deployment guidance identified during the production containerization of MURA.

---

## 1. Audio Storage Architecture: Local Parity vs. Railway Production

### Problem
In the existing codebase (`docker-entrypoint.sh`), the API and Worker processes were historically colocated in a single container. The comment documented:
> *"The API writes uploaded audio to a local directory and the worker reads it back. Split across two Railway services those are two different filesystems and the handoff simply fails, because a Railway volume mounts to exactly one service. Until audio lives in object storage, sharing a container is the only arrangement in which the pipeline actually runs."*

### Why the Problem Exists
- In **Docker Compose (local production parity)**, multiple services (`api` and `worker`) can simultaneously mount the same named volume (`audio_data:/app/data/audio`).
- In **Railway (cloud multi-service deployment)**, persistent disk volumes are strictly 1:1 with a specific service. A volume attached to the `api` service cannot be mounted by the `worker` service.

### Solution & Recommended Action
1. **Local Development / Docker Compose:**
   - Handled cleanly via the shared named volume `audio_data` in `docker-compose.yml`. Both containers read and write to `/app/data/audio` with non-root UID `10001` (`mura`).
2. **Production Railway Deployment:**
   - **Do not colocate API and Worker in production.** API and Worker must scale independently (API handles bursty user traffic, while Worker handles compute-heavy AI operations).
   - **Migration Task:** Audio storage must migrate from `LocalAudioStorage` (`AUDIO_STORAGE_BACKEND=local`) to an Object Storage backend (`Supabase Storage`, `AWS S3`, or `Cloudflare R2`) where:
     - API uploads audio directly or issues pre-signed upload URLs.
     - Worker streams the audio file from object storage during transcription.
   - The backend storage abstraction (`src/mura/storage/audio.py`) already anticipates storage backends; implementing `S3AudioStorage` will be a dedicated, non-breaking task.
   - **Do not colocate API and Worker in production.** API and Worker scale independently (API handles user traffic, while Worker handles compute-heavy AI operations).
   - **Object Storage Implemented (Phase 1.3):** Audio storage seamlessly supports Supabase Storage (`AUDIO_STORAGE_BACKEND=supabase` via `SupabaseAudioStorage`).
     - API streams uploads directly to the private bucket (`mura-audio`).
     - Worker materializes temporary audio files during Whisper transcription and automatically cleans them up in a `finally` block.
   - For full details, see [`STORAGE_DEPLOYMENT.md`](file:///d:/Mura_production/STORAGE_DEPLOYMENT.md) and [`RAILWAY_DEPLOYMENT.md`](file:///d:/Mura_production/RAILWAY_DEPLOYMENT.md).

---

## 2. Queue Architecture: Preservation of PostgreSQL (No Redis)

### Architectural Rationale
- Target production diagrams frequently suggest introducing Redis for worker queues.
- However, MURA is strictly designed around the **"Evidence before facts"** principle and durable relational persistence.
- Work is claimed with:
  ```sql
  SELECT ... FOR UPDATE SKIP LOCKED
  ```
  under a robust database lease (`job_lease_seconds=300`) with periodic heartbeats (`job_heartbeat_seconds=60`).
- If a worker crashes mid-flight, the lease expires and another worker reclaims the recording job without dropping data or requiring state synchronization across external brokers.
- **Decision:** Redis is deliberately **not** introduced. PostgreSQL remains the single source of truth for both relational entity data and job queues.

---

## 3. Database Migrations & Container Startup

### Decoupling Migrations from Image Builds
- Per production rules, database migrations (`alembic upgrade head`) are **never** executed inside the Dockerfile `RUN` stage, because:
  1. Build environments should not require active database network access.
  2. Running migrations during image build bakes environment-specific state into immutable images.
- In `docker-compose.yml`, migrations are executed by a dedicated, ephemeral `db-migrate` service:
  ```yaml
  db-migrate:
    build:
      context: ./Mura_project
      dockerfile: Dockerfile
    command: ["alembic", "upgrade", "head"]
    depends_on:
      postgres:
        condition: service_healthy
  ```
- Both `api` and `worker` depend on `db-migrate: condition: service_completed_successfully`, eliminating race conditions.
- On **Railway**, migrations should run as a **Pre-deploy Release Command**:
  ```bash
  alembic upgrade head
  ```

---

## 4. Authentication Architecture: Dev Auth vs. Production OIDC

### Production OIDC Boundary
- `Mura Core` strictly requires asymmetric OIDC verification (`AUTH_MODE=oidc`, `RS256` or `ES256`).
- Core verifies `iss`, `aud`, `exp`, `nbf`, and `sub` against a configured JWKS URL (e.g. Clerk).
- `AUTH_MODE=disabled` is restricted to local/test environments and fails closed in staging/production.

### Dev Auth Guard
- The frontend includes a local development token issuer (`MURA_DEV_AUTH=true`).
- In `MURA-app/src/lib/auth/providers/dev/config.ts`, Dev Auth is guarded by three mandatory conditions:
  1. `MURA_DEV_AUTH=true`
  2. `NODE_ENV !== "production"`
  3. `VERCEL_ENV` unset or `"development"`
- In the production container (`NODE_ENV=production`), Dev Auth is inactive by design. Production deployments supply Clerk credentials (`NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` and `CLERK_SECRET_KEY`).

---

## 5. Security & Container Hardening

1. **Non-Root Execution:**
   - **Backend (`Mura_project/Dockerfile` & `Dockerfile.worker`):** Runs as user `mura` (UID `10001`, GID `10001`).
   - **Frontend (`MURA-app/Dockerfile`):** Runs as user `nextjs` (UID `1001`, GID `1001`).
2. **Minimal Final Images:**
   - Multi-stage builds:
     - Backend: Builder creates wheel/virtualenv; runtime image only includes compiled venv and application code.
     - Frontend: Standalone output (`output: "standalone"`) bundles only necessary `node_modules` and static assets, reducing image size from ~1.2 GB to ~150 MB.
3. **Container Healthchecks:**
   - `api`: Native Python healthcheck:
     ```bash
     python -c "import os, urllib.request; urllib.request.urlopen(f'http://localhost:{os.environ.get(\"PORT\", 8000)}/health')"
     ```
   - `frontend`: Native alpine `wget` check against `http://localhost:3000/`.
   - `postgres`: Native `pg_isready`.

---

## 6. Verification Commands (For Future Deployment Testing)

When running the stack on an environment with an active Docker daemon:

### 1. Build Containers
```bash
docker compose build
```

### 2. Launch Stack
```bash
docker compose up -d
```

### 3. Verify Container Status & Logs
```bash
docker compose ps
docker compose logs db-migrate
docker compose logs -f api
docker compose logs -f worker
```

### 4. Verify HTTP Endpoints
- **Core API Liveness:**
  ```bash
  curl -i http://localhost:8000/health
  # Expected: HTTP 200 {"status":"ok","service":"mura-core"}
  ```
- **Core API Readiness (Database Check):**
  ```bash
  curl -i http://localhost:8000/ready
  # Expected: HTTP 200 {"status":"ready","service":"mura-core","database":"ready"}
  ```
- **Frontend UI:**
  ```bash
  curl -i http://localhost:3000
  # Expected: HTTP 200 OK
  ```

### 5. Tear Down Stack
```bash
docker compose down
```
To also purge database and audio volumes:
```bash
docker compose down -v
```

