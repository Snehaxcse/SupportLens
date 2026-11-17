# SupportLens M5-B release hardening

The preflight fetched `main` and matched local HEAD, `origin/main`, FETCH_HEAD and GitHub at `85d1c51a2ecf7c1f31ea3400ea283073a5ffa2b8`. The M5-A audit, body boundary and regression tests are committed. At entry all 48 protected M1 hashes matched and guarded read-only Neon checks matched 122 development and 433 test rows. M5-A's migration revision and audit history were present. `Decisions.md` and `frontend/AGENTS.md` remain intentionally untracked.

## Release profile

The supported release profile is `SYNTHETIC_DEMO` (`ENVIRONMENT=synthetic_demo`). It is a restricted, independent demonstration using fictional accounts, fictional ticket fixtures and original synthetic knowledge. It does not represent Spotify or its support procedures. Public production remains disabled.

The API accepts two explicitly provisioned Firebase identities from a separate demo project. The database determines their roles and enforces active-account checks. The configured roles must agree with the database, and database account emails are fictional. Public onboarding is closed. Creation and editing endpoints allow only the curated text in `src/supportlens/backend/api/demo.py` before persistence. An unrestricted retrieval preview query is evaluated locally and kept out of persistent records and logs. The browser build requires an exact HTTPS API origin, labels the profile and removes public registration. The Firebase operator must restrict project signup separately. Backend onboarding is closed even if the identity provider has accepted a signup.

Gemini configuration is rejected for this profile. The effective budget is zero. Semantic reuse defaults to disabled. A controlled cache evaluation requires `DEMO_CACHE_EVALUATION=true`, `RESPONSE_CACHE_ENABLED=true` and the exact fictional `DEMO_CACHE_TICKET_ID`. No generation provider is enabled by this opt-in. Existing workflow confidence floors, current revision and approval rules, explicit sends and deduplication remain in force.

## Configuration and startup

| Environment | Database | Identity and operating behavior |
| --- | --- | --- |
| `development` | Approved `SupportLens_dev` and existing isolation guards | Local configuration and explicit existing feature flags. |
| `test` | Approved `SupportLens_test`; opt-in for live cases | Stubbed providers for ordinary tests. Gemini disabled. Test mode refuses development writes. |
| `synthetic_demo` | Dedicated `SupportLens_demo`, role `supportlens_demo_app` | Separate Firebase, explicit HTTPS origin and Host allowlist, two fictional identities, restricted persisted text. |
| `production` | Refused before creating a connection | Future real-customer release remains an owner decision. |

Set the demo values in an isolated process environment. Remove the development and test URL variables from this environment and avoid loading the development `.env`. The backend can be launched from a working directory with no development `.env` after installing the package. Required settings are `ENVIRONMENT`, `DEMO_DATABASE_URL`, `DEMO_FIREBASE_PROJECT_ID`, `DEMO_CUSTOMER_UID`, `DEMO_AGENT_UID`, `GOOGLE_APPLICATION_CREDENTIALS`, `CORS_ALLOWED_ORIGINS` and `DEMO_TRUSTED_HOSTS`. Encode origin and host lists as JSON arrays. The Firebase service-account certificate must match the dedicated project. Only the API origin, profile and Firebase web configuration belong in `NEXT_PUBLIC_*` values. No private values go in the frontend.

Launch one API process using `python -m supportlens.backend.api.serve`. It disables forwarded-header trust and access logs, caps concurrent connections and sets bounded keepalive and shutdown settings. Place HTTPS termination and an external IP limiter at the chosen ingress. Shared ingress peers share the application's peer quota. The app uses bearer tokens and omits browser cookies. TLS certificate and hostname checks, channel binding and PostgreSQL UTC remain required. Synthetic startup verifies the fixed classifier and encoder files when those features are enabled. Use separately provisioned, read-only artifact directories in the release image. The service never downloads model assets at request time.

