# MURA — Family Book Generation: Architecture Audit

Audit for **Phase 2.1 — Grounded Family Book Generation** (`FAMILY_BOOK`).

Everything below was read out of the repository at commit `734de73` (branch `main`) and,
where a claim is testable, executed. Nothing here is inferred from documentation alone;
the two places where the checked-in docs disagree with the code are called out explicitly.

---

## 0. Verified environment and regression baseline

| Check | Result |
| --- | --- |
| Backend collection | `1122 tests` across `94` files |
| Backend suite | `1122 passed, 0 failed, 0 skipped, 2 warnings in 152.30s` |
| Frontend suite | `41 files, 475 passed` |
| Backend interpreter | project `.venv` is Python 3.13.1 **without** pytest; pytest 8.4.2 is at `C:\Python313`. `tests/conftest.py` injects `.venv\Lib\site-packages` into `sys.path`, so `C:\Python313\python.exe -m pytest` is the working invocation. |
| `TEST_POSTGRES_URL` | set in the user environment (`postgresql+psycopg://mura_test@127.0.0.1:5432/mura_leases_test`) — **0 skips**, so the PostgreSQL-gated BOLA and lease proofs really ran. |
| Docker | `29.1.3`, available — Linux-container verification of the PDF engine is possible |
| pandoc / wkhtmltopdf / weasyprint / reportlab / ebooklib | not installed locally |
| `DEEPSEEK_API_KEY` in `Mura_project/.env` | `sk-local-dev…` — **a placeholder**, so no real-provider generation can be verified in this environment |

### Documentation drift found

`README.md` states "18 tables", "437 tests" and "1032 tests". The repository actually has
more tables and **1122 backend / 475 frontend** tests. This audit corrects the README as part
of the feature work rather than propagating the stale numbers.

---

## 1. Reusable components

The family book is **not** a new pipeline. It is a new consumer of the archive that already
exists. Every item below was verified by reading it.

### 1.1 Durable jobs and leases

| Component | Location | What it gives us |
| --- | --- | --- |
| `ProcessingJobRow` | `src/mura/storage/database.py` | The queue row shape: `status`, `stage`, `attempts`, `next_attempt_at`, `lease_owner`, `claimed_at`, `lease_expires_at`, `last_heartbeat_at`. |
| `RecordingRepository.claim_next_job()` | `src/mura/storage/database.py` | The claim algorithm: one `SELECT … with_for_update(skip_locked=True)`, two eligibility classes (due-queued, expired-lease), residency written inside the same transaction. |
| `LeaseHeartbeat` | `src/mura/leases.py` | A dedicated thread that renews from its own short-lived session while the main flow blocks in a provider call. **Already job-agnostic** — no recording terms in it. |
| `new_worker_id()`, `LeaseOwnershipLost` | `src/mura/leases.py` | Privacy-safe operational identity; the "this process may no longer mutate this job" signal. |
| `finalize_recording_job` / `defer_recording_job` / `fail_recording_job` | `src/mura/storage/completion.py` | The terminal-write pattern: `lease_owner` re-checked **inside** the writing transaction, so a stale worker cannot overwrite a reclaimed job. |

**Decision.** Reuse the *algorithm* and the *lease primitives*; do not reuse the *table* (§3).

### 1.2 Authorization

| Component | Location | What it gives us |
| --- | --- | --- |
| `FamilyAuthorizationService.authorize()` | `src/mura/identity/context.py` | One join → `AuthorizedFamilyContext`, raising `FamilyAccessDenied` for **both** "no such family" and "not a member". |
| `build_family_context_dependency` / `build_capability_dependency` | `apps/api/authz.py` | The mechanical 401/403/404 mapping. A route asks for a capability and never inspects a role string. |
| `Capability` table | `src/mura/identity/policy.py` | `viewer ⊂ editor ⊂ owner`. |

**Decision.** Book routes are Principal-native like every other family route. No new
authorization concept, no new status code, no weakening. Two capabilities are added to the
existing table (§5.4).
### 1.3 Archive read models

`src/mura/storage/archive_read.py` is the single product projection over the archive, and its
module docstring states the two rules the book depends on:

