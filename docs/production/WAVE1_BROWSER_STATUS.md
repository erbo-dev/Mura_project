# Wave 1 Milestone C browser/product status

## Baseline
- Main at branch creation: `8f16e1a22e044ade948929d033f958c5779115bc` (Milestone A was merged by the user). Milestone B is independently open as PR #59; its final-head Backend, Frontend, Quality, Security and CodeQL workflows PASS. GitHub dependency review remains BLOCKED because dependency graph is disabled.
- Branch: `fix/frontend-production-correctness`, independent of B; PR NOT EXECUTED. No production deployment or merge.

## Milestone C
- Status: IN PROGRESS; full milestone exit validation NOT EXECUTED.
- Confirmed: Review UI lists questions/conflicts but has no decision controls, despite Core's family-scoped conflict endpoints. The proxy does not allow `DELETE /v1/me` though Core implements it. Core audio has no byte ranges; Next proxy drops binary response headers and applies a 30-second total request deadline to all routes.
- Planned work: review decisions with claim/source context; account deletion with accurate provider-state wording; authenticated range audio and safe binary proxy; Playwright real-browser journeys. Do not begin Milestone D while C's exit gate is red.
- Transport slice: narrow `DELETE /v1/me` allowlist, binary header allowlist, audio-only Range forwarding, route-specific upload timeout and header-only deadline for binary streams. Core local and Supabase bounded range reads, canonical 416 with `Content-Range`, family-first authorization and chunked stream closure. Targeted frontend proxy tests and backend storage/authorization tests PASS; Ruff, mypy, Bandit PASS. Full Milestone C validation remains NOT EXECUTED.

## External blockers
- Real Clerk/Supabase staging and protected browser journeys: BLOCKED without staging credentials.
- Dependency-review and branch-protection evidence: BLOCKED as recorded in B.

## Decisions
- Core remains the authorization boundary. Binary response headers are selected by explicit allowlist; private storage keys never go to browser. No provider deletion claim without provider confirmation.

## Remaining roadmap
- Complete and validate C, open independent PR without merging. Then D staging tooling and optional local integration validation.