Provision a fresh demo database with runtime and migration roles. The migration role owns the isolated database/schema. The runtime role receives usage, read and narrowly scoped inserts/updates only. The runtime process rejects the migration credential. The bootstrap command uses the separately configured direct migration URL, confirms the runtime and owner agree on the empty database identity, acquires an advisory lock, installs the existing Alembic chain, verifies schema and seeds the two fictional database accounts in one transaction. Seeded account emails use `example.invalid`. Firebase account provisioning belongs to the separate demo project operator. The bootstrap command does not call Firebase.

Create a ticket with subject **Fictional playback interruption** and description **The fictional audio player pauses partway through a sample track.** A valid fictional public reply is **The fictional playback guide lists the demo steps for reviewing the playback trace.** The approved article fields, alternate ticket, reply and note text live in `src/supportlens/backend/api/demo.py`.

## Readiness and resilience

Use `/health` for process liveness and a separate `/ready` probe for dependency readiness. The latter uses a fresh, read-only PostgreSQL check for identity, verified TLS, UTC, revision, demo runtime privileges and required release relations. It probes Firebase and PostgreSQL concurrently, and checks that configured workers are running. The demo Firebase probe validates an approved identity using the live Admin interface when a project is configured. Local rehearsal uses an explicit stub for Firebase.

The default readiness response budget is 4 seconds. Healthy loopback target: below 1 second. Hung dependency target: a 503 before 5 seconds. One in-flight check per dependency prevents repeated timeouts from spawning more probe threads. The next request checks dependencies again instead of using a stale success. Connection and statement timeouts remain enforced. Native threads cannot be forcibly cancelled safely; a timed-out probe may finish in the background. The public response omits dependency and driver details. The full Alembic/catalog verification is a separate release/bootstrap check and is outside the HTTP probe.

In the Windows/WSL PostgreSQL 16.15 rehearsal, the five healthy HTTP readiness samples were 247.62, 171.45, 109.57, 129.92 and 104.32 ms. Median was **129.92 ms**. Simulated Firebase failure returned 503 in **98.51 ms**. Simulated database failure and a stopped required worker also returned 503. A separate read-only Neon measurement of the PostgreSQL check alone varied from 3.068 s to 16.628 s. The HTTP deadline caps exposure to a slow external database, and that latency variation is a hosting constraint.

Guarded Neon still uses fresh SQLAlchemy NullPool connections. Runtime transactions retain identity and TLS attestation, with expensive inventory queries removed from the hot path. Transaction rollback remains under the existing context manager. Automatic replay of ambiguous mutations is disabled. A transport close in M5-A occurred during schema verification and passed on an unchanged retry. The evidence does not identify a defective persistent pool. Keep durable job leases and fencing, and recover eligible work after restart.

## Abuse controls and frontend behavior

The peer budget defaults to 180 requests per minute and verified-actor budget to 120. Additional per-actor per-minute limits are 8 onboarding attempts, 12 ticket creates, 30 message writes, 12 draft requests, 10 article writes and 20 retrievals. The initial draft budget of six interrupted a verified multi-ticket workflow before it could return its policy decisions. The provider reservations, retry bounds and daily generation quota remain independently enforced. Readiness has a separate budget of 20 requests per peer and 60 total per minute. Process liveness remains available. The service ignores forwarded IP headers with the supported entrypoint. The bounded in-memory limiter is per instance and resets on restart. Multi-instance enforcement requires an approved shared ingress budget. Firebase login is a separate provider surface with its own policy and throttles.

The API rejects oversized bodies at 1.25 MiB and returns 408 on a slow body upload after 10 seconds by default. A 429 returns a bounded Retry-After and safe error envelope. The client displays retry guidance, enforces a 30-second network deadline and distinguishes an unconfirmed mutation from a failed read. Existing pending-action guards prevent duplicate clicks. Users check the conversation after an ambiguous write. Firebase token refresh retries only on an authenticated API rejection. Existing worker, disabled-draft and cache panels continue to show their operational states.

