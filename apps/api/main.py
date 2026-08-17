from __future__ import annotations

import atexit
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, HttpUrl, field_validator
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from apps.api.conflicts import register_conflict_routes
from apps.api.errors import REQUEST_ID_HEADER, register_error_handlers
from mura.asr import RemoteASRClient
from mura.capabilities import (
    AsrRegistration,
    CapabilitiesView,
    derive_capabilities,
)
from mura.config import CoreSettings
from mura.deepseek import DeepSeekClient, DeepSeekPipelineService
from mura.domain.models import PipelineRequest, PipelineResult, ResolutionStatus
from mura.jobs import (
    JobStatus,
    JobView,
    RecordingAccepted,
    RecordingResultView,
    ReviewItemsView,
    resolve_retry_state,
)
from mura.orchestration import AudioStorageError, LocalAudioStorage, RecordingJobWorker
from mura.pipeline import MuraPipeline
from mura.security import verify_bearer_token
from mura.storage.database import (
    Database,
    DatabaseRuntimeSettings,
    ProcessingJobRow,
    RecordingRepository,
    WorkerRegistrationRow,
    postgres_connect_args,
)

API_TITLE = "Mura Core API"
API_VERSION = "0.2.0"
API_DESCRIPTION = (
    "Audio ingestion, asynchronous ASR orchestration, source-linked family-memory "
    "extraction, review items, and entity resolution."
)

_REQUEST_ID_PATTERN = re.compile(r"\A[A-Za-z0-9_-]{8,64}\Z")

router = APIRouter()

_runtime_lock = Lock()
_runtime: CoreRuntime | None = None
_readiness_lock = Lock()
_readiness_engines: dict[str, Engine] = {}


def resolve_request_id(supplied: str | None) -> str:
    """Reuse a caller-supplied correlation id only when it is short and opaque."""

    if supplied is not None and _REQUEST_ID_PATTERN.fullmatch(supplied):
        return supplied
    return f"req_{uuid.uuid4().hex}"


def _readiness_engine(settings: CoreSettings) -> Engine:
    """Pool-free probe engine so readiness checks never consume application pool slots."""

    database_url = settings.database_url
    with _readiness_lock:
        engine = _readiness_engines.get(database_url)
        if engine is None:
            connect_args: dict[str, object] = {}
            if database_url.startswith(("postgresql", "postgres")):
                connect_args = dict(
                    postgres_connect_args(_database_runtime_settings(settings)),
                )
            elif database_url.startswith("sqlite"):
                connect_args = {"check_same_thread": False}
            engine = create_engine(
                database_url,
                poolclass=NullPool,
                future=True,
                connect_args=connect_args,
            )
            _readiness_engines[database_url] = engine
        return engine


def _database_runtime_settings(settings: CoreSettings) -> DatabaseRuntimeSettings:
    return DatabaseRuntimeSettings(
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle_seconds=settings.db_pool_recycle_seconds,
        connect_timeout_seconds=settings.db_connect_timeout_seconds,
        statement_timeout_seconds=settings.db_statement_timeout_seconds,
    )


class WorkerRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    status: str = "ready"

    @field_validator("url")
    @classmethod
    def require_https(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https":
            raise ValueError("worker URL must use HTTPS")
        return value


@dataclass
class CoreRuntime:
    settings: CoreSettings
    database: Database
    repository: RecordingRepository
    pipeline: MuraPipeline
    storage: LocalAudioStorage
    worker: RecordingJobWorker

    def stop(self) -> None:
        self.worker.stop()


def get_settings() -> CoreSettings:
    try:
        return CoreSettings()  # type: ignore[call-arg]
    except Exception as exc:
        # Validation messages can echo supplied secrets, so only a stable code escapes.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Core service is not configured",
        ) from exc


def _build_pipeline(settings: CoreSettings) -> MuraPipeline:
    client = DeepSeekClient(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        primary_model=settings.deepseek_model,
        fallback_model=settings.deepseek_fallback_model,
    )
    return MuraPipeline(DeepSeekPipelineService(client, focused_extraction=True))


def get_runtime(
    settings: Annotated[CoreSettings, Depends(get_settings)],
) -> CoreRuntime:
    global _runtime
    if _runtime is None:
        with _runtime_lock:
            if _runtime is None:
                database = Database(
                    settings.database_url,
                    runtime=_database_runtime_settings(settings),
                )
                if settings.database_auto_create:
                    database.create_schema()
                repository = RecordingRepository(database)
                pipeline = _build_pipeline(settings)
                storage = LocalAudioStorage(
                    settings.audio_storage_dir,
                    max_upload_bytes=settings.core_max_upload_mb * 1024 * 1024,
                )
                worker = RecordingJobWorker(
                    repository=repository,
                    pipeline=pipeline,
                    asr_client=RemoteASRClient(
                        api_key=settings.kaggle_asr_api_key,
                        timeout_seconds=settings.asr_request_timeout_seconds,
                    ),
                    poll_interval_seconds=settings.job_poll_interval_seconds,
                    asr_retry_seconds=settings.asr_retry_seconds,
                )
                runtime = CoreRuntime(
                    settings=settings,
                    database=database,
                    repository=repository,
                    pipeline=pipeline,
                    storage=storage,
                    worker=worker,
                )
                _runtime = runtime
                worker.start()
                atexit.register(runtime.stop)
    assert _runtime is not None
    return _runtime


