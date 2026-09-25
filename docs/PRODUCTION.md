# MURA production entrypoint

This is the current deployment contract. Follow [the Railway deployment runbook](../RAILWAY_DEPLOYMENT.md) for service provisioning and [private storage setup](../STORAGE_DEPLOYMENT.md) for the `mura-audio` and `mura-books` buckets. [Monitoring](../MONITORING.md) and [backup/restore](../BACKUP_RESTORE.md) describe operational work; they do not prove that a live drill or alert has run. [The old launch checklist](../PRODUCTION_LAUNCH_CHECKLIST.md) is historical and must not be used to configure deployment.

## Trust boundaries

- Clerk manages browser identity. In Vercel production/preview set `MURA_AUTH_PROVIDER=clerk`, `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, server-only `CLERK_SECRET_KEY` and `CLERK_JWT_TEMPLATE`. The template token's `aud` must equal Core's `AUTH_AUDIENCE` exactly.
- Next.js proxies browser traffic through the explicit `/api/mura/*` allowlist. API uses `AUTH_MODE=oidc`, deployment-configured `AUTH_ISSUER` and `AUTH_JWKS_URL`, and asymmetric `AUTH_ALLOWED_ALGORITHMS` matching the provider. The token cannot select a key server.
- API requires `CORE_API_KEY`, a distinct `OPERATIONS_API_KEY`, `WORKER_REGISTRATION_TOKEN`, explicit `CORS_ALLOWED_ORIGINS`, `ALLOWED_HOSTS`, and verified `FORWARDED_ALLOW_IPS`. Do not guess Railway ingress addresses: trusted proxy CIDRs remain **BLOCKED** until verified at deployment.
- API capabilities and recording workers must share `ASR_PROVIDER=whisper`, `WHISPER_API_KEY`, `WHISPER_BASE_URL`, `WHISPER_MODEL`; book and recording workers need DeepSeek. Workers do not need API-only HTTP/auth secrets. Isolate production workers using `WORKER_QUEUES=recording`, `book`, `cleanup`; local compose may combine all three.
- API and workers share the same private Supabase storage backend and bucket names. `DATABASE_URL` must be PostgreSQL with explicit `sslmode=require`, `verify-ca` or `verify-full` in staging/production. Prefer `verify-full` plus a trusted root cert where supported. Never output the URL, provider keys, or storage keys in logs.

## Verification

Before starting a staging service, run `python Mura_project/scripts/check_staging_config.py --role api` or `--role worker` with that service's actual environment; this invokes the same settings model as process startup. It is deliberately staging-only. After deploying to staging, prove auth, readiness, queue isolation, private storage, rollback, and restore with actual credentials. Unavailable cloud checks remain **BLOCKED**, never PASS. No production deployment is authorized by this document.
