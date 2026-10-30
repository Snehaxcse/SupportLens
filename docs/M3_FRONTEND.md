# SupportLens M3 frontend

M3 adds a local Next.js App Router customer portal and agent workspace to the existing FastAPI application. Firebase establishes identity; PostgreSQL remains authoritative for roles and activation. There are no new account operations, schema migrations, AI responses, Spotify integrations, or authorization bypasses.

## Architecture and routes

The independent `frontend/` package uses strict TypeScript, Tailwind CSS, Firebase Web Authentication, Lucide icons and accessible Radix primitives. Operational data comes from the existing API. OpenAPI-generated types reflect the saved M2-C contract; generation does not require a running backend or expose public API documentation.

| Route | Purpose |
|---|---|
| `/` | Public introduction and independent-service disclosure |
| `/sign-in`, `/sign-up` | Firebase email/password authentication |
| `/verify-email` | Normal verification email and refresh flow |
| `/auth-error` | Recoverable session/account problems |
| `/customer` | Complete authorized ticket counts and recent requests |
| `/customer/tickets` | Paginated own-ticket list |
| `/customer/tickets/new` | Validated ticket creation |
| `/customer/tickets/[ticketId]` | Public conversation, history, reply and reopen |
| `/customer/account` | Database-backed profile, read only |
| `/agent` | Complete queue counts and recently updated tickets |
| `/agent/tickets` | URL-persisted filters, search, ordering and pagination |
| `/agent/tickets/[ticketId]` | Conversation, assignment, lifecycle, notes and audit history |
| `/agent/account` | Database-backed profile, read only |

The browser obtains ID tokens through the Firebase SDK. It never stores tokens itself, logs tokens, or sends role fields during onboarding. Firebase session persistence belongs to the SDK. An unverified account cannot mount an operational screen. The backend profile establishes the permitted route group. Backend authorization remains mandatory on every request; client route guards are only user experience controls.

Requests are cancellable and use `no-store`. A 401 triggers at most one SDK token refresh and one retry. Other errors are not automatically retried. Mutation controls prevent concurrent submissions; success is shown only after an API response. An uncertain network failure on a mutation requires checking the ticket before retrying.

Dashboard aggregation reads every authorized page. It rejects incomplete or changing pagination rather than displaying partial totals as global metrics. This is appropriate for the current project scale; a future reviewed aggregate endpoint would reduce requests for large queues.

Customer DTOs and views expose public information only. Text is rendered as escaped plain text. Agent public and internal composers have separate drafts and explicit visibility labels. Saving an internal note never converts its draft or mode to a public reply. Resolved tickets reject public replies until reopened. Audit history is paginated and read only.

## Environment and local startup

Use Node.js 22.13+ within Node 22, Node 24, or Node 26+. This implementation was checked on Node 22.19.0 and npm 10.9.3. Install from the reviewed frontend lockfile with `npm ci --ignore-scripts` in `frontend/`. Copy `frontend/.env.example` to ignored `frontend/.env.local` and configure only Firebase **public web configuration** and the API origin. Never copy the root backend environment file into the frontend.

Required browser variables:

- `NEXT_PUBLIC_API_BASE_URL`: approved backend origin, locally `http://127.0.0.1:8000`.
- `NEXT_PUBLIC_FIREBASE_API_KEY`: Firebase Web API key, subject to project API restrictions.
- `NEXT_PUBLIC_FIREBASE_PROJECT_ID`: intended project, currently `supportlens-fc39b`.
- `NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN`: registered Firebase authentication domain, when applicable.
- `NEXT_PUBLIC_FIREBASE_APP_ID`: registered web app ID, when applicable; never invent one.

Admin credentials, database URLs, private keys and test passwords belong exclusively to ignored backend/local verification configuration. Firebase Web API keys identify the public project; they do not grant database authorization. Restrict authorized domains/API usage in Firebase before deployment.

From the repository root, in the backend development environment:

