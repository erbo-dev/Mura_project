"""Tests for report-only storage reconciliation."""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from mura.ops.storage_reconciliation import (
    StorageReconciler,
    validate_key_safety,
)
from mura.storage.book import BookExportRow, BookRow
from mura.storage.database import Database, RecordingRow
from mura.storage.identity import FamilyRow


@pytest.fixture
def test_db(tmp_path: Path) -> Database:
    db_file = tmp_path / "recon_test.db"
    db = Database(f"sqlite+pysqlite:///{db_file}")
    db.create_schema()
    return db


def test_validate_key_safety() -> None:
    assert validate_key_safety("families/fam_1/audio.wav")
    assert validate_key_safety("fam_1/rec_1/audio.wav")
    assert not validate_key_safety("../traversal.wav")
    assert not validate_key_safety("fam_1/../../etc/passwd")
    assert not validate_key_safety("")
    assert not validate_key_safety("  ")


def test_reconciliation_healthy_and_missing(test_db: Database, tmp_path: Path) -> None:
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    # 1. Healthy object
    healthy_key = "fam_1/rec_healthy/test.wav"
    healthy_path = audio_dir / healthy_key
    healthy_path.parent.mkdir(parents=True, exist_ok=True)
    content = b"RIFF-WAVE-HEALTHY"
    healthy_path.write_bytes(content)
    healthy_sha = hashlib.sha256(content).hexdigest()

    # 2. Missing object
    missing_key = "fam_1/rec_missing/missing.wav"

    with test_db.session_factory.begin() as session:
        session.add(
            RecordingRow(
                recording_id="rec_healthy",
                family_id="fam_1",
                speaker_id="spk_1",
                speaker_name="Speaker",
                original_filename="test.wav",
                audio_path=str(healthy_path),
                storage_key=healthy_key,
                audio_size_bytes=len(content),
                audio_sha256=healthy_sha,
            )
        )
        session.add(
            RecordingRow(
                recording_id="rec_missing",
                family_id="fam_1",
                speaker_id="spk_1",
                speaker_name="Speaker",
                original_filename="missing.wav",
                audio_path=str(audio_dir / missing_key),
                storage_key=missing_key,
                audio_size_bytes=100,
                audio_sha256="abc",
            )
        )

    reconciler = StorageReconciler(test_db, audio_dir=audio_dir, min_orphan_age_seconds=10.0)
    report = reconciler.reconcile()

    assert report.status == "FAIL"
    assert report.healthy == 1
    assert report.missing == 1
    assert report.orphaned == 0
    assert report.automatic_deletion == "NO"

    missing_issue = next(i for i in report.issues if i.issue_type == "missing")
    assert missing_issue.storage_key == missing_key


def test_reconciliation_orphan_age_filter(test_db: Database, tmp_path: Path) -> None:
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    # Old orphan object (modified 600s ago)
    old_orphan = audio_dir / "fam_1/rec_old/orphan.wav"
    old_orphan.parent.mkdir(parents=True, exist_ok=True)
    old_orphan.write_bytes(b"old-data")
    old_time = time.time() - 600
    os.utime(old_orphan, (old_time, old_time))

    # Fresh in-flight object (modified 2s ago)
    fresh_orphan = audio_dir / "fam_1/rec_fresh/inflight.wav"
    fresh_orphan.parent.mkdir(parents=True, exist_ok=True)
    fresh_orphan.write_bytes(b"fresh-data")

    reconciler = StorageReconciler(test_db, audio_dir=audio_dir, min_orphan_age_seconds=300.0)
    report = reconciler.reconcile()

    assert report.orphaned == 1
    orphan_issue = next(i for i in report.issues if i.issue_type == "orphan")
    assert orphan_issue.storage_key == "fam_1/rec_old/orphan.wav"
    assert "inflight.wav" not in [i.storage_key for i in report.issues]


