# MURA — Family Book Generation: Implementation Plan

Companion to `FAMILY_BOOK_AUDIT.md`. Exact files to create, modify, migrate and test, in the
order they must be built, with the gate that proves each layer before the next one starts.

Approved decision: **WeasyPrint for PDF + a pure-Python EPUB 3 writer.** EPUB is verified in
the normal pytest suite; PDF is verified by a real `Dockerfile.worker` build and render.

---

## 1. Layer order

| # | Layer | Deliverable | Gate before moving on |
| --- | --- | --- | --- |
| 1 | Migration + storage | `20260918_0013_family_books.py`, `storage/book.py`, `domain/book_models.py` | `alembic upgrade head` / `downgrade -1` / re-`upgrade`; `alembic heads` single |
| 2 | Grounding compiler | `book/snapshot.py`, `archive_read.grounding_bundle()` | `test_book_source_snapshot.py` |
| 3 | Blueprint schema + validation | `book/blueprint_validation.py` | `test_book_blueprint_validation.py` (no LLM) |
| 4 | Planner | `book/prompts.py`, `book/planner.py`, `client.py` temperature | planner test with a fake client |
| 5 | Job + lease + resume | `storage/book.py` claim, `orchestration/books.py` | `test_book_worker_resume.py` |
| 6 | Writer + continuity | `book/writer.py`, `book/continuity.py` | writer/gate tests |
| 7 | Reviewer + gates | `book/reviewer.py`, `book/chapter_gates.py` | `test_book_chapter_gates.py` |
| 8 | Export | `book/exporter.py`, `book/export_document.py`, `storage/object_store.py`, `storage/book_artifacts.py` | `test_book_export.py` + Docker PDF render |
| 9 | API | `apps/api/books.py`, `identity/policy.py`, `apps/api/main.py` | `test_book_family_api.py` |
| 10 | Frontend | `book-api.ts`, `/books`, components, i18n, proxy | `vitest` + `next build` |
| 11 | Observability + cost + privacy | `logging.py`, `ai_usage.py`, `config.py`, `monitoring.py`, `apps/worker/main.py` | `test_book_usage_privacy.py` |
| 12 | Regression + docs | full suite, lint, types, `FAMILY_BOOK.md`, README | all green, numbers reported exactly |

Each layer is independently testable and leaves the suite green.

---

## 2. Migration

`Mura_project/migrations/versions/20260918_0013_family_books.py`
`revision = "20260918_0013"`, `down_revision = "20260917_0012"`.

Creates, in dependency order:

1. `books`
2. `book_source_snapshots` (FK → `books`, `UNIQUE(book_id)`)
3. `book_plans` (FK → `books`, `UNIQUE(book_id)`)
4. `book_chapters` (FK → `books`, `UNIQUE(book_id, chapter_number)`)
5. `book_continuity_states` (FK → `books`, `UNIQUE(book_id, after_chapter_number)`)
6. `book_exports` (FK → `books`, `UNIQUE(book_id, format)`)
7. `book_jobs` (FK → `books`, composite index `(status, next_attempt_at)`)
8. `ALTER ai_usage_events` + `book_id`, `chapter_number`, `ix_ai_usage_events_book_id`

Every FK that points at family data is `ON DELETE CASCADE`; `books.created_by_user_id` is
`RESTRICT`, so deleting an account can never silently delete a family's book.

---

## 3. Configuration added

All optional and additive, so every existing deployment and test config keeps validating:

```
MURA_BOOK_ENABLED                 bool   default True      # worker kill switch
MURA_BOOK_MAX_ATTEMPTS            int    default 3
MURA_BOOK_LEASE_SECONDS           float  default = job_lease_seconds
MURA_BOOK_HEARTBEAT_SECONDS       float  default = job_heartbeat_seconds
MURA_BOOK_POLL_INTERVAL_SECONDS   float  default 2.0
MURA_BOOK_TARGET_WORDS_MIN        int    default 20000
MURA_BOOK_TARGET_WORDS_MAX        int    default 30000
MURA_BOOK_CHAPTER_MIN             int    default 10
MURA_BOOK_CHAPTER_MAX             int    default 15
MURA_BOOK_MIN_CHAPTER_WORDS       int    default 700
MURA_BOOK_MAX_CHAPTER_WORDS       int    default 3500
MURA_BOOK_REPAIR_ATTEMPTS         int    default 2
MURA_BOOK_PLAN_REPAIR_ATTEMPTS    int    default 1
MURA_BOOK_MAX_SOURCE_RECORDINGS   int    default 100
MURA_BOOK_MAX_EVIDENCE_QUOTES     int    default 400
MURA_BOOK_TEMPERATURE_PLAN        float  default 0.4
MURA_BOOK_TEMPERATURE_WRITE       float  default 0.75
MURA_BOOK_TEMPERATURE_REPAIR      float  default 0.5
MURA_BOOK_TEMPERATURE_REVIEW      float  default 0.1
MURA_BOOK_TEMPERATURE_CONTINUITY  float  default 0.2
BOOK_STORAGE_DIR                  path   default <AUDIO_STORAGE_DIR>/books
SUPABASE_BOOKS_BUCKET             str    default mura-books
```

