# SupportLens M2-C: ticket management backend

M2-C adds customer and agent ticket workflows to the existing FastAPI factory. It reuses Firebase verification, database-backed roles, the guarded PostgreSQL session infrastructure and the original seven-table schema. M2-B checkpoint `078b3d5` was verified committed and present on its configured remote before implementation. There are no frontend, RAG, Gemini, external Spotify actions or deployment changes.

## Architecture and contracts

`tickets/routes.py` handles HTTP schemas and existing role dependencies. `tickets/service.py` owns transaction boundaries and database operations. Explicit Pydantic DTOs in `tickets/schemas.py` define separate customer/agent projections. The service returns a successful result only after its owning transaction commits. No raw ORM object is returned to a customer.

The application factory registers the ticket routers and permits PATCH in the existing explicit-origin CORS policy. Existing authentication/onboarding/health routes remain available. Model loading stays independent of startup. Ticket requests never run ML or automated customer-account operations.

All `/api/v1` operations require a Firebase ID token in `Authorization: Bearer <ID_TOKEN>`. The SDK checks token validity/revocation; roles and activation come from PostgreSQL. Ticket mutations recheck the actor's role/activation under a shared row lock, then lock the ticket for update. This prevents an in-flight mutation from using an actor whose permissions changed concurrently.

## API inventory

All ticket IDs and assignment IDs are UUIDs. Unknown request fields are rejected. Missing/invalid credentials return 401, wrong roles/inactive accounts 403, and a missing or other customer's ticket 404 with the same `ticket_not_found` response.

| Method | Route | Request / response |
|---|---|---|
| POST | `/api/v1/tickets` | `{subject, description}` -> 201 customer ticket summary |
| GET | `/api/v1/tickets` | `page`, `page_size` -> own ticket page |
| GET | `/api/v1/tickets/{ticket_id}` | `messages_page`, `history_page`, `page_size` -> description, public messages/history |
| POST | `/api/v1/tickets/{ticket_id}/messages` | `{body}` -> 201 public message |
| POST | `/api/v1/tickets/{ticket_id}/reopen` | Empty body or `{}` -> reopened customer ticket |
| GET | `/api/v1/agent/tickets` | Inbox filters below -> agent ticket page |
| GET | `/api/v1/agent/tickets/{ticket_id}` | `messages_page`, `events_page`, `page_size` -> full agent detail |
| POST | `/api/v1/agent/tickets/{ticket_id}/claim` | Empty body or `{}` -> assigned agent ticket |
| PATCH | `/api/v1/agent/tickets/{ticket_id}/assignment` | `{assigned_agent_id: UUID or null}` -> agent ticket |
| PATCH | `/api/v1/agent/tickets/{ticket_id}/status` | `{status}` -> agent ticket |
| PATCH | `/api/v1/agent/tickets/{ticket_id}/priority` | `{priority}` -> agent ticket |
| POST | `/api/v1/agent/tickets/{ticket_id}/messages` | `{body, visibility?: PUBLIC or INTERNAL}` -> 201 agent message |
| GET | `/api/v1/agent/tickets/{ticket_id}/events` | `page`, `page_size` -> full authorized event page |
| GET | `/api/v1/agent/users` | `page`, `page_size` -> active agents, only UUID/display name |

Customer ticket summaries contain UUID, subject, status, priority and timestamps. Agent summaries additionally contain customer/assigned-agent UUIDs. Details add the initial description and bounded conversation/history pages. The description is the initial request; it is not duplicated into a separate first message.

Subject length is 1–200 characters, description/message length 1–20,000. Leading/trailing whitespace is trimmed; whitespace-only text and unsafe control characters are rejected. Text is stored/returned as plain text. A future frontend must escape it when rendering. Customers cannot supply owner, author, assignment, status, priority, timestamps, message visibility or event metadata.

Pages use `{items, total, page, page_size}`. Page numbers are 1–10,000; default page size is 25 and maximum 100. Empty/out-of-range pages return an empty item list with the filtered total. Messages/events sort by ascending `(created_at, id)`. Ticket lists use descending `(created_at, id)` by default; agents can request oldest first. UUIDs break timestamp ties deterministically. Public totals exclude hidden internal content.

