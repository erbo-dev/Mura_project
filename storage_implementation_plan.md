# MURA Storage Implementation Plan: Production Object Storage (Supabase Storage)

Prepare and transition MURA's audio storage architecture from a shared local filesystem to secure, private Object Storage using **Supabase Storage** for production (Railway API + Worker), while maintaining seamless local filesystem parity for development and testing.

---

## 1. Current Audio Lifecycle Audit

```text
[User / Browser]
       │
       ▼ POST /v1/families/{family_id}/recordings
[FastAPI Core API] (apps/api/recordings.py)
       │
       ├─▶ storage.save(...) ──────────────▶ Local Filesystem (.mura/audio)
       │                                     Returns StoredAudio(storage_key, backend, ...)
       │
       ├─▶ Database.create_recording(...) ──▶ PostgreSQL recordings table
       │                                     Persists storage_key, storage_backend, sha256...
       │
       └─▶ Database.create_job(...) ────────▶ PostgreSQL processing_jobs table (status=queued)
                                             Returns HTTP 202 Accepted {recording_id, job_id}
       ┌─────────────────────────────────────────────────────────┐
       │ In-flight Asynchronous Processing                       │
       ▼                                                         │
[mura-worker] (apps/worker/main.py)                              │
       │                                                         │
       ├─▶ SELECT ... FOR UPDATE SKIP LOCKED ────────────────────┘
       │   Acquires lease and starts heartbeat
       │
       ├─▶ materialize_recording_audio(recording, storage)
       │   Yields Path to audio file
       │
       ├─▶ Whisper ASR Client
       │   Transcribes audio bytes over HTTPS to OpenAI/Whisper host
       │
       ├─▶ DeepSeek Pipeline
       │   Extracts structured facts, claims, people, and relationships
       │
       └─▶ Finalizes Job (status=completed)
```

### Retrieval & Deletion Lifecycles
- **Playback (`GET /v1/families/{family_id}/recordings/{recording_id}/audio`)**:
  - Checks family membership in PostgreSQL.
  - Calls `storage.open(storage_key) -> BinaryIO`.
  - Returns `StreamingResponse` with `cache-control: no-store, private`.
- **Deletion (`RecordingDeletionService.delete_recording`)**:
  - Deletes database rows first in transaction.
  - Calls `storage.delete(storage_key)` best-effort.

---

## 2. Files Currently Coupled to Filesystem Paths

| File | Current Coupling | Required Change |
| :--- | :--- | :--- |
| `src/mura/storage/audio.py` | `AudioStorageBackend` only enum value is `LOCAL = "local"`. `LocalAudioStorage` assumes filesystem root. | Add `SUPABASE = "supabase"` enum; implement `SupabaseAudioStorage`; add `build_audio_storage` factory. |
| `src/mura/config.py` | Line 271 enforces `AUDIO_STORAGE_DIR` must be absolute in production. No Supabase storage configuration. | Add `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_STORAGE_BUCKET`. Relax `audio_storage_dir` requirement when backend is `supabase`. |
| `apps/api/main.py` | Lines 165, 204 hardcode `LocalAudioStorage(settings.audio_storage_dir)`. | Replace with `build_audio_storage(settings)`. Type `CoreRuntime.storage` as `AudioStorage`. |
| `apps/worker/main.py` | Lines 60-63 hardcode `LocalAudioStorage(settings.audio_storage_dir)`. | Replace with `build_audio_storage(settings)`. |
| `src/mura/storage/__init__.py` | Exports only `LocalAudioStorage`. | Re-export `SupabaseAudioStorage` and `build_audio_storage`. |
| `src/mura/orchestration/__init__.py` | Exports only `LocalAudioStorage`. | Re-export `SupabaseAudioStorage` and `build_audio_storage`. |

---

## 3. Proposed Storage Abstraction

The domain contract (`AudioStorage` Protocol in `src/mura/storage/audio.py`) is already decoupled from filesystem semantics:

```python
class AudioStorage(Protocol):
    backend: AudioStorageBackend

    def save(
        self,
        *,
        family_id: str,
        recording_id: str,
        original_filename: str,
        content_type: str | None,
        source: BinaryIO,
    ) -> StoredAudio: ...

    def exists(self, storage_key: str) -> bool: ...

    def delete(self, storage_key: str) -> bool: ...

    def open(self, storage_key: str) -> BinaryIO: ...

    def materialize(self, storage_key: str) -> AbstractContextManager[Path]: ...
```

### New Implementation: `SupabaseAudioStorage`
- **Dependencies**: Uses `requests` (already a production dependency in `pyproject.toml`). Zero new third-party SDK dependencies.
- **REST Endpoints**:
  - `POST {url}/storage/v1/object/{bucket}/{storage_key}`: Uploads object with `x-upsert: true`.
  - `GET {url}/storage/v1/object/authenticated/{bucket}/{storage_key}`: Streams object with authentication header.
  - `HEAD /storage/v1/object/authenticated/{bucket}/{storage_key}`: Checks existence.
  - `DELETE {url}/storage/v1/object/{bucket}/{storage_key}`: Deletes object idempotently.