Production validation mirrors the audio rules: a rooted `BOOK_STORAGE_DIR` and Supabase
credentials whenever the backend is Supabase.
---

## 4. Module map

### 4.1 `src/mura/domain/book_models.py`
Enums (`BookStatus`, `BookStage`, `ChapterStatus`, `ExportFormat`, `ExportStatus`,
`ReviewStatus`, `IssueSeverity`, `IssueType`), the snapshot models, `BookBlueprint`,
`ChapterPlan`, `ContinuityState`, `ReviewResult`, `ReviewIssue`, `GateIssue`, `GateReport`,
`BlueprintValidationReport`, and the API view DTOs (`BookAccepted`, `BookSummaryView`,
`BookDetailView`, `BookChapterSummaryView`, `BookChapterView`, `BookProgressView`,
`BookSourceOptionView`). All extend the existing `StrictModel` (`extra="forbid"`).

### 4.2 `src/mura/book/snapshot.py`
`compile_source_snapshot(...)` → `CompiledSnapshot` (models + `content_hash` + manifest).
Pure, deterministic, provider-free. Caps from config. Never logs content.

### 4.3 `src/mura/book/blueprint_validation.py`
`validate_blueprint(blueprint, snapshot, limits)` → `BlueprintValidationReport`.
Checks every referenced id, chapter count, per-chapter and total word budgets, grounding
presence, year support, duplicate-event spread, language match, anchor grounding.
`repair_blueprint_arithmetic(...)` fixes budget/count failures deterministically so a second
LLM call is not spent on arithmetic.

### 4.4 `src/mura/book/chapter_gates.py`
`run_chapter_gates(...)` → `GateReport`. Eight gates (named person, year, relationship,
correction, quote, evidence coverage, word count, language). Blocker vs warning severity.
Reuses `mura/linguistics/*`, `mura/factual_support.py`, `mura/asr/language.py`.

### 4.5 `src/mura/book/prompts.py`
`BOOK_PLANNER_PROMPT_V1`, `BOOK_WRITER_PROMPT_V1`, `BOOK_CHAPTER_REPAIR_PROMPT_V1`,
`BOOK_REVIEWER_PROMPT_V1`, `BOOK_CONTINUITY_PROMPT_V1`, each with a `*_PROMPT_VERSION`
constant and a `BOOK_PROMPT_VERSIONS` mapping. The prompts carry the hard rules; the code
carries the enforcement. Neither alone is the safety mechanism.

### 4.6 `src/mura/book/{planner,writer,reviewer,continuity}.py`
One role each, no autonomy, no framework. Each returns a validated Pydantic model plus a
privacy-safe usage/telemetry dict. The reviewer never rewrites; repair is a writer call.

### 4.7 `src/mura/book/export_document.py`
`build_book_html(...)` from approved chapters (title page, chapter headings, page numbers,
serif body, optional epigraph, no invented imagery) and `write_epub3(...)` — a real EPUB 3
container: `mimetype` stored first and uncompressed, `META-INF/container.xml`, OPF package
with manifest + spine, one XHTML per approved chapter, `nav.xhtml`, a stylesheet.

### 4.8 `src/mura/book/exporter.py`
`ExportService.export_pdf(...)` / `export_epub(...)`. `render_pdf` is a thin seam:
`WeasyPrintRenderer` in production, injectable for tests. A missing engine raises a named
`ExportEngineUnavailable`, never a silent empty file.