> *Nothing is invented.* Every field comes from a persisted row. Where the archive does not
> know something — a date, a title, who someone is — the DTO says so with a null rather than a
> plausible default.

> *The trust rules stay in Core.* Which relationship claims are strong enough to appear as an
> edge is already decided when the graph is materialized. This module reads that decision; it
> does not re-derive it.

`resolve_mentions(session, family_id, recording_ids)` is the only honest mention→person
mapping: a mention is a string somebody said, a person is a durable node, and the link was
written by entity resolution. Names are explicitly **not** identity.

**Decision.** The Grounding Compiler reads the archive **through this module** (one additive
method) rather than issuing its own SQL. The alternative — a second projection — is exactly
how two parts of the product end up disagreeing about who someone is.

### 1.4 Evidence, corrections, uncertainty, conflicts

| Concept | Persisted at | Why the book needs it |
| --- | --- | --- |
| Evidence spans and quoted support | `pipeline_results.result` (`EvidenceBackedObject`, `evidence_ids`) | The writer may only speak from these; the quote gate checks against them. |
| Self-corrections | `archive_corrections` (`original_value`, `corrected_value`, `explanation`, `kind`) | `original_value` is a **forbidden** string. Rule 16 of the brief. |
| Uncertainty | `unresolved_questions`, `cleaned_transcript.uncertain_fragments`, `EpistemicStatus.UNCERTAIN` / `REMEMBERED_IMPRECISELY` | Uncertain memory must stay uncertain in prose. |
| Conflicts | `archive_conflicts` + `archive_conflict_decisions` | Two versions of a memory are preserved, never merged into one asserted truth. |
| Evidence ladder | `EvidenceClass` A–U, `AssertionMode`, `RelationshipState` | The book must not promote a `D`, `E` or `U` claim into confident prose. |

These are precisely the anti-hallucination inputs the brief requires, and they already exist.
The book does not need a new evidence model.

### 1.5 AI usage and cost accounting

`ai_usage_events` + `AIUsageLedger.record_usage()` + `mura/cost.py::calculate_ai_cost` already
provide: provider/model/operation, token counts including cache hits, latency, success,
attempt, `error_code`, `estimated_cost_usd`, `pricing_version`, and correlation to
`request_id` / `job_id` / `recording_id` / `family_id`. Unknown pricing returns
`(None, None)` rather than a fabricated number — a rule the book must not break.

`DeepSeekClient(on_usage=…)` is the single hook; it already exists and is already wired in
`apps/worker/main.py`. Book calls therefore reach the ledger **without new plumbing**, once
`book_id` and `chapter_number` are carried in the correlation context.

### 1.6 Private object storage

### 1.7 Observability and privacy

`mura/logging.py` provides a JSON single-line production format, ContextVars for
`request_id` / `job_id` / `recording_id` / `family_id` / `attempt` / `worker_id`, and a
centralized `LogSanitizer` that redacts by **key substring** (`text`, `content`, `prompt`,
`story`, `quote`, `transcript`, `token`, `secret`, …). `mura/observability.py` sanitizes trace
attributes through the same idea. `mura/sentry.py` is independent per process.

**Decision.** Extend the existing sanitizer with book-specific key substrings rather than
building a second redaction path (§5.6).

### 1.8 Language handling

`mura/asr/language.py` detects every language actually present in produced text using the nine
graphemes that exist in Kazakh Cyrillic but not Russian, plus closed-class function words from
both. `mura/linguistics/{russian,kazakh,kazakh_kinship,morphology,multilingual}.py` and
`mura/factual_support.py` provide surface-form matching, kinship lexicons and morphological
normalization.

**Decision.** The named-person, relationship and language gates reuse these modules instead of
inventing new regexes. `mura/linguistics/corrections.py` shows how self-correction is already
modelled.

### 1.9 The corpus compiler as reference material

`MURA/build.py` is a dataset compiler with ten quality gates. Four map directly onto the
deterministic chapter gates this feature needs:

| Gate | Implementation | Reused for |
| --- | --- | --- |
| **G4** | every `evidence_quote` must be a **verbatim substring** of `raw_transcript` after whitespace normalization | quote gate |
| **G5** | the superseded (wrong) variant must never reach grounding or the chapter; the corrected value must be present | correction gate |
| **G6** | every proper name in a chapter must be anchored in the transcript/grounding, with a `SOFT_TERMS` downgrade list so cultural and institutional vocabulary is not mistaken for a person | named-person gate |
| **G7** | every four-digit year in a chapter must be grounded | year gate |
| **G9** | language/script coherence | language gate |

This is strong evidence that the brief's §29 gates are reachable deterministically, and it
already encodes the false-positive escapes the brief warns about (soft terms, sentence-start
heuristics, length guards).

**Constraint.** `MURA/samples/*.json` are **synthetic corpus samples**. They are reference
material for *rules*, never for *family data*. Book generation reads only the authorized
family archive. A test enforces that book code never opens `MURA/`.

---

## 2. Missing domain models

Confirmed by repository-wide search for `book_jobs|BookJob|family_book|FamilyBook`: **no
matches**. There is no book table, model, route, worker, prompt or UI.

What is genuinely missing:

1. A durable **book aggregate** (identity, lifecycle, output language, target length).
2. An **immutable source snapshot** so an already-generated book stays explainable when the
   archive changes tomorrow.
3. A **validated structured blueprint** — the archive has no notion of a plan.
4. **Chapter-level persisted state**, so a crash at chapter 8 does not restart chapters 1–7.
5. A **review outcome** with issues and a bounded repair count.
6. **Export artifacts** with private object keys.
7. **Book job rows** with their own lease (see §3).
8. A **correlation extension** on `ai_usage_events` so cost is attributable per book/chapter.

Everything else the brief lists is derivable from components that already exist.

---

## 3. Job strategy: dedicated `book_jobs`, not `processing_jobs`

`processing_jobs.recording_id` is a **`NOT NULL` foreign key to `recordings`**, and its status
vocabulary is ASR-specific (`queued`, `transcribing`, `cleaning`, `extracting`, `resolving`).
Book work has no recording, and `transcribing` is not a true statement about it.

Forcing book work into that table would require making `recording_id` nullable — which
silently weakens the recording queue's own invariant — and would overload a status enum with
values that mean different things depending on which client reads it.

**Decision: a dedicated `book_jobs` table that mirrors the column set exactly.** The claim
algorithm, lease semantics, heartbeat, terminal-write guard and crash recovery are identical;
only the row and its repository differ. This is the option the brief explicitly allows, and it
is the one that keeps `processing_jobs` honest.
---

## 4. Source snapshot strategy

**When:** at `POST /v1/families/{family_id}/books`, in the API. It is bounded, deterministic
and calls no provider — which is exactly what the brief's §35 sequence prescribes and what
keeps the request fast while every creative call stays in the worker.

**What:** only archive material authorized for this book, read through the archive read model:

- selected recordings (or all eligible completed ones), each with its persisted
  `pipeline_results` payload;
- canonical people, **with `DateView` precision preserved** (`value`, `precision`,
  `original_expression`, `approximate`) — rendering "1 января 1978" from "в семьдесят
  восьмом" would invent a day and a month nobody said;
- canonical relationships from the **materialized** graph only;
- events, stories and claims with their evidence references, `evidence_class`,
  `assertion_mode` and `verification_status`;
- resolved corrections (`original_value` → `corrected_value`);
- uncertainties (`unresolved_questions`, uncertain fragments, uncertain epistemic status);
- conflicts — open **and** resolved, with their rationale;
- an evidence index (id → text, recording, speaker), capped;
- `allowed_years`, derived only from grounded text;
- a manifest: source recording/story/claim/event/person ids, counts, `snapshot_created_at`,
  `content_hash` (sha256 over canonical JSON), `compiler_version`.

**Immutability.** The snapshot is written once and has **no update path** in the repository.
`book_source_snapshots.book_id` is `UNIQUE`, so a retry cannot create a second one. A book
therefore always remains explainable against the truth it was written from; regenerating after
the archive changes produces a **new** book row that references the old one.

