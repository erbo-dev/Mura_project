"""Audio persistence behind a provider-neutral boundary.

The domain must not know where bytes physically live. Everything above this
module addresses audio by an opaque ``storage_key``; only an implementation
knows that a key maps to a file, a bucket object or anything else.

The key is always server-generated from canonical identifiers. An uploaded
filename is untrusted display metadata and never contributes to a path, which
is what keeps traversal and absolute-path injection impossible by construction.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Protocol, cast

import requests

from mura.storage.storage_errors import StorageDeleteError, storage_delete_http_error

if TYPE_CHECKING:
    from mura.config import CoreSettings

#: Read size for streaming. Audio is never loaded into memory whole.
CHUNK_BYTES = 1024 * 1024

#: Canonical identifiers are the only things allowed into a key.
_SAFE_SEGMENT = re.compile(r"\A[A-Za-z0-9_-]{1,128}\Z")

ALLOWED_AUDIO_EXTENSIONS = {
    ".wav",
    ".mp3",
    ".m4a",
    ".mp4",
    ".aac",
    ".ogg",
    ".opus",
    ".webm",
    ".flac",
}

ALLOWED_CONTENT_TYPES = {
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
    "audio/mpeg",
    "audio/mp3",
    "audio/mp4",
    "audio/m4a",
    "audio/x-m4a",
    "audio/aac",
    "audio/ogg",
    "audio/opus",
    "audio/webm",
    "audio/flac",
    "audio/x-flac",
    "video/mp4",
    "video/webm",
}


class AudioStorageBackend(StrEnum):
    LOCAL = "local"
    SUPABASE = "supabase"


class AudioStorageError(ValueError):
    """Upload rejected before anything durable was written."""


class AudioTooLargeError(AudioStorageError):
    pass


class UnsupportedAudioError(AudioStorageError):
    pass


@dataclass(frozen=True)
class StoredAudio:
    """Durable, truthful facts about one stored object."""

    storage_key: str
    backend: AudioStorageBackend
    sha256: str
    size_bytes: int
    content_type: str


def safe_extension(original_filename: str) -> str:
    """Extension from an untrusted filename, validated against the allowlist."""

    suffix = Path(Path(original_filename or "audio.bin").name).suffix.lower()
    if suffix not in ALLOWED_AUDIO_EXTENSIONS:
        raise UnsupportedAudioError(f"unsupported audio extension: {suffix or '<none>'}")
    return suffix


def validate_content_type(content_type: str | None) -> str:
    if content_type is None or not content_type.strip():
        return "application/octet-stream"
    declared = content_type.split(";", 1)[0].strip().lower()
    if declared not in ALLOWED_CONTENT_TYPES:
        raise UnsupportedAudioError(f"unsupported audio content type: {declared}")
    return declared


def sniff_container(head: bytes) -> str | None:
    """Identify the container from its header bytes.

    This is header sniffing, not decoding: it proves the file begins like a
    known audio container, which is enough to reject an arbitrary binary named
    ``.wav``. It does not prove the stream is fully valid or playable -- that
    would need a real decoder pass.
    """

    if len(head) < 12:
        return None
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:3] == b"ID3" or (head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "mp3"
    if head[4:8] == b"ftyp":
        return "mp4"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    return None


#: Which containers may legitimately carry a given extension.
_EXTENSION_CONTAINERS: dict[str, set[str]] = {
    ".wav": {"wav"},
    ".mp3": {"mp3"},
    ".flac": {"flac"},
    ".ogg": {"ogg"},
    ".opus": {"ogg"},
    ".m4a": {"mp4"},
    ".mp4": {"mp4"},
    ".aac": {"mp3", "mp4"},
    ".webm": {"webm"},
}


def validate_container(extension: str, head: bytes) -> str | None:
    """Reject a payload whose header contradicts its extension."""

    container = sniff_container(head)
    if container is None:
        raise UnsupportedAudioError("audio content does not match a supported container")
    expected = _EXTENSION_CONTAINERS.get(extension, set())
    if expected and container not in expected:
        raise UnsupportedAudioError("audio content does not match its file extension")
    return container


def build_storage_key(*, family_id: str, recording_id: str, extension: str) -> str:
    """Server-generated key. Only canonical identifiers reach it."""

    for segment in (family_id, recording_id):
        if not _SAFE_SEGMENT.fullmatch(segment):
            raise AudioStorageError("storage key segments must be canonical identifiers")
    if extension not in ALLOWED_AUDIO_EXTENSIONS:
        raise UnsupportedAudioError(f"unsupported audio extension: {extension}")
    return f"family/{family_id}/recordings/{recording_id}/original{extension}"


class AudioStorage(Protocol):
    """The contract the domain depends on. No filesystem concepts appear here."""

    backend: AudioStorageBackend

    def save(
        self,
        *,
        family_id: str,
        recording_id: str,
        original_filename: str,
        content_type: str | None,
        source: BinaryIO,
    ) -> StoredAudio: ...

    def exists(self, storage_key: str) -> bool: ...

    def delete(self, storage_key: str) -> bool: ...

    def open(self, storage_key: str) -> BinaryIO: ...

    def materialize(self, storage_key: str) -> AbstractContextManager[Path]: ...


class LocalAudioStorage:
    """Filesystem implementation for local development and tests.

    The mapping from key to path lives here and nowhere else. A future object
    store implements the same protocol without the rest of the system changing.
    """

    backend = AudioStorageBackend.LOCAL

    def __init__(self, root: Path, *, max_upload_bytes: int) -> None:
        self.root = Path(root).resolve()
        self.max_upload_bytes = max_upload_bytes
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, storage_key: str) -> Path:
        # Keys are server-generated, but resolve and re-check anyway so a
        # corrupted row can never escape the storage root.
        candidate = (self.root / storage_key).resolve()
        if self.root not in candidate.parents:
            raise AudioStorageError("storage key escapes the storage root")
        return candidate

    def save(
        self,
        *,
        family_id: str,
        recording_id: str,
        original_filename: str,
        content_type: str | None,
        source: BinaryIO,
    ) -> StoredAudio:
        extension = safe_extension(original_filename)
        declared = validate_content_type(content_type)
        storage_key = build_storage_key(
            family_id=family_id,
            recording_id=recording_id,
            extension=extension,
        )
        destination = self._path(storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".uploading")

        digest = hashlib.sha256()
        total = 0
        head = b""
        try:
            with temporary.open("wb") as output:
                while chunk := source.read(CHUNK_BYTES):
                    if not head:
                        head = chunk[:16]
                        # Reject a mislabelled payload before writing more of it.
                        validate_container(extension, head)
                    total += len(chunk)
                    if total > self.max_upload_bytes:
                        raise AudioTooLargeError(
                            "audio exceeds maximum upload size of "
                            f"{self.max_upload_bytes // (1024 * 1024)} MB"
                        )
                    # Hashed in the same streaming pass; never re-read.
                    digest.update(chunk)
                    output.write(chunk)
            if not total:
                raise UnsupportedAudioError("audio upload was empty")
            os.replace(temporary, destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

        return StoredAudio(
            storage_key=storage_key,
            backend=self.backend,
            sha256=digest.hexdigest(),
            size_bytes=total,
            content_type=declared,
        )

    def exists(self, storage_key: str) -> bool:
        return self._path(storage_key).is_file()

    def delete(self, storage_key: str) -> bool:
        """Delete idempotently while preserving real failure semantics."""

        try:
            path = self._path(storage_key)
            path.unlink()
        except FileNotFoundError:
            return False
        except PermissionError as exc:
            raise StorageDeleteError(
                code="storage_permission_denied",
                retryable=False,
                message="local storage deletion permission denied",
            ) from exc
        except OSError as exc:
            raise StorageDeleteError(
                code="storage_io_error",
                retryable=True,
                message="local storage deletion failed",
            ) from exc
        # Directory pruning is cosmetic; object deletion already succeeded.
        for parent in (path.parent, path.parent.parent):
            try:
                parent.rmdir()
            except OSError:
                break
        return True

    def open(self, storage_key: str) -> BinaryIO:
        return self._path(storage_key).open("rb")

    @contextmanager
    def materialize(self, storage_key: str) -> Iterator[Path]:
        """Expose a local file for providers that still require one.

        This is the single sanctioned place where a key becomes a Path. The
        Kaggle ASR worker uploads a file, so the boundary lives here rather than
        leaking a Path into the domain. An object-store implementation would
        download to a temporary file and clean it up on exit.
        """

        yield self._path(storage_key)


class LegacyLocalAudioStorage:
    """Deletion-only adapter for rows created before opaque storage keys."""

    backend = "legacy_local"

    def delete(self, storage_key: str) -> bool:
        path = Path(storage_key)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
        except PermissionError as exc:
            raise StorageDeleteError(
                code="storage_permission_denied",
                retryable=False,
                message="legacy audio deletion permission denied",
            ) from exc
        except OSError as exc:
            raise StorageDeleteError(
                code="storage_io_error",
                retryable=True,
                message="legacy audio deletion failed",
            ) from exc


@contextmanager
def materialize_legacy_path(audio_path: str) -> Iterator[Path]:
    """Escape hatch for recordings created before storage keys existed."""

    yield Path(audio_path)


def copy_stream(source: BinaryIO, destination: BinaryIO) -> int:
    shutil.copyfileobj(source, destination, CHUNK_BYTES)
    return destination.tell()


class SupabaseAudioStorage:
    """Supabase Object Storage implementation for production deployment.

    Audio is kept in a private bucket. Uploads and downloads speak to
    Supabase's Storage REST API using the trusted backend service role key.
    """

    backend = AudioStorageBackend.SUPABASE

    def __init__(
        self,
        *,
        url: str,
        service_role_key: str,
        bucket: str = "mura-audio",
        max_upload_bytes: int = 25 * 1024 * 1024,
        timeout_seconds: float = 60.0,
        session: requests.Session | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.service_role_key = service_role_key
        self.bucket = bucket
        self.max_upload_bytes = max_upload_bytes
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.service_role_key}",
            "apikey": self.service_role_key,
        }
        if extra:
            headers.update(extra)
        return headers

    def save(
        self,
        *,
        family_id: str,
        recording_id: str,
        original_filename: str,
        content_type: str | None,
        source: BinaryIO,
    ) -> StoredAudio:
        extension = safe_extension(original_filename)
        declared = validate_content_type(content_type)
        storage_key = build_storage_key(
            family_id=family_id,
            recording_id=recording_id,
            extension=extension,
        )

        buffer = io.BytesIO()
        digest = hashlib.sha256()
        total = 0
        head = b""

        while chunk := source.read(CHUNK_BYTES):
            if not head:
                head = chunk[:16]
                validate_container(extension, head)
            total += len(chunk)
            if total > self.max_upload_bytes:
                raise AudioTooLargeError(
                    "audio exceeds maximum upload size of "
                    f"{self.max_upload_bytes // (1024 * 1024)} MB"
                )
            digest.update(chunk)
            buffer.write(chunk)

        if not total:
            raise UnsupportedAudioError("audio upload was empty")

        buffer.seek(0)
        upload_url = f"{self.url}/storage/v1/object/{self.bucket}/{storage_key}"
        headers = self._headers(
            {
                "Content-Type": declared,
                "x-upsert": "true",
            }
        )

        try:
            try:
                from mura.testing.fault_injection import (
                    FAULT_STORAGE_503,
                    consume_fault,
                    is_fault_injection_enabled,
                )

                if is_fault_injection_enabled() and consume_fault(FAULT_STORAGE_503):
                    raise AudioStorageError(
                        "Supabase Storage rejected upload with HTTP 503: Service Unavailable"
                    )
            except ImportError:
                pass
            response = self.session.post(
                upload_url,
                headers=headers,
                data=buffer.getvalue(),
                timeout=(10.0, self.timeout_seconds),
            )
        except Exception as exc:
            raise AudioStorageError(f"failed to upload audio to Supabase Storage: {exc}") from exc

        if response.status_code >= 400:
            raise AudioStorageError(
                "Supabase Storage rejected upload with HTTP "
                f"{response.status_code}: {response.text}"
            )

        return StoredAudio(
            storage_key=storage_key,
            backend=self.backend,
            sha256=digest.hexdigest(),
            size_bytes=total,
            content_type=declared,
        )

    def exists(self, storage_key: str) -> bool:
        url = f"{self.url}/storage/v1/object/authenticated/{self.bucket}/{storage_key}"
        try:
            response = self.session.get(
                url,
                headers=self._headers(),
                stream=True,
                timeout=(5.0, 10.0),
            )
            response.close()
            return response.status_code == 200
        except Exception:
            return False

    def delete(self, storage_key: str) -> bool:
        url = f"{self.url}/storage/v1/object/{self.bucket}/{storage_key}"
        try:
            response = self.session.delete(
                url,
                headers=self._headers(),
                timeout=(5.0, 15.0),
            )
        except requests.Timeout as exc:
            raise StorageDeleteError(
                code="storage_timeout",
                retryable=True,
                message="storage deletion timed out",
            ) from exc
        except requests.RequestException as exc:
            raise StorageDeleteError(
                code="storage_unavailable",
                retryable=True,
                message="storage deletion transport failed",
            ) from exc

        if response.status_code in {200, 204}:
            return True
        if response.status_code == 404:
            return False
        raise storage_delete_http_error(response.status_code, response.headers)

    def open(self, storage_key: str) -> BinaryIO:
        url = f"{self.url}/storage/v1/object/authenticated/{self.bucket}/{storage_key}"
        try:
            response = self.session.get(
                url,
                headers=self._headers(),
                stream=True,
                timeout=(10.0, self.timeout_seconds),
            )
        except Exception as exc:
            raise AudioStorageError(
                f"failed to retrieve audio from Supabase Storage: {exc}"
            ) from exc

        if response.status_code == 404:
            raise FileNotFoundError(f"recording audio {storage_key} not found in Supabase Storage")
        if response.status_code >= 400:
            raise AudioStorageError(
                f"Supabase Storage returned HTTP {response.status_code} for {storage_key}"
            )

        response.raw.decode_content = True
        return cast(BinaryIO, response.raw)

    @contextmanager
    def materialize(self, storage_key: str) -> Iterator[Path]:
        extension = Path(storage_key).suffix or ".bin"
        temp = tempfile.NamedTemporaryFile(suffix=extension, delete=False)
        temp_path = Path(temp.name)
        try:
            stream = self.open(storage_key)
            with temp:
                shutil.copyfileobj(stream, temp, CHUNK_BYTES)
            yield temp_path
        finally:
            temp_path.unlink(missing_ok=True)


def build_audio_storage(settings: CoreSettings) -> AudioStorage:
    if settings.audio_storage_backend == AudioStorageBackend.LOCAL:
        return LocalAudioStorage(
            settings.audio_storage_dir,
            max_upload_bytes=settings.core_max_upload_mb * 1024 * 1024,
        )
    if settings.audio_storage_backend == AudioStorageBackend.SUPABASE:
        return SupabaseAudioStorage(
            url=settings.supabase_url or "",
            service_role_key=settings.supabase_service_role_key or "",
            bucket=settings.supabase_storage_bucket,
            max_upload_bytes=settings.core_max_upload_mb * 1024 * 1024,
            timeout_seconds=settings.supabase_storage_timeout_seconds,
        )
    raise ValueError(f"unsupported audio storage backend: {settings.audio_storage_backend}")