- **`materialize(storage_key)`**:
  - Downloads object into a managed `tempfile.NamedTemporaryFile`.
  - Yields the temporary `Path` to Whisper.
  - Guarantees immediate deletion of the temporary file in a `finally` block on both success and failure.

---

## 4. Object Key Design

Object keys are server-generated and deterministic:

```text
family/{family_id}/recordings/{recording_id}/original{extension}
```

- **Canonical IDs**: `family_id` and `recording_id` are regex-checked against `^[A-Za-z0-9_-]{1,128}$`.
- **Sanitized Extension**: `safe_extension(original_filename)` validates against `ALLOWED_AUDIO_EXTENSIONS` (`.wav`, `.mp3`, `.m4a`, `.mp4`, `.aac`, `.ogg`, `.opus`, `.webm`, `.flac`).
- **Container Sniffing**: `validate_container(extension, head_bytes)` ensures binary header matches declared extension before upload.
- **Zero PII**: No email, username, or client-provided directory names in the key.

---

## 5. Database Impact

> [!NOTE]
> **No Schema Migrations Required!**
> The `recordings` table already includes all necessary metadata columns:
> - `storage_key` (`Text`, nullable)
> - `storage_backend` (`String(32)`, nullable)
> - `audio_sha256` (`String(64)`, nullable)
> - `audio_size_bytes` (`Integer`, nullable)
> - `audio_mime_type` (`String(255)`, nullable)
>
> When `AUDIO_STORAGE_BACKEND=supabase`, the repository persists `storage_backend="supabase"`, keeping existing rows and legacy rows fully intact.

---

## 6. Security Model

1. **Private Bucket**:
   - The Supabase bucket (e.g. `mura-audio`) is private by default. Public access is disabled.
2. **Credential Boundary**:
   - `SUPABASE_SERVICE_ROLE_KEY` is provided strictly to trusted backend processes (Railway API and Railway Worker).
   - The frontend (Vercel) never receives `SERVICE_ROLE_KEY`.
3. **No Public URLs in DB**:
   - The database stores only the opaque `storage_key`.
   - Audio is streamed to authorized family members through `GET /v1/families/{family_id}/recordings/{recording_id}/audio`, which checks PostgreSQL family membership on every single request before fetching bytes.
4. **Temporary Artifact Hygiene**:
   - The worker's materialized local audio files are unlinked in `finally` blocks, leaving no plaintext audio on the worker container filesystem.
5. **Safe Logging**:
   - Service keys, authorization headers, and signed URLs are never printed in application logs.

---

## 7. Test Strategy

1. **Local Audio Storage Regression Tests**:
   - Run existing `tests/test_audio_storage.py` to ensure local filesystem parity remains 100% green.
2. **Unit Tests for `SupabaseAudioStorage` (`tests/test_supabase_storage.py`)**:
   - Mocked HTTP responses via `unittest.mock` / `requests.Session`:
     - Successful upload (`save`) with SHA-256 calculation.
     - Oversized upload rejection (`AudioTooLargeError`).
     - Bad header / extension mismatch rejection (`UnsupportedAudioError`).
     - Object download stream (`open`).
     - Materialization tempfile creation and guaranteed cleanup (`materialize`).
     - Existence check (`exists`).
     - Idempotent deletion (`delete`).
     - Handling Supabase 401/403/500 HTTP errors gracefully as `AudioStorageError`.
3. **Factory Tests**:
   - Test that `build_audio_storage` constructs `LocalAudioStorage` for `AUDIO_STORAGE_BACKEND=local` and `SupabaseAudioStorage` for `AUDIO_STORAGE_BACKEND=supabase`.
4. **End-to-End API Upload & Worker Materialization Mock Test**:
   - Test recording upload route with `SupabaseAudioStorage` stub to verify job creation and orphan cleanup on database failure.

---

## 8. Exact Files to Modify

1. `Mura_project/src/mura/storage/audio.py`
2. `Mura_project/src/mura/config.py`
3. `Mura_project/apps/api/main.py`
4. `Mura_project/apps/worker/main.py`
5. `Mura_project/src/mura/storage/__init__.py`
6. `Mura_project/src/mura/orchestration/__init__.py`
7. `Mura_project/tests/test_supabase_storage.py` (New test suite)
8. `Mura_project/.env.example` & `.env.example`
9. `RAILWAY_DEPLOYMENT.md` & `DOCKERIZATION_NOTES.md`
10. `STORAGE_DEPLOYMENT.md` (New deployment guide)