def test_reconciliation_size_and_hash_mismatch(test_db: Database, tmp_path: Path) -> None:
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    # Size mismatch object
    size_key = "fam_1/rec_size/size.wav"
    size_path = audio_dir / size_key
    size_path.parent.mkdir(parents=True, exist_ok=True)
    size_path.write_bytes(b"actual-12-bytes")

    # Hash mismatch object
    hash_key = "fam_1/rec_hash/hash.wav"
    hash_path = audio_dir / hash_key
    hash_path.parent.mkdir(parents=True, exist_ok=True)
    hash_data = b"correct-length-bytes"
    hash_path.write_bytes(hash_data)

    with test_db.session_factory.begin() as session:
        session.add(
            RecordingRow(
                recording_id="rec_size",
                family_id="fam_1",
                speaker_id="spk_1",
                speaker_name="Speaker",
                original_filename="size.wav",
                audio_path=str(size_path),
                storage_key=size_key,
                audio_size_bytes=999,  # Mismatch: expected 999, actual is 15
                audio_sha256=hashlib.sha256(b"actual-12-bytes").hexdigest(),
            )
        )
        session.add(
            RecordingRow(
                recording_id="rec_hash",
                family_id="fam_1",
                speaker_id="spk_1",
                speaker_name="Speaker",
                original_filename="hash.wav",
                audio_path=str(hash_path),
                storage_key=hash_key,
                audio_size_bytes=len(hash_data),
                audio_sha256="wrong_sha256_hash_value",  # Mismatch
            )
        )

    reconciler = StorageReconciler(test_db, audio_dir=audio_dir)
    report = reconciler.reconcile()

    assert report.size_mismatch == 1
    assert report.hash_mismatch == 1
    assert report.healthy == 0


def test_reconciliation_mixed_audio_and_book(test_db: Database, tmp_path: Path) -> None:
    audio_dir = tmp_path / "audio"
    book_dir = tmp_path / "books"
    audio_dir.mkdir(parents=True, exist_ok=True)
    book_dir.mkdir(parents=True, exist_ok=True)

    # Audio
    audio_key = "fam_1/rec_1/audio.wav"
    audio_file = audio_dir / audio_key
    audio_file.parent.mkdir(parents=True, exist_ok=True)
    audio_data = b"audio-content"
    audio_file.write_bytes(audio_data)

    # Book
    book_key = "families/fam_1/books/book_1/book.pdf"
    book_file = book_dir / book_key
    book_file.parent.mkdir(parents=True, exist_ok=True)
    book_data = b"%PDF-1.4 book content"
    book_file.write_bytes(book_data)

    with test_db.session_factory.begin() as session:
        session.add(FamilyRow(family_id="fam_1", name="Test Family"))
        session.add(
            BookRow(
                book_id="book_1",
                family_id="fam_1",
                title="Family Storybook",
                status="completed",
                stage="export",
                output_language="kk",
                target_word_count=5000,
            )
        )
        session.add(
            RecordingRow(
                recording_id="rec_1",
                family_id="fam_1",
                speaker_id="spk_1",
                speaker_name="Speaker",
                original_filename="audio.wav",
                audio_path=str(audio_file),
                storage_key=audio_key,
                audio_size_bytes=len(audio_data),
                audio_sha256=hashlib.sha256(audio_data).hexdigest(),
            )
        )
        session.add(
            BookExportRow(
                export_id="exp_1",
                book_id="book_1",
                format="pdf",
                status="completed",
                storage_key=book_key,
                storage_backend="local",
                size_bytes=len(book_data),
                sha256=hashlib.sha256(book_data).hexdigest(),
            )
        )

    reconciler = StorageReconciler(test_db, audio_dir=audio_dir, book_dir=book_dir)
    report = reconciler.reconcile()

    assert report.status == "PASS"
    assert report.checked_db_records == 2
    assert report.checked_storage_objects == 2
    assert report.healthy == 2
    assert report.missing == 0
    assert report.orphaned == 0


def test_reconciliation_storage_backend_error_produces_incomplete(test_db: Database) -> None:
    with patch("mura.ops.storage_reconciliation.query_supabase_storage_objects") as mock_query:
        mock_query.side_effect = RuntimeError("Supabase storage connection timeout")

        reconciler = StorageReconciler(
            test_db,
            supabase_url="https://fake.supabase.co",
            supabase_service_role_key="fake-key",
        )
        report = reconciler.reconcile()

        assert report.status == "INCOMPLETE"
        assert report.storage_errors >= 1
        assert any("timeout" in i.details for i in report.issues)
