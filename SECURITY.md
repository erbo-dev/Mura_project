# MURA (Мұра) — Application Security & Abuse Hardening
### Threat Model · Tenancy Isolation (BOLA) · HTTP Security Headers · AI Abuse Quotas · Privileged Route Shielding

This document defines the security architecture, defensive controls, and abuse prevention mechanisms implemented in MURA.

---

## 1. Security Architecture & Threat Model

```text
┌────────────────────────────────────────────────────────────────────────┐
│                              Web Browser                               │
│  - CSP: default-src 'self', script-src 'self', microphone=(self)       │
│  - Strict-Origin-When-Cross-Origin, X-Frame-Options: DENY              │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    │ HTTPS (Next.js App Router)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        Next.js Server Proxy                            │
│  - Route allowlist: Blocks /v1/operations/* from browser access        │
│  - Session token exchange and cookie validation                        │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    │ HTTPS (Bearer JWT / Operations Token)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                            FastAPI Backend                             │
│  - SecurityHeadersMiddleware (nosniff, frame denial, permissions)     │
│  - TrustedHostMiddleware (Host header validation)                      │
│  - PyJWT JWKS validation with ES256 cryptographic verification         │
│  - Tenancy Isolation & Capability Authorization Engine                 │
│  - Concurrency row locks (FOR UPDATE) & Daily AI Generation Quotas     │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Authentication & Authorization Boundaries

### 2.1 Identity Verification (JWKS & JWT)
- **OIDC Provider**: Clerk supplies signed JWT-template tokens; Core verifies the configured issuer, audience, JWKS signature and asymmetric algorithm allowlist.
- **Cryptographic Trust Boundary**: PyJWT is pinned to exact versions (`PyJWT[crypto]==2.13.0`) in `pyproject.toml` to guard against supply chain manipulation.
- **JWKS Cache & Re-verification**: Keys are retrieved from the configured JWKS URI and cached with automatic rollover.

### 2.2 Broken Object Level Authorization (BOLA) Defenses
Every API request targeting a family resource (`/v1/families/{family_id}/...`) is resolved against the authenticated user's active membership:
1. **Tenant Isolation**: Queries strictly enforce `family_id == :family_id` in SQL.
2. **Capability Engine**: Users are assigned roles (`OWNER`, `EDITOR`, `VIEWER`), each possessing an explicit set of capabilities:
   - `read_recordings`, `manage_recordings`, `upload_audio`
   - `read_books`, `create_book`
   - `read_family`, `manage_members`
3. **Canonical 404 vs 403**:
   - If a resource does not belong to the requested `family_id`, the system returns `404 Not Found` (preventing existence probing across tenants).
   - If the user lacks the required role capability within their family, the system returns `403 Forbidden`.

---

## 3. HTTP Security Headers & Permissions Policy

### 3.1 Backend Security Middleware (`apps/api/security_headers.py`)
All responses emitted by the FastAPI backend include:
- `X-Content-Type-Options: nosniff`: Prevents MIME-type sniffing exploits.
- `X-Frame-Options: DENY`: Complete clickjacking defense by disallowing framing.
- `Referrer-Policy: strict-origin-when-cross-origin`: Restricts referrer information to origin-only for cross-origin requests.
- `Permissions-Policy: camera=(), geolocation=(), payment=(), usb=()`: Explicitly disables unneeded browser device sensors.

### 3.2 Frontend CSP & Microphone Preservation
Because MURA is an oral history audio recording platform, browser microphone access is critical:
- **API Permissions Policy**: Does NOT disable microphone.
- **Next.js Content Security Policy (`next.config.ts`)**:
  - Sets `microphone=(self)` to preserve browser microphone access for recording oral memories while denying access to third-party frames.

---

## 4. Abuse Protection & AI Cost Controls

Family Book generation invokes multi-stage LLM chains (Blueprinting, Chapter Drafting, Review, Gate Checks, and Repair). To prevent runaway costs, accidental concurrent triggers, or malicious quota exhaustion:

### 4.1 Durable Quota Enforcement (`src/mura/quotas.py`)
Configured via `src/mura/config.py`:
- `BOOK_MAX_ACTIVE_PER_FAMILY = 1`: At most one book generation job in `queued` or `running` state per family at any time.
- `BOOK_MAX_CREATED_PER_FAMILY_PER_DAY = 3`: At most three books may be created or regenerated per family within any sliding 24-hour window.

### 4.2 Race-Free Row Locking
To eliminate race conditions when concurrent requests attempt to trigger generation:
```sql
SELECT family_id FROM families WHERE family_id = :family_id FOR UPDATE;
```
Acquiring an exclusive row lock on the `families` table ensures that concurrent requests evaluate active and 24-hour counts sequentially.

### 4.3 Client Error Envelopes
- Active job collision returns HTTP `409 Conflict`:
  ```json
  {
    "code": "book_generation_already_active",
    "message": "A book generation job is already active for this family. Wait for it to complete.",
    "request_id": "req_..."
  }
  ```
- Daily quota exhaustion returns HTTP `429 Too Many Requests`:
  ```json
  {
    "code": "book_daily_limit_reached",
    "message": "Daily book creation limit (3 per 24 hours) reached for this family.",
    "request_id": "req_..."
  }
  ```

---

## 5. Privileged Endpoint Shielding

Operational metrics and stuck-job inspection endpoints reside under `/v1/operations/*`:
1. **API Key Guard**: Requires `OPERATIONS_API_KEY` via `Authorization: Bearer` or `X-Operations-Token`.
2. **Reverse Proxy Allowlist**: The Next.js API route proxy (`/api/mura/[...path]`) enforces an allowlist that strictly rejects any path beginning with `/v1/operations/`. It is impossible to query operator endpoints from the web client.

---

## 6. Canonical Error Masking

In production (`ENVIRONMENT != "development"`):
- Unhandled exceptions are intercepted by top-level FastAPI middleware.
- Raw SQL query dumps, Postgres connection strings, object storage credentials, and Python tracebacks are **never** returned in HTTP response bodies.
- The client receives a sanitized error payload with a unique `request_id` to enable correlated tracing in Sentry and Railway logs:
  ```json
  {
    "code": "internal_server_error",
    "message": "An unexpected error occurred. Reference request_id when contacting support.",
    "request_id": "req_5f8a9e2c1d"
  }
  ```