```powershell
$env:ENVIRONMENT = 'development'
.\.venv\Scripts\python.exe -B -m uvicorn supportlens.backend.api.app:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

Set backend `CORS_ALLOWED_ORIGINS` to the exact approved local browser origins (for example `["http://localhost:3000","http://127.0.0.1:3000"]`). Never use wildcard origins. Backend `.env` must select `ENVIRONMENT=development`; test commands use a separate explicit test process. Preserve verified TLS and database isolation settings.

In a second terminal:

```powershell
Set-Location frontend
npm run dev -- --hostname 127.0.0.1 --port 3000
```

To inspect the compiled production application instead, run `npm run build` and then `npm run start -- --hostname 127.0.0.1 --port 3000`. Use a free local port and preserve unrelated processes. The build reads only the frontend public environment file; private backend configuration stays in the repository root's ignored `.env`.

Firebase signup sends a normal verification email. Open its normal link, return to the verification screen and refresh verification. Onboarding creates only an active CUSTOMER, or returns the existing database user. An existing inactive account remains blocked. No public route provisions an AGENT.

## Approved agent test identity: owner steps

The owner completed this procedure with a separate, dedicated verified test identity and explicitly approved its exact UID for promotion. The existing trusted M2-B command promoted only that new development user; its durable promotion journal and active database AGENT role were verified. The original approved customer was preserved. The procedure below remains the required approach for any future test identity. Do not use token custom claims, direct SQL role updates, or client-side bypasses.

1. In Firebase Console, select the intended SupportLens project, then **Authentication → Users → Add user**. Create a separate, dedicated email/password test account. Keep the credentials private. Do not create another project or service account.
2. Sign in through SupportLens, request the standard verification email, follow the received link and refresh verification. Onboarding creates the separate CUSTOMER row. Do not create tickets with this account before promotion: existing support-history constraints can prevent changing an established customer's role.
3. Obtain the new Firebase UID from the Firebase Users console. In an approved read-only development database session, inspect only this identity:

```sql
SELECT current_database(); -- must be SupportLens_dev
SELECT id, firebase_uid, role, is_active
FROM public.users WHERE firebase_uid = '<NEW_FIREBASE_UID>';
```

4. **Request explicit owner approval before provisioning.** Once approved, use the existing M2-B trusted administrative command, with backend `ENVIRONMENT=development`. The target must be the new row's PostgreSQL UUID, not its Firebase UID. Keep the durable journal outside Git or under ignored `artifacts/`:

```powershell
.\.venv\Scripts\python.exe -B -m supportlens.backend.auth.admin --target development --user-id <POSTGRES_USER_UUID> --reason "Approved M3 agent UI verification" --audit-file <ABSOLUTE_PRIVATE_JOURNAL_PATH>
```

The command verifies approved databases, checks an existing active CUSTOMER and requires the exact interactive confirmation `PROMOTE <POSTGRES_USER_UUID>`. Preserve the journal. If the command reports an ambiguous result, inspect the row and journal read only; never retry blindly or delete history.

5. In the ignored **root** `.env`, store `FIREBASE_AGENT_TEST_EMAIL`, `FIREBASE_AGENT_TEST_PASSWORD` and optionally `FIREBASE_AGENT_TEST_UID`. These are private local verification inputs, never `NEXT_PUBLIC_*` variables. Do not paste their values into chat.
6. Repeat the read-only query. Confirm the exact approved Firebase UID, `role = 'AGENT'`, `is_active = true`, and `current_database() = 'SupportLens_dev'`. Sign out and back in so the frontend reloads its database-backed role. Explicitly approve the new UID for agent UI verification. No promotion or agent test-account deletion occurs automatically.

## Verification

Frontend checks run inside `frontend/`. Browser checks require installed Google Chrome; Playwright uses its `chrome` channel without downloading a browser. They start a separate Next.js server on port 3100, use `.next-e2e/`, and intercept external services. Their API origin is deliberately port 8900, not the real development backend:

```powershell
npm run lint
npm run typecheck
npm test
npm run build
npm run test:e2e
```

Unit tests mock external services. Browser scenarios use clearly synthetic identities and records with network interception; no production authentication bypass or fake operational-data mode exists. Screenshots must contain synthetic data only. Real Firebase/customer checks are a separate private workflow, keep credentials/tokens only in memory, and do not capture account screenshots or persist browser storage.

Existing backend regression checks run from the root in a dedicated test process:

```powershell
$env:ENVIRONMENT = 'test'
$env:SUPPORTLENS_RUN_DB_TESTS = '1'
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -q
.\.venv\Scripts\python.exe -B -m pip --disable-pip-version-check check
.\.venv\Scripts\python.exe -B -m supportlens.ml.cli verify
```

The existing live concurrency test deliberately retains a uniquely identified synthetic fixture in **SupportLens_test** for auditability. Other database test fixtures roll back. Never clear tables to clean up testing. The local redacting verification runner is ignored and preserves prior milestone reports.

## Deployment considerations and verification record

Deploy the frontend and backend independently using compatible Node/Python runtimes. Configure exact HTTPS CORS origins, Firebase authorized domains, public build-time configuration, verified backend database TLS and private Admin credentials. Public Next.js configuration is embedded at build time; a changed public API/Firebase project requires rebuilding. Do not expose the backend `.env`, private datasets, classifier binaries or administrative scripts as static files.

M3 is verified locally, not deployed. Backend tools in this milestone continue to reject production database targets. Treat test-account credentials and the administrative promotion journal as private operational inputs; never ship them with the frontend. Review free-tier cold-start behavior and queue aggregation costs before setting performance expectations. No hosting resources or production records were created here.

No deployment, commit or push is part of M3. Real agent runtime verification and the complete post-verification regression run have passed after the separate identity procedure above. Planned commands are not counted as passing evidence.

## Implementation details

- `src/lib/api/schema.d.ts` is generated from `contracts/openapi.json`, the audited M2-C contract. `client.ts` attaches SDK ID tokens, retries a 401 once after supported SDK refresh, rejects responses from an identity that changed during a request, and maps errors to safe, actionable messages. It never displays raw transport/server error content.
- `src/lib/auth.tsx` listens to Firebase authentication state, blocks unverified accounts, onboards only when the backend explicitly reports `onboarding_required`, then reads `/me`. Database roles and activation establish access. Browser session persistence survives reloads within the browser session; the application does not implement a token store. Focusing the window rechecks the database profile. Session invalidation cancels any pending profile refresh, preventing an older response from restoring stale access.
- `src/lib/hooks.ts` cancels stale resource requests, clears unauthorized data, prevents concurrent mutation submissions, and retains a ticket during its own pagination/refresh so independent composer drafts survive. Changing the ticket clears its old conversation. Deterministic hydration snapshots keep delayed routes consistent with server-rendered authentication loading states.
- `src/components/customer.tsx` implements complete dashboard aggregation, paginated own tickets, validated creation, public conversation/activity and reopening. `agent.tsx` implements the overview, URL-based inbox, directory, assignment, priority, lifecycle and audit workspace. Inbox return filters survive opening and leaving a ticket. Inbox ordering is explicitly by creation time; dashboard recent updates sort actual `updated_at` values after reading the full queue.
- `composer.tsx` keeps separate PUBLIC/INTERNAL drafts. Confirmed submission clears only the submitted draft; failure preserves it. Internal mode stays internal after saving. Resolved public composers remain read only until reopening, while agents can still save internal notes.
- `ticket-ui.tsx` renders text through React and refuses an unexpected internal customer projection. Customer views do not render privileged actor IDs or metadata. Agent activity displays recognized lifecycle/priority/assignment fields rather than dumping arbitrary metadata.
- Only public Firebase App/Auth SDK modules are installed (`@firebase/app`, `@firebase/auth`); unused Firebase services are not needed. Lint uses current ESLint, TypeScript, Hooks and Next plugins without incompatible legacy plugins. Exact versions and the lockfile are checked in; `next-env.d.ts`, build output and verification reports are generated and ignored.

## Verification evidence and current limits

The mandatory preflight confirmed the committed/pushed M2-C checkpoint `66e4965e4d0e780909f47957a10051e068a23be7`. The complete backend run passed **298 tests, zero failures/skips**, including **12 live PostgreSQL cases** and **65 M1 cases**. It completed in 1,684.02 seconds. Backend implementation, migrations and tests were not modified afterward. An earlier run was interrupted without a final report and is not counted as a passing run.

The required full backend rerun after real AGENT verification also passed **298 tests, zero failures/errors/skips**, including all **12 live PostgreSQL cases** with explicit test opt-in and **65 M1 regression cases**, in **1,959.16 seconds**. This includes committed concurrent-claim ownership with exactly one winning claim event, immutable audit history, rollback on audit failure, role constraints, customer/agent lifecycle and inbox pagination/filter/no-op behavior. Its final report was verified as newer than the recorded post-agent run start; the earlier passing report was archived rather than mistaken for the new result.

The final frontend unit/component run passed **74 tests**. Coverage includes authenticated token attachment/refresh, verified-email gating, onboarding, database roles/inactivity, route separation, complete pagination, validation, reply/reopen rules, claim conflicts, assignment/null semantics, status/priority requests, internal/public drafts, HTML escaping, cancellation, duplicate submissions, hydration and session races. Lint, strict typecheck and formatting checks passed. A clean lockfile install completed with install scripts disabled; dependency inspection found no missing/invalid dependencies, unsupported Node engines or audit vulnerabilities. `npm ls` exits successfully but reports `@emnapi/runtime` and `@img/sharp-wasm32` as extraneous even after the clean install; these remain ignored local dependency files.

Browser scenarios run at **1440×1000**, **900×1000** and **390×844**. They use synthetic identities/tickets only, and check signup, verification, session reload, complete totals, customer create/reply/reopen, role boundaries, inbox filters, claim conflicts, assignment, priority, resolve/reopen, private notes, network/404/inactive/stale states, keyboard tabs, mobile navigation focus and sign-out. Core screens run axe checks for WCAG 2 A/AA and 2.1 A/AA, check horizontal overflow, and reject hydration/uncaught browser errors. This is automated accessibility coverage, not a manual screen-reader audit or a cross-browser certification.

Synthetic screenshots are retained locally under ignored `artifacts/m3/visuals/`: landing, sign-in, customer overview/detail, agent inbox/workspace and notes for each viewport. Desktop and mobile captures were visually inspected; the original mobile banner wrapping issue was corrected and the affected scenarios rerun. These are test artifacts, not embedded operational sample data. Production application source has no synthetic-data mode or auth bypass.

Intent classification, retrieval, LLM generation, knowledge-base UI, attachments, password-reset UI, profile editing, realtime ticket subscriptions and external Spotify actions are outside M3. Dashboard aggregation is correct across pages but has linear request cost and cannot provide an atomic cross-page snapshot during concurrent updates. Large queues should eventually use a reviewed aggregate endpoint. Offset pagination reflects the backend contract. After an uncertain create/message network failure, check the ticket before retrying: the current API does not provide mutation idempotency keys. No automatic retry is made for uncertain mutations.

Real customer and agent runtime results, database effects, build evidence and the acceptance matrix are recorded below. The separately created test user was promoted only after explicit approval. The original customer remains unchanged and no user or ticket was deleted.

## Safe manual Git checkpoint

Only the frontend, this document and the additive ignore rules are eligible for review. Existing backend/ML source, migrations, constraints and tests remain unchanged. Keep the pre-existing `Decisions.md`, private environment/credentials, datasets, model binaries, dependency folders, build output, test reports and ignored verification helpers out of the checkpoint.

From the repository root, a review-only staging preview is:

```powershell
git add --dry-run -- .gitignore docs/M3_FRONTEND.md frontend/.env.example frontend/.prettierignore frontend/package.json frontend/package-lock.json frontend/next.config.mjs frontend/postcss.config.mjs frontend/eslint.config.mjs frontend/tsconfig.json frontend/vitest.config.ts frontend/playwright.config.ts frontend/public frontend/contracts frontend/src frontend/tests
```

After completed M3 acceptance and owner review, use the same exact paths without `--dry-run`, inspect `git diff --cached --name-only` and the staged changes, and repeat the credential/dataset review. Never use a broad `git add .`. Generated `next-env.d.ts` is deliberately excluded. Suggested commit subject: `feat(frontend): add customer portal and agent workspace`. This run stages nothing and performs no commit, push or deployment.

## Live verification: 2026-10-30

The compiled production frontend was served at `http://127.0.0.1:3000` and the existing backend at `http://127.0.0.1:8000`. Both root frontend and backend health/readiness returned 200. Earlier unchanged-backend checks returned 401 for missing/invalid tokens. The real Firebase Web SDK accepted the specifically approved dedicated CUSTOMER identity in project `supportlens-fc39b`; its UID and verified-email claim were checked without exposing credentials. `/me` returned the active database CUSTOMER, repeated `{}` onboarding returned 200 and the same identity, and access to the agent inbox returned 403.