## Observability and confidentiality

Structured HTTP logs include a server-generated correlation ID, route template, status and latency. The AGENT-only operations endpoint exposes bounded counters, worker running status, dependency outcomes, queue states, classification/draft states, and Gemini reservation and quota aggregates. Existing cache metrics carry durable hit, miss and invalidation accounting when enabled. Explicit-draft-send conflict and equivalent replay have fixed counters. Worker exceptions emit only approved event/category names. Evidence text, usernames, credentials, URLs with parameters, raw paths, bearer tokens, prompts and private ticket data stay out of operational logs. In-process counters reset at restart, while queue states and provider accounting come from PostgreSQL.

## Dependency inventory

| Package or path | Audited versions and advisories | Applicability and update |
| --- | --- | --- |
| Mako | 1.3.10 → 1.3.12, GHSA-2h4p-vjrc-8xpq / PYSEC-2026-2617 | Alembic tooling dependency. The HTTP service accepts no Mako templates. Same-line patch. |
| Pygments | 2.19.2 → 2.20.0, GHSA-5239-wwwm-4pmq / PYSEC-2026-2987 | Test/report toolchain. No direct HTTP highlighting path. Compatible minor update. |
| OpenTelemetry API | 1.45.1 → 1.45.2, GHSA-mf5v-w3w3-f6cg | Transitive runtime API. No application inbound trace parsing was configured. Same-line patch. |
| pip | 22.3.1 → 26.2.0, GHSA-mq26-g339-26xf, GHSA-4xh5-x5gv-qwph, GHSA-6vgw-5pg2-w6jp, GHSA-58qw-9mgm-455v, GHSA-jp4c-xjxw-mgf9, GHSA-wf93-45jw-7689, GHSA-qwm4-qh6w-59xr, PYSEC-2023-228, PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-3721, PYSEC-2026-196 | Installer only. Updated to the compatible Python 3.11 version covering the latest listed fix. |
| pytest | 8.4.2 → 9.0.3, GHSA-6w46-j5rx-g56g / PYSEC-2026-1845 | Test runner only. First patched major selected because the published fix is on 9.x. The suite was exercised on the new pin. |
| python-dotenv | 1.1.1 → 1.2.2, GHSA-mf9w-mj56-hr94 / PYSEC-2026-2270 | Settings reads local configuration. The vulnerable `set_key` write path is absent. Compatible update. |
| setuptools | 65.5.0 installed / 80.9.0 build pin → 84.1.0, GHSA-r9hx-vwmv-q579, GHSA-5rjg-fvgr-3xxf, GHSA-cx63-2mw6-8hw5, GHSA-h35f-9h28-mq5c, GHSA-xmj3-9gf7-h52w, PYSEC-2022-43012, PYSEC-2025-49, PYSEC-2026-1918, PYSEC-2026-3447 | Installer/build boundary. Updated to the Python 3.11 compatible pin and verified editable installation. |
| wheel | 0.45.1 → 0.46.2, GHSA-8rrh-rw8j-w5fx | The clean CI environment surfaced this build-tool advisory. The affected CLI unpack path is outside the app runtime. The release pin uses the first patched version. [Wheel advisory](https://github.com/advisories/GHSA-8rrh-rw8j-w5fx). |
| Next.js | 16.4.3 → 16.4.6, GHSA-mfr8-8qww-v76h | Affected production version. Required nested cache calls were absent. Same-minor patch with matching lint plugin. |
| ESLint minimatch tree | ESLint 10.13.0 → 10.15.0 and typescript-eslint 8.72.1 → 8.73.0, GHSA-g3hv-75wg-m249 | Development-toolchain chain. Both parents now declare patched minimatch 11 ranges. The lockfile follows upstream supported dependency ranges. |

Post-update public registry results reported zero advisories for 85 installed workspace Python versions, 86 public versions in the fresh release environment and both npm trees. The exact M5 toolchain in `requirements-release.txt` uses the preinstalled secure build pins and `--no-build-isolation`. The original M1 dependency files retain their artifact-provenance values. The findings concern the versions and registries scanned. [Next.js advisory](https://github.com/advisories/GHSA-mfr8-8qww-v76h), [minimatch advisory](https://github.com/advisories/GHSA-g3hv-75wg-m249), [pytest 9 release history](https://docs.pytest.org/en/9.0.x/changelog.html).

## CI and release operations

`.github/workflows/release-checks.yml` defines separate GitHub-hosted backend and frontend checks. Official actions are pinned to immutable commits, use read-only contents rights and leave checkout credentials disabled. Jobs install the explicit toolchain, validate dependencies, scan tracked files, verify the pinned public encoder, run backend tests without Neon, and build and test Next.js with mock public configuration. A private M1 source-data case is optional in a public checkout. Generated private M1 input and model binaries stay out of CI. Backend fixture tests exercise generated model artifacts and digest rejection. Run the guarded credentialed PostgreSQL suite manually against the approved test database. No workflow deploys or configures billing. [GitHub Actions hardening guidance](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions).

## Retention and recovery

The demo has a configurable ticket inventory cap (`DEMO_TICKET_LIMIT`, default 250) checked atomically before admitting new tickets. Per-request limits cap submitted fixture text and write rate. Historical audit records remain append-only. The operator owns encrypted, least-access backups and reviews stored demo history and volumes monthly. The proposed demo RPO is 24 hours and RTO is 4 hours. Use a short agreed archive retention interval and review expiration with the operator. There is no automatic deletion of customer or audit records in this milestone.

The isolated rehearsal creates a fresh loopback PostgreSQL cluster using a short-lived CA and random credentials, bootstraps a source with fictional fixtures, dumps it in custom format and restores it into a distinct database in that private cluster. Verify TLS, schema, triggers and row fingerprints. The dump excludes roles, grants, Firebase accounts, external provider settings, and model artifacts. Recreate privileges separately and verify current pinned artifacts. The rehearsal never points at development, test, or production.

The rehearsal passed on PostgreSQL 16.15. It restored **22 fictional rows across 14 verified tables**, matched row fingerprints and verified revision `20261116_0003` and all 12 guards. The local loopback PostgreSQL process was stopped after verification. Firebase was stubbed for this exercise, and Gemini reservations were zero.

```bash
bash scripts/release/demo_recovery.sh setup artifacts/m5_b/rehearsal
python scripts/release/demo_rehearsal.py exercise artifacts/m5_b/rehearsal
bash scripts/release/demo_recovery.sh restore artifacts/m5_b/rehearsal
python scripts/release/demo_rehearsal.py verify artifacts/m5_b/rehearsal
bash scripts/release/demo_recovery.sh stop artifacts/m5_b/rehearsal
```

## Hosting and M5-C

A controlled local or operator-managed single-process demonstration provides the complete worker flow with no mandatory external billing. A hosted frontend can use an eligible no-cost personal portfolio plan. Render Free web services idle and use ephemeral storage. Free instances provide neither dedicated background workers nor continuous execution. Frequent worker polling may keep Neon awake and exhaust its free compute allowance. Start with one worker-enabled process, measured capacity and explicit quota handling. Keep the classifier and ONNX artifacts outside ephemeral request-time downloads. [Render Free](https://render.com/docs/free), [Neon plans](https://neon.com/docs/introduction/plans).

The local factory, including trusted classifier loading, took **13.95 s**. The Python process grew from **111.86 to 313.79 MiB** working set once both models were loaded. The complete measured exercise consumed **13.75 CPU seconds**. Indexing the fictional article took **3.84 s** and the restarted worker processed a pending ticket in **0.87 s**. The synthetic Next.js server returned its first home response in **5.21 s**, using **87.76 MiB** working set and **3.67 CPU seconds**. These are one-machine measurements. Sustained CPU, hosting idle/resume behavior, and production memory headroom require a pilot on the selected host.

Before M5-C, approve the exact synthetic hosting profile, domains, project and access controls, verify the first hosted CI run, rehearse the worker lifecycle on that host, and assign backup ownership. Real customer production still requires production-specific authorization and isolation, customer retention/deletion approval, approved knowledge and model/provider privacy review. Gemini stays disabled until separately approved. Deployment remains outside M5-B.

## Test evidence

- The full backend invocation recorded 499 passes, one assertion failure, one fixture setup error and zero skips. The ticket lifecycle fixture lost its Neon connection during the schema check and passed unchanged on retry. The workflow test identified the initial draft-request limit as the cause of an unexpected 429 and passed unchanged after the limit was corrected. The final offline suite passed 475 cases. Two additional guarded lease regressions passed after the telemetry change. Altogether **504 distinct backend cases passed**, including 29 live PostgreSQL cases and 65 M1 cases.
- The deliberately hung dependency returned HTTP 503 after **4,043.41 ms**. Liveness remained available and returned 200. Focused tests check peer and actor limits, Retry-After, upload deadlines, profile validation and absence of stale-success readiness.
- The frontend passed **101 unit tests**, **51 browser tests** (17 each on desktop, tablet and mobile), lint, formatting, typecheck and production build. A first run hit a Vitest worker-start timeout. A single-threaded retry passed. The pinned Next.js build for the synthetic profile returned the demo banner and HSTS and redirected signup to sign-in.
- The local workflow validation passed YAML shape, least-privilege permissions, immutable pins and credential-free step checks. The fresh pinned Python environment reported 475 passing offline tests and passed dependency validation. A clean download of the public model and tokenizer matched their fixed hashes. The GitHub-hosted workflow remains pending the manual repository checkpoint.
- Candidate scanning verified the exact 47 staging paths, credential signatures and absence of known local private values. The Git staging dry run matched the allowlist and left the index empty. Both M1 integrity mechanisms passed after the release scripts were moved outside the top-level historical script set.
- The final comparison after all live retries matched **555 preexisting Neon rows**. Development matched all 122 rows with zero additions. Test matched all 433 rows and retained 81 synthetic concurrency fixture rows, including five tickets. Both retained distinct identities, verified TLS, UTC, revision `20261116_0003`, zero schema differences and all 12 guards.

## Remaining P0 and P1 findings

- **Real-customer production P0s.** The production profile remains disabled. Production identity and database design, customer retention and deletion, recovery ownership, approved knowledge and provider privacy decisions require separate approval.
- **Deployment P1s.** A reliable always-on worker host and sustained resource budget are unverified. The remote Neon probe sometimes exceeded the HTTP budget. The application uses per-process limits and lacks durable replay protection for ordinary ticket creation and manual replies. Keep the synthetic release private until the selected ingress, project, backup ownership and hosted workflow are verified.

## Acceptance gates

| Gate | Result | Evidence |
| --- | --- | --- |
| A — Git and milestone preservation | PASS | Pushed M5-A checkpoint verified and all 48 protected M1 hashes matched. |
| B — Safe SYNTHETIC_DEMO profile | PASS | Role-bound fictional accounts, curated writes, blocked onboarding and isolated runtime rehearsal. |
| C — Production configuration isolation | PASS | Dedicated demo database and Firebase settings, guarded roles, HTTPS origin and Host controls, with production access refused. |
| D — Bounded health and readiness | PASS | Median healthy 129.92 ms locally. Hung dependency returned 503 in 4.04 s with liveness still available. |
| E — Database resilience | PASS | Verified TLS and preserved migration guards, lean runtime probe, fail-closed timeouts and rollback/lease regression coverage. |
| F — API abuse prevention | PASS | Peer, actor, route and readiness budgets, slow-body 408, preserved policy responses and verified 429 retry guidance. |
| G — Dependency security remediation | PASS | Exact audited release pins. Post-update Python and production/development npm scans reported zero findings. |
| H — Reproducible CI checks | BLOCKED | SHA-pinned workflows, clean dependency install, model verification and local checks passed. The GitHub-hosted workflow still needs the manual checkpoint. |
| I — Safe logging and observability | PASS | Correlation, latency, dependency, worker, queue and safe outcome telemetry. Operations access verified with distinct roles. |
| J — Synthetic data recovery rehearsal | PASS | Restored 22 fictional rows into a distinct loopback database. All 14 tables, fingerprints, TLS and 12 guards verified. |
| K — Worker resource and hosting assessment | PASS | Measured local startup, memory, CPU and restart recovery. Managed hosting limitations documented. |
| L — Frontend operational UX | PASS | Unit, three-viewport browser and synthetic-build smoke checks passed, including retry and ambiguous-write handling. |
| M — AI safety and ₹0 API budget | PASS | Demo generation disabled, cache opt-in and conservative policy retained. Zero real Gemini calls. |
| N — Full regression verification | PASS | All 504 backend cases passed across the full run, final offline suite and focused retries. Frontend checks passed. |
| O — Database and model preservation | PASS | Verified database fingerprints and protected M1 artifacts. |
| P — Safe Git checkpoint readiness | PASS | Exact preview of 47 paths, empty index, no private values among allowlisted paths. |

## Exact manual Git staging allowlist

Run from the repository root after reviewing the evidence. Preview the path list, then stage only these paths. `Decisions.md`, `frontend/AGENTS.md`, local credentials, demo dumps, models, data, virtual environments and generated logs are outside the checkpoint.

```powershell
$m5bPaths = @(
  '.github/workflows/release-checks.yml',
  'alembic/env.py',
  'constraints-auth.txt',
  'constraints-backend.txt',
  'constraints-release.txt',
  'constraints-retrieval.txt',
  'docs/M5_B_RELEASE_HARDENING.md',
  'frontend/.env.example',
  'frontend/next.config.mjs',
  'frontend/package-lock.json',
  'frontend/package.json',
  'frontend/src/app/(auth)/sign-up/page.tsx',
  'frontend/src/app/layout.tsx',
  'frontend/src/app/page.tsx',
  'frontend/src/components/auth-screens.tsx',
  'frontend/src/lib/api/client.ts',
  'frontend/src/lib/auth.tsx',
  'frontend/src/lib/release.ts',
  'frontend/tests/api.test.ts',
  'requirements-release.txt',
  'scripts/release/checks.py',
  'scripts/release/demo_recovery.sh',
  'scripts/release/demo_rehearsal.py',
  'src/supportlens/backend/api/abuse.py',
  'src/supportlens/backend/api/app.py',
  'src/supportlens/backend/api/config.py',
  'src/supportlens/backend/api/database.py',
  'src/supportlens/backend/api/demo.py',
  'src/supportlens/backend/api/errors.py',
  'src/supportlens/backend/api/operations.py',
  'src/supportlens/backend/api/readiness.py',
  'src/supportlens/backend/api/serve.py',
  'src/supportlens/backend/auth/dependencies.py',
  'src/supportlens/backend/auth/firebase.py',
  'src/supportlens/backend/classification/service.py',
  'src/supportlens/backend/classification/worker.py',
  'src/supportlens/backend/config.py',
  'src/supportlens/backend/db/demo.py',
  'src/supportlens/backend/db/preflight.py',
  'src/supportlens/backend/db/session.py',
  'src/supportlens/backend/drafts/routes.py',
  'src/supportlens/backend/drafts/service.py',
  'src/supportlens/backend/knowledge/indexing.py',
  'src/supportlens/backend/knowledge/routes.py',
  'src/supportlens/backend/tickets/routes.py',
  'src/supportlens/backend/tickets/service.py',
  'tests/backend/test_release_hardening.py'
)
git add --dry-run -- @m5bPaths
git add -- @m5bPaths
git diff --cached --name-only
git diff --cached --check
```
