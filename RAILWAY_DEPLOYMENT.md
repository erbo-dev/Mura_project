# MURA (Мұра) — Production Deployment Runbook
### Target Platforms: Supabase (PostgreSQL + Storage) · Clerk (OIDC) · Railway (API + Worker) · Vercel (Next.js)

This runbook provides complete, step-by-step instructions for deploying the MURA production infrastructure from scratch.

---

## 1. Architecture Overview

```text
[ Browser ]
    │
    ▼ HTTPS
[ Vercel: Next.js App ] (MURA-app)
    │
    │ Server-side Proxy (MURA_API_URL)
    ▼ HTTPS
[ Railway: FastAPI Core API ] (Mura_project/Dockerfile)
    │                             │
    │ Claims durable jobs         │ Reads/writes
    ▼ (SKIP LOCKED)               ▼
[ Railway: Worker ] ─────────▶ [ Supabase: PostgreSQL 16 ]
(Mura_project/Dockerfile.worker)  │
                                  ▼
                           [ Supabase Storage ]

[ Browser / Vercel ] ─────────▶ [ Clerk OIDC ]
```

- **Frontend (Vercel):** Serves UI, manages client session, and proxies requests to Core API via `MURA_API_URL`.
- **Backend API (Railway):** FastAPI handling HTTP requests, authorization policies, job queueing, and capabilities. Runs as non-root user `mura` (UID `10001`).
- **Worker (Railway):** Standalone `mura-worker` process claiming jobs from PostgreSQL via `SELECT ... FOR UPDATE SKIP LOCKED` with heartbeats and leases.
- **Database (Supabase):** PostgreSQL with linear Alembic migrations. Single source of truth for all family archives, relationships, and durable job states.
- **Object Storage (Supabase):** Private audio and generated Book artifact buckets accessed only by server-side service-role credentials.
- **Identity (Clerk):** Frontend session provider. The server-side frontend forwards a Clerk JWT template token; Core validates it as standards-based OIDC/JWT using the configured issuer, audience, JWKS URL, and asymmetric algorithm allowlist.

---

## 2. Prerequisites