def require_core_token(
    settings: Annotated[CoreSettings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    verify_bearer_token(authorization, expected_token=settings.core_api_key)


def require_worker_token(
    settings: Annotated[CoreSettings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    verify_bearer_token(
        authorization,
        expected_token=settings.worker_registration_token,
    )


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness only: never touches the database, runtime, or any external provider."""

    return {"status": "ok", "service": "mura-core"}


@router.get("/ready")
def ready(settings: Annotated[CoreSettings, Depends(get_settings)]) -> JSONResponse:
    """Readiness: bounded PostgreSQL check, no provider calls and no worker start."""

    try:
        with _readiness_engine(settings).connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unavailable", "service": "mura-core", "database": "unavailable"},
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"status": "ready", "service": "mura-core", "database": "ready"},
    )


@router.get(
    "/v1/capabilities",
    response_model=CapabilitiesView,
    dependencies=[Depends(require_core_token)],
)
def capabilities(
    settings: Annotated[CoreSettings, Depends(get_settings)],
) -> CapabilitiesView:
    """Locally-known product capabilities. Never calls GigaAM or DeepSeek."""

    registration = AsrRegistration.UNKNOWN
    registered_at: datetime | None = None
    try:
        with Session(_readiness_engine(settings)) as session:
            row = session.get(WorkerRegistrationRow, "kaggle-asr")
        if row is None or row.status != "ready":
            registration = AsrRegistration.UNAVAILABLE
        else:
            registration = AsrRegistration.REGISTERED
            registered_at = _aware_required(row.registered_at)
    except Exception:
        # The registration table could not be read; say so rather than guess.
        registration = AsrRegistration.UNKNOWN

    return derive_capabilities(
        asr_registration=registration,
        asr_registered_at=registered_at,
        analysis_configured=bool(settings.deepseek_api_key),
        now=datetime.now(UTC),
    )


@router.post(
    "/v1/recordings",
    response_model=RecordingAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_core_token)],
)
def create_recording(
    runtime: Annotated[CoreRuntime, Depends(get_runtime)],
    file: Annotated[UploadFile, File(...)],
    family_id: Annotated[str, Form(min_length=1, max_length=128)],
    speaker_id: Annotated[str, Form(min_length=1, max_length=128)],
    speaker_name: Annotated[str, Form(min_length=1, max_length=256)],
) -> RecordingAccepted:
    recording_id = f"rec_{uuid.uuid4().hex}"
    job_id = f"job_{uuid.uuid4().hex}"
    original_filename = Path(file.filename or "audio.bin").name

    try:
        file.file.seek(0)
        audio_path = runtime.storage.save(
            recording_id=recording_id,
            original_filename=original_filename,
            source=file.file,
        )
    except AudioStorageError as exc:
        message = str(exc)
        code = (
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            if "maximum upload size" in message
            else status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
        )
        raise HTTPException(status_code=code, detail=message) from exc

    try:
        runtime.repository.create_recording_and_job(
            recording_id=recording_id,
            job_id=job_id,
            family_id=family_id,
            speaker_id=speaker_id,
            speaker_name=speaker_name,
            original_filename=original_filename,
            content_type=file.content_type,
            audio_path=audio_path,
        )
    except Exception:
        audio_path.unlink(missing_ok=True)
        raise

    return RecordingAccepted(recording_id=recording_id, job_id=job_id)


@router.get(
    "/v1/jobs/{job_id}",
    response_model=JobView,
    dependencies=[Depends(require_core_token)],
)
def get_job(
    job_id: str,
    runtime: Annotated[CoreRuntime, Depends(get_runtime)],
) -> JobView:
    row = runtime.repository.get_job(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _job_view(row)


@router.get(
    "/v1/recordings/{recording_id}",
    response_model=RecordingResultView,
    dependencies=[Depends(require_core_token)],
)
def get_recording_result(
    recording_id: str,
    runtime: Annotated[CoreRuntime, Depends(get_runtime)],
) -> RecordingResultView:
    recording = runtime.repository.get_recording(recording_id)
    if recording is None:
        raise HTTPException(status_code=404, detail="recording not found")
    job = runtime.repository.get_job_for_recording(recording_id)
    if job is None:
        raise HTTPException(status_code=500, detail="recording has no processing job")
    result = runtime.repository.get_pipeline_result(recording_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "recording processing is not completed",
                "job": _job_view(job).model_dump(mode="json"),
            },
        )
    return RecordingResultView(
        recording_id=recording.recording_id,
        family_id=recording.family_id,
        speaker_id=recording.speaker_id,
        speaker_name=recording.speaker_name,
        job_id=job.job_id,
        status=JobStatus(job.status),
        result=result,
    )


@router.get(
    "/v1/recordings/{recording_id}/review-items",
    response_model=ReviewItemsView,
    dependencies=[Depends(require_core_token)],
)
def get_review_items(
    recording_id: str,
    runtime: Annotated[CoreRuntime, Depends(get_runtime)],
) -> ReviewItemsView:
    result = runtime.repository.get_pipeline_result(recording_id)
    if result is None:
        raise HTTPException(status_code=404, detail="completed recording result not found")

    extractor_usage = result.processing.get("extractor_usage", {})
    extraction_issues = (
        extractor_usage.get("extraction_issues", []) if isinstance(extractor_usage, dict) else []
    )
    return ReviewItemsView(
        recording_id=recording_id,
        uncertain_fragments=[
            item.model_dump(mode="json") for item in result.cleaned_transcript.uncertain_fragments
        ],
        detected_corrections=[
            item.model_dump(mode="json") for item in result.cleaned_transcript.detected_corrections
        ],
        unresolved_questions=[
            item.model_dump(mode="json") for item in result.extraction.unresolved_questions
        ],
        extraction_issues=(extraction_issues if isinstance(extraction_issues, list) else []),
        ambiguous_resolutions=[
            item.model_dump(mode="json")
            for item in result.resolutions
            if item.status == ResolutionStatus.NEEDS_REVIEW
        ],
    )


@router.post(
    "/v1/process-transcript",
    response_model=PipelineResult,
    dependencies=[Depends(require_core_token)],
)
def process_transcript(
    request: PipelineRequest,
    runtime: Annotated[CoreRuntime, Depends(get_runtime)],
) -> PipelineResult:
    return runtime.pipeline.process(request)


@router.post(
    "/v1/workers/register",
    dependencies=[Depends(require_worker_token)],
)
def register_worker(
    registration: WorkerRegistration,
    runtime: Annotated[CoreRuntime, Depends(get_runtime)],
) -> dict[str, object]:
    row = runtime.repository.register_worker(
        url=str(registration.url),
        status=registration.status,
    )
    return {
        "accepted": True,
        "url": row.url,
        "status": row.status,
        "registered_at": row.registered_at.isoformat(),
    }


@router.get(
    "/v1/workers/current",
    dependencies=[Depends(require_worker_token)],
)
def current_worker(
    runtime: Annotated[CoreRuntime, Depends(get_runtime)],
) -> dict[str, object]:
    row = runtime.repository.current_worker()
    if row is None:
        return {"url": None, "status": "missing", "registered_at": None}
    return {
        "url": row.url,
        "status": row.status,
        "registered_at": row.registered_at.isoformat(),
    }


def _job_view(row: ProcessingJobRow) -> JobView:
    job_status = JobStatus(row.status)
    retryable, retry_after_seconds, next_retry_at = resolve_retry_state(
        status=job_status,
        stage=row.stage,
        next_attempt_at=_aware_required(row.next_attempt_at),
        now=datetime.now(UTC),
    )
    return JobView(
        job_id=row.job_id,
        recording_id=row.recording_id,
        status=job_status,
        stage=row.stage,
        attempts=row.attempts,
        retryable=retryable,
        retry_after_seconds=retry_after_seconds,
        next_retry_at=next_retry_at,
        error_code=row.error_code,
        created_at=_aware_required(row.created_at),
        started_at=_aware_optional(row.started_at),
        completed_at=_aware_optional(row.completed_at),
        updated_at=_aware_required(row.updated_at),
    )


def _aware_required(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _aware_optional(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return _aware_required(value)


def _bootstrap_settings() -> CoreSettings | None:
    """Settings for wiring the app itself. Absent config fails closed, never open."""

    try:
        return CoreSettings()  # type: ignore[call-arg]
    except Exception:
        return None


def create_app(settings: CoreSettings | None = None) -> FastAPI:
    resolved = settings if settings is not None else _bootstrap_settings()
    docs_enabled = resolved.api_docs_enabled if resolved is not None else False
    allowed_origins = list(resolved.cors_allowed_origins) if resolved is not None else []
    allowed_hosts = list(resolved.allowed_hosts) if resolved is not None else []

    application = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        description=API_DESCRIPTION,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )

    @application.middleware("http")
    async def assign_request_id(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    if allowed_hosts:
        application.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    if allowed_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER],
            allow_credentials=False,
            expose_headers=[REQUEST_ID_HEADER],
        )

    register_error_handlers(application)
    application.include_router(router)
    register_conflict_routes(
        application,
        get_runtime_dependency=get_runtime,
        core_token_dependency=require_core_token,
    )
    return application


app = create_app()
