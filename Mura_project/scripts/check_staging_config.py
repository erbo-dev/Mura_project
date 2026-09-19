"""Staging Environment Configuration Validator.

Validates the SHAPE and presence of all required staging environment variables
without printing secret values. Exits with non-zero status on validation failure.
"""

from __future__ import annotations

import os
import sys
from urllib.parse import urlparse

REQUIRED_VARS = [
    "APP_ENV",
    "DATABASE_URL",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_STORAGE_BUCKET",
    "SUPABASE_BOOKS_BUCKET",
    "DEEPSEEK_API_KEY",
    "OPERATIONS_API_KEY",
    "SENTRY_DSN",
    "SENTRY_ENVIRONMENT",
    "CORS_ORIGINS",
    "ALLOWED_HOSTS",
]

SECRET_KEYS = {
    "DATABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "DEEPSEEK_API_KEY",
    "OPERATIONS_API_KEY",
}


def check_staging_configuration(env_dict: dict[str, str] | None = None) -> bool:
    env = env_dict if env_dict is not None else os.environ
    passed = True

    print("=== MURA Staging Configuration Cross-Check ===")

    # 1. Check APP_ENV
    app_env = env.get("APP_ENV", "")
    if app_env == "staging":
        print("[OK] APP_ENV=staging")
    else:
        print(f"[FAIL] APP_ENV must be 'staging', got: {app_env or 'UNSET'}")
        passed = False

    # 2. Check DATABASE_URL
    db_url = env.get("DATABASE_URL", "")
    if db_url:
        parsed = urlparse(db_url)
        if parsed.scheme.startswith(("postgres", "postgresql")):
            print(f"[OK] DATABASE_URL configured (scheme: {parsed.scheme}, host: {parsed.hostname or 'configured'})")
        elif parsed.scheme.startswith("sqlite"):
            print(f"[WARN] DATABASE_URL is SQLite (allowed for local simulation: {parsed.scheme})")
        else:
            print(f"[FAIL] DATABASE_URL invalid scheme: {parsed.scheme}")
            passed = False
    else:
        print("[FAIL] DATABASE_URL is UNSET")
        passed = False

    # 3. Check Supabase Configuration
    sb_url = env.get("SUPABASE_URL", "")
    if sb_url and sb_url.startswith("https://"):
        print(f"[OK] SUPABASE_URL configured ({urlparse(sb_url).hostname})")
    else:
        print("[FAIL] SUPABASE_URL missing or not HTTPS")
        passed = False

    sb_key = env.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if sb_key and len(sb_key) >= 32:
        print("[OK] SUPABASE_SERVICE_ROLE_KEY present and length verified")
    else:
        print("[FAIL] SUPABASE_SERVICE_ROLE_KEY missing or too short")
        passed = False

    # 4. Buckets
    audio_bucket = env.get("SUPABASE_STORAGE_BUCKET", "")
    if audio_bucket == "mura-audio-staging":
        print(f"[OK] Audio bucket configured ({audio_bucket})")
    elif audio_bucket:
        print(f"[WARN] Audio bucket configured with non-standard name: {audio_bucket}")
    else:
        print("[FAIL] SUPABASE_STORAGE_BUCKET is UNSET")
        passed = False

    books_bucket = env.get("SUPABASE_BOOKS_BUCKET", "")
    if books_bucket == "mura-books-staging":
        print(f"[OK] Book bucket configured ({books_bucket})")
    elif books_bucket:
        print(f"[WARN] Book bucket configured with non-standard name: {books_bucket}")
    else:
        print("[FAIL] SUPABASE_BOOKS_BUCKET is UNSET")
        passed = False

    # 5. DeepSeek
    ds_key = env.get("DEEPSEEK_API_KEY", "")
    if ds_key and len(ds_key) >= 16:
        print("[OK] DEEPSEEK_API_KEY present and length verified")
    else:
        print("[FAIL] DEEPSEEK_API_KEY missing or too short")
        passed = False

    # 6. Operations API Key
    ops_key = env.get("OPERATIONS_API_KEY", "")
    if ops_key and len(ops_key) >= 16:
        print("[OK] OPERATIONS_API_KEY present and length verified")
    else:
        print("[FAIL] OPERATIONS_API_KEY missing or too short")
        passed = False

    # 7. Sentry
    sentry_dsn = env.get("SENTRY_DSN", "")
    sentry_env = env.get("SENTRY_ENVIRONMENT", "")
    if sentry_dsn and sentry_env == "staging":
        print(f"[OK] Sentry configured (environment: {sentry_env})")
    else:
        print(f"[FAIL] Sentry config incomplete (DSN set: {bool(sentry_dsn)}, env: {sentry_env})")
        passed = False

    # 8. CORS & Allowed Hosts
    cors = env.get("CORS_ORIGINS", "")
    if cors:
        print(f"[OK] CORS_ORIGINS configured ({len(cors.split(','))} origins)")
    else:
        print("[FAIL] CORS_ORIGINS is UNSET")
        passed = False

    hosts = env.get("ALLOWED_HOSTS", "")
    if hosts:
        print(f"[OK] ALLOWED_HOSTS configured ({len(hosts.split(','))} hosts)")
    else:
        print("[FAIL] ALLOWED_HOSTS is UNSET")
        passed = False

    print("===============================================")
    if passed:
        print("RESULT: ALL STAGING CONFIGURATION CHECKS PASSED")
    else:
        print("RESULT: CONFIGURATION VALIDATION FAILED")
    return passed


if __name__ == "__main__":
    success = check_staging_configuration()
    sys.exit(0 if success else 1)

