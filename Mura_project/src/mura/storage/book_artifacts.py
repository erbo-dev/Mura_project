"""Private book export artifact storage.

Stores generated PDF and EPUB artifacts securely behind an opaque storage_key.
Supports local filesystem storage (BOOK_STORAGE_DIR) and Supabase storage.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
import re
from typing import Any, Protocol

import requests

from mura.domain.book_models import ExportFormat
from mura.storage.storage_errors import StorageDeleteError, storage_delete_http_error

_SAFE_SEGMENT = re.compile(r"\A[A-Za-z0-9_-]{1,128}\Z")


class BookArtifactStorageBackend(StrEnum):
    LOCAL = "local"
    SUPABASE = "supabase"


class BookArtifactStorageError(RuntimeError):
    """Raised when a book artifact storage operation fails."""


def _validate_ids(family_id: str, book_id: str) -> None:
    if not _SAFE_SEGMENT.match(family_id):
        raise ValueError(f"Storage path traversal detected: invalid family_id segment {family_id!r}")
    if not _SAFE_SEGMENT.match(book_id):
        raise ValueError(f"Storage path traversal detected: invalid book_id segment {book_id!r}")


def _validate_storage_key(storage_key: str) -> None:
    safe = storage_key.replace("\\", "/").strip("/")
    parts = safe.split("/")
    if any(p in {"", ".", ".."} for p in parts):
        raise ValueError(f"Storage path traversal detected for key: {storage_key}")


class BookArtifactStorage(Protocol):
    @property
    def backend(self) -> BookArtifactStorageBackend: ...

    def store(
        self,
        *,
        family_id: str,
        book_id: str,
        export_format: ExportFormat,
        data: bytes,
    ) -> str:
        """Store bytes and return an opaque storage_key."""
        ...

    def retrieve(self, *, storage_key: str) -> bytes:
        """Retrieve bytes by storage_key."""
        ...

    def exists(self, *, storage_key: str) -> bool:
        """Check if an artifact exists."""
        ...

    def delete(self, *, storage_key: str) -> bool:
        """Delete an artifact by storage_key."""
        ...


class LocalBookArtifactStorage:
    """Local filesystem storage for family book exports."""

    backend = BookArtifactStorageBackend.LOCAL

    def __init__(self, base_dir: Path | str) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _key_to_path(self, storage_key: str) -> Path:
        # Normalize relative path and prevent traversal
        _validate_storage_key(storage_key)
        safe_rel = storage_key.replace("\\", "/").lstrip("/")
        target = (self.base_dir / safe_rel).resolve()
        if not target.is_relative_to(self.base_dir):
            raise ValueError(f"Storage path traversal detected for key: {storage_key}")
        return target

    def store(
        self,
        *,
        family_id: str,
        book_id: str,
        export_format: ExportFormat,
        data: bytes,
    ) -> str:
        _validate_ids(family_id, book_id)
        filename = f"book.{export_format.value}"
        storage_key = f"families/{family_id}/books/{book_id}/{filename}"
        target_path = self._key_to_path(storage_key)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(data)
        return storage_key

    def retrieve(self, *, storage_key: str) -> bytes:
        target_path = self._key_to_path(storage_key)
        if not target_path.exists():
            raise FileNotFoundError(f"Artifact not found for key: {storage_key}")
        return target_path.read_bytes()

    def exists(self, *, storage_key: str) -> bool:
        try:
            return self._key_to_path(storage_key).exists()
        except (ValueError, FileNotFoundError):
            return False

    def delete(self, *, storage_key: str) -> bool:
        try:
            target_path = self._key_to_path(storage_key)
            target_path.unlink()
        except FileNotFoundError:
            return False
        except PermissionError as exc:
            raise StorageDeleteError(
                code="storage_permission_denied",
                retryable=False,
                message="local storage deletion permission denied",
            ) from exc
        except ValueError as exc:
            raise StorageDeleteError(
                code="storage_invalid_key",
                retryable=False,
                message="local storage key is invalid",
            ) from exc
        except OSError as exc:
            raise StorageDeleteError(
                code="storage_io_error",
                retryable=True,
                message="local storage deletion failed",
            ) from exc
        for parent in (target_path.parent, target_path.parent.parent):
            try:
                if parent != self.base_dir and parent.is_relative_to(self.base_dir):
                    parent.rmdir()
            except OSError:
                break
        return True


class SupabaseBookArtifactStorage:
    """Supabase Object Storage implementation for family book exports.

    Artifacts are stored in a private bucket (e.g. 'mura-books').
    Communicates via Supabase Storage REST API using backend service role key.
    """

    backend = BookArtifactStorageBackend.SUPABASE

    def __init__(
        self,
        *,
        url: str,
        service_role_key: str,
        bucket: str = "mura-books",
        timeout_seconds: float = 60.0,
        session: requests.Session | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.service_role_key = service_role_key
        self.bucket = bucket
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

    def store(
        self,
        *,
        family_id: str,
        book_id: str,
        export_format: ExportFormat,
        data: bytes,
    ) -> str:
        _validate_ids(family_id, book_id)
        filename = f"book.{export_format.value}"
        storage_key = f"families/{family_id}/books/{book_id}/{filename}"

        if export_format == ExportFormat.PDF:
            content_type = "application/pdf"
        elif export_format == ExportFormat.EPUB:
            content_type = "application/epub+zip"
        else:
            content_type = "application/octet-stream"

        upload_url = f"{self.url}/storage/v1/object/{self.bucket}/{storage_key}"
        headers = self._headers({
            "Content-Type": content_type,
            "x-upsert": "true",
        })

        try:
            response = self.session.post(
                upload_url,
                headers=headers,
                data=data,
                timeout=(10.0, self.timeout_seconds),
            )
        except Exception as exc:
            raise BookArtifactStorageError(f"Failed to upload book artifact to Supabase: {exc}") from exc

        if response.status_code >= 400:
            raise BookArtifactStorageError(
                f"Supabase Storage rejected upload with HTTP {response.status_code}: {response.text}"
            )

        return storage_key

    def retrieve(self, *, storage_key: str) -> bytes:
        _validate_storage_key(storage_key)
        url = f"{self.url}/storage/v1/object/authenticated/{self.bucket}/{storage_key}"
        try:
            response = self.session.get(
                url,
                headers=self._headers(),
                timeout=(10.0, self.timeout_seconds),
            )
        except Exception as exc:
            raise BookArtifactStorageError(f"Failed to retrieve book artifact from Supabase: {exc}") from exc

        if response.status_code == 404:
            raise FileNotFoundError(f"Artifact not found for key: {storage_key}")
        if response.status_code >= 400:
            raise BookArtifactStorageError(
                f"Supabase Storage error HTTP {response.status_code} for {storage_key}"
            )

        return response.content

    def exists(self, *, storage_key: str) -> bool:
        try:
            _validate_storage_key(storage_key)
            url = f"{self.url}/storage/v1/object/authenticated/{self.bucket}/{storage_key}"
            response = self.session.head(
                url,
                headers=self._headers(),
                timeout=(5.0, 10.0),
            )
            return response.status_code == 200
        except Exception:
            return False

    def delete(self, *, storage_key: str) -> bool:
        try:
            _validate_storage_key(storage_key)
        except ValueError as exc:
            raise StorageDeleteError(
                code="storage_invalid_key",
                retryable=False,
                message="book artifact storage key is invalid",
            ) from exc

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


def build_book_artifact_storage(settings: Any) -> BookArtifactStorage:
    """Construct book artifact storage from BOOK_STORAGE_BACKEND."""
    backend_val = getattr(
        settings,
        "book_storage_backend",
        BookArtifactStorageBackend.LOCAL,
    )
    backend = getattr(backend_val, "value", backend_val)
    if backend == BookArtifactStorageBackend.SUPABASE.value:
        url = getattr(settings, "supabase_url", None)
        key = getattr(settings, "supabase_service_role_key", None)
        if not url or not key:
            raise ValueError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY required when storage backend is supabase"
            )
        bucket = getattr(settings, "supabase_books_bucket", "mura-books")
        timeout = getattr(settings, "supabase_storage_timeout_seconds", 60.0)
        return SupabaseBookArtifactStorage(
            url=url,
            service_role_key=key,
            bucket=bucket,
            timeout_seconds=timeout,
        )

    if backend == BookArtifactStorageBackend.LOCAL.value:
        base_dir = getattr(settings, "book_storage_dir", Path(".mura/books"))
        return LocalBookArtifactStorage(base_dir)

    raise ValueError(f"Unsupported BOOK_STORAGE_BACKEND: {backend!r}")