The customer created one explicitly synthetic request through the production UI (201), sent one public reply (201), loaded the persisted conversation and signed out. A broad smoke-helper text locator initially matched the composer draft before the asynchronous refresh was confirmed. The retained record was inspected read only, then the existing conversation was verified in a read-only resumed browser run with all additional database mutations blocked. Nothing was resubmitted. Browser assertions were strengthened to scope message waits to the rendered conversation rather than a draft. No real-account screenshots, traces, token exports, Firebase user creation, role changes or deletions occurred.

The owner subsequently created a separate Firebase test identity, completed its normal email-verification flow and onboarded it as a separate CUSTOMER. Following explicit approval of its exact UID, the existing M2-B administrative CLI promoted only its mapped PostgreSQL UUID in `SupportLens_dev`. The journal contains a verified hash chain with promotion intent and committed records. Its active AGENT role, project binding and email verification were checked read only. No Firebase verification flag or custom role claim was altered.

Real Firebase Web SDK sessions then exercised **14 confirmed UI mutations** on the retained synthetic request: claim; HIGH priority; unassign; assign the approved agent through the directory control; start work; internal note; public reply; resolve; another internal note while resolved; customer reopen; customer follow-up; agent restart work; resolve again; and agent reopen. Public and internal drafts remained separate. Resolved public replies were disabled. The final rendered agent audit timeline contained all 16 events, including the original two, with the expected actors/actions and no duplicated operations. Both sessions signed out and reported zero uncaught browser errors. These were actual production-frontend/backend/Firebase interactions, with no mocked authentication or ticket responses.

