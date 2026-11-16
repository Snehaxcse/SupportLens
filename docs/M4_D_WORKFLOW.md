# M4-D AI support workflow

Tickets create an M4-A classification job in the existing customer ticket transaction. The agent workspace shows the classifier's advisory score and opens a workflow panel with the current policy, evidence availability and review status. The agent requests generation after checking eligibility. The existing M4-C worker retrieves evidence, checks policy again, reserves the provider attempt and calls Gemini outside the transaction. Approval records an internal decision. Public delivery requires a separate confirmation showing the exact approved text.

## Policy

The deterministic policy uses the current validated M4-A result, status and priority, public context and current knowledge. Its raw-score floor is the larger of `WORKFLOW_MIN_CONFIDENCE` (default `0.7`) and the run's M4-A advisory threshold. Neither score is calibrated or a production accuracy estimate. No classifier output permits public delivery by itself.

| Decision | Conditions | Agent action |
| --- | --- | --- |
| `NEEDS_AGENT` | Detected sensitive or injection content, high priority, resolved ticket, intent outside the allowlist | Review manually. Reopen a resolved ticket before sending. |
| `CLASSIFICATION_PENDING` | No classification or an active classification job | Check progress or request classification from the intent panel. Manual replies remain available. |
| `CLASSIFICATION_UNCERTAIN` | Failed or invalid classification, truncation, low score, or a public customer follow-up outside the classifier's subject and description input | Review the conversation and reply manually. |
| `INSUFFICIENT_EVIDENCE` | Retrieval found no complete, eligible source lines | Review or update the approved knowledge base, or reply manually. |
| `GENERATION_UNAVAILABLE` | Provider disabled, daily cap reached, quota exhaustion or bounded queue full | Keep the ticket in the existing agent inbox and use manual replies. |
| `AI_DRAFT_ELIGIBLE` | Allowlisted `APP_TECHNICAL`, `CONTENT`, or `FEATURE` suggestion with acceptable score, current context, usable evidence and generation capacity | Request a draft. |

The policy blocks account access, security, billing, payment and subscriptions even if the model reports a high score. AI routing never changes ticket status, priority or assignment. The intent model runs on the original subject and description. Any public customer follow-up routes the AI workflow to manual handling until a future classifier can evaluate that context. The policy treats public ticket text and excerpts as untrusted, using normalized content for text-risk checks. The existing extractive allowlist and mandatory review still apply. These checks do not quantify coverage against unseen sensitive topics or adversarial text.

The workflow GET previews current policy and retrieval with no provider call. The draft request transaction audits allowed or denied admission with versioned reasons and collapses repeated observations of the same decision. The worker stores the final outcome on the draft attempt, including no safe evidence, stale inputs or failed provider attempts. Browser status can become stale between reads. Every write uses the authoritative transaction checks.

## State and recovery

```text
Ticket created -> classification queued -> current classification result
Agent checks policy and evidence -> requests draft -> queued/running
Worker rechecks ticket, policy, budget and evidence -> draft / abstained / failed
Draft -> edit -> approve or reject
Approved version -> agent confirms the exact public text -> one public reply
```

The existing draft kind stores a versioned policy snapshot with the classification run ID, risk, intent and a digest of the ticket status, priority and recent public context. Recent public messages and internal notes remain separate. The worker checks the snapshot before reserving a provider attempt and before persisting a draft. Approval and send recheck it with current evidence. A changed classification, newly arrived public message, status or priority can invalidate approval. Current article publication, exact revision, chunk text and hash must also match. Older M4-C approvals without a policy snapshot remain visible as history. Generate and review a new attempt before using the M4-D send route.

The existing lease and fencing mechanism recovers unfinished jobs. A reserved attempt with an unknown outcome becomes interrupted instead of calling the provider again. The per-agent limit and ticket cooldown remain in effect. `WORKFLOW_DRAFT_QUEUE_LIMIT` defaults to 32 active jobs and is serialized by a PostgreSQL advisory lock at admission. Pending jobs become terminal if generation scope or configuration is disabled. The actual reservation enforces the database-wide Pacific-day generation limit and stops new reservations once a 429 is recorded. Manual support remains available throughout. MiniLM stays local. No paid fallback, billing change or new dependency is required.

## Agent API and public delivery

All routes require verified Firebase identity and an active database AGENT. Existing agent ticket scope applies.

| Route | Behavior |
| --- | --- |
| `GET /api/v1/agent/tickets/{ticket_id}/workflow` | Policy, reason, risk, classification ID and evidence count |
| `GET /api/v1/agent/tickets/{ticket_id}/drafts` | Existing attempts with additive send-candidate and sent-message fields |
| `POST /api/v1/agent/tickets/{ticket_id}/drafts` | Request generation with current policy admission |
| `POST /api/v1/agent/tickets/{ticket_id}/drafts/{run_id}/review` | Edit, reject or approve the observed draft version |
| `POST /api/v1/agent/tickets/{ticket_id}/drafts/{run_id}/send` | Send the stored approved text using `request_id`, `expected_version`, and `confirm_public_send: true` |

The send transaction rechecks the active agent, exact ticket and run, current approval, policy snapshot and evidence. It uses the existing ticket message service, including its lifecycle and audit rules. The ticket and draft row locks serialize contenders. One transaction inserts the normal public reply and `AGENT_REPLY` event, persists its message identity on the draft, and adds the private `DRAFT_SENT` event. The same actor, request UUID and version replay the prior message. Competing keys or actors get a conflict. The request has no customer body or visibility override. The public message API and customer projection remain unchanged. Customers see the published message and public timeline only.