### 4.9 `src/mura/book/service.py`
The stage machine: `ensure_plan`, `write_next_chapter`, `review_chapter`, `update_continuity`,
`ensure_exports`, `is_cancelled`. Every transition persisted before the next step. Resume
skips `approved` chapters without a provider call.

### 4.10 `src/mura/storage/book.py`
Rows and repositories: `BookRepository`, `BookSourceSnapshotRepository` (write-once),
`BookPlanRepository`, `BookChapterRepository`, `BookContinuityRepository`,
`BookExportRepository`, `BookJobRepository` (claim / renew / defer / fail / complete /
cancel). All family-scoped queries carry `family_id` in the predicate.

### 4.11 `src/mura/orchestration/books.py`
---

## 5. API surface

| Method | Path | Capability | Notes |
| --- | --- | --- | --- |
| `POST` | `/v1/families/{fid}/books` | `create_book` | 202 `BookAccepted`. Snapshots sources, creates the job, returns. |
| `GET` | `/v1/families/{fid}/books/sources` | `read_books` | Eligible memories for the picker. |
| `GET` | `/v1/families/{fid}/books` | `read_books` | Paginated list. |
| `GET` | `/v1/families/{fid}/books/{bid}` | `read_books` | Detail + truthful progress. No raw planner JSON. |
| `GET` | `/v1/families/{fid}/books/{bid}/chapters` | `read_books` | Titles and statuses, no text. |
| `GET` | `/v1/families/{fid}/books/{bid}/chapters/{n}` | `read_books` | **Approved chapters only.** |
| `GET` | `/v1/families/{fid}/books/{bid}/download?format=` | `read_books` | Streams a private artifact. |
| `POST` | `/v1/families/{fid}/books/{bid}/cancel` | `create_book` | Requests cancellation. |
| `POST` | `/v1/families/{fid}/books/{bid}/regenerate` | `create_book` | Creates a **new** book row; never overwrites. |

`401` no usable identity · `403` confirmed member lacking the capability · `404` for both
"no such book" and "not your book". Cross-family `recording_ids` in the create body resolve to
`404`, not `403`, so an id cannot be used as a probe.

---

## 6. Frontend

| File | Purpose |
| --- | --- |
| `src/lib/mura/book-api.ts` | Typed client + `bookDownloadUrl`. No `any`, no client-side filling-in. |
| `src/lib/mura/book-progress.ts` | Stage code → locale key. **No percentage.** |
| `src/app/books/page.tsx` | List + entry point. |
| `src/app/books/[id]/page.tsx` | Progress while generating, reader when done. |
| `src/components/book/create-book-flow.tsx` | Memories (all/selected), output language, approximate length. |
| `src/components/book/book-progress.tsx` | "Пишем главу 3 из 12". |
| `src/components/book/book-reader.tsx` | Lazy per-chapter fetch; only the open chapter is loaded. |
| `src/components/book/book-downloads.tsx` | PDF/EPUB, enabled only when the artifact exists. |

Modified: `archive-doorways.tsx` (one doorway), `proxy.ts` + `proxy-allowlist.test.ts`,
`i18n.tsx` (three dictionaries).

---

## 7. Verification

1. `pytest` — full suite, `TEST_POSTGRES_URL` set so BOLA really executes; report exact counts.
2. `ruff check .`, `ruff format --check .`, `mypy src apps services scripts`,
   `python -m compileall -q src apps services scripts`.
3. `alembic upgrade head` / `downgrade -1` / `upgrade head` / `alembic heads`.
4. `npm test`, `npm run build`.
5. **PDF** — `docker build -f Dockerfile.worker`, then render a fixture book inside the
   container and report page count, size and sha256. If the build fails, PDF is reported
   **unverified**.
6. **EPUB** — asserted in pytest by reopening the produced container.
7. **Mocked E2E** — deterministic fake provider + fake renderer driving the real API, worker,
   service and export code through create → plan → chapters → review → export → download, over
   the eight-fixture archive. Labelled mocked everywhere.
8. **Not claimed** — a real 20k–30k-word generation (no usable `DEEPSEEK_API_KEY` here), and
   any browser click-through (no browser automation available). Both stated in the final
   report.
`BookJobWorker` — same lifecycle as `RecordingJobWorker` (claim, heartbeat, run, stop), plus a
cancellation check between chapters. `apps/worker/main.py` builds it next to the recording
worker when `MURA_BOOK_ENABLED`.