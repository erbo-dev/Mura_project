"""Recognised text published while extraction is still running.

Recognition takes seconds and extraction most of a minute. The preview lets a
speaker read their own words during that minute. These tests pin who can see
it, when it disappears, and that a worker which lost its lease cannot write it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app, get_auth_verifier, get_runtime, get_settings
from mura.identity.policy import CAPABILITIES_BY_ROLE, Capability
from mura.jobs import JobStatus
from mura.leases import LeaseOwnershipLost
from mura.storage.database import Database, ProcessingJobRow, RecordingRepository, RecordingRow
from mura.storage.identity import IdentityRepository
from tests.authz_factories import FakePrincipalVerifier, create_family_with_id, create_test_user
from tests.test_family_recording_api import (
    FAMILY_A,
    FAMILY_B,
    _Runtime,
    _seed,
    _seed_recording,
    _settings,
)

TEXT = "Моя бабушка Гульнара жила в Алматы."


@pytest.fixture
def harness(tmp_path: Path) -> tuple[TestClient, Database, dict[str, str]]:
    settings = _settings()
    database = Database(settings.database_url)
    database.create_schema()
    _seed(database)
    _seed_recording(database, family_id=FAMILY_A, recording_id="rec_a", job_id="job_a", with_result=False)
    _seed_recording(database, family_id=FAMILY_B, recording_id="rec_b", job_id="job_b", with_result=False)

    identity = IdentityRepository(database)
    verifier = FakePrincipalVerifier()
    member = create_test_user(identity, subject="preview-owner", verifier=verifier)
    create_family_with_id(database, family_id=FAMILY_A, name="Family A", owner=member)
    create_family_with_id(
        database,
        family_id=FAMILY_B,
        name="Family B",
        owner=create_test_user(identity, subject="preview-stranger", verifier=verifier),
    )

    application = create_app(settings)
    runtime = _Runtime(database, tmp_path)
    application.dependency_overrides[get_settings] = lambda: settings
    application.dependency_overrides[get_runtime] = lambda: runtime
    application.dependency_overrides[get_auth_verifier] = lambda: verifier
    return TestClient(application, raise_server_exceptions=False), database, member.headers


def _set_preview(database: Database, recording_id: str, text: str | None) -> None:
    with database.session_factory.begin() as session:
        session.get(RecordingRow, recording_id).transcript_preview = text


def _set_status(database: Database, job_id: str, status: JobStatus) -> None:
    with database.session_factory.begin() as session:
        session.get(ProcessingJobRow, job_id).status = status.value


def test_an_unfinished_job_carries_the_recognised_text(harness) -> None:
    client, database, headers = harness
    _set_preview(database, "rec_a", TEXT)
    _set_status(database, "job_a", JobStatus.EXTRACTING)

    body = client.get(f"/v1/families/{FAMILY_A}/jobs/job_a", headers=headers).json()

    assert body["transcript_preview"] == TEXT


def test_before_recognition_there_is_no_preview(harness) -> None:
    client, _database, headers = harness

    body = client.get(f"/v1/families/{FAMILY_A}/jobs/job_a", headers=headers).json()

    assert body["transcript_preview"] is None


@pytest.mark.parametrize("status", [JobStatus.COMPLETED, JobStatus.FAILED])
def test_a_finished_job_does_not_repeat_it(harness, status: JobStatus) -> None:
    # Once finished, the stored result is the authoritative transcript; a second
    # copy on every poll would be redundant and could disagree with it.
    client, database, headers = harness
    _set_preview(database, "rec_a", TEXT)
    _set_status(database, "job_a", status)

    body = client.get(f"/v1/families/{FAMILY_A}/jobs/job_a", headers=headers).json()

    assert body["transcript_preview"] is None


def test_another_familys_preview_is_not_reachable(harness) -> None:
    client, database, headers = harness
    _set_preview(database, "rec_b", TEXT)

    response = client.get(f"/v1/families/{FAMILY_B}/jobs/job_b", headers=headers)

    assert response.status_code == 404
    assert TEXT not in response.text


def test_reading_jobs_never_grants_more_than_reading_recordings() -> None:
    """The job poll carries recording text, so READ_JOBS must imply READ_RECORDINGS.

    If a role is ever given job status without recording access, the preview on
    the job route would disclose content that role cannot otherwise read.
    """

    for role, capabilities in CAPABILITIES_BY_ROLE.items():
        if Capability.READ_JOBS in capabilities:
            assert Capability.READ_RECORDINGS in capabilities, role


def test_a_worker_that_lost_the_lease_cannot_write_the_preview(harness) -> None:
    _client, database, _headers = harness
    with database.session_factory.begin() as session:
        session.get(ProcessingJobRow, "job_a").lease_owner = "worker_current"
    repository = RecordingRepository(database)

    with pytest.raises(LeaseOwnershipLost):
        repository.save_transcript_preview("job_a", "stale text", lease_owner="worker_stale")
    repository.save_transcript_preview("job_a", TEXT, lease_owner="worker_current")

    with database.session_factory() as session:
        assert session.get(RecordingRow, "rec_a").transcript_preview == TEXT


def test_the_text_is_readable_while_extraction_is_still_running(tmp_path: Path) -> None:
    """The point of the preview: it exists before extraction finishes, not after."""

    from typing import cast

    from mura.asr import RemoteASRClient
    from mura.orchestration import RecordingJobWorker
    from mura.pipeline import MuraPipeline
    from tests.test_archive_worker_integration import (
        _ArchiveAwarePipeline,
        _FakeASR,
        _queue_recording,
    )

    database = Database(f"sqlite+pysqlite:///{tmp_path / 'preview.db'}")
    database.create_schema()
    repository = RecordingRepository(database)
    repository.register_worker(url="https://worker.example", status="ready")
    seen_during_extraction: list[str | None] = []

    class _Watching(_ArchiveAwarePipeline):
        def process(self, request, **kwargs):  # type: ignore[no-untyped-def]
            with database.session_factory() as session:
                seen_during_extraction.append(
                    session.get(RecordingRow, request.transcript.recording_id).transcript_preview
                )
            return super().process(request, **kwargs)

    worker = RecordingJobWorker(
        repository=repository,
        pipeline=cast(MuraPipeline, _Watching()),
        asr_client=cast(RemoteASRClient, _FakeASR()),
    )
    _queue_recording(repository, tmp_path=tmp_path, recording_id="rec_1")

    assert worker.process_once() is True
    assert seen_during_extraction == ["Ерлан туралы әңгіме."]
