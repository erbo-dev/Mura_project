from __future__ import annotations

from decimal import Decimal

from mura.cost import DEFAULT_PRICING_VERSION, calculate_ai_cost
from mura.logging import job_id_ctx, recording_id_ctx, request_id_ctx
from mura.storage.ai_usage import AIUsageEventRow, AIUsageLedger
from mura.storage.database import Database


def test_calculate_ai_cost_deepseek_flash_with_cache() -> None:
    # 1,000,000 total prompt tokens, 800,000 cached (hit), 200,000 miss
    # Miss: 200,000 * $0.27 / 1M = $0.054
    # Hit:  800,000 * $0.07 / 1M = $0.056
    # Completion: 100,000 * $1.10 / 1M = $0.110
    # Total = 0.054 + 0.056 + 0.110 = 0.220000
    cost, version = calculate_ai_cost(
        provider="deepseek",
        model="deepseek-v4-flash",
        input_tokens=1_000_000,
        cached_input_tokens=800_000,
        output_tokens=100_000,
    )
    assert version == DEFAULT_PRICING_VERSION
    assert cost == Decimal("0.220000")


def test_calculate_ai_cost_deepseek_pro_no_cache() -> None:
    # Input: 10,000 * $0.55 / 1M = $0.005500
    # Output: 5,000 * $2.19 / 1M = $0.010950
    # Total: $0.016450
    cost, version = calculate_ai_cost(
        provider="deepseek",
        model="deepseek-v4-pro",
        input_tokens=10_000,
        output_tokens=5_000,
    )
    assert version == DEFAULT_PRICING_VERSION
    assert cost == Decimal("0.016450")


def test_calculate_ai_cost_whisper_audio() -> None:
    # 60 seconds of audio * $0.000100/s = $0.006000
    cost, version = calculate_ai_cost(
        provider="whisper",
        model="whisper-1",
        audio_seconds=60.0,
    )
    assert version == DEFAULT_PRICING_VERSION
    assert cost == Decimal("0.006000")


def test_calculate_ai_cost_unknown_model_returns_none() -> None:
    cost, version = calculate_ai_cost(
        provider="unknown_provider",
        model="custom-model-99",
        input_tokens=5000,
        output_tokens=1000,
    )
    assert cost is None
    assert version is None


def test_calculate_ai_cost_decimal_precision_no_float_drift() -> None:
    # 1 token miss: 1 * 0.27 / 1M = 0.00000027 -> rounded to 0.000000
    cost_small, _ = calculate_ai_cost(
        provider="deepseek",
        model="deepseek-chat",
        input_tokens=1,
    )
    assert isinstance(cost_small, Decimal)
    assert cost_small == Decimal("0.000000")

    # 10 tokens: 10 * 0.27 / 1M = 0.0000027 -> 0.000003
    cost_ten, _ = calculate_ai_cost(
        provider="deepseek",
        model="deepseek-chat",
        input_tokens=10,
    )
    assert cost_ten == Decimal("0.000003")


def test_ai_usage_ledger_records_events_and_context_vars() -> None:
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    ledger = AIUsageLedger(database)

    req_token = request_id_ctx.set("req_test_123")
    job_token = job_id_ctx.set("job_test_456")
    rec_token = recording_id_ctx.set("rec_test_789")

    try:
        event = ledger.record_usage(
            provider="deepseek",
            model="deepseek-v4-flash",
            operation="cleaner",
            latency_ms=450,
            success=True,
            input_tokens=2500,
            output_tokens=300,
            cached_input_tokens=1500,
        )
        assert event.request_id == "req_test_123"
        assert event.job_id == "job_test_456"
        assert event.recording_id == "rec_test_789"
        assert event.success is True
        assert event.estimated_cost_usd is not None
        assert event.pricing_version == DEFAULT_PRICING_VERSION

        # Verify database row persisted
        with database.session_factory() as session:
            row = session.get(AIUsageEventRow, event.event_id)
            assert row is not None
            assert row.provider == "deepseek"
            assert row.operation == "cleaner"
            assert row.latency_ms == 450
            assert row.input_tokens == 2500
            assert row.output_tokens == 300
            assert row.cached_input_tokens == 1500
            assert row.estimated_cost_usd == event.estimated_cost_usd
    finally:
        request_id_ctx.reset(req_token)
        job_id_ctx.reset(job_token)
        recording_id_ctx.reset(rec_token)


def test_ai_usage_ledger_records_failure_event() -> None:
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    ledger = AIUsageLedger(database)

    event = ledger.record_usage(
        provider="deepseek",
        model="deepseek-v4-flash",
        operation="extractor",
        latency_ms=1200,
        success=False,
        error_code="provider_timeout",
        attempt=2,
    )
    assert event.success is False
    assert event.error_code == "provider_timeout"
    assert event.attempt == 2
    assert event.estimated_cost_usd is None

    with database.session_factory() as session:
        row = session.get(AIUsageEventRow, event.event_id)
        assert row is not None
        assert row.success is False
        assert row.error_code == "provider_timeout"


def test_ai_usage_ledger_24h_summary() -> None:
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    ledger = AIUsageLedger(database)

    ledger.record_usage(
        provider="deepseek",
        model="deepseek-v4-flash",
        operation="cleaner",
        latency_ms=300,
        success=True,
        input_tokens=10_000,
        output_tokens=1_000,
        cached_input_tokens=5_000,
    )
    ledger.record_usage(
        provider="whisper",
        model="whisper-1",
        operation="transcription",
        latency_ms=1500,
        success=True,
        audio_seconds=120.0,
    )

    summary = ledger.get_summary_24h()
    assert summary["requests_count"] == 2
    assert summary["input_tokens"] == 10_000
    assert summary["output_tokens"] == 1_000
    assert summary["cached_input_tokens"] == 5_000
    assert summary["audio_seconds"] == 120.0
    assert Decimal(summary["estimated_cost_usd"]) > Decimal("0")