Real assignment used the single approved AGENT (claim, unassign and directory assignment back to that account). Reassignment to a different real Firebase agent was not attempted; additional agents were not provisioned. Cross-agent cases remain covered by the synthetic browser and backend tests. Live Neon-dependent checks can take longer than a short health timeout; the eventual readiness check passed with a longer verification deadline. This run does not establish a production latency or load SLA.

The first helper run timed out during customer navigation after nine verified mutations. Its journal was preserved; a separate run blocked all ticket mutations and reconciled the actual retained state (4 messages, 11 events, RESOLVED). Customer confidentiality checks passed read only. The helper used DOM readiness followed by explicit rendered-content assertions, then resumed only the remaining five operations after rechecking the exact retained state. No completed mutation was replayed, and no application or authorization policy was changed to pass this check.

The real customer DOM and API projection excluded both internal note bodies, all internal-note events, assignment events, privileged actor IDs and assignment fields, before and after reopening. The customer saw **3 PUBLIC messages and 11 public events**, while the agent saw **5 messages (including 2 INTERNAL notes) and 16 events**. Customer access to the agent API returned 403. No real-account screenshots, traces, storage exports or token/password logs were produced.

Retained development effects are **2 users (original CUSTOMER and approved AGENT), 1 synthetic ticket, 5 messages and 16 audit events**. The ticket remains REOPENED, HIGH priority and assigned to the approved test agent. Its original messages and audit events are preserved; the original customer's complete row hash matches the pre-M3 baseline after all verification. `SupportLens_dev` remains OID 16564 and `SupportLens_test` OID 16567; both use verified client TLS, UTC and revision `20261027_0001`. Final test counts are **9 synthetic users, 3 tickets, 0 messages and 6 audit events**. The post-agent regression added one retained concurrency fixture (three users, one ticket, two events) to the six users/two tickets/four events already present. Other database fixtures rolled back. Neither database was reset, cleared, downgraded or migrated during M3.

