# Wave 1 production-readiness status

## Baseline
- Initial main SHA: d26b821644433f0bbf73da9578eb971166ee564f (verified before changes).
- Open PRs at start: none via connected GitHub search; local gh is unauthenticated.
- Active root workflows: backend.yml, frontend.yml only.

## Milestone A - Runtime contract
- Status: IN PROGRESS; branch fix/production-runtime-contract from verified main; no PR or deployment.
- Confirmed: staging validator uses APP_ENV and CORS_ORIGINS; production hosts and PostgreSQL TLS not enforced; deployment example omits MURA_AUTH_PROVIDER and WORKER_QUEUES.
- HANDOFF CLAIM DISPROVEN: a legacy WorkerSettings class exists, but standalone worker uses CoreSettings. SQLite, wildcard CORS, and wildcard forwarded IPs already fail closed in production.
- Python 3.11 full pytest with coverage on isolated PostgreSQL: PASS (89.55% coverage). Python 3.13 suite: PASS. Explicit cross-family recording quota PostgreSQL race: PASS. Single Alembic head and fresh upgrade: PASS.
- Changed-file Ruff lint/format, production-source mypy, Bandit, pip-audit, compileall: PASS. API and worker Docker builds: PASS. Frontend Vitest: PASS (502 tests); frontend npm audit: PASS (zero vulnerabilities); frontend production build: PASS.
- GitHub PR CI: NOT EXECUTED until branch push and PR creation.
- External API/worker runtime and cloud verification: BLOCKED without staging credentials; no deployment attempted.

## Milestone B - CI/security
- Status: PENDING; workflow implementation NOT EXECUTED. Exploratory full-repository Ruff lint: FAIL (254 existing findings); full-repository Ruff format: FAIL (56 existing files). Full production backend source alone has 85 lint findings and 21 unformatted files. These broad quality gates were never active in root CI and require a separate reviewable Milestone B remediation; do not report them as green.

## Milestone C - Browser/product correctness
- Status: PENDING; validation NOT EXECUTED.

## Milestone D - Staging operations
- Status: PENDING; validation NOT EXECUTED.

## Blocked external checks
- Railway trusted ingress CIDRs: BLOCKED - deployment evidence unavailable.
- Cloud staging: BLOCKED - credentials unavailable.
- Branch protection: BLOCKED pending API evidence.

## Decisions
- Separate independent branches; no merges, force-pushes, or production deployments.
- Absent credentials never count as a PASS.
- Milestone A preserves changed-file quality gates; the full-repository quality debt is explicitly reserved for Milestone B, whose exit gate cannot pass until fixed without suppressions.

## Remaining roadmap
- Milestones B-D and integration validation.