The existing `ai_runs`, immutable citations, tickets, messages, and events are sufficient. There is no migration. Regular send requires reviewed context configuration. Synthetic send also requires the exact `GEMINI_SYNTHETIC_TICKET_ID` and an active ticket owner matching the explicitly approved `WORKFLOW_SYNTHETIC_CUSTOMER_UID`. Keep those blank by default and enable synthetic retrieval only in isolated local verification. Keep `GEMINI_FREE_TIER_CONFIRMED=false` for any controlled-provider harness. Use the existing M4-C privacy and source approval process before enabling regular generation.

## Evaluation and local checks

```powershell
$env:ENVIRONMENT = 'test'
$env:SUPPORTLENS_RUN_DB_TESTS = '1'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -B -m supportlens.ml.cli verify
.\.venv\Scripts\python.exe -B -m pip check
```

Run `npm run typecheck`, `npm run lint`, `npm test`, `npm run build`, and `npm run test:e2e` from the frontend directory. Use the [M4-C local configuration](M4_C_GROUNDED_RAG.md) for normal startup. The synthetic scenarios cover eligibility and uncertainty, injection in ticket content and retrieval evidence, billing and security, quota, provider failures, stale revisions, review, public send, replay, cross-ticket access, competing agents and lease recovery. The isolated multi-agent race retains its uniquely marked fictional records in `SupportLens_test` for audit and unpublishes its source. Other new PostgreSQL scenarios roll back their fixtures. Test results measure system behavior on synthetic inputs.

The localhost verification used real Firebase AGENT and CUSTOMER identities, the trusted M1 classifier, Neon development, local MiniLM and one controlled generation response. It made zero Gemini network requests. The test-only process used a `0.18` raw-score floor for the exact fictional ticket so its `APP_TECHNICAL` score of approximately `0.203` could exercise the eligible branch. The default floor remains `0.7`. The agent edited the draft, approved it without sending, confirmed the exact text and created one reply. Replaying the same request returned the original message. The customer saw that public reply and received 403 for the internal workflow and draft endpoints. The source was unpublished and the preview backend returned to safe defaults. Production quality or default-policy coverage cannot be estimated from this check.

The backend verified 447 distinct cases, including 26 guarded PostgreSQL cases. The full run recorded 446 passes and one failure in the earlier lease-expiry fixture. That fixture mutated a JSON object in place, so it did not persist the simulated expiration. The corrected fixture passed independently and also checked rejection after a new article revision replaced the cited one. The offline rerun on the final source passed all 421 cases. The PostgreSQL send race produced one public reply and one `DRAFT_SENT` event across two agents.

The final frontend verification passed 99 unit tests and 48 Playwright tests across desktop, tablet and mobile. Typecheck, lint, production build and formatting checks passed. The unit suite required a rerun after local worker startup and test timeouts during concurrent build and browser work. The live browser check resumed after confirming the original test writes and used a longer UI wait without repeating those writes.

Final read-only Neon fingerprints matched all preexisting rows in development and test. Both databases retained revision `20261114_0002`, all nine guards, verified TLS and zero model metadata differences. The development smoke retained one fictional ticket, two AI runs, one internal note, one public reply, one citation, the unpublished fictional article with its index, and the corresponding audit events. PostgreSQL confirmed that the sent public text exactly matched the approved edit. The guarded test run retained uniquely marked synthetic race fixtures, including three tickets across the existing and new race scenarios.

## M4-E integration points

The existing scrubbed issue normalization and fingerprint, the new policy snapshot and final outcome, and the approved draft body provide internal inputs for a future eligibility policy. The approval version and actor, immutable evidence revisions, prompt and model versions, embedding identity, provider attempts and timestamps remain available in the draft record. Ticket and knowledge lifecycle events can signal reevaluation. Cache eligibility remains false after approval and delivery. A future cache must separately invalidate evidence and context and explicitly approve cross-ticket reuse. No lookup, storage or reuse ships with M4-D.

## Exact staging preview

Run from the repository root after reviewing the diffs. The allowlist excludes local environments and credentials, retained journals, datasets, model binaries, build and browser output, `Decisions.md`, and `frontend/AGENTS.md`.

```powershell
$m4dPaths = @(
  '.env.example',
  'docs/M4_D_WORKFLOW.md',
  'frontend/contracts/openapi.json',
  'frontend/src/components/agent.tsx',
  'frontend/src/components/draft-panel.tsx',
  'frontend/src/lib/api/client.ts',
  'frontend/src/lib/api/schema.d.ts',
  'frontend/tests/e2e/drafts.spec.ts',
  'src/supportlens/backend/api/app.py',
  'src/supportlens/backend/api/config.py',
  'src/supportlens/backend/classification/service.py',
  'src/supportlens/backend/drafts/grounding.py',
  'src/supportlens/backend/drafts/routes.py',
  'src/supportlens/backend/drafts/schemas.py',
  'src/supportlens/backend/drafts/service.py',
  'src/supportlens/backend/drafts/worker.py',
  'src/supportlens/backend/tickets/schemas.py',
  'src/supportlens/backend/workflow/__init__.py',
  'src/supportlens/backend/workflow/policy.py',
  'src/supportlens/backend/workflow/service.py',
  'tests/backend/test_tickets_postgresql_integration.py',
  'tests/backend/test_workflow.py',
  'tests/backend/test_workflow_postgresql_integration.py'
)
git --literal-pathspecs add --dry-run -- @m4dPaths
```

After previewing the allowlist, manually stage and check those paths.

```powershell
git --literal-pathspecs add -- @m4dPaths
git diff --cached --name-only
git diff --cached --check
```
