from __future__ import annotations

import hashlib
import io
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from mura.config import AudioStorageBackend, CoreSettings
from mura.storage.storage_errors import StorageDeleteError
from mura.storage.audio import (
    AudioStorageError,
    AudioTooLargeError,
    LocalAudioStorage,
    SupabaseAudioStorage,
    UnsupportedAudioError,
    build_audio_storage,
)

FAMILY = "family_supabase_test"
RECORDING = "rec_" + "f" * 32
WAV = b"RIFF$\x00\x00\x00WAVEfmt " + b"\x00" * 32
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 32

SUPABASE_URL = "https://mock-proj.supabase.co"
SERVICE_ROLE_KEY = "mock-service-role-key-12345678901234567890"
BUCKET = "mura-audio"


@pytest.fixture
def mock_session() -> MagicMock:
    return MagicMock(spec=requests.Session)


@pytest.fixture
def storage(mock_session: MagicMock) -> SupabaseAudioStorage:
    return SupabaseAudioStorage(
        url=SUPABASE_URL,
        service_role_key=SERVICE_ROLE_KEY,
        bucket=BUCKET,
        max_upload_bytes=1024 * 1024,
        session=mock_session,
    )


# ------------------------------------------------------------------- save


def test_supabase_save_uploads_object_with_authentication(
    storage: SupabaseAudioStorage,
    mock_session: MagicMock,
) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_session.post.return_value = mock_response

    payload = WAV + b"\x01\x02\x03"
    stored = storage.save(
        family_id=FAMILY,
        recording_id=RECORDING,
        original_filename="voice.wav",
        content_type="audio/wav",
        source=io.BytesIO(payload),
    )

    assert stored.backend is AudioStorageBackend.SUPABASE
    assert stored.storage_key == f"family/{FAMILY}/recordings/{RECORDING}/original.wav"
    assert stored.sha256 == hashlib.sha256(payload).hexdigest()
    assert stored.size_bytes == len(payload)
    assert stored.content_type == "audio/wav"

    expected_url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{stored.storage_key}"
    mock_session.post.assert_called_once()
    call_args, call_kwargs = mock_session.post.call_args
    assert call_args[0] == expected_url
    assert call_kwargs["headers"]["Authorization"] == f"Bearer {SERVICE_ROLE_KEY}"
    assert call_kwargs["headers"]["apikey"] == SERVICE_ROLE_KEY
    assert call_kwargs["headers"]["Content-Type"] == "audio/wav"
    assert call_kwargs["headers"]["x-upsert"] == "true"
    assert call_kwargs["data"] == payload


def test_supabase_save_rejects_empty_payload(storage: SupabaseAudioStorage) -> None:
    with pytest.raises(UnsupportedAudioError, match="empty"):
        storage.save(
            family_id=FAMILY,
            recording_id=RECORDING,
            original_filename="empty.wav",
            content_type="audio/wav",
            source=io.BytesIO(b""),
        )


def test_supabase_save_rejects_oversized_payload(
    mock_session: MagicMock,
) -> None:
    small_storage = SupabaseAudioStorage(
        url=SUPABASE_URL,
        service_role_key=SERVICE_ROLE_KEY,
        bucket=BUCKET,
        max_upload_bytes=64,
        session=mock_session,
    )
    with pytest.raises(AudioTooLargeError):
        small_storage.save(
            family_id=FAMILY,
            recording_id=RECORDING,
            original_filename="big.wav",
            content_type="audio/wav",
            source=io.BytesIO(WAV + b"\x00" * 128),
        )


def test_supabase_save_raises_on_http_error(
    storage: SupabaseAudioStorage,
    mock_session: MagicMock,
) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.text = "Permission denied"
    mock_session.post.return_value = mock_response

    with pytest.raises(AudioStorageError, match="HTTP 403"):
        storage.save(
            family_id=FAMILY,
            recording_id=RECORDING,
            original_filename="voice.wav",
            content_type="audio/wav",
            source=io.BytesIO(WAV),
        )


# ------------------------------------------------------------------- exists


def test_supabase_exists_returns_true_on_200(
    storage: SupabaseAudioStorage,
    mock_session: MagicMock,
) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_session.get.return_value = mock_response

    key = f"family/{FAMILY}/recordings/{RECORDING}/original.wav"
    assert storage.exists(key) is True
    mock_session.get.assert_called_with(
        f"{SUPABASE_URL}/storage/v1/object/authenticated/{BUCKET}/{key}",
        headers={
            "Authorization": f"Bearer {SERVICE_ROLE_KEY}",
            "apikey": SERVICE_ROLE_KEY,
        },
        stream=True,
        timeout=(5.0, 10.0),
    )


def test_supabase_exists_returns_false_on_404(
    storage: SupabaseAudioStorage,
    mock_session: MagicMock,
) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_session.get.return_value = mock_response

    assert storage.exists("nonexistent_key") is False


# ------------------------------------------------------------------- open


