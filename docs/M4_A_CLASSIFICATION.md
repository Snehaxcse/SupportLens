# M4-A: advisory ticket classification

M4-A connects the existing trusted M1 classifier to newly created tickets. Agents receive an advisory intent suggestion; every result requires human review. Classification never changes ticket status, priority, assignment, resolution, or customer replies. No Spotify account/billing integration, retrieval, embeddings, LLM generation, or automated support action is implemented.

## Architecture and data flow

1. The existing ticket-creation transaction inserts the ticket, its original creation event, and a PENDING `ai_runs` row. No model inference occurs in this transaction. The customer response contract is unchanged.
2. After commit, `TicketService` wakes a background worker. A lost wake is recoverable: startup and periodic polling also inspect durable jobs. Wake failures cannot turn a committed ticket into an HTTP failure.
3. The worker claims one pending or expired-running job with a short `FOR UPDATE SKIP LOCKED` transaction, using the database clock and a unique lease token. It copies subject/description into an immutable in-memory job and commits before inference.
4. Inference runs on a worker thread, outside the event loop and database transactions. The model is cached; no external model service is needed.
5. A separate short transaction publishes the result and an internal audit event atomically. It accepts only the matching RUNNING lease. A stale worker cannot overwrite a newer lease or terminal result.
6. The agent-only API projects a strict DTO. The independent classification panel reads it without blocking the existing conversation and management controls.

