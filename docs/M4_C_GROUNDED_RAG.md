# M4-C Grounded Gemini drafts

Agents can request an AI draft from an authorized ticket. The draft panel handles processing, missing evidence, provider failures, edits, rejection and explicit internal approval. The public-message composer remains the separate manual sending workflow.

## Provider and ₹0 development configuration

SupportLens calls the official Gemini Developer API `generateContent` REST endpoint through pinned HTTPX. Google lists standard text input and output for `gemini-3.1-flash-lite` on the Free Tier, and this is the only model the application allowlists. The application uses an API key in a header and a fixed HTTPS origin. It does not discover another credential or provider automatically. A configuration change to the model must stay within the reviewed allowlist. [Pricing](https://aistudio.google.com/docs/pricing#gemini-3.1-flash-lite) · [Gemini REST reference](https://aistudio.google.com/api-reference/generate-content)

1. Create or select a Gemini Developer API Free Tier project in Google AI Studio. Keep billing disabled. Check that status before enabling the integration. The Gemini API key does not prove a project's billing state.
2. Set `GEMINI_API_KEY` in the ignored server root `.env`. Never expose it in the browser environment, a URL, a test fixture, or logs.
3. Set `GEMINI_FREE_TIER_CONFIRMED=true` only for the verified project. Set `GEMINI_SYNTHETIC_TICKET_ID` to the exact approved fictional development ticket and enable `KNOWLEDGE_ALLOW_SYNTHETIC` during the isolated lifecycle check. Revert those flags afterward. Keep synthetic source bodies clearly labeled and unpublish them after verification.
4. `GEMINI_DAILY_LIMIT` defaults to 8 attempts and accepts 0–20. Zero disables new runs. Keep it within the account's actual quota. `GEMINI_TIMEOUT_SECONDS` defaults to 20 seconds and `DRAFT_POLL_SECONDS` defaults to 30. Production connections are denied by the established database environment guard. The application factory disables real Gemini in the test environment.

The budget guard counts durable attempt reservations in the development database by Pacific calendar day, including attempts from previously queued runs. The lock and count are shared by all workers on that database. Calls from other apps sharing the same key or project are outside this local cap. Once a provider 429 is recorded, the app suppresses further reservations through the same database until the next Pacific day. The free-tier project setting is what prevents paid calls. The client uses standard generation without tools, search grounding, context caching, service-tier overrides or paid fallback. [Billing](https://aistudio.google.com/docs/billing) · [Rate limits](https://aistudio.google.com/docs/rate-limits)

Free-tier data terms can include product improvement. Keep `GEMINI_ALLOW_REVIEWED_CONTEXT=false` unless the owner has reviewed customer privacy, provider terms, source approval and product disclosure. That option admits eligible real development tickets only when the synthetic ticket scope is unset. Redaction is best effort and does not make arbitrary user text anonymous. [Google Gemini API terms](https://ai.google.dev/gemini-api/terms)

## Knowledge approval before regular use

1. Have the product owner identify first-party guidance or an authorized internal procedure and verify its current accuracy, reuse rights and suitability for provider processing. Historical support conversations and synthetic fixtures are excluded from this approval process.
2. Record the source owner, reference, rights and approval through the M4-B knowledge workflow. Use an `APPROVED_REFERENCE` or `INTERNAL_PROCEDURE` draft and review the exact revision before publication.
3. Publish the reviewed revision and confirm indexing. Check representative queries for retrieval relevance, safe coverage and abstention. Unpublish outdated guidance and review any replacement as a new revision.
4. Review the resulting exposure of customer ticket text and source excerpts under the provider terms before enabling reviewed context. The existing manual reply workflow covers insufficient evidence and sensitive cases.

## Context, evidence and output

The worker rechecks the initiating agent and exact ticket. It reads the bounded subject, description and up to two recent public messages. Internal notes never enter the request. Known sensitive topics and common prompt injection patterns trigger manual handling before the provider is called. The worker scrubs common contact details, URLs, long unique-looking tokens and numbers from the bounded provider context. It uses the existing M4-B hybrid retrieval service, including its lexical fallback. No M4-A output gates access to knowledge.

Eligible evidence must come from a current, published, indexed revision. Synthetic scope uses synthetic sources only. Ordinary scope excludes synthetic sources. The versioned system message describes the trust boundary and separate JSON fields carry scrubbed ticket context and candidate evidence sentences. The request uses JSON structured output with temperature 0, one candidate and 512 output tokens. It sends a bounded allowlist of opaque sentence IDs with complete literal source sentences. Gemini selects IDs or abstains/escalates. The server validates the schema and IDs, assembles the text from the immutable excerpts, and rejects unsafe phrases or unsupported references. Untrusted provider text never goes directly into the draft. A successful draft still needs agent review for relevance, completeness and policy.

The worker checks evidence immediately before reserving a call and again before persisting a draft. The approval transaction locks the relevant catalog entries and rechecks their publication state, revision, chunk, excerpt and hash. Agent edits can reorder or remove complete source lines while retaining the fixed opening. Freeform guidance belongs in the manual reply composer. Approval records an internal decision. There is no approval-to-send or copy-to-composer operation. Historical source links remain accessible to agents after unpublication.

## Persistence and recovery

M4-C uses the existing `ai_runs`, `knowledge_citations`, and append-only `ticket_events` tables. It requires no migration. Draft rows use a versioned kind distinct from M4-A classification. Store the requested model, prompt version, retrieval method, immutable citations, original and edited text, decision/version/actor, timestamps, safe failure category, and per-attempt latency and token counts. The worker records only a fingerprint of normalized issue context. Credentials, raw ticket context, raw provider replies, and provider error bodies are excluded.

An observed prior run ID and request UUID, protected by a ticket row lock, prevent duplicate generation and conflicting active runs. Review uses the same ticket lock, an observed version and a request UUID. The API allows three new runs per agent per minute and a 15-second per-ticket cooldown. The worker commits its lease before retrieving or calling Gemini. Each call has a separate committed daily reservation and at most two attempts per run. Temporary server or connect-timeout failures allow bounded retry with backoff. Quota, rejected requests and ambiguous transport failures terminate safely. An expired lease is recoverable before a provider attempt, and an unknown in-flight outcome is closed as interrupted instead of replayed automatically. A later worker cannot publish with the old lease token. A failed HTTP call never makes a synthetic provider result.

## Agent API

| Operation | Endpoint |
| --- | --- |
| Read attempts | `GET /api/v1/agent/tickets/{ticket_id}/drafts?page=1` |
| Generate or regenerate | `POST /api/v1/agent/tickets/{ticket_id}/drafts` |
| Edit, approve, reject | `POST /api/v1/agent/tickets/{ticket_id}/drafts/{run_id}/review` |

All routes use verified Firebase identity and active database AGENT authorization, scope the run to the ticket, and return strict projections. Customer DTOs and public audit history exclude draft information. Provider configuration is optional and health/readiness remain independent of provider availability. Existing public and internal note operations are separate.

## Testing and limitations

Deterministic evaluations check valid grounded text, invented IDs and fields, sensitive scenarios, prompt injection, unsupported statements, missing evidence, disabled provider, quota, provider errors and retry policies. PostgreSQL integration tests use a controlled provider with real publication, chunk, citation, review, quota and lease persistence. Browser fixtures test the internal approval interaction and confidentiality. A separate live run uses real Firebase, Neon and Gemini on fictional data. No representative approved Spotify corpus or quality benchmark was supplied. The regex checks are conservative filters and do not detect every adversarial or sensitive formulation. Extractive grounding and agent review are separate controls. Exact excerpts can still be irrelevant or misleading when their source is inaccurate or context is incomplete.

The live smoke used one `gemini-3.1-flash-lite` call and returned 274 tokens in provider usage, with 243 prompt and 31 candidate tokens. PostgreSQL verified one exact article revision and chunk hash, the requested/completed/edited/approved audit trail and `cache_eligible=false`. Gemini selected a single evidence line. The live browser saved the edit action with that line retained and confirmed approval. The controlled browser case exercised removal of one line from a two-line draft. The existing ticket's public message count stayed the same. The signed-in customer received 403 on the draft route and had no draft panel. The article was returned to draft and the localhost backend was restored with new generation disabled.

The full backend run passed 416 cases. A focused rerun passed all 28 draft cases, including two PostgreSQL cases added after the full run collected, for 418 unique passing backend cases across the runs. The draft suite covers controlled provider responses, late-queued quota reservations, expired lease fencing and stale evidence. The frontend suite passed 99 tests. Playwright passed 48 cases across desktop, tablet and mobile, including edit, explicit approval, keyboard and accessibility checks, and the customer boundary. These checks describe system behavior on synthetic inputs.

The post-test read-only verification confirmed unchanged fingerprints for all preexisting development and test rows. Both databases still had revision `20261114_0002`, matching model metadata, all nine guards and verified TLS. The retained development additions are the single synthetic draft with one citation and four ticket audit events, plus its now-unpublished source, index data and three lifecycle events. The regression suite retained the existing synthetic race fixtures in the test database. The three new draft integration cases rolled back their fixtures.

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q tests/backend/test_drafts.py
$env:ENVIRONMENT = 'test'
$env:SUPPORTLENS_RUN_DB_TESTS = '1'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -B -m supportlens.ml.cli verify
.\.venv\Scripts\python.exe -B -m pip check
```

Use the existing [M4-B local startup instructions](M4_B_RETRIEVAL.md) and run `npm run lint`, `npm run typecheck`, `npm test`, `npm run build` and `npm run test:e2e` from the frontend directory. Start the backend from the repository root with the exact existing CORS origins.

## M4-D and M4-E contracts

M4-D can consume safe failure classes, mandatory review, active source references and the provenance/decision state. Any future send integration must independently revalidate authorization, current evidence, approved version, customer context and explicit send intent, and use the existing public-message service.

M4-E can use retrieval embedding identity, prompt and model versions, immutable evidence references, generation time, issue fingerprint, safety classification and review status. The fingerprint is versioned by this run kind and is not an embedding or a reuse decision. Every draft persists `cache_eligible=false`, including an approved one. Future cache eligibility needs sanitized reusable guidance, explicit separate approval, independent context and sensitivity checks, version and evidence validation, and measured false-positive policy. It must use the separate response index documented in M4-B.

## Exact staging preview

Run from the repository root after reviewing the results. Keep private environments, provider keys, model binaries, datasets, build output, verification journals, `Decisions.md` and `frontend/AGENTS.md` out of staging.

```powershell
$m4cPaths = @(
  '.env.example',
  'docs/M4_C_GROUNDED_RAG.md',
  'frontend/contracts/openapi.json',
  'frontend/src/components/agent.tsx',
  'frontend/src/components/draft-panel.tsx',
  'frontend/src/lib/api/client.ts',
  'frontend/src/lib/api/schema.d.ts',
  'frontend/tests/drafts-api.test.ts',
  'frontend/tests/e2e/drafts.spec.ts',
  'pyproject.toml',
  'src/supportlens/backend/api/app.py',
  'src/supportlens/backend/api/config.py',
  'src/supportlens/backend/drafts/__init__.py',
  'src/supportlens/backend/drafts/grounding.py',
  'src/supportlens/backend/drafts/provider.py',
  'src/supportlens/backend/drafts/routes.py',
  'src/supportlens/backend/drafts/schemas.py',
  'src/supportlens/backend/drafts/service.py',
  'src/supportlens/backend/drafts/worker.py',
  'src/supportlens/backend/knowledge/indexing.py',
  'src/supportlens/backend/tickets/schemas.py',
  'tests/backend/test_drafts.py',
  'tests/backend/test_drafts_postgresql_integration.py',
  'tests/backend/test_tickets_postgresql_integration.py'
)
git --literal-pathspecs add --dry-run -- @m4cPaths
```

After reviewing the preview and the diffs, manually stage and check the same paths.

```powershell
git --literal-pathspecs add -- @m4cPaths
git diff --cached --name-only
git diff --cached --check
```
