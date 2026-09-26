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
- Status: PASS for repository-controlled gates; independent open PR #59 on `ci/activate-production-gates`, based on main `8f16e1a22e044ade948929d033f958c5779115bc`, unmerged. External dependency-graph and branch-protection checks remain BLOCKED, not PASS. Rebase before first push preserved A's staging validator and test bootstrap.
- Six security/quality/evaluation workflows exist only in nested `Mura_project/.github/workflows` and were inactive. The nested quality workflow assumes package-root relative paths. Root workflows use mutable action tags and do not satisfy the existing pin registry.
- Existing full-repository Ruff lint: FAIL (257 baseline findings); formatting FAIL (at least 56 baseline files). These were remediated with targeted source fixes and a mechanical formatting-only change; historical migrations are excluded from reformatting.
- Root Quality, Security, CodeQL, scheduled offline evaluation, and manually gated live workflows added. Live workflows require main, a protected `staging-ml-evaluation` environment, a GPU runner, environment secrets, and approved local manifest directory. Provider calls never run on PRs. External protection/runner configuration remains BLOCKED until proven.
- Local validation after scanner/Bandit repair: full Python 3.11 suite PASS on isolated UTF-8 PostgreSQL (17,917 statements, 1,872 missed, 90% rounded coverage); Python 3.13 compatibility PASS; changed-file Bandit PASS; scoped Ruff, mypy, full source Bandit, workflow policy and focused regression tests PASS. Seven-test PostgreSQL atomicity gate, fresh Alembic upgrade and single head `20260923_0018`, pip-audit, compileall and four deterministic provider-free gates PASS on the prior rebased head. Shared test database was inconsistent and not destructively repaired; a first isolated database inherited WIN1251 and was replaced with explicit UTF-8.
- Rebased frontend: `npm ci`, production `npm audit`, ESLint, TypeScript, 502 Vitest tests, and production build PASS. Rebased API and Worker Docker images PASS. PR #59 initial CI failure was fixed without disabling Bandit or secret scanning. On code HEAD `66fa0a7c0c643f4a734ffb0bca7a95bc4b09b6b1`, GitHub Backend CI run 36222750428 PASS (Python 3.11/3.13, PostgreSQL gate, migration, Docker), Frontend CI run 36222750405 PASS, Quality run 36222750409 PASS (including workflow policy and deterministic ML release gates), Security run 36222750421 PASS (verified secret scan and dependency audits), CodeQL run 36222750417 PASS (Python and JavaScript/TypeScript). Dependency-change review was SKIPPED with an explicit BLOCKED warning because the repository dependency graph is disabled; do not count it as PASS.

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