PostgreSQL documents [`SKIP LOCKED` for queue consumers](https://www.postgresql.org/docs/17/sql-select.html) and [transaction advisory locks](https://www.postgresql.org/docs/17/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS). The queue uses the former; cross-ticket retry budgeting uses the latter. These are short locks, never inference-duration locks.

## Trusted M1 provenance and loading

The fixed backend-owned directory is `artifacts/models/m1_tfidf_lr_v1`. No HTTP parameter or environment variable selects a model file. The reviewed SHA-256 identities are pinned in `src/supportlens/backend/classification/constants.py`:

| File | SHA-256 |
|---|---|
| `model.joblib` | `6c5d3088aa6222658d4ce5a5d4753c2496cebdf11f8401aa3e959fe29bd63e4f` |
| `metadata.json` | `db41bfdd874604fd33e19f929a12c6b35a5a0db987b8ba47d2e3d4bd57a48db4` |
| `completed_run.json` | `0aaec677d76e871c51071b947e05da992fe313d4015213add25c67fb012a0dff` |

The loader bounds reads, rejects symlinks, checks all three hashes, then validates completion/metadata, feature configuration, taxonomy/class order, dependency versions and the fitted TF-IDF/Logistic Regression pipeline. It deserializes the authenticated model bytes through `BytesIO`, avoiding a second path read between verification and deserialization. Joblib remains an executable serialization format: only this reviewed, pinned local artifact is trusted. This is not a sandbox for arbitrary model uploads.

Lazy ML imports keep absent/incompatible ML dependencies from blocking the support application. A private adapter initializes the verified artifact while inheriting the original M1 prediction/preprocessing methods unchanged. Successful models are cached for the service lifetime behind a lock; failed loading is cached for 30 seconds to limit repeated expensive failures. Replacing an approved artifact requires a separate provenance review, hash update and process restart.

The artifact records Python 3.11.3, scikit-learn 1.7.2, NumPy 2.2.6, pandas 2.3.3, SciPy 1.15.3, joblib 1.5.2, threadpoolctl 3.6.0 and SupportLens 0.1.0. Exact runtime compatibility is checked. No dependencies, model, training code, dataset, split or M1 evaluation were changed for M4-A.

## Input and uncertainty

Input is the trimmed subject, a blank line, and the trimmed description. The original ticket bounds (200/20,000 characters) are validated before applying M1's 10,000-character limit to the combined input. Truncation is explicitly persisted and displayed; original ticket text is preserved. Empty/malformed input is a safe FAILED outcome. Raw text is not duplicated into AI metadata.

Seven supported output classes, in artifact order: ACCOUNT_ACCESS, ACCOUNT_SECURITY, APP_TECHNICAL, BILLING_PAYMENT, CONTENT, FEATURE, SUBSCRIPTION. UNKNOWN, NON_SUPPORT and OTHER_SUPPORT are not learned classes. The classifier necessarily chooses among seven classes even for out-of-scope input; it cannot reliably detect those unsupported intents. An unexpected unsupported model output is rejected rather than treated as a valid prediction.

Existing M1 held-out evidence contains 35 examples: accuracy approximately 65.71%, macro F1 approximately 0.6227, and APP_TECHNICAL recall 0/4. These are historical measurements, not new M4-A quality claims. Raw scores overlap between correct and incorrect cases; calibration and a reliable acceptance threshold have not been established. Twitter-thread training text also differs from web ticket subject/description input.

`CLASSIFICATION_LOW_SCORE_THRESHOLD=0.5` is a configurable, **unvalidated advisory flag**. The API always reports `threshold_validated=false`, `requires_human_review=true` and `automated_actions_allowed=false`. Confidence is labeled `UNCALIBRATED_RAW_PROBABILITY`; the UI explicitly says it is not an accuracy estimate. Failed/unavailable inference has no intent/confidence and is distinct from a successful low-score suggestion.

## Persistence, recovery and retries

No migration is required. Both databases retain revision `20261027_0001`; SQLAlchemy models, constraints, five database guard triggers and existing migrations are unchanged. Existing `ai_runs` columns provide ticket linkage, model version, intent, review flag, status and timestamp. Versioned JSON metadata (`kind=supportlens.intent-classification.v1`) provides generation, artifact hash, request source/actor, retry parent, execution/lease fields, confidence/class scores, threshold, truncation, completion time, duration and a safe failure category.

PENDING and RUNNING are durable. A dead worker leaves a lease; after expiry a later worker reclaims it. At most three executions are attempted by default, then a fenced terminal `RECOVERY_EXHAUSTED` result is published. A failed publication/audit transaction rolls back the result too. Another worker can recover the lease afterward. This is at-least-once computation with a single accepted terminal publication per run, not exactly-once execution. A running backend or explicit worker is required for progress.

Unavailable model conditions are stored as existing database FAILED status plus a safe category and projected as UNAVAILABLE. Missing, invalid, incompatible or disabled artifacts do not fail ticket creation. Neither `/health` nor core `/ready` requires an available classifier: readiness continues to check required Firebase/database dependencies. If Firebase configuration is missing, factory health startup creates no classification worker/database connection.

Retries append new rows and retain terminal history. The required body `expected_run_id` identifies the observed attempt; null means observed absence. A ticket-row lock serializes generations. Replaying the same predecessor returns its already accepted child, including after completion or later generations. An active attempt coalesces requests. A stale unknown predecessor returns 409. GET determines the actual current generation; the POST response is the acknowledged attempt and need not be current on historical replay.

New attempts have a default 60-second ticket cooldown and a 10-per-minute agent budget serialized by a transaction advisory lock. Replays/coalesced requests do not consume new budget. Database-backed active AGENT authorization is rechecked inside the operation as well as at the HTTP boundary. Manual writes to versioned queue metadata are unsupported; no new database uniqueness constraint was added. Existing query/index costs are suitable for the current scope, not a demonstrated high-volume queue SLA.

Internal `AI_DECISION_RECORDED` events record retry requests and terminal completion. The initial enqueue adds no extra creation event. The existing customer history projection excludes AI events. No classification operation modifies ticket timestamps or lifecycle fields.

Existing tickets are not backfilled automatically. They report NOT_REQUESTED until an authorized agent explicitly requests classification; the existing M3 verification ticket and its history are preserved.

## Agent API and UI

| Endpoint | Behavior |
|---|---|
| `GET /api/v1/agent/tickets/{ticket_id}/classification` | Current generation, up to five recent attempts, total count, model limitations and retry availability; NOT_REQUESTED when absent |
| `POST /api/v1/agent/tickets/{ticket_id}/classification/retry` | Required `expected_run_id` UUID or null; 202 for an active acknowledgment, 200 for terminal replay |

For an observed absence, send `{ "expected_run_id": null }`; otherwise send the observed run UUID as a JSON string. States are NOT_REQUESTED, PENDING, RUNNING, SUCCEEDED, FAILED and UNAVAILABLE. Errors retain existing safe JSON semantics: 401 unauthenticated, 403 unauthorized/inactive, 404 unknown ticket, 409 stale/conflicting state, 422 invalid body, 429 cooldown/budget, 503 unavailable dependency/invalid stored projection. No raw driver/model errors, lease tokens, metadata dumps, filesystem paths or ticket text are returned.

The typed OpenAPI snapshot/client add only these agent operations and related schemas; customer contracts remain unchanged. The client rejects cross-ticket/safety-invalid classification data. The compact panel sits below ticket management controls and shows advisory intent, raw score, review requirement, low flag, truncation, provenance, limitations and saved-attempt count. Pending work polls after completed reads; cooldown refresh is scheduled; errors stay local and do not block ticket tools. Retry sends the observed generation ID and refreshes GET after acknowledgment. React renders escaped text. Existing customer screens, authentication, composers and internal-note boundaries are preserved.

## Security

Only the existing Firebase-authenticated, active database AGENT can inspect or retry classification. Customers receive 403 at the agent API and no ML diagnostics in ticket DTOs/history/DOM. Private artifacts and datasets are outside frontend public assets and are never HTTP-served. Browser bundles must contain only intended public Firebase configuration, never Admin/database credentials or test passwords.

Logs use fixed safe categories and route templates, without ticket content, model exception details, token values or connection strings. Test credentials come only from ignored local configuration and remain in memory during real verification. Real-account screenshots, traces and storage exports are prohibited. No role provisioning, Firebase user changes, automated support actions, production database access, commit, push or deployment are part of M4-A.

## Local configuration and startup

Use the existing reviewed Python environment, ignored root `.env`, private approved Firebase credential source, verified database TLS and distinct development/test identities. Optional server-only settings are documented in `.env.example`: enabled flags; advisory threshold 0.5; poll 30 seconds; lease 300 seconds; maximum executions 3; cooldown 60 seconds; agent budget 10/minute. There is no model-path setting. Match the existing frontend public configuration and exact CORS origins.

From the repository root in a dedicated backend terminal:

```powershell
$env:ENVIRONMENT = 'development'
.\.venv\Scripts\python.exe -B -m uvicorn supportlens.backend.api.app:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

In a separate terminal, use `npm run dev -- --hostname 127.0.0.1 --port 3000` in `frontend/`. For production preview, use `npm run build` then `npm run start -- --hostname 127.0.0.1 --port 3000`. Preserve unrelated processes and existing production output during browser tests. These are local commands, not deployment instructions.

For a bounded manual queue drain when the development backend worker is stopped:

```powershell
$env:ENVIRONMENT = 'development'
.\.venv\Scripts\python.exe -B -m supportlens.backend.classification.worker --target development --max-jobs 100
```

This command writes pending results/audit records; run it only against the approved development database. A test target also requires explicit database-test opt-in. No reset, downgrade or data cleanup is appropriate.

## Tests and synthetic evaluation

Run backend checks in a dedicated test process, preserving development service configuration:

```powershell
$env:ENVIRONMENT = 'test'
$env:SUPPORTLENS_RUN_DB_TESTS = '1'
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -q
.\.venv\Scripts\python.exe -B -m pip --disable-pip-version-check check
.\.venv\Scripts\python.exe -B -m supportlens.ml.cli verify
.\.venv\Scripts\python.exe -B -m supportlens.backend.classification.evaluate
```

Full verification includes all prior 298 cases and 66 new classification cases (three live PostgreSQL), including trusted loading, corruption/compatibility/caching, strict result validation, input limits, deterministic reuse, safe failures, auth boundaries, transactional enqueue, lease recovery/fencing, audit rollback, idempotency/rate protection and real concurrent retry locking. The original ticket integration test now asserts the exact original nine plus two additive agent endpoints, replacing its obsolete count of nine; its lifecycle assertions are preserved.

Live tests require isolated `SupportLens_test` with certificate/hostname verification. Existing M2-C and new M4-A independent-session concurrency tests deliberately retain uniquely identified synthetic fixtures and history. Other transactional fixtures roll back. Do not clear tables. Console failure output can contain local infrastructure details; the local ignored runner captures/redacts it before review. On Windows, restricted socket creation can prevent TestClient/Playwright from starting; use the approved host test process rather than changing application code/TLS for that environment restriction.

In `frontend/`, run `npm run lint`, `npm run typecheck`, `npm test`, `npm exec -- prettier --check src tests *.ts *.mjs *.json`, production build and `npm run test:e2e`. The existing separate build-context/port configuration preserves the running production preview. Unit tests total 94; browser scenarios total 36 across 1440×1000, 900×1000 and 390×844. Browser scenarios use synthetic identities/data, axe checks and overflow/browser-error assertions. This does not certify manual screen-reader use or all browsers.

`evaluation/m4_a/integration_summary.json` is a reproducible aggregate of six explicitly synthetic invocation/mapping/determinism/truncation cases. Disabled-model and invalid-input outcomes are distinguished. It contains no private samples, labels from held-out data, operational identities or credentials. It measures integration correctness only. Database persistence and UI correctness are verified separately by PostgreSQL/browser/live checks. No training, held-out reevaluation, calibration or new accuracy claim is made.

## Verification record and acceptance

The M3 `main` checkpoint `c4064acdc6a37670bdcfc36fb67049a6ba3eff2e` was verified against the intended remote before editing. Fresh baseline checks passed 298 backend tests (12 live PostgreSQL, 65 M1), 74 frontend tests and 30 browser scenarios, with artifact/dependency integrity. Private evidence is retained under ignored `artifacts/m4_a/`; it is not part of the source checkpoint.

The final complete backend run passed **364 tests, zero failures/errors/skips**, including **all original 298 cases, all 15 live PostgreSQL cases and all 65 M1 cases**, in **2,387.53 seconds**, completed 2026-10-31. The final frontend checks passed **94 unit/component tests and 36 browser scenarios**, with zero failures/skips/flaky retries, plus lint, strict typecheck, formatting and isolated production build. A separate standard production build also passed for the running preview. Six focused new browser cases and 66 focused classification/backend cases passed earlier. Protected M1 hashes, gold/split/evaluation integrity, deterministic synthetic evaluation reproduction and Python dependency consistency passed.

Failed/interrupted attempts are retained and are not counted as passing evidence. One early run exposed the original health test's no-database-I/O requirement when Firebase project configuration is missing; worker startup was gated appropriately and the unchanged auth suite passed again. The obsolete agent endpoint count was replaced with the exact additive endpoint set as described above. A subsequent full run encountered six closed-connection PostgreSQL setup errors after an overnight interval; the external cause is unverified. Fresh read-only TLS/schema preflight passed without weakening verification, and the final fresh complete run passed all required cases.

### Live verification: 2026-10-31

Real Firebase Web SDK sessions verified the existing explicitly approved CUSTOMER and AGENT in the approved SupportLens project, with verified email and active database roles. No identity was created, promoted, deleted or otherwise changed. The production frontend, FastAPI and real database were used without mocked operational responses. No account screenshots, traces, token/password exports or persisted browser storage were produced; both sessions signed out with zero uncaught browser errors.

The CUSTOMER created one uniquely marked synthetic ticket and sent one public reply. The worker automatically published generation 1 from the trusted M1 artifact. The AGENT saw its model score, required review, provenance and limitations in the actual UI, then requested one retry after cooldown. Generation 2 completed; generation 1 remained unchanged in history. Status, priority, assignment and ticket timestamps were unchanged across retry. The real customer DTO, history and DOM contained no ML hash/version/run IDs or privileged fields; its UI made no classification requests and a direct agent-classification request returned 403. Customer history remained exactly two public events while agent history contained five events.

Retained M4-A development effects: **one synthetic ticket, one PUBLIC message, two SUCCEEDED classification runs and five audit events**. The original M3 ticket was not reused or mutated. A separate read-only database inspection verified both generations' model identity/hash, request source, actor/parent linkage, confidence, completion time/duration and the absence of duplicated raw ticket text or remaining lease credentials. The first source is TICKET_CREATION, the second AGENT_RETRY. Initial cold inference included model-loading cost; this check does not establish a latency SLA or semantic prediction quality.

The helpers journal every attempted mutation and never replay an uncertain creation/reply/retry. A local read-only checker initially used the Python event attribute instead of its SQL column name; that checker was corrected, with failed evidence preserved. A subsequent transient connection error was followed by a passing read-only check; no application, database or TLS setting was changed for either issue.

The production preview remains at `http://127.0.0.1:3000` (listener PID 10264), with backend at `http://127.0.0.1:8000` (listener PID 21748). Final frontend/health/readiness returned 200; anonymous `/me` and classification returned 401. The model-binary path returned 404 from both servers. Services were restarted only when required to load the tested M4-A code/production build and were left running afterward. These are local services, not a deployment.

### Final database preservation

Read-only post-verification inventory confirmed distinct `SupportLens_dev` (OID 16564) and `SupportLens_test` (OID 16567), verified client TLS with certificate/hostname verification, UTC, revision `20261027_0001`, zero SQLAlchemy schema differences and all five guard triggers in both databases. Every preexisting row hash from both the initial and post-baseline snapshots still matches. No tables/records were cleared, deleted, reset or downgraded; no migration was applied.

| Database | Users | Tickets | Messages | Audit events | AI runs |
|---|---:|---:|---:|---:|---:|
| Development before M4-A | 2 | 1 | 5 | 16 | 0 |
| Development final | 2 | 2 | 6 | 21 | 2 |
| Test before fresh baseline | 9 | 3 | 0 | 6 | 0 |
| Test after fresh baseline | 12 | 4 | 0 | 8 | 0 |
| Test final | 27 | 9 | 0 | 24 | 8 |

The development delta is exactly the single journaled live synthetic workflow. Test additions are retained synthetic concurrency fixtures across baseline, focused and full verification attempts: the M2-C fixture retains three users/one ticket/two events; each M4-A concurrency fixture retains three users/one ticket/two AI runs/four events. Two retained legacy ticket fixtures also have PENDING initial outbox rows because injected test runtimes intentionally do not launch application workers. They are isolated test records, not stuck development work. Other live fixtures roll back. Failed/interrupted verification attempts were never cleaned up destructively.

### Final acceptance matrix

| Gate | Status | Evidence |
|---|---|---|
| A — Repository safety | PASS | Pushed M3 checkpoint verified; protected files and all preexisting database rows preserved |
| B — Trusted inference | PASS | Pinned artifact/metadata/completion bytes, original M1 prediction parity, runtime compatibility and live trusted result |
| C — Ticket integration | PASS | Atomic pending enqueue, after-commit wake, failure isolation, real customer create/reply and unchanged ticket state |
| D — Persistence | PASS | Live generation/linkage/history verification, lease recovery/fencing, audit rollback and real concurrent retry tests |
| E — Authorization | PASS | Real active AGENT access, customer 403 and clean customer DTO/DOM; inactive/anonymous/unsafe retry tests |
| F — Agent UI | PASS | Real suggestion/retry/history; 94 units and 36 browser cases with responsive/accessibility checks and synthetic visual review |
| G — Model honesty | PASS | Raw uncalibrated scores, unvalidated advisory flag, seven-class/APP_TECHNICAL limitations, mandatory review and no automation |
| H — Testing | PASS | Final 364 backend / 15 live / 65 M1, 94 frontend, 36 browser; static/build/dependency consistency and integrity checks |
| I — Runtime | PASS | Real approved Firebase customer-to-classification-to-agent-retry workflow; healthy localhost services and HTTP artifact isolation |
| J — Git readiness | PASS | Exact 33-file manual scope, zero staged files, unchanged HEAD, no credential/private-data/generated-file matches |

The full npm advisory audit remains a documented development-toolchain finding below; these gate results do not assert a clean complete vulnerability audit. There are zero production-dependency findings in the separate production-only audit. Existing Starlette TestClient emits one deprecation warning about the pinned httpx adapter; dependencies were not changed to suppress it.

**M4-A acceptance is complete within the requested integration scope.** M4-B is technically unblocked after owner review and the safe manual checkpoint; the dependency-maintenance follow-up remains explicit. No M4-B work, commit, push or deployment was performed.

M4-B must wait for completed M4-A acceptance and the owner's review/manual Git checkpoint. No M4-B implementation is included.

### Dependency security follow-up

On 2026-10-31, the dependency tree and all 352 checked installed-package Node engine constraints passed. Python dependency consistency passed. The complete npm audit returned **14 moderate affected-package entries** propagated from one minimatch advisory, with zero high/critical findings. `npm audit --omit=dev` returned **zero vulnerabilities**. This is an outstanding development-toolchain finding, not a clean complete dependency audit. The two previously documented extraneous optional Wasm packages remain local/ignored.

The [maintainer advisory GHSA-g3hv-75wg-m249](https://github.com/isaacs/minimatch/security/advisories/GHSA-g3hv-75wg-m249) describes resource amplification for attacker-controlled glob patterns at optimization level 2 or above and identifies version 11.0.1 as patched. In this lockfile, the affected chains are development lint/OpenAPI tooling; no SupportLens ticket/API input is passed to glob matching. This exposure assessment is a code/dependency-graph inference, not a proof of arbitrary-tool safety. Avoid processing unreviewed glob configuration in those tools. A separate dependency-maintenance change should update compatible upstream chains and rerun the frontend checks. Do not apply npm's suggested major downgrades or a cross-major minimatch override without compatibility review. M4-A changed no manifest or lockfile.

## Exact safe manual checkpoint

After acceptance and owner review, use this explicit scope from the repository root. The first command previews staging. Remove `--dry-run` only when ready to stage manually, then inspect the staged names/diff before making a commit. No staging or commit is performed by this milestone run.

```powershell
$m4Paths = @(
  '.env.example',
  'docs/M4_A_CLASSIFICATION.md',
  'evaluation/m4_a/integration_summary.json',
  'src/supportlens/backend/api/app.py',
  'src/supportlens/backend/api/config.py',
  'src/supportlens/backend/tickets/schemas.py',
  'src/supportlens/backend/tickets/service.py',
  'src/supportlens/backend/classification/__init__.py',
  'src/supportlens/backend/classification/constants.py',
  'src/supportlens/backend/classification/evaluate.py',
  'src/supportlens/backend/classification/inference.py',
  'src/supportlens/backend/classification/queue.py',
  'src/supportlens/backend/classification/routes.py',
  'src/supportlens/backend/classification/schemas.py',
  'src/supportlens/backend/classification/service.py',
  'src/supportlens/backend/classification/worker.py',
  'tests/backend/test_classification_api.py',
  'tests/backend/test_classification_inference.py',
  'tests/backend/test_classification_postgresql_integration.py',
  'tests/backend/test_classification_worker.py',
  'tests/backend/test_tickets_postgresql_integration.py',
  'frontend/contracts/openapi.json',
  'frontend/src/app/workspace.css',
  'frontend/src/components/agent.tsx',
  'frontend/src/components/classification.tsx',
  'frontend/src/lib/api/client.ts',
  'frontend/src/lib/api/schema.d.ts',
  'frontend/src/lib/api/types.ts',
  'frontend/tests/classification-api.test.ts',
  'frontend/tests/classification-fixture.ts',
  'frontend/tests/classification.test.tsx',
  'frontend/tests/e2e/fixture.ts',
  'frontend/tests/e2e/workflows.spec.ts'
)
git add --dry-run -- @m4Paths
git diff --cached --name-only
git diff --cached --check
```

Keep `Decisions.md`, `frontend/AGENTS.md`, local environments/credentials, private datasets/models, dependency/build folders and all private `artifacts/` evidence/helpers out of staging. Avoid broad `git add .`. Suggested commit subject after approval: `feat(ml): integrate advisory ticket intent classification`.
