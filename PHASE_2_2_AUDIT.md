# MURA Production Phase 2.2 — Runtime Integration & Book Hardening Audit

**Audit Date**: 2026-09-18  
**Scope**: Production Integration, Reliability & Book Runtime Hardening  
**Target Architecture**:
```
Vercel (Next.js)
    │
    ▼
FastAPI API (Railway)
    │
    ├── PostgreSQL / Supabase DB (Alembic 0013 schema)
    └── Supabase Storage (mura-audio, mura-books)
    ▲
    │
mura-worker (Railway)
    ├── WorkerSupervisor
    │     ├── RecordingJobWorker
    │     └── BookJobWorker
    ├── DeepSeek Client (with ContextVar chapter usage correlation)
    ├── WeasyPrint (PDF export with native Debian libraries)
    └── Storage Client (SupabaseBookArtifactStorage / LocalBookArtifactStorage)
```

---

## 1. Executive Summary & Baseline Validation

Before applying changes, baseline verification was conducted against both backend and frontend codebases:
1. **Backend (`Mura_project`)**:
   - Compilation: `python -m compileall -q src/mura apps` → 0 errors.
   - Linting: `python -m ruff check ...` → 1 import sorting error in `apps/api/books.py:11:1`.
   - Python tests: `python -m pytest -k book` → 70 passed, 1122 deselected (all existing book tests pass).
2. **Frontend (`MURA-app`)**:
   - Unit tests: `npm test -- --run` → 42 test files passed, 478 tests passed.
   - TypeScript: `npx tsc --noEmit` → FAILED due to duplicate imports in `src/components/home/archive-doorways.tsx` (P0-I).

---

## 2. Confirmed P0 Integration Defects

| Issue ID | Subsystem | File(s) Affected | Severity | Current Defect | Target Fix |
|---|---|---|---|---|---|
| **P0-A** | Worker Supervisor | `apps/worker/main.py` | Critical | Only `RecordingJobWorker` is constructed and run. `BookJobWorker` is never executed; queued books never make progress. | Implement `WorkerSupervisor` running both workers concurrently via thread pool with unified signal handling. |
| **P0-B** | Artifact Storage | `src/mura/storage/book_artifacts.py`, `apps/api/books.py` | Critical | Only `LocalBookArtifactStorage` exists; API hardcodes local storage. Railway containers cannot share ephemeral disk. | Implement `SupabaseBookArtifactStorage`, `BookArtifactStorageBackend`, and factory `build_book_artifact_storage`. |
| **P0-C** | Exporter Metadata | `src/mura/book/exporter.py:157` | High | Hardcodes `storage_backend="local"`, corrupting export row metadata when stored in Supabase. | Dynamically record `self.artifact_storage.backend.value`. |
| **P0-D** | Worker Packaging & Fallback | `Dockerfile.worker`, `pyproject.toml`, `src/mura/orchestration/books.py` | Critical | Worker Dockerfile omits `[books]` and native Debian libraries (libpango, etc.). Worker catches `ExportEngineUnavailable` and skips PDF, completing books without PDF. | Add `books` optional dependencies with `weasyprint`, add Debian libraries to Dockerfile, require both PDF and EPUB exports to succeed. |
| **P0-E** | API Book Creation | `apps/api/books.py`, `src/mura/storage/book.py` | High | Requested recording IDs are discarded. Book and Job are created in non-atomic transactions without creating snapshot. | Implement `resolve_book_source_ids` and `BookCreationRepository.create_queued_book` executing Book, Snapshot, and Job creation in a single atomic transaction. |
| **P0-F** | Worker Snapshot Freezing | `src/mura/orchestration/books.py` | High | Worker lazily compiles snapshot from all family recordings, leaking unrequested or subsequent recordings into book. | Worker requires existing snapshot at job start; fails immediately if snapshot is absent. Never compiles snapshot on the fly. |
| **P0-G** | Book Regeneration | `apps/api/books.py` | Medium | Regeneration without requested IDs does not preserve original source recordings. | Fallback to original snapshot's `source_recording_ids` when `requested_recording_ids` is omitted. |
| **P0-H** | Word Count Contract | `src/mura/domain/book_models.py`, `MURA-app/src/components/book/BookCreateModal.tsx`, `i18n.tsx` | High | UI allowed 10k/15k/25k; API allowed 1k-60k; validator required 20k-30k. Selecting 10k or 15k leads to blueprint rejection. | Unify contract: `MIN_BOOK_WORDS=20000`, `DEFAULT_BOOK_WORDS=25000`, `MAX_BOOK_WORDS=30000`. Update UI presets to 20k, 25k, 30k. |
| **P0-I** | Frontend TypeScript | `MURA-app/src/components/home/archive-doorways.tsx` | High | Duplicate `lucide-react` imports break `tsc --noEmit`. | Clean and merge import statements. |
| **P0-J** | Configuration & Invariants | `src/mura/config.py` | Medium | `validate_environment_invariants` enforces rooted `BOOK_STORAGE_DIR` even when `audio_storage_backend == AudioStorageBackend.SUPABASE`. Missing `SUPABASE_BOOKS_BUCKET`. | Add `supabase_books_bucket`, only enforce rooted `book_storage_dir` if backend is LOCAL. |

