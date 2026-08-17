from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    Response,
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
from apps.api.identity import register_identity_routes
from apps.api.recordings import register_recording_routes
from mura.capabilities import (
    AsrRegistration,
    CapabilitiesView,
    derive_capabilities,
)
from mura.config import CoreSettings
from mura.deepseek import DeepSeekClient, DeepSeekPipelineService
from mura.domain.models import PipelineRequest, PipelineResult
from mura.identity.auth import (
    ApplicationAuthVerifier,
    AuthenticationError,
    AuthMode,
    OidcAuthVerifier,
    Principal,
)
from mura.jobs import (
    JobStatus,
    JobView,
    resolve_retry_state,
)
from mura.orchestration import LocalAudioStorage
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
from mura.storage.identity import IdentityRepository

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
    """Everything the API needs to serve requests -- and nothing more.

    There is deliberately no worker here. Recording jobs are claimed and
    executed by the standalone mura-worker process, so serving a request can
    never start job processing.
    """

    settings: CoreSettings
    database: Database
    repository: RecordingRepository
    pipeline: MuraPipeline
    storage: LocalAudioStorage


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
                # Recording jobs are executed by the standalone mura-worker
                # process. The API only submits and reads them.
                _runtime = CoreRuntime(
                    settings=settings,
                    database=database,
                    repository=repository,
                    pipeline=pipeline,
                    storage=storage,
                )
    assert _runtime is not None
    return _runtime


def require_core_token(
    settings: Annotated[CoreSettings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    verify_bearer_token(authorization, expected_token=settings.core_api_key)


def get_identity_repository(
    runtime: Annotated[CoreRuntime, Depends(get_runtime)],
) -> IdentityRepository:
    return IdentityRepository(runtime.database)


def get_auth_verifier(
    settings: Annotated[CoreSettings, Depends(get_settings)],
) -> ApplicationAuthVerifier:
    """Built from configuration only; tests override this dependency."""

    if settings.auth_mode is not AuthMode.OIDC:
        raise HTTPException(status_code=401, detail="authentication required")
    assert settings.auth_issuer and settings.auth_audience and settings.auth_jwks_url
    return OidcAuthVerifier(
        issuer=settings.auth_issuer,
        audience=settings.auth_audience,
        jwks_url=settings.auth_jwks_url,
        allowed_algorithms=tuple(settings.auth_allowed_algorithms),
        clock_skew_seconds=settings.auth_clock_skew_seconds,
        jwks_cache_seconds=settings.auth_jwks_cache_seconds,
    )


def get_principal(
    verifier: Annotated[ApplicationAuthVerifier, Depends(get_auth_verifier)],
    identity: Annotated[IdentityRepository, Depends(get_identity_repository)],
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """Verify the bearer token, then resolve it to an internal MURA user."""

    try:
        verified = verifier.verify(authorization)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail="authentication required") from exc
    return identity.resolve_principal(verified)


def require_operations_token(
    settings: Annotated[CoreSettings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Destructive operator routes only. Never satisfied by CORE_API_KEY."""

    verify_bearer_token(authorization, expected_token=settings.operations_api_key)


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
    register_identity_routes(
        application,
        principal_dependency=get_principal,
        identity_repository_dependency=get_identity_repository,
    )
    register_recording_routes(
        application,
        get_runtime_dependency=get_runtime,
        core_token_dependency=require_core_token,
        job_view_builder=_job_view,
    )
    register_conflict_routes(
        application,
        get_runtime_dependency=get_runtime,
        core_token_dependency=require_core_token,
        operations_token_dependency=require_operations_token,
    )
    return application


app = create_app()
