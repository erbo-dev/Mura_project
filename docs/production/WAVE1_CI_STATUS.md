# Wave 1 Milestone B CI/security status

## Baseline
- Initial main SHA: d26b821644433f0bbf73da9578eb971166ee564f. Current observed main SHA: 8f16e1a22e044ade948929d033f958c5779115bc (PR #58 merged by the user while B was local).
- Open PRs at start: none. Root Actions at baseline: backend.yml and frontend.yml.

## Milestone A - Runtime contract
- Status: PASS and merged by the user as PR #58 (main merge 8f16e1a22e044ade948929d033f958c5779115bc). Code validation HEAD: 78fc6971ed68f0afb92bd8528321471998efea3a; status-only HEAD: 7c6099d3e41700f2f42c6593e2a3a238d1427b3e.
- Python 3.11 PostgreSQL full suite: PASS, 89.55% coverage. Python 3.13, PG concurrency, single Alembic head/upgrade, scoped Ruff/mypy/Bandit/pip-audit/compileall, API and Worker Docker: PASS.
- Frontend Vitest (502), lint, tsc, npm production audit, build: PASS. GitHub Backend CI run 36148955621 and Frontend CI run 36148955464: PASS on final status-only HEAD.
- Handoff claim disproven: legacy `WorkerSettings` existed but standalone worker instantiated `CoreSettings`; production SQLite, wildcard CORS, wildcard forwarded IPs already rejected.

## Milestone B - CI/security
- Status: IN PROGRESS; independent branch `ci/activate-production-gates` rebased locally onto current main before its first push (no shared history rewritten). Rebase preserved A's staging validator and test bootstrap.
- Six security/quality/evaluation workflows exist only in nested `Mura_project/.github/workflows` and were inactive. The nested quality workflow assumes package-root relative paths. Root workflows use mutable action tags and do not satisfy the existing pin registry.
- Existing full-repository Ruff lint: FAIL (257 baseline findings); formatting FAIL (at least 56 baseline files). These were remediated with targeted source fixes and a mechanical formatting-only change; historical migrations are excluded from reformatting.
- Root Quality, Security, CodeQL, scheduled offline evaluation, and manually gated live workflows added. Live workflows require main, a protected `staging-ml-evaluation` environment, a GPU runner, environment secrets, and approved local manifest directory. Provider calls never run on PRs. External protection/runner configuration remains BLOCKED until proven.
- Rebased-head local validation: full Python 3.11 suite PASS on isolated UTF-8 PostgreSQL (90% rounded coverage); Python 3.13 compatibility PASS; seven-test PostgreSQL atomicity gate PASS before rebase and included in the rebased full run; single Alembic head `20260923_0018` and fresh upgrade PASS. Ruff check/format, mypy, Bandit, pip-audit, compileall, workflow policy, and four deterministic provider-free gates PASS on the rebased head. The preexisting shared test database was inconsistent, so no destructive repair was attempted; a first isolated database inherited WIN1251 and was replaced with explicit UTF-8 before the passing full run.
- Rebased frontend: `npm ci`, production `npm audit`, ESLint, TypeScript, 502 Vitest tests, and production build PASS. Rebased API and Worker Docker images PASS. Initial PR #59 CI: Quality, CodeQL, Frontend PASS; Security FAIL (TruffleHog image tag included an unsupported `v` prefix; dependency-review action reports the repository dependency graph disabled); Backend FAIL (changed-file Bandit catches six existing low-severity findings). These are being repaired without disabling the security scans.

## Milestone C - Browser/product correctness
- Status: PENDING; validation NOT EXECUTED.

## Milestone D - Staging operations
- Status: PENDING; validation NOT EXECUTED.

## Blocked external checks
- Railway trusted ingress CIDRs: BLOCKED (no deployment evidence).
- Cloud staging execution: BLOCKED (credentials unavailable).
- Branch protection: BLOCKED (rulesets API returned empty; classic branch protection API returned 403). Administrator must verify.
- Dependency change review: BLOCKED (GitHub says dependency graph is disabled). Repository owner must enable the dependency graph, then set repository variable `MURA_DEPENDENCY_GRAPH_ENABLED=true`; until then the dependency-review job is explicitly skipped. Independent `pip-audit` and production `npm audit` still run and must pass.

## Decisions
- Independent PR branches, no agent-performed merges, no force pushes, no production deployment. The user independently merged PR #58 while B was local, so B was rebased onto current main before push.
- Absent credentials count as BLOCKED, never PASS.

## Remaining roadmap
- Complete B quality/security/CodeQL/ML gates, then C browser product correctness, then D staging operations.
