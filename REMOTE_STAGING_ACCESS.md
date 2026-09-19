# MURA Remote Staging Access Matrix

Generated in accordance with Phase 2.4 Remote Staging Validation protocol.

| Provider | CLI | Auth | Existing staging resource | Can mutate |
|---|---|---|---|---|
| GitHub | AVAILABLE NOT AUTHENTICATED | VERIFIED AUTHENTICATED | VERIFIED AUTHENTICATED | VERIFIED AUTHENTICATED |
| Railway | VERIFIED AUTHENTICATED | VERIFIED AUTHENTICATED | NOT AVAILABLE | BLOCKED USER ACTION |
| Vercel | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | BLOCKED USER ACTION |
| Supabase | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | BLOCKED USER ACTION |
| Sentry | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | BLOCKED USER ACTION |
| DeepSeek | NOT AVAILABLE | NOT AVAILABLE | NOT AVAILABLE | BLOCKED USER ACTION |

---

## Detailed Status & Evidence

### 1. GitHub
- **CLI**: `C:\Program Files\GitHub CLI\gh.exe` is installed, but `gh auth status` reports: `You are not logged into any GitHub hosts.` -> `AVAILABLE NOT AUTHENTICATED`.
- **Git Push Auth**: `git remote -v` is configured for `https://github.com/erbo-dev/Mura_project.git`. `git push --dry-run origin main` succeeded with exit code 0 via Windows Git Credential Manager -> `VERIFIED AUTHENTICATED`.
- **Repository Resource**: `erbo-dev/Mura_project` exists on GitHub -> `VERIFIED AUTHENTICATED`.
- **Can Mutate**: Git commits can be pushed directly to origin main/branches -> `VERIFIED AUTHENTICATED`.

### 2. Railway
- **CLI**: `C:\Users\erbos\AppData\Roaming\npm\railway.ps1` (v5.26.2) -> `VERIFIED AUTHENTICATED`.
- **Account Auth**: `railway whoami` confirmed authenticated as `pororosororo184@gmail.com` -> `VERIFIED AUTHENTICATED`.
- **Staging Resource**: `railway list` displays only account `cheatplayer-code-2` with project `closeros-staging-2`. No MURA staging project (`mura-api-staging`, `mura-worker-staging`) exists on Railway -> `NOT AVAILABLE`.
- **Can Mutate**: Per Rule 4 ("Never run destructive operations against an environment unless it is positively identified as staging. Do not reuse production resources for chaos testing"), mutating unrelated project `closeros-staging-2` is strictly forbidden -> `BLOCKED USER ACTION`.

### 3. Vercel
- **CLI**: Not installed globally (`NOT AVAILABLE`).
- **Auth**: No `.vercel` project link or Vercel token in environment (`NOT AVAILABLE`).
- **Staging Resource**: No Vercel staging deployment or project linked for MURA (`NOT AVAILABLE`).
- **Can Mutate**: Requires user action to configure Vercel credentials and project link (`BLOCKED USER ACTION`).

### 4. Supabase
- **CLI**: Supabase CLI not installed (`NOT AVAILABLE`).
- **Auth / Secrets**: Neither `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, nor `SUPABASE_JWT_SECRET` are provided in the environment. Only local PostgreSQL at `127.0.0.1:5432/mura_leases_test` exists (`NOT AVAILABLE`).
- **Staging Resource**: No remote isolated Supabase staging database or private buckets (`mura-audio-staging`, `mura-books-staging`) (`NOT AVAILABLE`).
- **Can Mutate**: Requires user action to provision/supply staging Supabase project credentials (`BLOCKED USER ACTION`).

### 5. Sentry
- **CLI**: Not installed (`NOT AVAILABLE`).
- **Auth / DSN**: `SENTRY_DSN` is empty/unconfigured for staging environment (`NOT AVAILABLE`).
- **Staging Resource**: No Sentry staging project configured (`NOT AVAILABLE`).
- **Can Mutate**: Requires user to provide staging Sentry DSN (`BLOCKED USER ACTION`).

### 6. DeepSeek
- **CLI**: Not applicable (`NOT AVAILABLE`).
- **Auth**: `DEEPSEEK_API_KEY` is not present in system environment variables (`NOT AVAILABLE`).
- **Staging Resource**: No remote DeepSeek staging credential active (`NOT AVAILABLE`).
- **Can Mutate**: Requires user to supply `DEEPSEEK_API_KEY` (`BLOCKED USER ACTION`).

---

## Safety Invariant Enforcement
Per Section 4 of Phase 2.4:
> **STOP IF STAGING CANNOT BE PROVEN**
> Never run destructive operations against an environment unless it is positively identified as staging.
> Required isolated topology:
> - Vercel staging
> - Railway API staging
> - Railway Worker staging
> - Supabase staging PostgreSQL
> - Supabase staging Auth
> - private mura-audio-staging
> - private mura-books-staging
> - Sentry environment=staging
>
> Do not reuse production resources for chaos testing.