All 30 browser scenarios passed across the three viewports after the auth corrections. Six focused conversation scenarios also passed after the stronger message assertions. Frontend lint, strict typecheck, formatting, 74 unit/component tests, clean lockfile installation and production build passed. Python dependency consistency and M1 artifact/gold/split integrity passed. Source preservation checks confirmed all prior milestone files unchanged except the seven additive ignore lines; the live backend OpenAPI still matches the frontend contract. No credentials or private dataset paths are staged/tracked, and private values were absent from reviewable files and browser bundles. The new public source/config/documentation checkpoint contains 61 files; the only modified previously tracked file is `.gitignore`.

After real agent verification, the frontend checks were repeated successfully: **74 tests, 30 browser scenarios, zero failures/skips/flaky retries**, lint, strict typecheck, formatting and a production build. The build used the existing `SUPPORTLENS_BUILD_CONTEXT=e2e` setting only to select separate `.next-e2e` output; build mode was production and public configuration came from the actual frontend environment. The running production `.next` output was preserved. Subsequent synthetic browser tests used their explicit synthetic API/Firebase configuration and separate port 3100. Python dependency/M1 integrity and frontend dependency/advisory checks also passed again, and 36 produced browser bundles were checked without finding private credential values. The complete post-agent backend regression also passed. Final localhost frontend/health/readiness checks returned 200, and unauthenticated `/me` returned 401. Neither production service was restarted during live verification or these regression checks.

