"""Tests for book artifact storage (Local and Supabase)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from mura.domain.book_models import ExportFormat
from mura.storage.book_artifacts import (
    BookArtifactStorageBackend,
    BookArtifactStorageError,
    LocalBookArtifactStorage,
    SupabaseBookArtifactStorage,
    build_book_artifact_storage,
)
from mura.storage.storage_errors import StorageDeleteError


def test_local_book_artifact_storage_roundtrip(tmp_path: Path) -> None:
    storage = LocalBookArtifactStorage(tmp_path)
    assert storage.backend == BookArtifactStorageBackend.LOCAL

    family_id = "fam_123"
    book_id = "book_456"
    pdf_bytes = b"%PDF-1.4 Fake PDF Content"

    # Store
    key = storage.store(
        family_id=family_id,
        book_id=book_id,
        export_format=ExportFormat.PDF,
        data=pdf_bytes,
    )
    assert key == f"families/{family_id}/books/{book_id}/book.pdf"
    assert storage.exists(storage_key=key)

    # Retrieve
    retrieved = storage.retrieve(storage_key=key)
    assert retrieved == pdf_bytes

    # Non-existent
    with pytest.raises(FileNotFoundError):
        storage.retrieve(storage_key="families/fam_123/books/book_456/book.epub")


def test_local_storage_traversal_prevention(tmp_path: Path) -> None:
    storage = LocalBookArtifactStorage(tmp_path)

    # Invalid ID segments
    with pytest.raises(ValueError, match="Storage path traversal detected"):
        storage.store(
            family_id="../../etc",
            book_id="book_123",
            export_format=ExportFormat.PDF,
            data=b"test",
        )

    with pytest.raises(ValueError, match="Storage path traversal detected"):
        storage.store(
            family_id="fam_123",
            book_id="../malicious",
            export_format=ExportFormat.PDF,
            data=b"test",
        )

    # Traversal in retrieve
    with pytest.raises(ValueError, match="Storage path traversal"):
        storage.retrieve(storage_key="../secret.txt")


def test_supabase_book_artifact_storage() -> None:
    mock_session = MagicMock()
    storage = SupabaseBookArtifactStorage(
        url="https://supabase.example.com",
        service_role_key="secret-service-key",
        bucket="mura-books",
        session=mock_session,
    )
    assert storage.backend == BookArtifactStorageBackend.SUPABASE

    # Test store
    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 200
    mock_session.post.return_value = mock_post_resp

    key = storage.store(
        family_id="fam_abc",
        book_id="book_xyz",
        export_format=ExportFormat.PDF,
        data=b"PDFDATA",
    )
    assert key == "families/fam_abc/books/book_xyz/book.pdf"
    mock_session.post.assert_called_once()
    called_url = mock_session.post.call_args[0][0]
    assert called_url == "https://supabase.example.com/storage/v1/object/mura-books/families/fam_abc/books/book_xyz/book.pdf"
    called_headers = mock_session.post.call_args[1]["headers"]
    assert called_headers["Authorization"] == "Bearer secret-service-key"
    assert called_headers["apikey"] == "secret-service-key"
    assert called_headers["Content-Type"] == "application/pdf"
    assert called_headers["x-upsert"] == "true"

    # Test retrieve success
    mock_get_resp = MagicMock()
    mock_get_resp.status_code = 200
    mock_get_resp.content = b"RETRIEVED_PDF"
    mock_session.get.return_value = mock_get_resp

    content = storage.retrieve(storage_key=key)
    assert content == b"RETRIEVED_PDF"

    # Test retrieve 404
    mock_get_resp.status_code = 404
    with pytest.raises(FileNotFoundError):
        storage.retrieve(storage_key=key)

    # Test retrieve 500
    mock_get_resp.status_code = 500
    with pytest.raises(BookArtifactStorageError):
        storage.retrieve(storage_key=key)

    # Test exists
    mock_head_resp = MagicMock()
    mock_head_resp.status_code = 200
    mock_session.head.return_value = mock_head_resp
    assert storage.exists(storage_key=key) is True

    mock_head_resp.status_code = 404
    assert storage.exists(storage_key=key) is False


def test_build_book_artifact_storage_factory(tmp_path: Path) -> None:
    class MockLocalSettings:
        audio_storage_backend = "supabase"
        book_storage_backend = "local"
        book_storage_dir = tmp_path

    storage_local = build_book_artifact_storage(MockLocalSettings())
    assert isinstance(storage_local, LocalBookArtifactStorage)

    class MockSupabaseSettings:
        audio_storage_backend = "local"
        book_storage_backend = "supabase"
        supabase_url = "https://example.supabase.co"
        supabase_service_role_key = "test-key"
        supabase_books_bucket = "test-books"
        supabase_storage_timeout_seconds = 30.0

    storage_supabase = build_book_artifact_storage(MockSupabaseSettings())
    assert isinstance(storage_supabase, SupabaseBookArtifactStorage)
    assert storage_supabase.bucket == "test-books"


def test_book_artifact_storage_rejects_unknown_backend(tmp_path: Path) -> None:
    class InvalidSettings:
        book_storage_backend = "typo"
        book_storage_dir = tmp_path

    with pytest.raises(ValueError, match="BOOK_STORAGE_BACKEND"):
        build_book_artifact_storage(InvalidSettings())




def test_supabase_book_delete_404_is_idempotent_success() -> None:
    session = MagicMock()
    response = MagicMock(status_code=404)
    response.headers = {}
    session.delete.return_value = response
    storage = SupabaseBookArtifactStorage(
        url="https://supabase.example.com",
        service_role_key="secret",
        session=session,
    )
    assert storage.delete(storage_key="families/fam/books/book/book.pdf") is False


@pytest.mark.parametrize("status_code", [429, 500, 502, 503, 504])
def test_supabase_book_delete_transient_failure_raises_retryable(status_code: int) -> None:
    session = MagicMock()
    response = MagicMock(status_code=status_code)
    response.headers = {"Retry-After": "9"} if status_code == 429 else {}
    session.delete.return_value = response
    storage = SupabaseBookArtifactStorage(
        url="https://supabase.example.com",
        service_role_key="secret",
        session=session,
    )
    with pytest.raises(StorageDeleteError) as exc_info:
        storage.delete(storage_key="families/fam/books/book/book.pdf")
    assert exc_info.value.retryable is True


def test_supabase_book_delete_403_is_terminal() -> None:
    session = MagicMock()
    response = MagicMock(status_code=403)
    response.headers = {}
    session.delete.return_value = response
    storage = SupabaseBookArtifactStorage(
        url="https://supabase.example.com",
        service_role_key="secret",
        session=session,
    )
    with pytest.raises(StorageDeleteError) as exc_info:
        storage.delete(storage_key="families/fam/books/book/book.pdf")
    assert exc_info.value.retryable is False
    assert exc_info.value.code == "storage_auth_failed"


def test_supabase_book_delete_timeout_is_retryable() -> None:
    session = MagicMock()
    session.delete.side_effect = requests.Timeout("timeout")
    storage = SupabaseBookArtifactStorage(
        url="https://supabase.example.com",
        service_role_key="secret",
        session=session,
    )
    with pytest.raises(StorageDeleteError) as exc_info:
        storage.delete(storage_key="families/fam/books/book/book.pdf")
    assert exc_info.value.retryable is True
