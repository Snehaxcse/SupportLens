# SupportLens M2-B: Firebase identity and database authorization

M2-B builds on committed M2-A checkpoint `aef1ebe`. It adds authentication, onboarding and local agent provisioning. There are no ticket APIs, frontend, retrieval/generation features, deployments or schema changes. All original migrations and database guards remain authoritative.

## Identity and permission boundaries

The Firebase Admin SDK verifies ID-token signatures, issuer, audience, project and expiration. Every authenticated request enables `check_revoked=True` with zero clock skew; revoked tokens and disabled Firebase users are rejected. The wrapper additionally binds the returned UID/subject, issuer and audience to the configured project. Firebase Auth emulator configuration is refused, including an empty emulator variable. Multi-tenant tokens are unsupported and rejected.

Only one correctly formed Bearer authorization header is accepted. Missing, malformed or invalid credentials return a generic 401 and `WWW-Authenticate: Bearer`. Credential/key-fetch/provider failures deny access with a generic 503. Tokens, SDK errors and request validation inputs are never logged or returned.

Authorization uses the active PostgreSQL user identified by the verified UID. Token custom claims, email domains and display names do not grant roles. Unknown users receive `onboarding_required`; inactive users are rejected. Reusable customer-only and agent-only dependencies enforce the persisted role. There is no publicly reachable role-change operation.

## Local configuration

Keep the existing ignored `.env` database/TLS configuration. Extend it privately using the placeholders in `.env.example`:

| Variable | Purpose |
|---|---|
| `FIREBASE_PROJECT_ID` | Exact Firebase project ID whose ID tokens this server accepts; required for authentication/readiness |
| `GOOGLE_APPLICATION_CREDENTIALS` | Optional absolute path to an existing private service-account JSON file; never the JSON contents |
| `CORS_ALLOWED_ORIGINS` | JSON array of exact browser origins, for example `["http://localhost:3000"]`; default `[]` |

An explicit credential file is loaded using `firebase_admin.credentials.Certificate`. When no path is supplied, `ApplicationDefault` uses Google's documented ADC discovery, including managed server credentials. The operator must select an approved project and supply the appropriate existing credentials/IAM permissions for user/revocation checks. No project, account or key is created by this milestone. Keep credential files outside the repository, protect them with OS permissions and never paste them into chat. A Firebase Web API key is not a backend credential.

Real Firebase configuration was absent at initial M2-B preflight. Offline tests use mocked SDK calls, plus the real SDK with in-memory synthetic signing keys and local public-key/user-record responses. These tests do not establish real-project credentials, IAM permissions or connectivity. Those remain manual configuration/verification requirements. `/ready` fails closed while the project or credentials are unavailable.

