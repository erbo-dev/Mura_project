from __future__ import annotations

import dataclasses
import signal
import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api.main import CoreRuntime, create_app, get_settings
from apps.worker.main import build_worker, install_signal_handlers, main
from apps.worker.main import (
    WorkerSupervisor,
    build_book_worker,
    build_worker,
    build_worker_supervisor,
    install_signal_handlers,
    main,
)
from mura.config import CoreSettings
from mura.orchestration import RecordingJobWorker
from mura.orchestration.books import BookJobWorker

CORE_TOKEN = "c" * 40


def _settings(**overrides: Any) -> CoreSettings:
    payload: dict[str, Any] = {
        "DEEPSEEK_API_KEY": "sk-" + "d" * 40,
        "CORE_API_KEY": CORE_TOKEN,
        "WORKER_REGISTRATION_TOKEN": "r" * 40,
        "KAGGLE_ASR_API_KEY": "a" * 40,
        "OPERATIONS_API_KEY": "o" * 40,
        "DATABASE_URL": "sqlite+pysqlite:///:memory:",
        "DATABASE_AUTO_CREATE": True,
    }
    payload.update(overrides)
    return CoreSettings.model_validate(payload)


# ------------------------------------------------------- API does not process


def test_api_runtime_has_no_worker() -> None:
    fields = {field.name for field in dataclasses.fields(CoreRuntime)}

    assert "worker" not in fields
    assert fields == {"settings", "database", "repository", "pipeline", "storage"}


def test_serving_requests_never_starts_job_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[str] = []
    monkeypatch.setattr(
        RecordingJobWorker,
        "start",
        lambda self: started.append("started"),
    )
    claimed: list[str] = []
    monkeypatch.setattr(
        RecordingJobWorker,
        "process_once",
        lambda self: claimed.append("claimed") or False,
    )
    settings = _settings()
    application = create_app(settings)
    application.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(application, raise_server_exceptions=False)

    client.get("/health")
    client.get("/ready")
    client.get("/v1/capabilities", headers={"Authorization": f"Bearer {CORE_TOKEN}"})

    # The API must never claim a recording job just because it served traffic.
    assert started == []
    assert claimed == []


def test_health_and_ready_do_not_depend_on_a_worker() -> None:
    settings = _settings()
    application = create_app(settings)
    application.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(application, raise_server_exceptions=False)

    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200


# ----------------------------------------------------------- worker lifecycle


def test_worker_is_constructed_with_lease_settings() -> None:
    worker = build_worker(_settings(JOB_LEASE_SECONDS=240, JOB_HEARTBEAT_SECONDS=60))

    assert worker.lease_seconds == 240
    assert worker.heartbeat_seconds == 60
    assert worker.worker_id.startswith("worker_")


def test_each_worker_process_gets_a_distinct_identity() -> None:
    first = build_worker(_settings())
    second = build_worker(_settings())

    assert first.worker_id != second.worker_id


def test_request_stop_ends_the_loop_without_touching_current_work() -> None:
    worker = build_worker(_settings())
    polled = threading.Event()

    def never_claims() -> bool:
        polled.set()
        return False

    worker.process_once = never_claims  # type: ignore[method-assign]
    thread = threading.Thread(target=worker.run_forever, daemon=True)
    thread.start()

    assert polled.wait(timeout=5)
    worker.request_stop()
    thread.join(timeout=5)

    assert not thread.is_alive()


def test_idle_polling_waits_instead_of_spinning() -> None:
    # 0.1s is the configured minimum poll interval.
    worker = build_worker(_settings(JOB_POLL_INTERVAL_SECONDS=0.1))
    calls: list[int] = []
    worker.process_once = lambda: calls.append(1) or False  # type: ignore[method-assign]
    thread = threading.Thread(target=worker.run_forever, daemon=True)

    thread.start()
    threading.Event().wait(0.4)
    worker.request_stop()
    thread.join(timeout=5)

    # With a 100ms interval a 400ms window allows only a handful of polls; a
    # spinning loop would produce thousands.
    assert len(calls) < 50


def test_signal_handlers_install_without_error() -> None:
    worker = build_worker(_settings())

    install_signal_handlers(worker)

    assert signal.getsignal(signal.SIGINT) is not None


def test_signal_handler_requests_stop_without_undefined_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed: dict[int, Any] = {}

    def capture_handler(signum: int, handler: Any) -> None:
        installed[signum] = handler

    class StopTarget:
        stopped = False

        def request_stop(self) -> None:
            self.stopped = True

    target = StopTarget()
    monkeypatch.setattr(signal, "signal", capture_handler)

    install_signal_handlers(target)

    handler = installed[signal.SIGINT]
    handler(signal.SIGINT, None)

    assert target.stopped is True


