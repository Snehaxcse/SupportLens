# M4-E semantic response cache

The cache reuses general guidance through the existing draft worker. A ticket must first pass M4-D policy. The worker retrieves current knowledge, tries the response cache, and creates a new ticket-specific draft on a valid hit. A miss can reserve an eligible Gemini attempt through the existing Free Tier controls. Review, approval and explicit public send remain separate. Account access, security, billing, payment and other M4-D manual cases stay outside reuse.

## Storage and explicit approval

The additive revision `20261116_0003` creates `response_cache_entries` and `response_cache_evidence`. Entries hold a generated English issue phrase and category, a 384-dimensional MiniLM vector, reusable guidance, issue intent and risk, scope, model and policy versions, the source draft/version, the approving agent and request, expiry, decision version and hit accounting. Evidence rows bind the entry to immutable article revisions and chunk hashes. Knowledge vectors remain in their original table. The cache stores no source ticket text, private note, customer identifier or credential. The source draft FK supports internal auditing. Target projections contain the reuse entry reference and expiry, with no source-ticket ID or source draft text beyond the approved general guidance.

An active AGENT must first approve the exact ticket draft, then use **Approve for reuse** and explicitly confirm that the guidance is safe across tickets. The server validates the current draft version, its M4-D policy snapshot and workflow provenance, current evidence, and the finished extractive text. It accepts generally reusable `APPROVED_REFERENCE` sources and separately scoped, explicitly enabled fictional sources. It excludes internal-procedure sources from cross-ticket reuse. Scope, rights and source accuracy still require operator review. Customer-supplied fields cannot choose the text, expiry, model or authorization. Cached drafts can be edited and rejected on their own tickets. A cached derivative cannot be approved as a new reusable source.

The approval request is idempotent for the exact actor, source run and reviewed version. A partial unique index and short advisory lock reject active duplicates by scope, versions, issue category, content and evidence. Entries expire seven days after approval by default. `RESPONSE_CACHE_TTL_DAYS` accepts 1–30. Expiry is enforced at read time and an expired identical entry can be retired while approving a fresh source. Each source draft keeps its original reuse review history. An agent can revoke the current entry from that draft's review panel.

The current cache entry and its evidence are authoritative for reuse. The source run's approval metadata records provenance and cannot authorize a later hit on its own.

## Matching and freshness

The v1 English issue templates cover general playback interruption, app launch crashes and playlist sorting suggestions. The matcher accepts only known words and rejects negation, device or account qualifiers, other unrecognized detail, any prior public conversation, and M4-D ineligibility. Ticket text is never embedded into the response-cache store. The local encoder embeds generated canonical phrases using the same digest-pinned MiniLM identity as M4-B. Choice of phrase preserves safe paraphrase differences. Case-folding and bounds apply before the closed issue checks.

Candidates must share the intent, standard risk, issue category, scope, M4-D policy version, matching-policy version and embedding identity. The cosine floor is `0.90` and `RESPONSE_CACHE_MIN_SIMILARITY` can raise it. Every dependency must be present in the target ticket's current retrieved evidence with the exact revision and hash. All cached text must still consist of admissible complete source sentences. Conflicting qualifying guidance yields a miss. Exact cosine search scans at most 80 compatible entries per scope and issue and returns a capacity miss beyond that. The implementation rejects incomplete, malformed and nonfinite vectors.

Article replacement, archive or unpublication invalidates dependent cache entries in the same PostgreSQL transaction. An immutable cache-history guard and append-only dependency guard protect past approvals. Expiry and model/version compatibility are checked at lookup, draft creation, approval and send. Raising the similarity floor also disqualifies a cached draft with a lower recorded score. Reindexing can temporarily make the same immutable revision unavailable for reuse. Before showing a hit as a new draft, the worker rechecks the ticket policy, article locks, entry version and content. Review and send repeat these checks. Approval and delivery still use the existing ticket locks. Replay of an already recorded delivery returns the existing message, including after later invalidation.

## Concurrency and accounting

The existing draft lease fences restarts and stale workers. Cache candidate reading and embedding finish before the final short write transaction. Cache usage and ticket-specific draft/citations commit together. Knowledge locks are acquired before cache-entry locks. Revocation competes with the usage update and either wins first or prevents subsequent approval and send. The worker uses a short digest of the compatible issue and exact retrieved evidence to suppress overlapping eligible Gemini misses while an existing lease is valid. It returns a recoverable busy state, with no indefinite wait or sharing of unapproved output. External provider requests still run outside database transactions.