1. **Accounts:**
   - [Supabase](https://supabase.com)
   - [Clerk](https://clerk.com)
   - [Railway](https://railway.com)
   - [Vercel](https://vercel.com)
2. **AI Provider Credentials:**
   - **DeepSeek API Key:** From [DeepSeek Platform](https://platform.deepseek.com) (`sk-...`)
   - **Whisper / OpenAI API Key:** From OpenAI or any compatible ASR provider
3. **Repository Access:**
   - Access to the GitHub repository containing `Mura_project` and `MURA-app`.

---

## 3. Step 1: Provision Supabase (Database & Private Storage)

### 1.1 Create Supabase Project
1. Go to the Supabase Dashboard and click **New project**.
2. Set the project name (e.g. `mura-production`), generate a strong database password, and select your preferred region (e.g. `eu-central-1` or `us-east-1`).
3. Note your **Project Reference ID** (e.g. `abcdefghijklmnop`).

### 1.2 Get Database Connection String
1. Navigate to **Project Settings** → **Database** → **Connection String**.
2. Select the **URI** tab. Choose **Direct connection** (Port `5432`) or **Session Pooler** (Port `5432`).
3. Replace the protocol scheme with SQLAlchemy's `postgresql+psycopg://`:
   ```text
   postgresql+psycopg://postgres:[YOUR-PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres?sslmode=require
   ```
   > [!IMPORTANT]
   > Do **not** use the Transaction Pooler (Port `6543`) for Alembic migrations or Core API, as startup parameters (`statement_timeout`) and DDL migrations require direct session mode.
   > Staging/production startup rejects URLs lacking an explicit TLS mode (`require`, `verify-ca`, or `verify-full`). `require` prevents plaintext; prefer `verify-full` with a trusted `sslrootcert` where the selected Supabase connection endpoint supports certificate/hostname verification. Keep credentials URL-encoded and never print the connection string.

### 1.3 Configure Clerk OIDC
1. Create or select the Clerk application used by `MURA-app`.
2. Configure the production frontend domain in Clerk.
3. Create the JWT template referenced by `CLERK_JWT_TEMPLATE` (the repository default is `mura-core`).
4. Configure the template so the token audience matches Core's `AUTH_AUDIENCE` exactly.
5. Record:
   - the Clerk publishable key for the browser;
   - the Clerk secret key for the Next.js server;
   - the Clerk issuer URL for the environment;
   - the issuer JWKS URL.
6. Keep Core provider-agnostic: it validates issuer, audience, time claims and asymmetric signatures from the configured JWKS endpoint; it does not use a Clerk SDK.

---

## 4. Step 2: Provision Railway (API & Worker)

### 2.1 Create Railway Project
1. Log into Railway and click **New Project** → **Deploy from GitHub repo**.
2. Select your repository.

---

### 2.2 Configure Service 1: `mura-api` (FastAPI Core)

1. In the Railway dashboard, rename the deployed service to `mura-api`.
2. Go to **Settings**:
   - **Source:**
     - Root Directory: `Mura_project`
   - **Build:**
     - Builder: `Dockerfile`
     - Dockerfile Path: `Dockerfile`
   - **Deploy:**
     - Pre-deploy Command (Release hook):
       ```bash
       alembic upgrade head
       ```
       *(This automatically migrates the Supabase database before new revisions take traffic).*
   - **Networking:**
     - Click **Generate Domain** (e.g. `mura-api-production.up.railway.app`).
3. Go to **Variables** and add the production environment variables:

| Variable | Recommended Value | Notes |
| :--- | :--- | :--- |
| `MURA_ENVIRONMENT` | `production` | Enables strict fail-closed security |
| `DATABASE_URL` | `postgresql+psycopg://postgres:[PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres?sslmode=require` | Supabase Direct connection with mandatory TLS |
| `DATABASE_AUTO_CREATE` | `false` | Migrations are owned by Alembic |
| `CORE_API_KEY` | *(Generate 32+ chars)* | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `OPERATIONS_API_KEY` | *(Generate 32+ chars)* | Must differ from `CORE_API_KEY` |
| `WORKER_REGISTRATION_TOKEN` | *(Generate 32+ chars)* | Service-to-service token |
| `ASR_PROVIDER` | `whisper` | Must match every recording worker or capabilities will misreport processing |
| `WHISPER_API_KEY` | *(Provider key)* | API must validate capability configuration; server-only |
| `WHISPER_BASE_URL` | `https://api.openai.com/v1` | Must match worker |
| `WHISPER_MODEL` | `whisper-1` | Must match worker |
| `AUTH_MODE` | `oidc` | Production requires OIDC |
| `AUTH_ISSUER` | `https://<your-clerk-issuer>` | Exact Clerk issuer for this environment |
| `AUTH_AUDIENCE` | `mura-core` | Must match the Clerk JWT template `aud` claim |
| `AUTH_JWKS_URL` | `https://<your-clerk-issuer>/.well-known/jwks.json` | Trusted JWKS endpoint configured by deployment, never from the token |
| `AUTH_ALLOWED_ALGORITHMS` | `RS256` | Keep asymmetric verification only; match the keys issued by your Clerk instance |
| `CORS_ALLOWED_ORIGINS` | `https://your-mura-app.vercel.app` | Exact Vercel domain without trailing slash |
| `ALLOWED_HOSTS` | `mura-api-production.up.railway.app,api.mura.kz` | Comma-separated HTTP Host allowlist read by Core |
| `FORWARDED_ALLOW_IPS` | deployment-verified trusted ingress IPs/CIDRs only | Uvicorn ignores forwarded headers from untrusted peers. `127.0.0.1` alone is only correct if ingress is local. BLOCKED pending Railway-side ingress verification; never use `*` |
| `AUDIO_STORAGE_BACKEND` | `supabase` | Production Object Storage (`supabase` for prod, `local` for dev) |
| `BOOK_STORAGE_BACKEND` | `supabase` | Production Book Artifact Storage (`supabase` for prod, `local` for dev) |
| `SUPABASE_URL` | `https://[PROJECT-REF].supabase.co` | Supabase Project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | *(Secret Service Role Key)* | From Supabase Project Settings → API |
| `SUPABASE_STORAGE_BUCKET` | `mura-audio` | Private audio storage bucket |
| `SUPABASE_BOOKS_BUCKET` | `mura-books` | Private book artifacts bucket (PDF/EPUB) |
| `BOOK_MAX_ACTIVE_PER_FAMILY` | `1` | At most 1 active book job per family |
| `BOOK_MAX_CREATED_PER_FAMILY_PER_DAY` | `3` | Daily book creation ceiling per family (rate limit) |
| `STUCK_BOOK_PENDING_SECONDS` | `1200` | Stuck book threshold for pending state (seconds) |
| `STUCK_BOOK_LEASE_GRACE_SECONDS` | `60` | Stuck book lease grace period (seconds) |
| `CORE_MAX_UPLOAD_MB` | `25` | Maximum upload size |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `LOG_FORMAT` | `json` | Single-line structured JSON logs for Railway |
| `SENTRY_DSN` | *(Optional)* | Sentry DSN for backend error capture |
| `SENTRY_ENVIRONMENT` | `production` | Sentry environment tag |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.1` | Lightweight APM tracing (health checks filtered to 0.0) |

> [!TIP]
> Notice that `PORT` is **not** set manually. Railway injects dynamic `$PORT` automatically, which `Mura_project/Dockerfile` evaluates at runtime via `sh -c "exec uvicorn ... --port \"${PORT:-8000}\""`.

---

### 2.3 Configure Worker Services (Standalone)

1. In the same Railway project, click **New Service** → **GitHub Repo** (select the same repository).
2. Create three services from the same image: `mura-recording-worker`, `mura-book-worker`, and `mura-cleanup-worker`.
3. Go to **Settings**:
   - **Source:**
     - Root Directory: `Mura_project`
   - **Build:**
     - Builder: `Dockerfile`
     - Dockerfile Path: `Dockerfile.worker`
   - **Deploy:**
     - Start Command: leave default (uses `CMD ["mura-worker"]` from image).
     - Pre-deploy Command: leave empty (Alembic is already run by `mura-api`).
   - **Networking:**
     - Do **not** generate a public domain (worker has no HTTP surface).
4. Go to **Variables** and add:

| Variable | Value | Notes |
| :--- | :--- | :--- |
| `MURA_ENVIRONMENT` | `production` | Strict production mode |
| `DATABASE_URL` | `postgresql+psycopg://postgres:[PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres?sslmode=require` | Same TLS-enforced Supabase URL |
| `WORKER_QUEUES` | `recording`, `book`, or `cleanup` | Set one queue per production service; local compose may use `recording,book,cleanup` |
| `DEEPSEEK_API_KEY` | `sk-...` | Only for recording/book services; not required by cleanup |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | DeepSeek endpoint |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | Primary model |
| `DEEPSEEK_FALLBACK_MODEL` | `deepseek-v4-pro` | Fallback model |
| `ASR_PROVIDER` | `whisper` | Primary ASR |
| `WHISPER_API_KEY` | `sk-...` | Only for recording service; must match API configuration |
| `WHISPER_BASE_URL` | `https://api.openai.com/v1` | Whisper API endpoint |
| `WHISPER_MODEL` | `whisper-1` | Model name |
| `AUDIO_STORAGE_BACKEND` | `supabase` | Matches API |
| `BOOK_STORAGE_BACKEND` | `supabase` | Matches API |
| `SUPABASE_URL` | `https://[PROJECT-REF].supabase.co` | Matches API |
| `SUPABASE_SERVICE_ROLE_KEY` | *(Secret Service Role Key)* | Matches API |
| `SUPABASE_STORAGE_BUCKET` | `mura-audio` | Matches API |
| `SUPABASE_BOOKS_BUCKET` | `mura-books` | Matches API |
| `BOOK_JOB_LEASE_SECONDS` | `600` | Lease duration for book generation jobs (seconds) |
| `BOOK_JOB_HEARTBEAT_SECONDS`| `60` | Lease renewal heartbeat interval (seconds) |
| `BOOK_JOB_POLL_INTERVAL_SECONDS` | `2.0` | Queue poll frequency (seconds) |
| `LOG_LEVEL` | `INFO` | Logging level |
| `LOG_FORMAT` | `json` | Single-line structured JSON logs for Railway |
| `SENTRY_DSN` | *(Optional)* | Sentry DSN for worker error capture |
| `SENTRY_ENVIRONMENT` | `production` | Sentry environment tag |

The API alone requires `CORE_API_KEY`, `OPERATIONS_API_KEY`, `WORKER_REGISTRATION_TOKEN`, OIDC and CORS/host controls. Do not copy these API-only credentials into worker services. Align `ASR_PROVIDER`, Whisper endpoint/model, database, and private buckets across API and recording worker. The `book` worker does not need the Whisper secret; the `cleanup` worker needs neither Whisper nor DeepSeek secrets.

---

## 5. Step 3: Provision Vercel (Next.js Frontend)

### 5.1 Import Project
1. In the Vercel Dashboard, click **Add New...** → **Project**.
2. Select your GitHub repository.
3. Configure project settings:
   - **Framework Preset:** `Next.js`
   - **Root Directory:** Click Edit and select `MURA-app`
   - **Build Command:** `next build` (configured automatically via `vercel.json`)
   - **Output Directory:** `.next`

### 5.2 Environment Variables
Add the following variables in the Vercel Project Settings:

| Variable | Value | Description |
| :--- | :--- | :--- |
| `MURA_API_URL` | `https://mura-api-production.up.railway.app` | Railway public URL of `mura-api` (no trailing slash) |
| `MURA_AUTH_PROVIDER` | `clerk` | Explicit selection required; missing value fails closed in production/preview |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | *(Clerk publishable key)* | Public browser credential |
| `CLERK_SECRET_KEY` | *(Clerk secret key)* | Server-only; never expose through `NEXT_PUBLIC_*` |
| `CLERK_JWT_TEMPLATE` | `mura-core` | JWT template whose audience matches `AUTH_AUDIENCE` |
| `NEXT_PUBLIC_CLERK_SIGN_IN_URL` | `/sign-in` | Sign-in route |
| `NEXT_PUBLIC_CLERK_SIGN_UP_URL` | `/sign-up` | Sign-up route |
| `NEXT_PUBLIC_SENTRY_DSN` | *(Optional)* | Frontend Sentry DSN for error boundary tracking |
| `SENTRY_ENVIRONMENT` | `production` | Sentry environment tag |

### 5.3 Deploy
Click **Deploy**. Vercel will build and assign your production domain (e.g. `https://mura-app.vercel.app`).

---

## 6. Step 4: Verification & Smoke Testing

### 6.1 Check Core API Liveness
```bash
curl -i https://mura-api-production.up.railway.app/health
```
**Expected Output:**
```json
HTTP/1.1 200 OK
Content-Type: application/json

{"status":"ok","service":"mura-core"}
```

### 6.2 Check Core API Readiness (Supabase Database Connectivity)
```bash
curl -i https://mura-api-production.up.railway.app/ready
```
**Expected Output:**
```json
HTTP/1.1 200 OK
Content-Type: application/json

{"status":"ready","service":"mura-core","database":"ready"}
```

### 6.3 Check Worker Logs
In Railway, open the `mura-worker` service and click **View Logs**.
Verify startup (formatted in single-line JSON):
```json
{"timestamp":"2026-09-17T15:00:00.000Z","level":"INFO","logger":"mura.worker","message":"worker starting worker_id=worker_... lease=300.0s heartbeat=60.0s","service":"mura-worker"}
```

### 6.4 Check Frontend & Auth Flow
1. Open `https://your-mura-app.vercel.app` in your browser.
2. Sign in via Clerk.
3. Verify that `/home` loads your archive and family context without 401 or 403 errors.

---

## 7. Storage & Observability Architecture

### Production Object Storage (Supabase Storage)
- In production Railway deployments, `mura-api` and `mura-worker` run as separate services with independent filesystems.
- Production storage uses **Supabase Storage** (`AUDIO_STORAGE_BACKEND=supabase`) via a private bucket (`mura-audio`).
- The API streams uploads directly to the private bucket; the Worker materializes temporary audio files during Whisper transcription and automatically cleans them up in a `finally` block.
- For complete setup instructions, bucket configuration, and security models, see [`STORAGE_DEPLOYMENT.md`](STORAGE_DEPLOYMENT.md).

### Structured Logging & Sentry Observability
- All services emit structured JSON logs (`LOG_FORMAT=json`) with request correlation (`request_id`, `X-Request-ID`) and worker job context (`job_id`, `recording_id`, `family_id`).
- Sentry captures 500+ unhandled server errors and frontend React error boundaries without logging transcripts, audio, bearer tokens, or PII.
- For complete details, log examples, and test runbooks, see [`OBSERVABILITY.md`](OBSERVABILITY.md).

### Monitoring, Queue Health & AI Cost Ledger
- Three-tier production monitoring: Railway native container metrics, Sentry error alerts, and Supabase PostgreSQL durable queue health.
- Stuck-job detection engine and durable AI usage ledger with deterministic `Decimal` cost calculations.
- Privileged operator monitoring endpoint: `GET /v1/operations/monitoring/summary`.
- For complete details, alert thresholds, and incident playbooks, see [`MONITORING.md`](MONITORING.md).

---

## 8. Troubleshooting Runbook

| Symptom | Cause | Resolution |
| :--- | :--- | :--- |
| `503 Service Unavailable` on `/ready` | Supabase connection failed | Check `DATABASE_URL` credentials; ensure port `5432` is used rather than `6543`. |
| `401 Unauthorized` (`invalid_token`) | Auth mismatch between Frontend & Core | Verify the Clerk JWT template audience matches `AUTH_AUDIENCE`, and that `AUTH_ISSUER`, `AUTH_JWKS_URL`, and `AUTH_ALLOWED_ALGORITHMS` match the configured Clerk instance. |
| `CORS Error` in Browser Console | `CORS_ALLOWED_ORIGINS` mismatch | Ensure `CORS_ALLOWED_ORIGINS` on `mura-api` matches Vercel URL exactly (no trailing slash). |
| Jobs stay in `queued` status | `mura-worker` stopped or cannot reach DB | Check `mura-worker` logs in Railway for crash or connection timeout. |
| Railway API fails to bind port | Hardcoded port in Dockerfile | Ensure `Mura_project/Dockerfile` uses `CMD ["sh", "-c", "exec uvicorn ... --port \"${PORT:-8000}\""]`. |