**Material anchor.** The snapshot exposes *candidate phrases harvested from evidence*. The
planner may select one; the deterministic validator requires it to be a normalized substring
of that evidence corpus, otherwise `material_anchor` is set to `null`. An heirloom therefore
cannot be invented — an unsupported anchor is stripped, not accepted.

**Eligibility.** A recording is eligible only when its job is `completed` and a
`pipeline_results` row exists. An empty eligible set returns `409 no_eligible_sources` rather
than producing an empty book.

---

## 5. Provider integration

### 5.1 The `temperature` gap — a real finding

The brief asks for planner ≈ 0.4 and writer ≈ 0.7–0.8, while instructing: *"Do not hardcode
unsupported model parameters"* and *"obey provider capabilities"*.

`DeepSeekClient._request_model_json` builds a **fixed** request body:

```python
{"model", "messages", "response_format": {"type": "json_object"},
 "thinking": {"type": "disabled"}, "max_tokens", "stream": False}
```

The string `temperature` appears in **no** `.py`, `.md` or `.toml` file in the repository. The
parameter is genuinely unsupported today, and no test asserts the request body.

**Decision.** Add `temperature: float | None = None` to `request_json` /
`_request_model_json`. When it is `None` the key is **omitted**, so every existing call
(`clean`, `extract`, repair passes) produces a byte-identical body and the 1122-test baseline
cannot move. Book stages pass 0.4 / 0.75 / 0.5 / 0.1 / 0.2 explicitly. This is an additive
provider-capability extension, documented as such — not a hidden change of default behaviour.

### 5.2 `thinking: {"type": "disabled"}` stays

That is the existing convention for every DeepSeek call in this repository. A 30–45-call book
with reasoning enabled would multiply cost for no grounding benefit, and changing it is out of
scope for this feature. Recorded as a documented cost lever, not silently altered.
---

## 6. Export requirements

| Format | Engine | Verified how |
| --- | --- | --- |
| PDF | **WeasyPrint** (HTML/CSS → PDF) | Requires pango/cairo/harfbuzz/gdk-pixbuf + a Cyrillic font. Not installable on this Windows host without GTK, so verification is by building `Dockerfile.worker` and rendering inside the container. |
| EPUB | **Pure-Python EPUB 3 writer** (`zipfile` + XHTML/OPF/nav) | Fully verifiable in the normal pytest suite, on any host, with no system binary. |

EPUB does **not** use Pandoc. Pandoc is the brief's preferred engine, but the brief permits
"another justified implementation if Pandoc integration is impractical" — and adding a ~150 MB
Haskell binary to an image that already carries a PDF engine, for a container format that is a
ZIP of XHTML files, is not a good trade. A hand-written EPUB 3 is deterministic,
dependency-free and **actually testable in CI**, which Pandoc here would not be. The deviation
is stated explicitly rather than dressed up as Pandoc support.

### Dockerfile surface

Two images exist: `Dockerfile` (API) and `Dockerfile.worker` (worker), both `python:3.11-slim`,
both `pip install .`.

- **Worker** gains the apt layer (`libpango-1.0-0`, `libpangoft2-1.0-0`, `libharfbuzz0b`,
  `libcairo2`, `libgdk-pixbuf-2.0-0`, `fonts-dejavu-core`, `shared-mime-info`) and installs
  `".[books]"`.
- **API** gains nothing: it only streams an already-rendered artifact, so the API image stays
  lean and the HTTP process keeps no rendering engine at all.

Rationale for worker-side rendering: the platform's central rule is that the API submits and
reads work while the worker executes it. Rendering a 50–80 page document is work.

`fonts-dejavu-core` is the deliberate choice for body typography: it covers Cyrillic including
the nine Kazakh-specific letters, so a Kazakh book does not render as tofu boxes.

---

## 7. Frontend integration points