Draft attempts record safe lookup outcome and duration and counts of rejected and incompatible candidates. Entry counters include last hit, expiry and invalidation. Provider records distinguish daily reservations, outbound Gemini attempts and observed responses. Historical attempts lacking the new flags stay out of the observed-request counters. A bypass counter measures the draft path taken on hits. It does not estimate an external bill or tokens for a request that never occurred.

## API and runtime

| Method and agent path | Purpose |
| --- | --- |
| `POST /api/v1/agent/tickets/{ticket_id}/drafts/{run_id}/reuse` | Observe `expected_draft_version`, submit `request_id` and `confirm_general_reuse: true` |
| `POST /api/v1/agent/tickets/{ticket_id}/drafts/{run_id}/reuse/{cache_id}/revoke` | Revoke the observed `expected_version` with an idempotent `request_id` |
| `GET /api/v1/agent/response-cache/metrics` | Aggregate counts and lookup latency without source text or ticket identifiers |

Draft lists show origin, lookup hit/miss, expiry, and independent reuse approval status. Public customer routes are unchanged. Cache mutation routes require verified Firebase identity and active database-backed AGENT authorization. They scope source management to the exact original ticket and run. Synthetic reuse stays on the configured exact fictional ticket and requires the approved active test owner. The runtime has no automatic customer send.

`RESPONSE_CACHE_ENABLED` defaults to false. Apply the additive schema first to test and then development. Existing identity, server certificate, timeout, and no-production guards remain in force. A cache-enabled backend requires the new verified schema for readiness. Use the previously documented local startup commands with exact CORS origins. Keep Gemini billing disabled and use the existing provider settings and daily limit.

```powershell
.\.venv\Scripts\python.exe -B -m supportlens.backend.db.cli migrate-cache --target test
.\.venv\Scripts\python.exe -B -m supportlens.backend.db.cli migrate-cache --target development
.\.venv\Scripts\python.exe -B -m supportlens.backend.db.cli verify
.\.venv\Scripts\python.exe -B -m supportlens.backend.response_cache.evaluate --output evaluation/m4_e/results.json
$env:ENVIRONMENT = 'test'
$env:SUPPORTLENS_RUN_DB_TESTS = '1'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -B -m supportlens.ml.cli verify
.\.venv\Scripts\python.exe -B -m pip check
```

From the frontend directory, run `npm test`, `npm run typecheck`, `npm run lint`, `npm run build`, and `npm run test:e2e`. The guarded PostgreSQL tests check cross-customer source isolation, explicit send with replay, no provider budget on hits, eligibility changes, fallback and concurrent revocation. The concurrency scenario retains uniquely marked fictional audit records in the test database and unpublishes its source.

Verification covered 476 distinct backend cases, including 29 live PostgreSQL cases. The full run reported 473 passes and two failures from prior quota fixtures that assumed an unused Pacific-day budget. The corrected fixtures count preserved reservations before allocating the test budget, and both passed separately. The final offline run passed 447 cases, including the new sort-order regression. The stronger similarity-floor send check passed in its focused live run. Frontend verification passed 99 unit tests and 51 browser tests across desktop, tablet and mobile with no browser failures, skips or flakes. Typecheck, lint, formatting, production build, dependency consistency and M1 integrity passed.

## Controlled benchmark and rollout limits

The fixed fixture has 23 distinct scenarios and six repeated equivalent queries submitted concurrently. At `0.90`, MiniLM accepted 10 of 29 queries. All 10 were labeled safe. It accepted zero of 18 negative queries and missed one of 11 labeled safe queries. The single miss used an unsupported synonym. Among distinct scenarios it accepted 4 of 23 and missed one of five labeled safe requests. The threshold leaves a small margin under the lowest accepted equivalent pair (`0.90786`). Hard policy, evidence and context checks prevented equal-score cases from bypassing invalidation and safety gates.

The recorded median local latency was 232.002 ms for hits and 0.161 ms for misses. This includes concurrent probes and inexpensive policy or context rejections. It excludes PostgreSQL and provider round trips. The benchmark exercised 10 simulated generation bypasses, made zero Gemini calls and has no measured counterfactual token savings. The version, fixtures and each score/outcome are in [the benchmark results](../evaluation/m4_e/results.json).