def test_supabase_open_returns_stream_on_200(
    storage: SupabaseAudioStorage,
    mock_session: MagicMock,
) -> None:
    mock_raw = io.BytesIO(WAV)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raw = mock_raw
    mock_session.get.return_value = mock_response

    key = f"family/{FAMILY}/recordings/{RECORDING}/original.wav"
    stream = storage.open(key)
    assert stream.read() == WAV


def test_supabase_open_raises_filenotfound_on_404(
    storage: SupabaseAudioStorage,
    mock_session: MagicMock,
) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_session.get.return_value = mock_response

    with pytest.raises(FileNotFoundError):
        storage.open("missing_key")


# ------------------------------------------------------------------- delete


def test_supabase_delete_returns_true_on_200(
    storage: SupabaseAudioStorage,
    mock_session: MagicMock,
) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_session.delete.return_value = mock_response

    key = f"family/{FAMILY}/recordings/{RECORDING}/original.wav"
    assert storage.delete(key) is True
    mock_session.delete.assert_called_with(
        f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{key}",
        headers={
            "Authorization": f"Bearer {SERVICE_ROLE_KEY}",
            "apikey": SERVICE_ROLE_KEY,
        },
        timeout=(5.0, 15.0),
    )


def test_supabase_delete_returns_false_on_404(
    storage: SupabaseAudioStorage,
    mock_session: MagicMock,
) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_session.delete.return_value = mock_response

    assert storage.delete("missing_key") is False


# ------------------------------------------------------------------- materialize


def test_supabase_materialize_creates_and_cleans_up_temporary_file(
    storage: SupabaseAudioStorage,
    mock_session: MagicMock,
) -> None:
    mock_raw = io.BytesIO(WAV)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raw = mock_raw
    mock_session.get.return_value = mock_response

    key = f"family/{FAMILY}/recordings/{RECORDING}/original.wav"

    temp_path: Path | None = None
    with storage.materialize(key) as path:
        temp_path = path
        assert path.exists()
        assert path.suffix == ".wav"
        assert path.read_bytes() == WAV

    # Guaranteed cleanup on exit
    assert temp_path is not None
    assert not temp_path.exists()


# ------------------------------------------------------------------- factory


def test_build_audio_storage_constructs_local_backend(tmp_path: Path) -> None:
    settings = CoreSettings.model_construct(
        audio_storage_backend=AudioStorageBackend.LOCAL,
        audio_storage_dir=tmp_path / "audio",
        core_max_upload_mb=25,
    )
    backend = build_audio_storage(settings)
    assert isinstance(backend, LocalAudioStorage)
    assert backend.backend is AudioStorageBackend.LOCAL


def test_build_audio_storage_constructs_supabase_backend() -> None:
    settings = CoreSettings.model_construct(
        audio_storage_backend=AudioStorageBackend.SUPABASE,
        supabase_url="https://test.supabase.co",
        supabase_service_role_key="test-key-1234567890",
        supabase_storage_bucket="test-bucket",
        supabase_storage_timeout_seconds=45.0,
        core_max_upload_mb=25,
    )
    backend = build_audio_storage(settings)
    assert isinstance(backend, SupabaseAudioStorage)
    assert backend.backend is AudioStorageBackend.SUPABASE
    assert backend.bucket == "test-bucket"
    assert backend.timeout_seconds == 45.0




@pytest.mark.parametrize("status_code", [408, 429, 500, 502, 503, 504])
def test_supabase_delete_transient_errors_are_retryable(
    storage: SupabaseAudioStorage,
    mock_session: MagicMock,
    status_code: int,
) -> None:
    response = MagicMock()
    response.status_code = status_code
    response.headers = {"Retry-After": "17"} if status_code == 429 else {}
    mock_session.delete.return_value = response

    with pytest.raises(StorageDeleteError) as exc_info:
        storage.delete("object.wav")
    assert exc_info.value.retryable is True
    if status_code == 429:
        assert exc_info.value.retry_after_seconds == 17.0


@pytest.mark.parametrize("status_code", [401, 403])
def test_supabase_delete_auth_errors_are_terminal(
    storage: SupabaseAudioStorage,
    mock_session: MagicMock,
    status_code: int,
) -> None:
    response = MagicMock()
    response.status_code = status_code
    response.headers = {}
    mock_session.delete.return_value = response

    with pytest.raises(StorageDeleteError) as exc_info:
        storage.delete("object.wav")
    assert exc_info.value.retryable is False
    assert exc_info.value.code == "storage_auth_failed"


def test_supabase_delete_timeout_is_retryable(
    storage: SupabaseAudioStorage,
    mock_session: MagicMock,
) -> None:
    mock_session.delete.side_effect = requests.Timeout("socket timeout")
    with pytest.raises(StorageDeleteError) as exc_info:
        storage.delete("object.wav")
    assert exc_info.value.retryable is True
    assert exc_info.value.code == "storage_timeout"
