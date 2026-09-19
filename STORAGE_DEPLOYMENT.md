# MURA (Мұра) — Production Object Storage Runbook
### Cloud Storage Provider: Supabase Storage (Private Bucket)

This runbook covers the architecture, setup, security, and operational lifecycle of MURA's Object Storage layer using **Supabase Storage**.

---

## 1. Architecture

```text
[ Browser / Client ]
       │
       ▼ (1) Upload Audio (multipart/form-data)
[ Railway: mura-api ] (FastAPI)
       │
       ├─▶ (2) Stream upload (POST /storage/v1/object/mura-audio/...)
       │       with SUPABASE_SERVICE_ROLE_KEY
       ▼
[ Supabase Storage ] (Private bucket: mura-audio)
  Object: family/{family_id}/recordings/{recording_id}/original.webm
       ▲
       │ (3) Stream download / Materialize to tempfile
       │     with SUPABASE_SERVICE_ROLE_KEY
[ Railway: mura-worker ] (apps.worker.main)
       │
       ▼ (4) Transcribe audio (Whisper) -> DeepSeek extraction
       │
       └─▶ (5) Unlink temporary file in finally block
```

### Key Principles
- **Decoupled from local filesystems**: API and Worker run as independent services on Railway without requiring a shared disk volume.
- **Provider-neutral domain**: The domain only works with canonical `storage_key` (`family/{family_id}/recordings/{recording_id}/original{extension}`).
- **Local parity**: Local development and tests seamlessly use `AUDIO_STORAGE_BACKEND=local` with `LocalAudioStorage`.

---

## 2. Setting Up the Private Supabase Storage Bucket

