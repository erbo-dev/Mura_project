"""Regression tests for fail-closed Book repair telemetry persistence."""

from __future__ import annotations

from typing import Any

import pytest

from mura.orchestration.books import _require_repair_telemetry


@pytest.mark.parametrize(
    ("telemetry", "field_name"),
    [
        ({"model": "deepseek-chat"}, "prompt_version"),
        ({"prompt_version": 123, "model": "deepseek-chat"}, "prompt_version"),
        ({"prompt_version": "book-repair-v1"}, "model"),
        ({"prompt_version": "book-repair-v1", "model": 123}, "model"),
    ],
)
def test_invalid_repair_telemetry_fails_closed(
    telemetry: dict[str, Any], field_name: str
) -> None:
    with pytest.raises(RuntimeError, match=field_name):
        _require_repair_telemetry(telemetry)


def test_valid_repair_telemetry_returns_durable_metadata() -> None:
    assert _require_repair_telemetry(
        {
            "prompt_version": "book-repair-v1",
            "model": "deepseek-chat",
            "total_tokens": 42,
        }
    ) == ("book-repair-v1", "deepseek-chat")