The live smoke used a real Firebase CUSTOMER and AGENT, two fictional playlist tickets, the trusted M1 classifier, local MiniLM, and the development Neon database. It used the `0.18` advisory and policy floor only in the isolated verification processes to exercise the eligible branch. The default M4-D policy remains unchanged. A controlled provider response supplied one miss draft. Its first reuse review returned 409 because the privacy matcher treated “sort order” as a purchase reference. A targeted correction kept purchase-order text ineligible. The corrected approval used the existing draft after checking that the failed request created no entry. The second ticket hit with the provider disabled and daily limit zero, added one cache hit without a reservation, then required edit, approval and explicit send. Replay returned the same message. Customer draft and metrics calls returned 403, and the real customer UI showed just the public fictional reply. Unpublishing the source invalidated the reused entry. The smoke made zero Gemini API calls.

Final read-only Neon fingerprints matched all preexisting rows. Both databases retained verified TLS and UTC, installed `20261116_0003`, matched model metadata and passed all 12 guards. Development retained two fictional tickets, four AI runs, one internal note, one public reply, two citations, the unpublished source and one invalidated cache entry with one recorded hit. PostgreSQL confirmed the public reply matched the approved edit and the hit had zero provider reservations. The guarded test run retained five synthetic race tickets and their audit records, including the invalidated cache race entry. Localhost frontend, health and readiness returned 200 after restoring defaults. Anonymous cache metrics returned 401.

The benchmark covers specific synthetic failure modes. Live coverage, safe thresholds and token savings on representative approved Spotify support content remain unknown. Wider rollout requires approved source rights, representative safe and hard-negative cases, provider privacy review, operational monitoring, and production configuration review. The cache remains disabled by default. M5 can consume these results and contracts after product-owner approval.

## Exact staging preview

From the repository root, preview the reviewed paths. Keep credentials, local environments, model binaries, private datasets, browser/build output, runtime journals, `Decisions.md`, and `frontend/AGENTS.md` out of staging.

```powershell
$m4ePaths = @(
  '.env.example',
  'alembic/env.py',
  'alembic/versions/20261116_0003_response_cache.py',
  'docs/M4_E_SEMANTIC_CACHE.md',
  'evaluation/m4_e/fixtures.json',
  'evaluation/m4_e/results.json',
  'frontend/contracts/openapi.json',
  'frontend/src/components/draft-panel.tsx',
  'frontend/src/lib/api/client.ts',
  'frontend/src/lib/api/schema.d.ts',
  'frontend/tests/e2e/drafts.spec.ts',
  'src/supportlens/backend/api/app.py',
  'src/supportlens/backend/api/config.py',
  'src/supportlens/backend/api/database.py',
  'src/supportlens/backend/db/cli.py',
  'src/supportlens/backend/db/migrations.py',
  'src/supportlens/backend/drafts/provider.py',
  'src/supportlens/backend/drafts/schemas.py',
  'src/supportlens/backend/drafts/service.py',
  'src/supportlens/backend/drafts/worker.py',
  'src/supportlens/backend/knowledge/evaluate.py',
  'src/supportlens/backend/response_cache/__init__.py',
  'src/supportlens/backend/response_cache/evaluate.py',
  'src/supportlens/backend/response_cache/matching.py',
  'src/supportlens/backend/response_cache/models.py',
  'src/supportlens/backend/response_cache/routes.py',
  'src/supportlens/backend/response_cache/schemas.py',
  'src/supportlens/backend/response_cache/service.py',
  'src/supportlens/backend/tickets/schemas.py',
  'src/supportlens/backend/workflow/service.py',
  'tests/backend/test_drafts_postgresql_integration.py',
  'tests/backend/test_response_cache.py',
  'tests/backend/test_response_cache_postgresql_integration.py',
  'tests/backend/test_tickets_postgresql_integration.py',
  'tests/backend/test_workflow_postgresql_integration.py'
)
git --literal-pathspecs add --dry-run -- @m4ePaths
```

After review, manually stage and check exactly these paths.

```powershell
git --literal-pathspecs add -- @m4ePaths
git diff --cached --name-only
git diff --cached --check
```
