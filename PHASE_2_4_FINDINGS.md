# MURA (Мұра) — Phase 2.4 Defect & Findings Log

This document records all defects, architectural friction points, and edge cases discovered and resolved during Phase 2.4 staging runtime validation. Every code modification in this phase directly addresses one of these documented findings.

---

### STAGING-001: WeasyPrint Native GTK3 Library Dependency on Bare Windows Host

- **Severity**: MEDIUM (Deployment Environment Friction)
- **Component**: `src/mura/book/exporter.py`
- **Symptom**:
  Calling `WeasyPrintRenderer.render_pdf()` on Windows hosts without GTK3 C-libraries installed raised `ExportEngineUnavailable: cannot load library 'libgobject-2.0-0'`. When simulating runtime PDF crashes via fault injection, the import error occurred before the fault hook was evaluated.
- **Root Cause**:
  `weasyprint` relies on native `libgobject-2.0-0.dll` via `ctypes.util.find_library`. The fault injection check was positioned after `import weasyprint`.
- **Resolution**:
  1. `WeasyPrintRenderer` already possessed a clean fallback to `ExportEngineUnavailable` when GTK3 is missing.
  2. Repositioned the staging fault injection hook (`FAULT_PDF_FAILURE`) to execute before the import, allowing controlled simulation of PDF renderer crashes in headless CI/development environments without native GTK runtime.
- **Regression Prevention**:
  `tests/staging/test_failure_injection.py::test_weasyprint_pdf_failure_records_failed_export` verifies that a renderer failure marks the export row as `status="failed"`, `error_code="RENDER_FAILED"`, while approved book chapters remain completely preserved.

---

### STAGING-002: DeepSeek Error Response Chaining & Classification Degradation

- **Severity**: HIGH (Reliability & Rate Limiting)
- **Component**: `src/mura/deepseek/client.py`, `src/mura/reliability/failures.py`
- **Symptom**:
  When DeepSeek API returned HTTP 429 (Rate Limit) or 401 (Invalid API Key), the error classifier fell back to generic `PROVIDER_UNKNOWN` or infinite retry loops rather than inspecting `Retry-After` headers or marking terminal failure.
- **Root Cause**:
  `DeepSeekClient._request_model_json` raised `DeepSeekError` as a standalone exception without attaching the `requests.Response` object or chaining `from last_error`. Downstream `classify_failure` could not inspect the HTTP status code or response headers.
- **Resolution**:
  1. Added `response: requests.Response | None = None` to `DeepSeekError.__init__`.
  2. Chained exception raising via `from last_error` in `DeepSeekClient`.
  3. Enhanced `classify_failure` in `src/mura/reliability/failures.py` to inspect `exc.__cause__` and `error_str` for timeouts and HTTP status codes (429, 401, 503).
- **Regression Prevention**:
  Verified by `tests/staging/test_failure_injection.py`:
  - `test_deepseek_429_adheres_to_retry_after_header` (confirms `retry_after_seconds == 45.0` and disposition `RETRY`).
  - `test_provider_401_marks_terminal_failure_without_looping` (confirms category `PROVIDER_AUTH_ERROR` and disposition `TERMINAL`).
  - `test_provider_503_causes_transient_retry` (confirms category `PROVIDER_SERVER_ERROR`).

---

### STAGING-003: Fault Injection Engine Metadata Loss on One-Shot Consume

- **Severity**: LOW (Test Harness Edge Case)
- **Component**: `src/mura/testing/fault_injection.py`
- **Symptom**:
  When a test injected a fault with metadata (e.g. `retry_after=45`), consuming the fault popped the metadata dictionary before the HTTP client handler could read it, defaulting to fallback values (30s).
- **Root Cause**:
  `consume_fault()` popped from both `_active_faults` and `_fault_metadata` on the initial trigger check.
- **Resolution**:
  Modified `consume_fault()` to only clear the fault trigger from `_active_faults`, retaining `_fault_metadata` until the test context exits (`reset_faults()`).
- **Regression Prevention**:
  Verified in `tests/staging/test_failure_injection.py::test_deepseek_429_adheres_to_retry_after_header`.

---

### STAGING-004: BookExportRow Attribute Mismatch in Privacy Data Export

- **Severity**: HIGH (GDPR / Privacy Compliance Defect)
- **Component**: `src/mura/privacy.py`
- **Symptom**:
  Invoking `GET /v1/families/{family_id}/privacy/export` on a family with completed book exports crashed with `AttributeError: 'BookExportRow' object has no attribute 'export_format'`.
- **Root Cause**:
  In `src/mura/privacy.py`, the exporter accessed `exp.export_format` and `exp.file_size_bytes`. The SQLAlchemy model `BookExportRow` defines these mapped columns as `exp.format` and `exp.size_bytes`.
- **Resolution**:
  Corrected attribute access in `src/mura/privacy.py` line 163 to `exp.format` and `exp.size_bytes`.
- **Regression Prevention**:
  `tests/staging/test_privacy_and_security.py::test_privacy_export_structure_and_zero_leakage` validates the complete JSON export schema for families containing books and exports, ensuring HTTP 200 and zero secret leakage.

---

### STAGING-005: Environment Setting Alias & Production Fault Safety Invariant

- **Severity**: HIGH (Security & Safety Guardrail)
- **Component**: `src/mura/config.py`, `src/mura/testing/fault_injection.py`
- **Symptom**:
  `CoreSettings` environment field alias is `MURA_ENVIRONMENT`. Setting `APP_ENV` alone left `environment` defaulting to `LOCAL`. Conversely, if `MURA_FAULT_INJECTION` is accidentally left enabled in production, the application must refuse to start.
- **Root Cause**:
  Lack of hard compile-time / startup validation across configuration settings.
- **Resolution**:
  1. Added explicit `mura_fault_injection` field to `CoreSettings`.
  2. Implemented `@model_validator(mode="after")` in `CoreSettings`:
     ```python
     if self.environment == Environment.PRODUCTION and self.mura_fault_injection:
         raise ValueError("Fault injection cannot be enabled in production.")
     ```
  3. Added `assert_fault_injection_allowed()` check in `src/mura/testing/fault_injection.py` that inspects both `MURA_ENVIRONMENT` and `APP_ENV`.
- **Regression Prevention**:
  Verified by `tests/staging/test_failure_injection.py::test_production_safety_gate_blocks_fault_injection`.