---

## 3. Detailed Audit Findings & Planned Changes

### P0-A: Standalone Worker Supervisor (`apps/worker/main.py`)
- **Evidence**: `apps/worker/main.py:36-97` defines `build_worker` returning `RecordingJobWorker`. `main()` calls `worker.run_forever()`. `BookJobWorker` from `mura.orchestration.books` is never imported or used.
- **Impact**: Any book generation job created via `/v1/families/{family_id}/books` remains stuck in `QUEUED` indefinitely on Railway.
- **Required Behavior**:
  - Worker entrypoint must run both `RecordingJobWorker` and `BookJobWorker` concurrently.
  - Graceful termination: SIGTERM/SIGINT stops claiming new work on both workers while allowing in-flight jobs to complete or lease to expire.
  - Dedicated DeepSeek clients: recording pipeline and book generation pipeline must use independent client instances (or shared client with isolated configuration) while reporting usage to the shared `AIUsageLedger`.
- **Planned Implementation**:
  - Create `WorkerSupervisor` class with `ThreadPoolExecutor(max_workers=2)`.
  - Submit `recording_worker.run_forever()` and `book_worker.run_forever()`.
  - `request_stop()` propagates `request_stop()` to both workers and joins threads.

### P0-B & P0-C: Book Artifact Storage (`src/mura/storage/book_artifacts.py`, `src/mura/book/exporter.py`)
- **Evidence**:
  - `src/mura/storage/book_artifacts.py:36` only implements `LocalBookArtifactStorage`.
  - `apps/api/books.py:65-68` instantiates `LocalBookArtifactStorage(storage_dir)`.
  - `src/mura/book/exporter.py:157` hardcodes `storage_backend="local"`.
- **Impact**: In Railway deployment, API and Worker run in separate Linux containers without shared disk. PDFs/EPUBs written to `/app/data/books` in the worker container are inaccessible to the API container, returning 404 to users.
- **Required Behavior**:
  - Define `BookArtifactStorageBackend(StrEnum): LOCAL = "local", SUPABASE = "supabase"`.
  - Add `backend: BookArtifactStorageBackend` to `BookArtifactStorage` protocol.
  - Implement `SupabaseBookArtifactStorage` using Supabase Storage REST API (`POST /object/{bucket}/{key}`, `GET /object/authenticated/{bucket}/{key}` or `/object/{bucket}/{key}`, `HEAD /object/info/{bucket}/{key}`).
  - Standard key pattern: `families/{family_id}/books/{book_id}/book.{format}` with strict validation preventing path traversal.
  - Add `build_book_artifact_storage(settings: CoreSettings)` factory.
  - In `exporter.py`, record `storage_backend=self.artifact_storage.backend.value`.

### P0-D: Worker Container & PDF Export Guarantee
- **Evidence**:
  - `Mura_project/Dockerfile.worker:25` has `RUN pip install .` without extras.
  - No `libpango`, `libcairo`, or font libraries installed in the container.
  - `src/mura/orchestration/books.py:620` catches `ExportEngineUnavailable` and logs a warning without failing the job.
- **Impact**: WeasyPrint cannot render without system libraries. Catching the exception allows a book without a PDF to be marked `COMPLETED`.
- **Required Behavior**:
  - Define `[project.optional-dependencies] books = ["weasyprint>=62,<70"]` in `pyproject.toml`.
  - In `Dockerfile.worker`, install `libpango-1.0-0 libharfbuzz0b libpangoft2-1.0-0 libjpeg62-turbo libopenjp2-7 libffi-dev` and run `pip install ".[books]"`.
  - In `books.py`, require both PDF and EPUB exports to succeed and be in `READY` status before marking the book `COMPLETED`. If export fails, raise and fail the job with code `book_export_incomplete`.

### P0-E, P0-F, P0-G: Source Freezing & Atomic Book Creation
- **Evidence**:
  - `apps/api/books.py:167-194`: `create_family_book` does not validate that each ID in `payload.requested_recording_ids` exists and belongs to the family. It discards `requested_recording_ids`.
  - `src/mura/orchestration/books.py:271`: `BookJobWorker` calls `compile_source_snapshot(self.db, family_id=book.family_id)` without recording filter.
- **Impact**:
  - Users selecting specific recordings have their selection ignored; the entire family archive is included.
  - Book creation is split across multiple transactions (Book, Job); failure leaves orphan rows without snapshots.
  - Recordings added while a book is queued get included unexpectedly.
