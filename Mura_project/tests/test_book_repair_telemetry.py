from __future__ import annotations

from typing import Any

import pytest

from mura.orchestration.books import _require_repair_telemetry


def test_repair_telemetry_missing_prompt_version_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="prompt_version"):
        _require_repair_telemetry({"model": "deepseek-chat"})


def test_repair_telemetry_wrong_prompt_version_type_fails_closed() -> None:
    telemetry: dict[str, Any] = {"prompt_version": 123, "model": "deepseek-chat"}
    with pytest.raises(RuntimeError, match="prompt_version"):
        _require_repair_telemetry(telemetry)


def test_repair_telemetry_missing_model_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="model"):
        _require_repair_telemetry({"prompt_version": "book-repair-v1"})


def test_repair_telemetry_wrong_model_type_fails_closed() -> None:
    telemetry: dict[str, Any] = {"prompt_version": "book-repair-v1", "model": 123}
    with pytest.raises(RuntimeError, match="model"):
        _require_repair_telemetry(telemetry)


def test_valid_repair_telemetry_returns_durable_metadata() -> None:
    telemetry: dict[str, Any] = {
        "prompt_version": "book-repair-v1",
        "model": "deepseek-chat",
        "total_tokens": 42,
    }
    assert _require_repair_telemetry(telemetry) == (
        "book-repair-v1",
        "deepseek-chat",
    )
