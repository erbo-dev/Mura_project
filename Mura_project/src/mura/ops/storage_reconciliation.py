"""Report-Only Storage Reconciliation Engine.

Reconciles PostgreSQL metadata with physical object storage (Local filesystem & Supabase).
Strictly REPORT-ONLY: Absolutely no deletion of storage objects or database records.

Detects:
1. DB reference -> missing physical object
2. Storage object -> no DB reference (with minimum age filter to protect in-flight uploads)
3. File size mismatch
4. Cryptographic SHA256 hash mismatch
5. Invalid storage key shape or directory traversal attempts
6. Backend provider connection/HTTP errors (produces INCOMPLETE/FAILED report)
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
from sqlalchemy import select

from mura.storage.book import BookExportRow
from mura.storage.database import Database, RecordingRow


class StorageReconciliationError(RuntimeError):
    """Raised when reconciliation encounters a terminal error."""


@dataclass(frozen=True)
class ReconciliationIssue:
    domain: str  # "audio" | "book"
    issue_type: (
        str  # "missing", "orphan", "size_mismatch", "hash_mismatch", "invalid_key", "storage_error"
    )
    storage_key: str
    details: str
    age_seconds: float | None = None
    expected_size: int | None = None
    actual_size: int | None = None
    expected_sha256: str | None = None
    actual_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StorageReconciliationReport:
    status: str  # "PASS" | "FAIL" | "INCOMPLETE"
    checked_db_records: int = 0
    checked_storage_objects: int = 0
    healthy: int = 0
    missing: int = 0
    orphaned: int = 0
    size_mismatch: int = 0
    hash_mismatch: int = 0
    invalid_keys: int = 0
    storage_errors: int = 0
    duration_seconds: float = 0.0
    automatic_deletion: str = "NO"  # Strict invariant
    issues: list[ReconciliationIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["issues"] = [issue.to_dict() for issue in self.issues]
        return data


def validate_key_safety(key: str) -> bool:
    """Verifies that a storage key does not contain directory traversal or absolute paths."""
    if not key or not key.strip():
        return False
    normalized = key.replace("\\", "/").strip("/")
    parts = normalized.split("/")
    if any(p in {"", ".", ".."} for p in parts):
        return False
    return True


@dataclass(frozen=True)
class PhysicalObject:
    storage_key: str
    size_bytes: int
    modified_at: datetime
    sha256: str | None = None


def scan_local_storage(root_dir: Path) -> dict[str, PhysicalObject]:
    """Scans a local storage root directory and returns a dictionary of relative storage keys."""
    objects: dict[str, PhysicalObject] = {}
    if not root_dir.exists():
        return objects

    resolved_root = root_dir.resolve()
    for root, _, files in os.walk(resolved_root):
        for f in files:
            file_path = Path(root) / f
            try:
                rel_path = file_path.relative_to(resolved_root).as_posix()
                stat = file_path.stat()
                mtime = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
                objects[rel_path] = PhysicalObject(
                    storage_key=rel_path,
                    size_bytes=stat.st_size,
                    modified_at=mtime,
                )
            except OSError:  # nosec B112
                continue
    return objects


def query_supabase_storage_objects(
    *,
    supabase_url: str,
    service_role_key: str,
    bucket: str,
    timeout_seconds: float = 30.0,
) -> dict[str, PhysicalObject]:
    """Queries Supabase Storage List API for objects within a bucket."""
    objects: dict[str, PhysicalObject] = {}
    base_url = supabase_url.rstrip("/")
    list_url = f"{base_url}/storage/v1/object/list/{bucket}"
    headers = {
        "Authorization": f"Bearer {service_role_key}",
        "apikey": service_role_key,
        "Content-Type": "application/json",
    }

    def _list_prefix(prefix: str = "") -> None:
        payload = {
            "prefix": prefix,
            "limit": 1000,
            "offset": 0,
            "sortBy": {"column": "name", "order": "asc"},
        }
        resp = requests.post(list_url, json=payload, headers=headers, timeout=timeout_seconds)
        if resp.status_code != 200:
            raise StorageReconciliationError(
                f"Supabase Storage list failed (HTTP {resp.status_code}): {resp.text}"
            )
        data = resp.json()
        for item in data:
            item_id = item.get("id")
            name = item.get("name")
            if not name:
                continue
            full_key = f"{prefix}/{name}".lstrip("/") if prefix else name
            # If item has no id, it is a directory prefix
            if item_id is None:
                _list_prefix(full_key)
            else:
                meta = item.get("metadata", {})
                size = meta.get("size", item.get("size", 0))
                updated = item.get("updated_at") or item.get("created_at")
                mtime = (
                    datetime.fromisoformat(updated.replace("Z", "+00:00"))
                    if updated
                    else datetime.now(UTC)
                )
                objects[full_key] = PhysicalObject(
                    storage_key=full_key,
                    size_bytes=size,
                    modified_at=mtime,
                )

    _list_prefix("")
    return objects


class StorageReconciler:
    """Reconciles database records against physical storage without mutating either."""

    def __init__(
        self,
        database: Database,
        *,
        audio_dir: Path | None = None,
        book_dir: Path | None = None,
        supabase_url: str | None = None,
        supabase_service_role_key: str | None = None,
        min_orphan_age_seconds: float = 300.0,  # 5 minutes
    ) -> None:
        self.database = database
        self.audio_dir = audio_dir
        self.book_dir = book_dir
        self.supabase_url = supabase_url
        self.supabase_service_role_key = supabase_service_role_key
        self.min_orphan_age_seconds = min_orphan_age_seconds

    def compute_file_sha256(self, file_path: Path) -> str:
        digest = hashlib.sha256()
        with file_path.open("rb") as f:
            while chunk := f.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def reconcile(self) -> StorageReconciliationReport:
        start_time = time.monotonic()
        report = StorageReconciliationReport(status="PASS")
        now = datetime.now(UTC)

        # -------------------------------------------------------------
        # 1. Reconcile Audio Storage
        # -------------------------------------------------------------
        audio_physical: dict[str, PhysicalObject] = {}
        try:
            if self.audio_dir and self.audio_dir.exists():
                audio_physical = scan_local_storage(self.audio_dir)
            elif self.supabase_url and self.supabase_service_role_key:
                audio_physical = query_supabase_storage_objects(
                    supabase_url=self.supabase_url,
                    service_role_key=self.supabase_service_role_key,
                    bucket="mura-audio",
                )
        except Exception as exc:
            report.status = "INCOMPLETE"
            report.storage_errors += 1
            report.issues.append(
                ReconciliationIssue(
                    domain="audio",
                    issue_type="storage_error",
                    storage_key="<all>",
                    details=f"Failed to scan audio physical storage: {type(exc).__name__}: {exc}",
                )
            )

        # Query DB audio records
        with self.database.session_factory() as session:
            recordings = session.scalars(
                select(RecordingRow).where(RecordingRow.storage_key.is_not(None))
            ).all()

            report.checked_db_records += len(recordings)
            matched_audio_keys: set[str] = set()

            for rec in recordings:
                key = rec.storage_key
                if not key:
                    continue

                if not validate_key_safety(key):
                    report.invalid_keys += 1
                    report.issues.append(
                        ReconciliationIssue(
                            domain="audio",
                            issue_type="invalid_key",
                            storage_key=key,
                            details="Storage key contains unsafe segments or traversal",
                        )
                    )
                    continue

                # Check existence in physical storage
                phys_obj = audio_physical.get(key)
                if not phys_obj:
                    report.missing += 1
                    report.issues.append(
                        ReconciliationIssue(
                            domain="audio",
                            issue_type="missing",
                            storage_key=key,
                            details=f"Recording {rec.recording_id} missing in storage",
                            expected_size=rec.audio_size_bytes,
                            expected_sha256=rec.audio_sha256,
                        )
                    )
                    continue

                matched_audio_keys.add(key)
                has_issue = False

                # Size check
                if rec.audio_size_bytes is not None and rec.audio_size_bytes > 0:
                    if phys_obj.size_bytes != rec.audio_size_bytes:
                        report.size_mismatch += 1
                        has_issue = True
                        report.issues.append(
                            ReconciliationIssue(
                                domain="audio",
                                issue_type="size_mismatch",
                                storage_key=key,
                                details="Audio size mismatch between database and storage",
                                expected_size=rec.audio_size_bytes,
                                actual_size=phys_obj.size_bytes,
                            )
                        )

                # Hash check for local files
                if rec.audio_sha256 and self.audio_dir:
                    local_path = self.audio_dir / key
                    if local_path.exists():
                        actual_sha = self.compute_file_sha256(local_path)
                        if actual_sha != rec.audio_sha256:
                            report.hash_mismatch += 1
                            has_issue = True
                            report.issues.append(
                                ReconciliationIssue(
                                    domain="audio",
                                    issue_type="hash_mismatch",
                                    storage_key=key,
                                    details="Cryptographic SHA256 mismatch",
                                    expected_sha256=rec.audio_sha256,
                                    actual_sha256=actual_sha,
                                )
                            )

                if not has_issue:
                    report.healthy += 1

            # Check orphans for audio
            report.checked_storage_objects += len(audio_physical)
            for phys_key, phys_obj in audio_physical.items():
                if phys_key not in matched_audio_keys:
                    age_seconds = (now - phys_obj.modified_at).total_seconds()
                    # Apply orphan age filter to avoid flagging in-flight uploads
                    if age_seconds >= self.min_orphan_age_seconds:
                        report.orphaned += 1
                        report.issues.append(
                            ReconciliationIssue(
                                domain="audio",
                                issue_type="orphan",
                                storage_key=phys_key,
                                details="Physical audio object has no referencing database record",
                                age_seconds=round(age_seconds, 1),
                                actual_size=phys_obj.size_bytes,
                            )
                        )

        # -------------------------------------------------------------
        # 2. Reconcile Book Artifact Storage
        # -------------------------------------------------------------
        book_physical: dict[str, PhysicalObject] = {}
        try:
            if self.book_dir and self.book_dir.exists():
                book_physical = scan_local_storage(self.book_dir)
            elif self.supabase_url and self.supabase_service_role_key:
                book_physical = query_supabase_storage_objects(
                    supabase_url=self.supabase_url,
                    service_role_key=self.supabase_service_role_key,
                    bucket="mura-books",
                )
        except Exception as exc:
            report.status = "INCOMPLETE"
            report.storage_errors += 1
            report.issues.append(
                ReconciliationIssue(
                    domain="book",
                    issue_type="storage_error",
                    storage_key="<all>",
                    details=f"Failed to scan book physical storage: {type(exc).__name__}: {exc}",
                )
            )

        with self.database.session_factory() as session:
            book_exports = session.scalars(
                select(BookExportRow).where(BookExportRow.storage_key.is_not(None))
            ).all()

            report.checked_db_records += len(book_exports)
            matched_book_keys: set[str] = set()

            for exp in book_exports:
                key = exp.storage_key
                if not key:
                    continue

                if not validate_key_safety(key):
                    report.invalid_keys += 1
                    report.issues.append(
                        ReconciliationIssue(
                            domain="book",
                            issue_type="invalid_key",
                            storage_key=key,
                            details="Storage key contains unsafe segments or traversal",
                        )
                    )
                    continue

                phys_obj = book_physical.get(key)
                if not phys_obj:
                    report.missing += 1
                    report.issues.append(
                        ReconciliationIssue(
                            domain="book",
                            issue_type="missing",
                            storage_key=key,
                            details=f"Book export {exp.export_id} missing in storage",
                            expected_size=exp.size_bytes,
                            expected_sha256=exp.sha256,
                        )
                    )
                    continue

                matched_book_keys.add(key)
                has_issue = False

                if exp.size_bytes is not None and exp.size_bytes > 0:
                    if phys_obj.size_bytes != exp.size_bytes:
                        report.size_mismatch += 1
                        has_issue = True
                        report.issues.append(
                            ReconciliationIssue(
                                domain="book",
                                issue_type="size_mismatch",
                                storage_key=key,
                                details="Book artifact size mismatch between DB and storage",
                                expected_size=exp.size_bytes,
                                actual_size=phys_obj.size_bytes,
                            )
                        )

                if exp.sha256 and self.book_dir:
                    local_path = self.book_dir / key
                    if local_path.exists():
                        actual_sha = self.compute_file_sha256(local_path)
                        if actual_sha != exp.sha256:
                            report.hash_mismatch += 1
                            has_issue = True
                            report.issues.append(
                                ReconciliationIssue(
                                    domain="book",
                                    issue_type="hash_mismatch",
                                    storage_key=key,
                                    details="Book artifact SHA256 mismatch",
                                    expected_sha256=exp.sha256,
                                    actual_sha256=actual_sha,
                                )
                            )

                if not has_issue:
                    report.healthy += 1

            # Check orphans for books
            report.checked_storage_objects += len(book_physical)
            for phys_key, phys_obj in book_physical.items():
                if phys_key not in matched_book_keys:
                    age_seconds = (now - phys_obj.modified_at).total_seconds()
                    if age_seconds >= self.min_orphan_age_seconds:
                        report.orphaned += 1
                        report.issues.append(
                            ReconciliationIssue(
                                domain="book",
                                issue_type="orphan",
                                storage_key=phys_key,
                                details="Physical book artifact has no referencing database record",
                                age_seconds=round(age_seconds, 1),
                                actual_size=phys_obj.size_bytes,
                            )
                        )

        report.duration_seconds = round(time.monotonic() - start_time, 3)
        if report.status != "INCOMPLETE":
            if (
                report.missing > 0
                or report.orphaned > 0
                or report.size_mismatch > 0
                or report.hash_mismatch > 0
                or report.invalid_keys > 0
            ):
                report.status = "FAIL"
            else:
                report.status = "PASS"

        return report