### 2.1 Create the Bucket
1. Go to the [Supabase Dashboard](https://supabase.com/dashboard) and select your project.
2. In the left navigation, click **Storage**.
3. Click **New bucket**.
4. Configure the bucket:
   - **Name:** `mura-audio`
   - **Public bucket:** **OFF** (Disabled / Unchecked). This is critical — audio must never be public!
   - **File size limit:** `25MB` (or match `CORE_MAX_UPLOAD_MB`).
   - **Allowed MIME types:** leave blank or specify audio formats (`audio/*, video/mp4, video/webm`).
5. Click **Save**.

### 2.2 Verify Bucket Privacy
- Under Storage Buckets, ensure `mura-audio` shows the **Private** badge.
- No public URLs can resolve files from this bucket.
### 2.2 Create Book Artifacts Bucket (`mura-books`)
1. In the Supabase Dashboard, click **Storage** → **New bucket**.
2. Configure the bucket:
   - **Name:** `mura-books`
   - **Public bucket:** **OFF** (Disabled / Unchecked). PDF/EPUB books contain sensitive family history and must remain strictly private!
   - **File size limit:** `50MB`.
   - **Allowed MIME types:** specify document formats (`application/pdf, application/epub+zip`).
3. Click **Save**.

### 2.3 Verify Bucket Privacy
- Under Storage Buckets, ensure both `mura-audio` and `mura-books` show the **Private** badge.
- No public URLs can resolve files from either bucket.

---

## 3. Required Environment Variables

For both `mura-api` and `mura-worker` on Railway:

| Variable | Value | Description |
| :--- | :--- | :--- |
| `AUDIO_STORAGE_BACKEND` | `supabase` | Selects `SupabaseAudioStorage` |
| `AUDIO_STORAGE_BACKEND` | `supabase` | Selects `SupabaseAudioStorage` (`supabase` or `local`) |
| `BOOK_STORAGE_BACKEND` | `supabase` | Selects `SupabaseBookArtifactStorage` (`supabase` or `local`) |
| `SUPABASE_URL` | `https://[PROJECT-REF].supabase.co` | Supabase Project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | `eyJhbGciOi...` | Supabase **Service Role** secret key (Project Settings → API) |
| `SUPABASE_STORAGE_BUCKET` | `mura-audio` | Private storage bucket name |
| `SUPABASE_STORAGE_BUCKET` | `mura-audio` | Private audio storage bucket name |
| `SUPABASE_BOOKS_BUCKET` | `mura-books` | Private book artifacts storage bucket name |
| `SUPABASE_STORAGE_TIMEOUT_SECONDS`| `60.0` | HTTP timeout for storage operations |

> [!CAUTION]
> `SUPABASE_SERVICE_ROLE_KEY` has administrative access to bypass Row Level Security.
> **Never** expose this key to the Vercel frontend or client bundles. It must only exist in Railway environment variables.

---

## 4. Railway Service Configurations

### 4.1 Railway API (`mura-api`)
1. Go to Railway Project → `mura-api` → **Variables**.
2. Set:
   ```env
   AUDIO_STORAGE_BACKEND=supabase
   BOOK_STORAGE_BACKEND=supabase
   SUPABASE_URL=https://[PROJECT-REF].supabase.co
   SUPABASE_SERVICE_ROLE_KEY=[YOUR-SERVICE-ROLE-KEY]
   SUPABASE_STORAGE_BUCKET=mura-audio
   SUPABASE_BOOKS_BUCKET=mura-books
   ```
3. Redeploy `mura-api`.

### 4.2 Railway Worker (`mura-worker`)
1. Go to Railway Project → `mura-worker` → **Variables**.
2. Set the identical variables:
   ```env
   AUDIO_STORAGE_BACKEND=supabase
   BOOK_STORAGE_BACKEND=supabase
   SUPABASE_URL=https://[PROJECT-REF].supabase.co
   SUPABASE_SERVICE_ROLE_KEY=[YOUR-SERVICE-ROLE-KEY]
   SUPABASE_STORAGE_BUCKET=mura-audio
   SUPABASE_BOOKS_BUCKET=mura-books
   ```
3. Redeploy `mura-worker`.

---

## 5. Security Model

1. **Private by Design**:
   - The Supabase bucket is private. No unauthenticated user can list or access files.
2. **PostgreSQL Family Authorization Guard**:
   - Audio playback (`GET /v1/families/{id}/recordings/{id}/audio`) is checked against the database on every request.
   - Core API opens an authenticated stream to Supabase Storage and pipes the bytes back to the authorized user with `cache-control: no-store, private`.
   - The user never receives a storage URL, bucket name, or storage credentials.
3. **No Plaintext Audio Retention on Worker**:
   - When the worker claims a job, `materialize_recording_audio` streams the object into a temporary file.
   - The temporary file path is provided to Whisper.
   - As soon as transcription finishes (success or failure), the context manager's `finally` block unlinks the file.

---

## 6. Upload Lifecycle (API)

1. Client sends `POST /v1/families/{family_id}/recordings`.
2. Validation:
   - File extension verified against `ALLOWED_AUDIO_EXTENSIONS`.
   - Container header sniffed (`sniff_container`) to prevent spoofed `.wav` binaries.
   - File size enforced against `CORE_MAX_UPLOAD_MB`.
3. Storage:
   - Streams bytes to `POST {SUPABASE_URL}/storage/v1/object/mura-audio/{storage_key}`.
   - SHA-256 computed on the fly.
4. Database:
   - Inserts row in `recordings` with `storage_key`, `storage_backend="supabase"`, `audio_sha256`.
   - Inserts row in `processing_jobs` with `status="queued"`.
5. Orphan Prevention:
   - If database insertion fails, `storage.delete(storage_key)` is called immediately in the `except` block to prevent orphaned storage objects.

---

## 7. Processing Lifecycle (Worker)

1. `mura-worker` polls PostgreSQL and claims next job:
   ```sql
   SELECT ... FOR UPDATE SKIP LOCKED
   ```
2. Heartbeat thread begins refreshing the lease.
3. Audio Materialization:
   - `storage.materialize(recording.storage_key)` fetches the audio from Supabase Storage and streams it into a secure `NamedTemporaryFile`.
4. Recognition & Extraction:
   - Whisper transcribes the temporary audio file.
   - DeepSeek extracts people, events, claims, and conflicts.
5. Cleanup:
   - Context manager exits and deletes the temporary file from the container.
   - Database job marked `completed`.

---

## 8. Migration Path from Local Storage

For existing deployments with audio in a local directory (`.mura/audio`):

1. **Identify Existing Files**:
   - Files are located at: `.mura/audio/family/{family_id}/recordings/{recording_id}/original.{ext}`.
2. **Upload to Supabase Storage**:
   - Run a migration script using `supabase-cli` or a Python helper script to upload each file maintaining the exact same key:
     ```bash
     supabase storage cp -r .mura/audio/family ss://mura-audio/family
     ```
3. **Update Database Rows**:
   - Update `recordings` rows to point to the new backend:
     ```sql
     UPDATE recordings
     SET storage_backend = 'supabase'
     WHERE storage_backend = 'local';
     ```
4. **Switch Configuration**:
   - Set `AUDIO_STORAGE_BACKEND=supabase` in your environment.

---

## 9. Troubleshooting

| Issue | Root Cause | Solution |
| :--- | :--- | :--- |
| `HTTP 403 / "Tenant not found"` | Invalid `SUPABASE_URL` | Ensure URL is `https://<ref>.supabase.co` without trailing slash. |
| `HTTP 401 / "Invalid API key"` | Incorrect `SUPABASE_SERVICE_ROLE_KEY` | Copy the `service_role` key from Project Settings → API (do not use `anon` key). |
| `AudioStorageError: HTTP 404` | Bucket does not exist | Verify bucket `mura-audio` is created in Supabase Storage. |
| Upload fails with `HTTP 413` | Audio exceeds size limit | Increase `CORE_MAX_UPLOAD_MB` and Supabase bucket upload size limit. |
| Worker fails with `FileNotFoundError` | Object deleted or key corrupted | Verify that `storage_key` matches the object in Supabase Storage. |