Examples, using synthetic content only:

```json
{"subject":"Playback stops on my test device","description":"Synthetic reproduction steps for a support request."}
```

```json
{"body":"Synthetic agent troubleshooting response","visibility":"PUBLIC"}
```

```json
{"body":"Synthetic internal investigation note","visibility":"INTERNAL"}
```

## RBAC and confidentiality

| Operation | Active CUSTOMER | Active AGENT |
|---|---|---|
| Create customer ticket | Own identity only | Denied |
| Customer list/detail/reply/reopen | Own tickets only | Denied on customer routes |
| Agent inbox/detail/events | Denied | All customer tickets |
| Claim/assign/status/priority | Denied | Allowed |
| Internal notes | Denied | Allowed |
| Active-agent directory | Denied | Allowed |
| Change account roles | No HTTP route | No HTTP route |

Customers receive PUBLIC messages only. Their author projection contains a display name and historical participant kind (ticket owner versus agent), without author UUID/email. The customer history projection contains only creation, public replies, lifecycle and priority changes. It excludes internal-note and assignment events, actor UUIDs and raw metadata. Filtering happens in SQL before counts/pagination; DTO validation additionally rejects an INTERNAL message in a customer response. Internal notes do not change the customer-visible ticket `updated_at` timestamp.

Agents receive authorized message visibility/author IDs and full ticket events. Inactive assignees are excluded from assignment selection. Agent directory responses omit email/Firebase UID and are paginated. No display name, email domain or token custom claim grants ticket permissions.

## Lifecycle and assignment

The existing approved transitions are unchanged:

```text
OPEN -> IN_PROGRESS or RESOLVED
IN_PROGRESS -> RESOLVED
RESOLVED -> REOPENED
REOPENED -> IN_PROGRESS or RESOLVED
```

Resolving sets `resolved_at` using PostgreSQL's clock; reopening clears it. Invalid/repeated status transitions return 409 `invalid_transition`. Customers may reopen their own resolved tickets without an invented time window. Viewing, claiming, assigning or replying never silently changes status.

All PUBLIC replies to a resolved ticket are rejected with 409 `ticket_resolved`; reopen first. Agents may add INTERNAL notes to resolved tickets. Reopening retains priority/assignment. Attachments, edits and deletion APIs are outside M2-C.

Any active agent may manage any ticket. Claim assigns the calling agent only if currently unassigned; an assigned ticket returns 409 `ticket_already_assigned`, including an attempted duplicate claim by its current agent. Claim does not automatically move OPEN to IN_PROGRESS. Assignment PATCH accepts a specific active AGENT or explicit null to unassign. A missing/inactive/customer target returns 422 `invalid_assignee`. Assignment/priority PATCH to the existing value is an idempotent no-op without a duplicate event.

## Inbox and query safety

The agent inbox supports `status`, `priority`, `assigned_agent_id`, `unassigned_only`, `search`, `order`, `page` and `page_size`. `order` is `newest` or `oldest`. An assignment ID and `unassigned_only=true` cannot be combined. Unknown filters and invalid enum values are rejected.

Search performs parameter-bound, case-insensitive literal subject matching and exact UUID lookup. `%` and `_` are escaped as literal subject characters; no SQL, column name or ordering expression comes from the client. There is no external search service. Existing customer/time and agent/status indexes are reused; no speculative indexes were added.

## Atomicity, concurrency and audit semantics

Every important mutation inserts its event in the same transaction as its ticket/message change. Actor/assignee permissions are rechecked under shared user locks; ticket mutations use PostgreSQL `SELECT FOR UPDATE`. Competing claims therefore wait on the same ticket row. After one transaction commits its assignment/event, the other sees the assigned row and returns 409. No application-memory lock is used to protect production claims.

