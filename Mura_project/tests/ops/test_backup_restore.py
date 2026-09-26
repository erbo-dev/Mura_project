"""Tests for PostgreSQL backup/restore safety guards and integrity verification."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mura.ops.backup_restore import (
    BackupRestoreError,
    UnsafeRestoreTargetError,
    assert_safe_restore_target,
    is_safe_restore_target,
    parse_pg_connection_params,
    redact_connection_url,
    run_pg_dump,
    run_pg_restore,
)


def test_redact_connection_url() -> None:
    url = "postgresql+psycopg://user:super_secret_pwd@127.0.0.1:5432/mura_db"
    redacted = redact_connection_url(url)
    assert "super_secret_pwd" not in redacted
    assert "user:***@127.0.0.1:5432/mura_db" in redacted

    # Without password
    url_nopass = "postgresql://user@127.0.0.1:5432/mura_db"
    assert redact_connection_url(url_nopass) == url_nopass


def test_parse_pg_params() -> None:
    url = "postgresql+psycopg://mura_user:secret_pass@db.example.com:5433/mura_verification"
    params = parse_pg_connection_params(url)
    assert params["host"] == "db.example.com"
    assert params["port"] == "5433"
    assert params["user"] == "mura_user"
    assert params["password"] == "secret_pass"
    assert params["dbname"] == "mura_verification"


def test_reject_prod_env() -> None:
    safe, reason = is_safe_restore_target(
        "postgresql://mura:pass@127.0.0.1:5432/mura_verification",
        environment="production",
    )
    assert not safe
    assert "explicitly 'production'" in reason

    with pytest.raises(UnsafeRestoreTargetError, match="explicitly 'production'"):
        assert_safe_restore_target(
            "postgresql://mura:pass@127.0.0.1:5432/mura_verification",
            environment="production",
        )


def test_reject_prod_db() -> None:
    unsafe_urls = [
        "postgresql://mura:pass@127.0.0.1:5432/mura_production",
        "postgresql://mura:pass@127.0.0.1:5432/mura_prod",
        "postgresql://mura:pass@127.0.0.1:5432/mura_live",
        "postgresql://mura:pass@127.0.0.1:5432/production_data",
        "postgresql://mura:pass@127.0.0.1:5432/mura",  # No safe marker
    ]
    for url in unsafe_urls:
        safe, _reason = is_safe_restore_target(url, environment="staging")
        assert not safe, f"Expected {url} to be rejected"
        with pytest.raises(UnsafeRestoreTargetError):
            assert_safe_restore_target(url, environment="staging")


def test_accept_safe_targets() -> None:
    safe_urls = [
        "postgresql://mura:pass@127.0.0.1:5432/mura_restore_test",
        "postgresql://mura:pass@127.0.0.1:5432/mura_verification",
        "postgresql://mura:pass@127.0.0.1:5432/mura_staging",
        "postgresql://mura:pass@127.0.0.1:5432/mura_dev_test",
        "postgresql://mura:pass@127.0.0.1:5432/mura_leases_test",
    ]
    for url in safe_urls:
        safe, reason = is_safe_restore_target(url, environment="staging")
        assert safe, f"Expected {url} to be accepted, reason: {reason}"
        assert_safe_restore_target(url, environment="staging")


def test_pg_dump_redaction(tmp_path: Path) -> None:
    dump_out = tmp_path / "test.dump"
    with patch("subprocess.run") as mock_run:
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "fatal: authentication failed for user 'mura' password 'super_secret'"
        mock_run.return_value = mock_proc

        with pytest.raises(BackupRestoreError) as exc_info:
            run_pg_dump("postgresql://mura:super_secret@localhost:5432/mura_test", dump_out)

        err_str = str(exc_info.value)
        assert "super_secret" not in err_str
        assert "***" in err_str


def test_pg_restore_safety(tmp_path: Path) -> None:
    dump_file = tmp_path / "fake.dump"
    dump_file.write_bytes(b"dummy")

    with pytest.raises(UnsafeRestoreTargetError):
        run_pg_restore(
            "postgresql://mura:secret@localhost:5432/mura_production",
            dump_file,
            environment="staging",
        )