### Acceptance gates

| Gate | Status | Evidence / remaining dependency |
|---|---|---|
| A — Repository safety | PASS | Previous milestone source/artifacts preserved; no schema/migration changes |
| B — Authentication | PASS | Real approved customer SDK sign-in, verified email, database identity and idempotent onboarding |
| C — Customer experience | PASS | Real create/reply/read, resolved-state restrictions, reopen/follow-up and sign-out |
| D — Agent experience | PASS | Approved real AGENT completed claim, assignment/unassignment, priority, public/internal replies, lifecycle and audit checks |
| E — Security | PASS | Real customer DOM/API exclude internal notes/private audit data; server RBAC and original customer preserved; credentials remain isolated |
| F — UX quality | PASS | Responsive visual review, keyboard/focus behavior, axe checks and overflow checks across three viewports |
| G — Testing | PASS | Post-agent 298 regressions including 12 live PostgreSQL/65 M1 cases; 74 frontend tests, 30 browser scenarios; lint/typecheck/build/format/dependency/M1 integrity passed |
| H — Runtime | PASS | Production frontend/backend healthy; real customer and approved agent lifecycle, confidentiality and sign-out verified |
| I — Git readiness | PASS | Explicit safe source paths, ignored local/private/generated files, no staged files or Git revision change |

**M3 acceptance is complete: gates A–I passed.** M4 has not started. The owner should review and make the safe manual Git checkpoint above before beginning M4. No commit, push or deployment was performed.