| Surface | File | Change |
| --- | --- | --- |
| Archive doorways on Home | `src/components/home/archive-doorways.tsx` | Add one doorway, shown only when eligible memories exist. |
| Typed Core client | `src/lib/mura/core-api.ts`, `archive-api.ts` | A third client module, same `coreRequest` boundary. No raw `fetch` in a screen. |
| Proxy allowlist | `src/app/api/mura/[...path]/proxy.ts` | Add book patterns; keep `/v1/operations/*` blocked. Forward `content-disposition` **only** so downloads get a filename. |
| i18n | `src/lib/i18n.tsx` | New keys in **all three** dictionaries; `i18n-parity.test.ts` already fails the build if one is missed. |
| Polling | `src/lib/mura/pipeline-state.ts` | Reuse `nextPollDelayMs` / backoff conventions for book progress polling. |
| Design language | `docs/LAYOUT.md`, `globals.css` tokens | `rounded-panel`, `bg-raised`, `text-body`, `text-meta`, `text-muted`, `shadow-soft`, `focus-ring`, `max-w-measure`, `bg-sand`, `bg-peach`. No new visual identity. |

The progress surface shows **truthful stages and chapter counters** ("Пишем главу 3 из 12"),
never a synthetic percentage — consistent with `REAL_USER_FLOW.md` §2 and the Product Bible's
"never fake AI processing".

---

## 8. Risks

| Risk | Why it matters | Mitigation |
| --- | --- | --- |
| Named-person gate false positives | A faithful chapter names institutions, months, cities and cultural terms. A regex-only gate would reject good prose. | Two severities: a known-given-name / surname-pattern token that is unanchored is a **blocker**; any other unanchored capitalised candidate is a **warning** plus a grounding penalty. An explicit non-person allowlist (institutions, months, snapshot places, `SOFT_TERMS`-style vocabulary) downgrades. Every candidate is reported, so nothing is silently dropped. |
| Relationship gate false positives | Kinship words appear figuratively (`аға` as an honorific, `брат` as a comrade). | Reuse the existing RU/KK kinship lexicons and only test sentences where both endpoints resolve to snapshot people; otherwise warn. |
| Snapshot size | A large archive could produce a very large bundle. | `MURA_BOOK_MAX_SOURCE_RECORDINGS` and `MURA_BOOK_MAX_EVIDENCE_QUOTES` bound it; evidence quotes are capped per person/story. |
| Planner references a real id in the wrong role | Structured output can be well-formed but wrong. | Deterministic validation of every referenced id, plus the relationship gate at chapter level. |
| Provider cost | 10–15 chapters × (write + review + bounded repair) + plan + continuity ≈ 30–45 calls per book. | Every call is in the ledger with `book_id`/`chapter_number`; targets are configurable; `thinking` stays disabled; repairs are bounded and prefer deterministic fixes where the failure is arithmetic. |
| PDF engine on Debian slim | Missing libs or font coverage would make "PDF export works" false. | The apt layer is explicit and the render is verified by an actual Docker run; a missing engine degrades to a named `error_code`, never to a silent empty file. |
| Deploying mid-generation | Railway restarts containers routinely. | Nothing in-process is authoritative: chapter statuses and continuity are persisted, the lease expires, another worker reclaims, and approved chapters are never regenerated. |
| Stale README numbers | Misleading regression signal. | Corrected as part of this work. |

---

## 9. Migrations

One new revision appended to the single linear chain:

`migrations/versions/20260918_0013_family_books.py`, `down_revision = "20260917_0012"`.

Creates `books`, `book_source_snapshots`, `book_plans`, `book_chapters`,
`book_continuity_states`, `book_exports`, `book_jobs`; alters `ai_usage_events` with
`book_id` + `chapter_number` and an index on `book_id`. Includes a real `downgrade()`.

Indexes are limited to the access patterns that actually exist: `books.family_id`,
`books.status`, `book_jobs.book_id`, `book_jobs.(status, next_attempt_at)`,
`book_exports.book_id`, `book_chapters.book_id` plus the `UNIQUE(book_id, chapter_number)`
resume key, and `ai_usage_events.book_id`. No speculative indexing.

---

## 10. Test strategy

Deterministic fakes only — **no paid provider call in the automated suite**, following the
repository's existing practice (the deterministic benchmark needs no GPU or API key).