def test_worker_refuses_to_start_without_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("DEEPSEEK_API_KEY", "CORE_API_KEY", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("apps.worker.main.CoreSettings", _raising_settings)

    assert main() == 2


class _raising_settings:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise ValueError("CORE_API_KEY too short: leaky-secret-value")


def test_main_runs_only_the_supervisor(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings()
    events: list[str] = []

    class SubWorker:
        def __init__(self, worker_id: str) -> None:
            self.worker_id = worker_id

    class FakeSupervisor:
        def __init__(self) -> None:
            self.recording_worker = SubWorker("worker_recording")
            self.book_worker = SubWorker("worker_book")

        def request_stop(self) -> None:
            events.append("request_stop")

        def run_forever(self) -> None:
            events.append("run")

        def stop(self, timeout_seconds: float = 5.0) -> None:
            events.append("stop")

    supervisor = FakeSupervisor()

    monkeypatch.setattr("apps.worker.main.CoreSettings", lambda: settings)
    monkeypatch.setattr(
        "apps.worker.main.build_worker",
        lambda _settings: (_ for _ in ()).throw(AssertionError("legacy worker must not be built")),
    )
    monkeypatch.setattr("apps.worker.main.build_worker_supervisor", lambda _settings: supervisor)
    monkeypatch.setattr("apps.worker.main.install_signal_handlers", lambda _target: None)
    monkeypatch.setattr("apps.worker.main.configure_logging", lambda *args, **kwargs: None)
    monkeypatch.setattr("apps.worker.main.init_sentry", lambda *args, **kwargs: None)
    monkeypatch.setattr("apps.worker.main.flush_sentry", lambda *args, **kwargs: None)

    assert main() == 0
    assert events == ["run", "stop"]


def test_build_book_worker_lease_and_heartbeat_settings() -> None:
    worker = build_book_worker(
        _settings(
            BOOK_JOB_LEASE_SECONDS=300,
            BOOK_JOB_HEARTBEAT_SECONDS=60,
            BOOK_JOB_POLL_INTERVAL_SECONDS=1.5,
        )
    )
    assert isinstance(worker, BookJobWorker)
    assert worker.lease_seconds == 300
    assert worker.heartbeat_seconds == 60
    assert worker.poll_interval_seconds == 1.5
    assert worker.worker_id.startswith("worker_")


def test_build_worker_supervisor_constructs_both_workers() -> None:
    supervisor = build_worker_supervisor(_settings())
    assert isinstance(supervisor, WorkerSupervisor)
    assert isinstance(supervisor.recording_worker, RecordingJobWorker)
    assert isinstance(supervisor.book_worker, BookJobWorker)
    assert supervisor.recording_worker.worker_id != supervisor.book_worker.worker_id
    assert supervisor.recording_worker.repository.database is supervisor.book_worker.db


def test_supervisor_request_stop_stops_both_workers() -> None:
    supervisor = build_worker_supervisor(_settings())
    assert not supervisor.recording_worker._stop_event.is_set()
    assert not supervisor.book_worker._stop_event.is_set()

    supervisor.request_stop()

    assert supervisor.recording_worker._stop_event.is_set()
    assert supervisor.book_worker._stop_event.is_set()


def test_supervisor_runs_both_workers_concurrently_and_stops_on_request() -> None:
    supervisor = build_worker_supervisor(_settings())
    rec_polled = threading.Event()
    book_polled = threading.Event()

    def rec_process_once() -> bool:
        rec_polled.set()
        return False

    def book_process_once() -> bool:
        book_polled.set()
        return False

    supervisor.recording_worker.process_once = rec_process_once  # type: ignore[method-assign]
    supervisor.book_worker.process_once = book_process_once  # type: ignore[method-assign]

    thread = threading.Thread(target=supervisor.run_forever, daemon=True)
    thread.start()

    assert rec_polled.wait(timeout=5)
    assert book_polled.wait(timeout=5)

    supervisor.request_stop()
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_supervisor_propagates_exception_and_stops_sibling() -> None:
    supervisor = build_worker_supervisor(_settings())

    def rec_crash() -> None:
        raise RuntimeError("simulated recording worker crash")

    def book_hang() -> None:
        while not supervisor.book_worker._stop_event.is_set():
            supervisor.book_worker._stop_event.wait(0.1)

    supervisor.recording_worker.run_forever = rec_crash  # type: ignore[method-assign]
    supervisor.book_worker.run_forever = book_hang  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="simulated recording worker crash"):
        supervisor.run_forever()

    assert supervisor.book_worker._stop_event.is_set()