References: [Firebase server credential setup](https://firebase.google.com/docs/admin/setup), [Python token verification](https://firebase.google.com/docs/reference/admin/python/firebase_admin.auth), [revocation checks](https://firebase.google.com/docs/auth/admin/manage-sessions).

## API endpoints

| Method | Route | Behavior |
|---|---|---|
| GET | `/health` | Process health only; no external dependency access or infrastructure details |
| GET | `/ready` | Credential initialization and read-only selected-database identity/schema checks; generic 503 on failure |
| GET | `/api/v1/me` | Verified token plus known, active database user; returns a bounded public user profile |
| POST | `/api/v1/auth/onboard` | Verified token and an empty JSON object `{}`; atomically creates/synchronizes the customer profile |

Onboarding rejects client UID, role, activation and profile fields. It requires a verified Firebase email because the existing users table requires an email. The verified email and a bounded plain-text display name are profile data only. Names must be escaped when a future frontend displays them. Unverified/missing emails require Firebase email verification before onboarding; existing registered users can still use `/me` with a valid UID-bearing token.

PostgreSQL's unique UID constraint arbitrates concurrent inserts using `ON CONFLICT DO NOTHING`, followed by a row lock for profile synchronization. Repeated requests retain the same UUID. Existing roles and activation flags are preserved. Inactive rows are never reactivated by login. A response is returned only after the owning transaction successfully commits.

Database connections reuse the M2-A TLS/session factories and independently verify identity before each transaction. Production remains forbidden. Readiness checks use read-only transactions and verify the original schema. Readiness validates local credential availability; a real token request is still needed to establish remote IAM/revocation-service access.

Responses use a consistent JSON error envelope, generated request IDs and `Cache-Control: no-store`. Structured request logs contain only a generated ID, method, route template and status. They omit raw paths, queries, headers, request bodies, tokens and profile fields. Documentation/debug routes are disabled. CORS permits only configured exact origins, GET/POST, Authorization/Content-Type headers and no credentialed cookies. Local HTTP origins are restricted to loopback hosts; other origins require HTTPS.

Run locally only after configuring the private environment:

```powershell
.\.venv\Scripts\python.exe -B -m uvicorn supportlens.backend.api.app:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

Keep Uvicorn access logging disabled so raw URLs/query strings cannot bypass the application's structured logging policy. Do not turn on SDK/HTTP debug logging around credentials. No model weights are loaded by application startup.

## Trusted local agent provisioning

The administrator uses the `supportlens-admin` command with an exact PostgreSQL UUID, explicit database target, reason and absolute private journal path. `ENVIRONMENT` must match the target. The command verifies both existing schemas, previews the selected active customer, requires typing `PROMOTE <exact UUID>`, then locks/rechecks the row in a transaction. There is no `--yes` bypass and no signup/domain/claim-based promotion.

Example shape only; substitute the exact UUID and approved local private paths without copying secrets into command arguments:

```text
supportlens-admin --target development --user-id <UUID> --reason "Approved support agent" --audit-file <ABSOLUTE_PRIVATE_JOURNAL_PATH>
```

Only a trusted operator with local process/filesystem access and approved database credentials may run this command. The recorded OS username is an operator label, not independent administrator authentication. Protect the command environment and journal directory with OS ACLs. No real account was promoted as part of implementation/testing.

The existing role-history guard prevents promoting a customer who owns tickets. This is deliberate: the schema currently has one role per user and requires customer-owned tickets. Use a separate approved agent identity where appropriate; do not bypass the guard or erase history.

### Administrative audit limitation and compatible solution

`ticket_events.ticket_id` is mandatory, so it cannot safely hold account-level administrative events. No fake ticket is created and no database guard is weakened. The compatible mechanism is a private, locked, append-only JSONL journal. In-repository journals must stay under ignored `artifacts/`; external private paths are also supported. Unexpected/invalid existing journal contents are refused without rewriting them. Every record is flushed/fsynced and hash-chained; each operation records a durable intent before mutation and a confirmed outcome after database commit. A failed audit-intent write prevents the privilege change.

The filesystem and PostgreSQL are separate commit domains. A crash or I/O failure after commit can leave a pending intent, and an ambiguous database commit is never automatically retried or reported as confirmed. Preserve the journal, inspect the exact UUID's role read-only and append a reviewed reconciliation record through a future reviewed administrative process. Never automatically reverse a role change. The journal is not tamper-proof against an operator who controls the file, and Windows ACLs require owner configuration. Retain/back up private journals; they are intentionally not Git artifacts.

If durable transactional, centralized administrative auditing is required for a later deployment, the smallest database solution is a new account-level administrative-event table with append-only protections, inserted in the same transaction as the role update. That requires owner approval and a new additive migration. It is proposed here, not implemented.

## Verification and checkpoint

All ordinary tests use synthetic identities and mocked external responses. Additional tests run the real SDK's signature/claim/revocation logic against in-memory synthetic RSA keys and local provider responses. No live token or service-account credential is a test fixture.

```powershell
.\.venv\Scripts\python.exe -B -m pip --disable-pip-version-check install -r requirements-dev.txt
.\.venv\Scripts\python.exe -B -m pip --disable-pip-version-check check
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -m 'not db_integration' -q
.\.venv\Scripts\python.exe -B -m supportlens.ml.cli verify
```

The full suite must explicitly opt into all configured PostgreSQL tests in a dedicated shell:

```powershell
$env:ENVIRONMENT = 'test'
$env:SUPPORTLENS_RUN_DB_TESTS = '1'
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -q
```

The M2-A fixture independently verifies both databases and the selected test schema. M2-B tests cover idempotent onboarding, profile synchronization, role preservation, inactive users, administrative promotion, support-history guards, rollback and concurrent UID insert blocking. All synthetic database writes use savepoints/outer transactions and roll back. The concurrent case verifies that a competing insert waits on the UID uniqueness lock and succeeds when the first synthetic request rolls back; committed-winner concurrency is not claimed by that rollback-only test.

`constraints-auth.txt` pins resolved M2-B additions while original ML/database constraints stay frozen. The pinned Starlette TestClient emits a deprecation warning for its supported `httpx` adapter; the adapter remains covered by the regression suite.

Review only this explicit checkpoint; exclude `Decisions.md`, environment files, credentials, datasets, models and private audit/verification files:

```powershell
git add --dry-run -- .env.example pyproject.toml requirements-dev.txt constraints-auth.txt src/supportlens/backend/api src/supportlens/backend/auth tests/backend/test_auth_api.py tests/backend/test_auth_admin.py tests/backend/test_auth_database.py tests/backend/test_auth_postgresql_integration.py tests/backend/test_firebase_sdk.py docs/M2_B_AUTHENTICATION.md
```

Only after owner review should those exact paths be staged and the staged diff checked. Suggested commit: `feat(auth): add Firebase identity and database role authorization`. No staging, commit or push is automatic. M2-C is outside this milestone.

## Initial verification before live Firebase configuration: 2026-10-29

The complete explicitly opted-in suite passed: **213 passed, zero failures, zero skips**, including 65 M1 tests, all 132 original M1/M2-A cases and 81 new M2-B cases. Eight PostgreSQL cases executed against the dedicated test database: six original and two new. All 79 offline M2-B tests passed, including real SDK cryptographic verification against synthetic keys. `pip check` found no broken requirements. The M1 verification command passed and all 48 protected M1 hashes are unchanged; committed M2-A implementation, migrations, guards, constraints and tests are unchanged.

Fresh post-test read-only checks confirmed `SupportLens_dev` (OID `16564`) and `SupportLens_test` (OID `16567`), verified client TLS, UTC/timeouts and revision `20261027_0001` on each. All seven application tables in both databases contain zero rows: no synthetic onboarding or promotion records persisted. No production database was connected.

The actual private-configuration ASGI smoke returned `/health` 200, `/ready` 503 and unauthenticated `/api/v1/me` 401. There is no configured Firebase project or explicit credential file. ADC discovery was deliberately not attempted without an approved project. Real Firebase identity, credential/IAM access and successful dependency readiness remain unverified. The implementation and offline/live-PostgreSQL tests are complete; full M2-B runtime acceptance remains blocked by that owner-supplied configuration.

| Gate | Status | Evidence |
|---|---|---|
| A: Repository safety | PASS | Protected M1 hashes and committed M2-A core preserved |
| B: Identity verification | BLOCKED | Offline SDK signature/claim/revocation checks pass; real project/credentials unverified |
| C: Authorization | PASS | Database roles/activation enforced; client role inputs rejected; no public promotion route |
| D: User persistence | PASS | Real PostgreSQL onboarding, idempotence, role preservation, concurrency and rollback checks |
| E: API | BLOCKED | Endpoint tests pass; actual health 200; successful readiness/authenticated real-project smoke awaits configuration |
| F: Tests | PASS | 213 passed; eight live DB cases; zero skips/failures |
| G: Security | PASS | Verified DB identities/TLS; no secrets tracked/staged; restricted local promotion with documented journal limits |
| H: Git readiness | PASS | Explicit 21-file dry run; nothing staged, committed or pushed |

Private verification evidence stays under ignored `artifacts/m2_b/`: `baseline.json`, `full_tests.json`, `full_tests_redacted.txt`, `m2b_live_tests.json`, `local_configuration.json`, `post_test_database.json`, `safety.json` and `verification.json`. No database schema changes or migrations were needed. No real account was promoted.

The configuration blockers in this initial snapshot were resolved by the live verification below. The earlier evidence is retained for traceability.

## Live Firebase completion: 2026-10-29

M2-B is complete. The configured project matches the owner's explicit approval, and the existing service-account JSON is outside the repository. A wrong Windows profile prefix was corrected only in the ignored local credential-path entry; other environment values and credential contents were preserved. No application code, database schema, model, guard or migration correction was required.

The Admin SDK initialized and successfully read the exact approved test identity using real credentials. Firebase initially reported its email as unverified. With explicit owner approval, an ignored development-only helper requested Firebase's standard verification email, and the owner completed the link. The verification flag was never changed through Admin SDK. A fresh sign-in afterward produced a real ID token that was used only in memory for localhost verification. No token, password, private key or credential path appears in source, public logs or Git.

The documented Uvicorn factory command started successfully at `http://127.0.0.1:8000` with access logging disabled. Real HTTP verification produced:

| Check | Result |
|---|---|
| `GET /health` | 200 |
| `GET /ready` | 200 |
| Unauthenticated `GET /api/v1/me` | 401 |
| Approved real token before onboarding | 403 `onboarding_required`, after successful identity verification |
| First authenticated `POST /api/v1/auth/onboard` | 200; persisted CUSTOMER |
| Repeated authenticated onboarding | 200; same database UUID |
| Authenticated `GET /api/v1/me` | 200; correct database-backed identity |
| Client-supplied AGENT role | 422; CUSTOMER role preserved |
| Invalid ID token / malformed Bearer token | 401 |

Exactly one owner-approved test CUSTOMER remains in `SupportLens_dev`. It was not deleted. Its UID/email and database identifier remain in private configuration/evidence rather than this document. All other development application tables and all seven test-database application tables remain empty. Both databases retain distinct verified identities, verified client TLS, UTC/timeouts and revision `20261027_0001`. No production database was accessed. No live account was promoted, revoked or deleted; no Firebase projects, service accounts or keys were created.

The full regression suite was rerun during live verification: **213 passed, zero failures, zero skips**, including all eight guarded PostgreSQL cases and 65 M1 cases. Dependency consistency and M1 artifact integrity checks passed. Live invalid-token rejection was exercised. Revoked/expired-token rejection remains covered by SDK regressions; no live test account was revoked. The existing private-journal audit limitations documented above remain unchanged.

| Final live acceptance gate | Result | Evidence |
|---|---|---|
| A: Repository safety | PASS | M1 and M2-A preserved; private files excluded |
| B: Real Firebase identity verification | PASS | Real approved credential, user lookup, sign-in and ID-token acceptance |
| C: Database-backed authorization | PASS | Persisted CUSTOMER identity; client AGENT role rejected |
| D: Idempotent onboarding | PASS | Same UUID on repeated requests; one row for approved UID |
| E: Runtime endpoints | PASS | Actual localhost health/readiness/me/onboarding HTTP checks |
| F: Full regression tests | PASS | 213 passed; zero failures/skips; eight live PostgreSQL cases |
| G: Security | PASS | Secrets/tokens excluded; scoped test identity; no destructive operations |
| H: Git readiness | PASS | Explicit 21-file checkpoint; no automatic staging/commit/push |

Private final evidence is under ignored `artifacts/m2_b/live_20261029/`, including `security_preflight.json`, `firebase_credential.json`, `authenticated_runtime.json`, `database_before.json`, `database_after.json`, `full_tests.json`, `full_tests_redacted.txt` and `completion.json`. `artifacts/m2_b/verification.json` records the final acceptance summary. Development-only sign-in/email helpers remain ignored and are excluded from the checkpoint. M2-C was not started and requires separate owner approval.
