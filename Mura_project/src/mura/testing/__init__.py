"""Testing utilities and fault injection."""

from mura.testing.fault_injection import (
    FAULT_DEEPSEEK_429,
    FAULT_DEEPSEEK_TIMEOUT,
    FAULT_PDF_FAILURE,
    FAULT_PROVIDER_401,
    FAULT_PROVIDER_503,
    FAULT_STORAGE_503,
    activate_fault,
    assert_fault_injection_allowed,
    consume_fault,
    deactivate_fault,
    fault_injected,
    is_fault_injection_enabled,
    reset_faults,
)

__all__ = [
    "FAULT_DEEPSEEK_429",
    "FAULT_DEEPSEEK_TIMEOUT",
    "FAULT_PDF_FAILURE",
    "FAULT_PROVIDER_401",
    "FAULT_PROVIDER_503",
    "FAULT_STORAGE_503",
    "activate_fault",
    "assert_fault_injection_allowed",
    "consume_fault",
    "deactivate_fault",
    "fault_injected",
    "is_fault_injection_enabled",
    "reset_faults",
]

