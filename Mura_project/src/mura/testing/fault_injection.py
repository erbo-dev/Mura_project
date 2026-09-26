"""Controlled Staging-Only Fault Injection Harness.

STRICT INVARIANTS:
1. Disabled by default.
2. Impossible to activate in production: startup validator and runtime check
   immediately raise ValueError if APP_ENV == "production".
3. Thread-safe one-shot triggers for simulating distributed failures:
   - DEEPSEEK_TIMEOUT_ONCE
   - DEEPSEEK_429_ONCE (with Retry-After)
   - PROVIDER_401_ONCE (terminal provider authentication failure)
   - PROVIDER_503_ONCE (transient provider unavailable)
   - STORAGE_503_ONCE (transient object store unavailable)
   - PDF_FAILURE_ONCE (PDF export engine crash)
4. Never logs, buffers, or captures customer or family data.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Generator
from contextlib import contextmanager

FAULT_DEEPSEEK_TIMEOUT = "DEEPSEEK_TIMEOUT_ONCE"
FAULT_DEEPSEEK_429 = "DEEPSEEK_429_ONCE"
FAULT_PROVIDER_401 = "PROVIDER_401_ONCE"
FAULT_PROVIDER_503 = "PROVIDER_503_ONCE"
FAULT_STORAGE_503 = "STORAGE_503_ONCE"
FAULT_PDF_FAILURE = "PDF_FAILURE_ONCE"

ALL_FAULTS = frozenset(
    {
        FAULT_DEEPSEEK_TIMEOUT,
        FAULT_DEEPSEEK_429,
        FAULT_PROVIDER_401,
        FAULT_PROVIDER_503,
        FAULT_STORAGE_503,
        FAULT_PDF_FAILURE,
    }
)

_lock = threading.Lock()
_active_faults: set[str] = set()
_fault_metadata: dict[str, dict[str, object]] = {}


def assert_fault_injection_allowed() -> None:
    """Hard gate: ensure fault injection can NEVER run in production."""
    env = (os.environ.get("MURA_ENVIRONMENT", "") or os.environ.get("APP_ENV", "")).strip().lower()
    if env == "production":
        raise ValueError("Fault injection cannot be enabled in production.")


def is_fault_injection_enabled() -> bool:
    assert_fault_injection_allowed()
    return os.environ.get("MURA_FAULT_INJECTION", "0").strip().lower() in {"1", "true", "yes"}


def activate_fault(fault_name: str, **metadata: object) -> None:
    assert_fault_injection_allowed()
    if not is_fault_injection_enabled():
        raise RuntimeError("MURA_FAULT_INJECTION must be enabled to activate faults.")
    if fault_name not in ALL_FAULTS:
        raise ValueError(f"Unknown fault: {fault_name}")

    with _lock:
        _active_faults.add(fault_name)
        if metadata:
            _fault_metadata[fault_name] = metadata


def deactivate_fault(fault_name: str) -> None:
    with _lock:
        _active_faults.discard(fault_name)
        _fault_metadata.pop(fault_name, None)


def reset_faults() -> None:
    with _lock:
        _active_faults.clear()
        _fault_metadata.clear()


def consume_fault(fault_name: str) -> bool:
    """Checks if a one-shot fault is active, and if so, clears and returns True."""
    assert_fault_injection_allowed()
    if not is_fault_injection_enabled():
        return False

    with _lock:
        if fault_name in _active_faults:
            _active_faults.discard(fault_name)
            return True
        return False


def get_fault_metadata(fault_name: str) -> dict[str, object]:
    with _lock:
        return dict(_fault_metadata.get(fault_name, {}))


@contextmanager
def fault_injected(fault_name: str, **metadata: object) -> Generator[None, None, None]:
    """Context manager for scoping an injected fault to a test block."""
    activate_fault(fault_name, **metadata)
    try:
        yield
    finally:
        deactivate_fault(fault_name)
