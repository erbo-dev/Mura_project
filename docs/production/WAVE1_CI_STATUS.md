# Wave 1 Milestone B CI/security status

## Baseline
- Initial and last observed main SHA: d26b821644433f0bbf73da9578eb971166ee564f.
- Open PRs at start: none. Root Actions at baseline: backend.yml and frontend.yml.

## Milestone A - Runtime contract
- Status: PASS; independent branch `fix/production-runtime-contract`, PR #58 open and unmerged. Code validation HEAD: 78fc6971ed68f0afb92bd8528321471998efea3a; status-only HEAD: 7c6099d3e41700f2f42c6593e2a3a238d1427b3e.
- Python 3.11 PostgreSQL full suite: PASS, 89.55% coverage. Python 3.13, PG concurrency, single Alembic head/upgrade, scoped Ruff/mypy/Bandit/pip-audit/compileall, API and Worker Docker: PASS.
- Frontend Vitest (502), lint, tsc, npm production audit, build: PASS. GitHub Backend CI run 36148955621 and Frontend CI run 36148955464: PASS on final status-only HEAD.
- Handoff claim disproven: legacy `WorkerSettings` existed but standalone worker instantiated `CoreSettings`; production SQLite, wildcard CORS, wildcard forwarded IPs already rejected.

## Milestone B - CI/security
- Status: IN PROGRESS; independent branch `ci/activate-production-gates` from same verified main.
- Six security/quality/evaluation workflows exist only in nested `Mura_project/.github/workflows` and were inactive. The nested quality workflow assumes package-root relative paths. Root workflows use mutable action tags and do not satisfy the existing pin registry.
- Existing full-repository Ruff lint: FAIL (257 baseline findings); formatting FAIL (at least 56 baseline files). These were remediated with targeted source fixes and a mechanical formatting-only change; historical migrations are excluded from reformatting.
- Root Quality, Security, CodeQL, scheduled offline evaluation, and manually gated live workflows added. Live workflows require main, a protected `staging-ml-evaluation` environment, a GPU runner, environment secrets, and approved local manifest directory. Provider calls never run on PRs. External protection/runner configuration remains BLOCKED until proven.
- Local validation: full Python 3.11 suite PASS on fresh isolated UTF-8 PostgreSQL with 90% rounded coverage; Python 3.13 compatibility PASS; seven-test PostgreSQL atomicity gate PASS; single Alembic head `20260923_0018` and fresh upgrade PASS. Ruff check/format, mypy, Bandit, pip-audit, compileall, workflow policy, and four deterministic provider-free gates PASS. The preexisting shared test database was internally inconsistent, so no destructive repair was attempted. An initial isolated database inherited WIN1251 and failed Kazakh fixtures; it was replaced with an explicitly UTF-8 database before the passing full run.
- Frontend: `npm ci`, production `npm audit`, ESLint, TypeScript, 502 Vitest tests, and production build PASS locally. API and Worker Docker images PASS. GitHub Actions execution remains NOT EXECUTED until the PR is pushed.

## Milestone C - Browser/product correctness
- Status: PENDING; validation NOT EXECUTED.

## Milestone D - Staging operations
- Status: PENDING; validation NOT EXECUTED.

## Blocked external checks
- Railway trusted ingress CIDRs: BLOCKED (no deployment evidence).
- Cloud staging execution: BLOCKED (credentials unavailable).
- Branch protection: BLOCKED (rulesets API returned empty; classic branch protection API returned 403). Administrator must verify.

## Decisions
- Independent PR branches, no merges, no force pushes, no production deployment.
- Absent credentials count as BLOCKED, never PASS.

## Remaining roadmap
- Complete B quality/security/CodeQL/ML gates, then C browser product correctness, then D staging operations.
