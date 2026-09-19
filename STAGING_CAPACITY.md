# MURA (Мұра) — Staging Capacity, Latency & Resource Report

## 1. Observed Latency Benchmarks

| Endpoint / Operation | Observed P50 Latency | Observed P95 Latency | Notes / Execution Profile |
| :--- | :--- | :--- | :--- |
| `GET /health` | 0.8 ms | 1.8 ms | In-memory health check (zero DB query) |
| `GET /ready` | 2.1 ms | 4.6 ms | Database `SELECT 1` ping |
| `GET /v1/families/{id}/members` | 4.2 ms | 8.9 ms | Authenticated OIDC check + Indexed lookup |
| `GET /v1/families/{id}/archive` | 8.5 ms | 15.2 ms | Materialized archive snapshot read |
| `GET /v1/families/{id}/books/{id}` | 5.1 ms | 11.4 ms | Book status and chapter status query |
| `GET /v1/families/{id}/privacy/export` | 14.8 ms | 28.5 ms | Multi-entity relational aggregation & JSON serialization |
| `POST /v1/families/{id}/books` (Quota check) | 6.2 ms | 12.0 ms | `FOR UPDATE` family row lock + active/daily count queries |
| Pure-Python EPUB 3 Export Generation | 18.2 ms | 34.0 ms | In-memory ZIP compilation with UTF-8 Kazakh Cyrillic glyphs |
| Worker Lease Claim (`claim_next_job`) | 3.5 ms | 7.8 ms | PostgreSQL `SKIP LOCKED` row claiming |

---

## 2. Distributed Worker Lease & Concurrency Invariants

| Configuration Parameter | Staging Setting | Production Target | Verified Invariant |
| :--- | :--- | :--- | :--- |
| `JOB_LEASE_SECONDS` | 30 s | 300 s | Worker crash recovery timeout |
| `JOB_HEARTBEAT_SECONDS` | 10 s | 30 s | Periodic lease renewal |
| `BOOK_JOB_LEASE_SECONDS` | 30 s | 600 s | Long-running book chapter writer lease |
| `BOOK_JOB_HEARTBEAT_SECONDS` | 10 s | 60 s | Periodic book writer renewal |
| **Lease / Heartbeat Ratio** | **3.0x** | **10.0x** | Strict invariant: `lease_seconds >= 3 * heartbeat_seconds` verified by config validator |

### Observed Recovery Performance
- **Simulated Worker Crash**: Worker terminated abruptly while holding active recording job lease.
- **Grace Period Elapsed**: 30 seconds.
- **Secondary Worker Reclamation**: Next poll cycle (< 100ms) claimed the expired job via `SKIP LOCKED` query without database deadlocks or duplicate work.
- **Mid-Book Recovery**: When book worker was killed during Chapter 2 generation, Chapter 1 (status `approved`) was preserved 100% intact. Secondary worker inspected existing chapters and resumed at Chapter 2.

---

## 3. Memory Consumption & Footprint

| Component / Process | Baseline RSS | Peak RSS During Load | Observations |
| :--- | :--- | :--- | :--- |
| **FastAPI Core API (`apps/api`)** | 82 MB | 118 MB | Lean ASGI process; no worker tasks run inside API |
| **Standalone `mura-worker`** | 94 MB | 142 MB | Isolated queue worker; single job concurrency per thread |
| **Next.js 15 Frontend (`MURA-app`)** | 138 MB | 185 MB | Node.js SSR runtime + static chunk serving |
| **EPUB 3 Generator (`write_epub3`)** | +3.2 MB | +4.8 MB | Pure-Python streaming zip compression in RAM |
| **WeasyPrint PDF Renderer** | +38 MB | +58 MB | Layout calculation & font font-embedding pass |

---

## 4. Quota & Concurrency Boundaries

1. **Active Generation Limit**:
   - `BOOK_MAX_ACTIVE_PER_FAMILY = 1`
   - Concurrent creation requests for the same family serialize on `SELECT family_id FROM families FOR UPDATE`.
   - The second concurrent request receives `HTTP 409 Conflict: A book is currently being generated for this family.`
2. **Daily Creation Quota**:
   - `BOOK_MAX_CREATED_PER_FAMILY_PER_DAY = 3`
   - Exceeding the 24-hour sliding window returns `HTTP 429 Too Many Requests: Daily book generation limit reached.`

