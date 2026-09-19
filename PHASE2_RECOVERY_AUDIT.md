# MURA — Phase 2.1 Family Book Recovery Audit

Conducted at: 2026-09-18T19:57:00+05:00
Target: Phase 2.1 — Grounded Family Book Generation (`FAMILY_BOOK`)

---

## 1. Audit Summary

The previous agent designed and initiated Phase 2.1, but hit a tool/context limit while writing `Mura_project/src/mura/storage/book.py`.

### Status Matrix

| Component | Path | Status | Notes |
| :--- | :--- | :--- | :--- |
| **Domain Models** | `Mura_project/src/mura/domain/book_models.py` | **Complete** | 722 lines. All Pydantic models (`StrictModel`), enums, DTOs, gate/review/plan models pass `ruff` and compile. |
| **Alembic Migration** | `Mura_project/migrations/versions/20260918_0013_family_books.py` | **Complete** | 323 lines. Single head `20260918_0013`. Valid `upgrade()` and `downgrade()` for 7 tables + `ai_usage_events` columns. |
| **Storage Layer** | `Mura_project/src/mura/storage/book.py` | **BROKEN / Incomplete** | 156 lines. Ends abruptly with `# __APPEND__`. Contains invalid import `BookGateReportPlaceholder`. Only `BookRow` ORM model defined; missing 6 ORM rows and all repository classes (`BookRepository`, `BookJobRepository`, etc.). |
| **Architecture Audit** | `FAMILY_BOOK_AUDIT.md` | **Complete** | Comprehensive architectural blueprint and decision log. |
| **Implementation Plan**| `family_book_implementation_plan.md` | **Complete** | 12-layer implementation roadmap. |
| **Domain Logic (`mura/book/`)** | `Mura_project/src/mura/book/*` | **Not Started** | Directory does not exist yet. |
| **Artifact Storage** | `src/mura/storage/object_store.py`, `book_artifacts.py` | **Not Started** | Does not exist yet. |
| **Worker Orchestration**| `src/mura/orchestration/books.py` | **Not Started** | Does not exist yet. |
| **API Endpoints** | `apps/api/books.py` | **Not Started** | Does not exist yet. |
| **Frontend Integration**| `MURA-app/src/lib/mura/book-api.ts`, `/books`, components | **Not Started** | Does not exist yet. |

---

## 2. Immediate Recovery Actions

1. **Repair & Finish `Mura_project/src/mura/storage/book.py`**:
   - Remove invalid import `BookGateReportPlaceholder`.
   - Implement all 7 SQLAlchemy ORM models matching migration `0013`:
     1. `BookRow`
     2. `BookSourceSnapshotRow`
     3. `BookPlanRow`
     4. `BookChapterRow`
     5. `BookContinuityStateRow`
     6. `BookExportRow`
     7. `BookJobRow`
   - Implement repository classes:
     - `BookRepository` (family-scoped CRUD, list, status updates, cancel flag)
     - `BookSourceSnapshotRepository` (write-once immutable snapshot)
     - `BookPlanRepository` (save/load blueprint + validation report)
     - `BookChapterRepository` (chapter rows, get chapter, update draft/review/gate, list chapters)
     - `BookContinuityRepository` (save/load latest continuity state)
     - `BookExportRepository` (save/load export records)
     - `BookJobRepository` (enqueue, claim with `with_for_update(skip_locked=True)`, heartbeat renew, defer, fail, complete, cancellation checks)
   - Verify with `ruff check` and successful Python import.

---

## 3. Execution Roadmap Following Recovery

- **Layer 1**: Storage Foundation (`storage/book.py`, testing migration).
- **Layer 2**: Grounding Compiler (`book/snapshot.py`, `archive_read.py` extension, snapshot unit tests).
- **Layer 3**: Blueprint Validation (`book/blueprint_validation.py`, deterministic validation tests).
- **Layer 4**: Planner (`book/prompts.py`, `book/planner.py`, `DeepSeekClient` optional temperature parameter).
- **Layer 5**: Job & Lease (`orchestration/books.py`, resume after crash tests).
- **Layer 6**: Writer & Continuity (`book/writer.py`, `book/continuity.py`).
- **Layer 7**: Reviewer & Deterministic Gates (`book/reviewer.py`, `book/chapter_gates.py`, gate unit tests).
- **Layer 8**: Export (`book/export_document.py`, `book/exporter.py`, pure-Python EPUB3, WeasyPrint PDF, `storage/book_artifacts.py`).
- **Layer 9**: API (`apps/api/books.py`, capability policy, BOLA tests).
- **Layer 10**: Frontend (`MURA-app` client, progress, reader, downloads, proxy allowlist, i18n).
- **Layer 11**: Observability & Privacy (`logging.py` sanitization, `ai_usage.py` correlation, monitoring).
- **Layer 12**: Verification & Documentation (`FAMILY_BOOK.md`, full pytest regression, full vitest regression).