| Action | Existing stored event type | Recorded data |
|---|---|---|
| Ticket creation | `TICKET_CREATED` | Initial OPEN/NORMAL state |
| Customer/agent public reply | `REPLY_ADDED` | Message UUID, PUBLIC visibility, semantic action |
| Internal note | `REPLY_ADDED` | Message UUID, INTERNAL visibility, semantic action |
| Assignment/unassignment | `ASSIGNMENT_CHANGED` | Before/after assigned-agent UUIDs |
| Status/reopening | `STATUS_CHANGED` | Before/after status and semantic action |
| Priority | `STATUS_CHANGED` | Explicit `PRIORITY_CHANGED` action and before/after priority |

The schema's existing state-change category is reused for priority; the explicit action and field-specific before/after values distinguish it from lifecycle transitions. This avoids changing frozen enums/constraints or weakening guards. Each event stores the actual actor and PostgreSQL operation timestamp. Message bodies, email addresses, credentials and tokens are not copied into event metadata. Clients cannot create arbitrary events. The existing append-only trigger and restrictive foreign keys remain unchanged.

The error envelope remains `{error: {code, message}, request_id}`. Other stable mutation errors include 409 `ticket_conflict` for integrity conflicts, 409 `retry_conflict` for retryable concurrency failures, and 503 for unavailable dependencies. Failed writes/events roll back together; no successful HTTP response is produced before commit. There is no automatic retry of a potentially ambiguous committed mutation.

## Database and protected data

No schema, migration, dependency, model, enum or guard change is required. Both databases retain revision `20261027_0001`. Production selection remains refused by the existing infrastructure. Automated writes target only verified `SupportLens_test`.

The persistent owner-approved development CUSTOMER must never be modified by these tests. Verification records a hash of its complete row and development table counts before/after; its email/profile is never printed. Real Firebase runtime regression uses only a read-only `/api/v1/me` request for that identity. Ticket workflow actors are synthetic test-database users with a test-only identity verifier.

Most integration fixtures use savepoints and an outer rollback. The committed-winner claim test necessarily saves one uniquely marked synthetic fixture so two independent PostgreSQL transactions can see the same ticket. It retains three synthetic users, one ticket and the creation/assignment audit events in the dedicated test database. These are not real Firebase accounts or seeded login credentials. It never deletes audit history or clears unrelated data. Each full test execution creates a fresh isolated marker; retained fixtures are recorded under ignored `artifacts/m2_c/` and reported explicitly.

## Testing and local runtime

```powershell
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -m 'not db_integration' -q
.\.venv\Scripts\python.exe -B -m pip --disable-pip-version-check check
.\.venv\Scripts\python.exe -B -m supportlens.ml.cli verify
```

For the complete suite, use a dedicated shell with the existing approved private configuration:

```powershell
$env:ENVIRONMENT = 'test'
$env:SUPPORTLENS_RUN_DB_TESTS = '1'
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -q
```

Close that dedicated shell afterward. Missing opt-in skips live cases by design; report them, never count them as passed. Full milestone verification explicitly enables all live cases. Tests cover route authentication/RBAC, field allowlists, ownership, internal-note confidentiality, inactive users, assignment validation, every allowed transition, resolved-reply rejection, filtering, literal search, timestamp-tied pagination, audit rollback and a committed-winner PostgreSQL claim race.

One lifecycle test starts an isolated real TCP Uvicorn server on a free loopback port, uses the actual API/services against guarded PostgreSQL fixtures, and stops the server in `finally`. Synthetic identity verification is injected only into this test runtime. Real Firebase verification/readiness is checked separately through the ordinary factory without changing the protected customer.

Normal local startup:

```powershell
.\.venv\Scripts\python.exe -B -m uvicorn supportlens.backend.api.app:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

`/health` is process-only; `/ready` checks configured Firebase credential initialization and read-only database identity/schema readiness. A real ID token is still required to prove authentication. Keep Uvicorn access logs disabled: application logs contain only request IDs, method, route template and status.

`app.openapi()` generates the complete schema, including bearer authentication, bounded query parameters, allowlisted DTOs and the actual JSON error envelope. Public `/openapi.json`, `/docs` and `/redoc` remain disabled to preserve M2-B's public surface. A private generated contract is retained with verification artifacts. No debug/token endpoint is added.

## Known limitations and safe checkpoint

Offset pagination is deterministic for a fixed dataset; concurrent inserts can shift page boundaries. Search uses literal subject matching/exact UUID rather than full-text search. Larger datasets may justify measured indexes or cursor pagination later. Public replies require explicit reopening; there are no attachments, deletions, optimistic version tokens or real Spotify actions. Existing administrative role-provisioning journal limitations remain as documented in M2-B.

Review only these explicit paths; private configuration, datasets, generated models, runtime logs, synthetic-fixture records and `Decisions.md` are excluded:

```powershell
git add --dry-run -- src/supportlens/backend/api/app.py src/supportlens/backend/tickets tests/backend/test_tickets_api.py tests/backend/test_tickets_postgresql_integration.py docs/M2_C_TICKETS.md
```

After owner review, stage those exact paths and inspect `git diff --cached --check`, `--stat` and `--name-only`. Suggested commit: `feat(tickets): add secure customer and agent ticket workflows`. No commit or push is automatic. M3/frontend work requires a separate owner instruction.

## Final verification: 2026-10-29

M2-C is complete. The final explicitly opted-in suite passed **298 tests, zero failures, zero skips**: all 213 original M1/M2-A/M2-B cases and 85 new ticket cases. Twelve live PostgreSQL cases ran, including the real TCP ticket lifecycle, inbox/pagination, audit-failure rollback and committed-winner claim race. Dependency consistency and M1 artifact verification passed. All 48 protected M1 hashes are unchanged. Existing authentication, database contracts, enums, guards, migrations, dependencies and tests are unchanged; the API factory is the sole modified existing source integration point.

The real Firebase regression against the normal localhost backend returned health/readiness 200, unauthenticated `/me` 401, approved authenticated `/me` 200 and invalid-token 401. It performed no development writes. The normal server at `http://127.0.0.1:8000` was stopped after verification. The isolated synthetic TCP ticket server also stopped successfully. The generated OpenAPI contract contains 18 operations (14 new ticket operations plus four existing operations); public schema/debug endpoints remain disabled.

Post-test read-only comparison confirmed the protected development customer's complete-row hash and every development table count match the baseline exactly: one existing user, no tickets or other application records. The dedicated test database retains only the one documented claim fixture: three synthetic users, one ticket and two events. Real PostgreSQL lock blocking was observed; the winning claim committed, the second agent received 409 and exactly one assignment event persisted. No audit history was deleted and no database was reset. Both databases retain verified client TLS/UTC, distinct identities and revision `20261027_0001`.

| Gate | Result | Concrete evidence |
|---|---|---|
| A: Repository safety | PASS | M1 hashes, existing auth/DB contracts/tests and development customer preserved |
| B: Customer workflows | PASS | Create/list/detail/public reply/reopen over real HTTP and PostgreSQL |
| C: Agent workflows | PASS | Inbox/filter/claim/assignment/status/priority/reply/notes/events/directory verified |
| D: Authorization | PASS | Ownership 404s, role boundaries, inactive accounts, field allowlists and note confidentiality |
| E: Data integrity | PASS | All tested audit failures roll back state; committed winner/loser race verified |
| F: API quality | PASS | Explicit DTOs, bounded/timestamp-tied pagination, stable errors and generated bearer-aware contract |
| G: Testing | PASS | 298 passed, 12 live PostgreSQL cases, zero failures/skips |
| H: Runtime | PASS | Normal Firebase-backed readiness/auth plus isolated real TCP ticket lifecycle; servers stopped |
| I: Git readiness | PASS | Explicit eight-file dry run; no secrets tracked/staged; no automatic commit/push |

Private evidence is under ignored `artifacts/m2_c/`: `verification.json`, `full_tests.json`, `full_tests_redacted.txt`, `runtime_ticket_lifecycle.json`, `real_firebase_runtime.json`, `concurrent_claim_result.json`, `retained_concurrency_fixtures.jsonl`, `database_before.json`, `database_after.json`, `openapi.json`, `route_inventory.json`, `server_shutdown.json` and `safety.json`. No M2-C blockers remain. M3/frontend work was not started.