| Area | What it must prove |
| --- | --- |
| Domain + API | creation, capability matrix, 401/403/404, BOLA (another family's book → 404), cross-family `recording_ids` → 404, empty archive → 409, cancel, regenerate creates a new row |
| Snapshot | eligibility, canonical people only, corrections/uncertainty/conflicts carried, hash stability, no update path, manifest completeness, selected vs all |
| Blueprint | valid plan accepted; unknown person / recording / evidence ref rejected; unsupported year rejected; zero-grounding chapter rejected; chapter-count and word-budget validation; arithmetic repair |
| Gates | all eight, including deliberate non-person proper nouns and two people with similar names |
| Writer / reviewer | unknown person and year rejected; corrections, uncertainty and conflicts preserved; reviewer approved / repair_required / blocked; bounded repair loop; reviewer rewrite not trusted |
| Worker | resume after crash, resume at chapter N **without re-writing approved chapters**, idempotent retry without duplicate chapters, terminal failure keeps approved chapters, cancellation between chapters, lease reclaim, attempt bound |
| Export | approved chapters only, rejected draft text absent, EPUB 3 container structure, artifact key shape, private access, engine-gated PDF |
| Privacy | no chapter text, plan, prompt or evidence quote in logs, traces or the ledger |
| Cost | book calls create usage events with `book_id`/`chapter_number`; unknown pricing stays `NULL` |
| Fixtures | the eight brief-mandated hard cases, asserting uncertainty and conflict are *preserved*, not resolved |
| Frontend | book API client, status states, processing UI, download UI, proxy allowlist |

---

## 11. What this audit deliberately does not do

No Redis, Celery or RabbitMQ. No agent framework. No authentication rewrite. No weakening of
BOLA. No public audio or artifacts. No family text in logs or Sentry. No fabricated material
anchor, date, person, relationship or quotation. No automatic resolution of conflicting
memories. No single-call whole-book generation. No raw planner JSON in the user interface. No
provider call inside an HTTP request. No change to the recording pipeline. No fine-tuning. No
synthetic `MURA/` sample used as family evidence. No `epubcheck` claim: EPUB validity is
asserted structurally by this repository's own tests.

### 5.3 Operation names

The repository convention is lower-case snake `{domain}_{action}` (`cleaner`,
`cleaner_repair`, `extractor`, `extractor_repair`). The book registers:

`book_plot`, `book_chapter_write`, `book_chapter_repair`, `book_chapter_review`,
`book_state_summary`.

`book_export_assist` is **deliberately not registered**: MVP export is deterministic code with
no model call, and inventing one so that a name exists in a table would be fake accounting.

`operation` is passed explicitly to `request_json` (the signature supports it) rather than
leaving `_detect_operation` to guess from the system prompt.

Reused unchanged from `mura/leases.py`: `LeaseHeartbeat`, `new_worker_id`,
`LeaseOwnershipLost`. Nothing in that module is recording-specific.

Failure policy mirrors `storage/completion.py`:

- **deferrable** (provider timeout, 429, truncated structured response, transient storage
  failure) → release the lease, `status` stays `queued`, `next_attempt_at` = now + exponential
  backoff, `stage` names what failed. `attempts` counts claims and is incremented by the claim.
- **terminal** (blueprint invalid after bounded repair, chapter gate failed after bounded
  repair, empty source snapshot, wrong language repeatedly) → `failed` with a stable
  `error_code` and a **privacy-safe** `error_detail`. Approved chapters are kept.
- **never retried**: authentication errors, schema bugs, invalid source references.

`AudioStorage` is a protocol with `LocalAudioStorage` and `SupabaseAudioStorage`. Keys are
always server-generated from canonical identifiers and validated against a strict segment
pattern; an uploaded filename never contributes to a path. Production keys are
`family/{family_id}/recordings/{recording_id}/original{ext}` in a **private** bucket. The API
streams objects to authorized members and the storage key never leaves the server.

**Decision.** The book reuses this *architecture* through a sibling module, and leaves
`audio.py` byte-for-byte untouched — including its audio-only extension and MIME allowlists,
which must not be widened for `.pdf`/`.epub` (§5.3).