- **Required Behavior**:
  - Implement `resolve_book_source_ids`: validate that all requested recording IDs exist, belong to `family_id`, and have completed pipelines. Return HTTP 400 on mismatch without disclosing private data.
  - Compile `BookSourceSnapshot` immediately upon creation.
  - Implement `BookCreationRepository.create_queued_book` that inserts `BookRow`, `BookSourceSnapshotRow`, and `BookJobRow` in a single database transaction (`with session.begin():`).
  - Worker only loads the snapshot from `snapshot_repo.get_snapshot(book_id)`. If missing, fails with `RuntimeError`.
  - On `regenerate_family_book`: if `requested_recording_ids` is None, load `original_snapshot.manifest.source_recording_ids`.

### P0-H: Unified Word Count Range
- **Evidence**:
  - Frontend: `BookCreateModal.tsx:21-25` has presets `[10000, 15000, 25000]`. Default: 15,000.
  - Backend API: `BookCreateRequest.target_word_count` allows `1000..60000`. Default: 20,000.
  - Blueprint Validator: `BlueprintLimits` enforces `min_total_words = 20000, max_total_words = 30000`.
- **Impact**: Choosing 10k or 15k fails validation during the planning phase of the worker.
- **Required Behavior**:
  - Define domain constants: `MIN_BOOK_WORDS = 20_000`, `DEFAULT_BOOK_WORDS = 25_000`, `MAX_BOOK_WORDS = 30_000`.
  - API schemas enforce `ge=MIN_BOOK_WORDS, le=MAX_BOOK_WORDS`.
  - Frontend presets: 20,000, 25,000, 30,000. Default: 25,000.
  - Update localized labels in `i18n.tsx`.

### P0-I: Frontend TypeScript Build Failure
- **Evidence**:
  - `MURA-app/src/components/home/archive-doorways.tsx:3-4`: duplicate identifiers imported from `lucide-react`.
- **Required Behavior**:
  - Combine into a single import statement so `npx tsc --noEmit` succeeds.

### P0-J: Settings Invariants for Supabase Storage
- **Evidence**:
  - `src/mura/config.py:341-345` raises `ValueError` if `not _is_rooted_path(self.book_storage_dir)` in staging/production, even when Supabase is used.
  - `supabase_books_bucket` setting is missing from `CoreSettings`.
- **Required Behavior**:
  - Add `supabase_books_bucket: str = Field(default="mura-books", alias="SUPABASE_BOOKS_BUCKET")`.
  - When `audio_storage_backend == AudioStorageBackend.SUPABASE`, validate `supabase_books_bucket` is present, and skip absolute path requirement for `book_storage_dir`.
  - When `audio_storage_backend == AudioStorageBackend.LOCAL`, require `book_storage_dir` to be rooted in production-like environments.

---

## 4. Chapter AI Usage Correlation Audit

- **Requirement**: AI token usage incurred during book generation (planning, writing, reviewing, repair, continuity) must be logged to `AIUsageLedger` and correctly attributed to the specific chapter number.
- **Current Behavior**: `write_chapter`, `review_chapter`, and `repair_chapter` call DeepSeek client hooks without passing `chapter_number` to the ledger, leaving `chapter_number` NULL in `ai_usage_events`.
- **Target Implementation**:
  - Introduce `chapter_number_ctx: ContextVar[int | None]` and `BookChapterContextManager` in `src/mura/logging.py`.
  - In `AIUsageLedger.record_usage()`, if `chapter_number is None`, read from `chapter_number_ctx.get()`.
  - In `BookJobWorker`, wrap chapter writing, review, repair, and continuity updates in `with BookChapterContextManager(chapter.chapter_number):`.

---

## 5. Planned Verification Suite

1. **Unit & Integration Tests**:
   - `tests/test_book_artifact_storage.py`: Local & Supabase artifact storage unit tests, path traversal checks, URL building.
   - `tests/test_book_creation_atomicity.py`: Verifies `create_queued_book` inserts Book, Snapshot, and Job in a single atomic transaction.
   - `tests/test_standalone_worker.py`: Verifies `WorkerSupervisor` runs both `RecordingJobWorker` and `BookJobWorker` concurrently and shuts down cleanly.
   - `tests/test_book_chapter_usage_correlation.py`: Verifies `ContextVar` propagates `chapter_number` into `AIUsageLedger`.
2. **Regression Tests**:
   - `python -m pytest -k book` (all existing 70 tests pass + new tests).
   - Full test suite: `python -m pytest`.
3. **Frontend Validation**:
   - `npx tsc --noEmit` in `MURA-app` passes with zero errors.
   - `npm test -- --run` in `MURA-app` passes (478+ tests).
   - `npm run build` in `MURA-app` builds successfully.

