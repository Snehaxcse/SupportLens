# M4-B Knowledge base and retrieval

SupportLens agents can create, search, edit, publish, unpublish, archive and reindex articles. The agent ticket workspace shows ranked evidence with the exact revision and source. The customer API and workspace retain their existing contracts. This milestone ends at retrieval.

## Knowledge-source policy

SupportLens is an independent Spotify-focused project. No authorized Spotify corpus was supplied. The checked-in corpus contains fictional integration fixtures only. Import requires an agent to supply the article body, source name/type, HTTPS URL for approved external sources, and a reuse approval or license reference. The operator must validate the rights and accuracy before publication. The application records their attestation and cannot independently verify it. Historic tickets and Twitter conversations remain untrusted support data.

Article and evidence text is rendered as plain React text. The server never fetches source URLs. A validated HTTPS source link opens only on agent action. Synthetic status cannot be changed after creation. Synthetic articles remain visible in agent management but are excluded from evidence retrieval. Publishing them requires explicit local test configuration. Keep this setting disabled outside an isolated smoke run.

## Schema, publication and provenance

Revision `20261114_0002` adds five tables. It preserves revision `20261027_0001`, existing table contents, guard definitions and citation foreign keys.

| Table | Responsibility |
| --- | --- |
| `knowledge_entries` | Stable article UUID, lifecycle, current revision, observed generation, agent provenance |
| `knowledge_revisions` | Metadata for the existing immutable `knowledge_articles` row, reuse rights, source and indexing lease |
| `knowledge_entry_events` | Append-only actor, lifecycle operation, revision and generation |
| `knowledge_chunks` | Immutable revision-scoped excerpt, content hash, embedding input hash and GIN full-text index |
| `knowledge_embeddings` | Immutable 384-element float vectors keyed by embedding-input hash and model key |

New bodies use the existing `knowledge_articles` rows as immutable revision storage. The old `is_active` flag is false on these rows. The catalog lifecycle and current-revision join determine retrieval eligibility; legacy rows with no catalog entry stay out of retrieval. No existing row is backfilled. The deferred foreign key guarantees that the stable article's current revision exists at commit. Existing citations retain their compound foreign key to `(knowledge_articles.id, knowledge_articles.version)`, so editing does not relink citations. Preview and ticket retrieval are read-only and do not insert citations.

Creation saves a draft. Each edit appends a revision and returns the article to draft, immediately removing the earlier revision from retrieval. Publishing atomically queues indexing for the observed generation. Unpublishing returns to draft and archive is terminal. Reindex queues the current published revision after an indexed, lexical-only or failed result. The catalog row lock and expected generation reject stale writes. Audit writes share the mutation transaction.

## Chunking, embeddings and recovery

Chunking normalizes Unicode to NFC, normalizes newlines, trims lines and preserves Markdown-style headings as plain text. It uses up to 900 characters with a 120-character overlap, preferring whitespace boundaries. Identical excerpts within a revision are deduplicated. The excerpt SHA-256 and revision UUID produce a stable chunk UUID. The embedded input is normalized title plus excerpt. Identical inputs share a vector under the same model key, including across revisions. A changed title can change the embedding input even when the body stays the same.

The local encoder uses `sentence-transformers/all-MiniLM-L6-v2`, upstream revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`, quantized AVX2 ONNX, 256-token limit, attention-mask mean pooling and L2 normalization. The ONNX file is about 23 MB. The encoder uses CPU, needs no paid service, and sends no ticket text off-host. It verifies the size and SHA-256 of fixed local ONNX and tokenizer bytes before loading. The process never downloads or accepts arbitrary model paths. Pinning the preprocessing and quantization in the model key prevents incompatible vectors from being mixed. Single-text ONNX calls prevent dynamic quantization from depending on other inputs in a batch. The model card documents English-language focus and 384-dimensional embeddings. [Upstream model and license](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)

A daemon wakes after commit and periodically polls durable jobs. Each claim locks the catalog row with `SKIP LOCKED`, assigns a five-minute lease and commits before computing chunks or embeddings. Publication checks the exact generation, current revision, status and lease token again. An abandoned lease can be retried up to three times. A stale computation cannot write chunks or attach vectors. Database failure during publication rolls back the full result. The fallback writes the lexical chunks and reports `LEXICAL_ONLY` if embeddings are unavailable. Once the local model is restored, use **Reindex article**. Matching vector keys skip embedding work. Two different articles indexed concurrently may compute the same missing vector before its first insert, but the unique key stores one copy.

## Ranking and ticket evidence

| Method | Algorithm and score |
| --- | --- |
| Lexical | PostgreSQL English full-text search with GIN and OR-combined bounded terms. Score is normalized cover-density text rank. |
| Semantic | Exact in-process cosine similarity over the current published chunks matching the pinned model. Keep scores of at least 0.32. |
| Hybrid | Reciprocal rank fusion over lexical and semantic candidates, using `k=60`. Score is the sum of reciprocal rank terms. |

None of the scores is a calibrated relevance probability. Ties use article/chunk UUIDs. The service caps each candidate ranking at 100, returns one representative chunk per article, and accepts a top-k limit from 1 through 10 (default 5). It returns an empty list when the query finds no matches. Exact semantic search has a hard 2,000-eligible-chunk limit and reports its availability. Hybrid falls back to lexical if semantic search is unavailable. Both guarded Neon environments offered pgvector 0.8.7 during preflight. This implementation uses persisted PostgreSQL `real[]` vectors and bounded exact search to avoid introducing an extension or approximate index before the corpus warrants one. A GIN index accelerates lexical candidate lookup. [Neon pgvector support](https://neon.com/docs/extensions/pgvector) · [PostgreSQL text ranking](https://www.postgresql.org/docs/current/textsearch-controls.html)

Ticket retrieval selects the requested ticket, its bounded subject and description, and up to two recent public messages for that same ticket. It never uses internal notes or a confidence threshold from M4-A to exclude evidence. It computes a request-local query embedding and does not store ticket query text or vectors. Candidate selection filters by current published and indexed revision, and a final query rechecks eligibility before returning.

An evidence result carries `article_id`, `revision_id`, `revision`, `chunk_id`, `content_hash`, exact excerpt, source name/type/URL, rank, method, channel ranks and the applicable embedding identity. M4-C should type-check these fields, recheck current publication when generating or reusing evidence, and treat the excerpt as untrusted data. Protect the prompt boundary against instructions in articles and tickets. Only persist citations for an actual future AI run after validating the evidence, using the immutable revision UUID in the existing citation FK and the chunk identity/hash in citation metadata. Human review remains required.

## Agent endpoints

Paths below are relative to `/api/v1/agent`. Firebase authentication and active database AGENT authorization apply to every path. Service transactions recheck the current role. Stale updates return 409, invalid fields return 422, and unknown articles/revisions/tickets return 404 without private details.

| Method and path | Purpose |
| --- | --- |
| GET `/knowledge` | Deterministic pagination, literal search and status/source filters |
| POST `/knowledge` | Create a draft |
| GET `/knowledge/{article_id}` | Current or explicitly requested `revision` |
| PATCH `/knowledge/{article_id}` | Save a new draft using `expected_generation` |
| POST `/knowledge/{article_id}/lifecycle` | Publish, unpublish, archive or reindex |
| GET `/knowledge/{article_id}/revisions` | Paginated revision history |
| POST `/knowledge/retrieval-preview` | Run bounded text retrieval without saving the query |
| GET `/tickets/{ticket_id}/knowledge` | Retrieve evidence for an authorized ticket |

## Operations and verification

Install the repository with the optional `retrieval` extra in the pinned Python environment. The regular API can run without that extra. Provision these two pinned artifacts under ignored `artifacts/models/m4b_minilm/` from the fixed upstream revision above:

| Upstream path | SHA-256 |
| --- | --- |
| `tokenizer.json` | `be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037` |
| `onnx/model_quint8_avx2.onnx` | `b941bf19f1f1283680f449fa6a7336bb5600bdcd5f84d10ddc5cd72218a0fd21` |

Keep existing Firebase, exact CORS, separate development/test and verified TLS settings. `KNOWLEDGE_WORKER_ENABLED` and `KNOWLEDGE_EMBEDDINGS_ENABLED` default to true. `KNOWLEDGE_POLL_SECONDS` defaults to 30. `KNOWLEDGE_ALLOW_SYNTHETIC` defaults to false. For an existing verified foundation, apply the additive migration first to test and then development using the explicit targets. The wrapper verifies database identity and the old schema, holds an advisory transaction lock, and commits only after schema verification. The migration has no supported downgrade because that would destroy retained revisions and evidence.

```powershell
.\.venv\Scripts\python.exe -B -m supportlens.backend.db.cli migrate-knowledge --target test
.\.venv\Scripts\python.exe -B -m supportlens.backend.db.cli migrate-knowledge --target development
.\.venv\Scripts\python.exe -B -m supportlens.backend.db.cli verify
.\.venv\Scripts\python.exe -B -m uvicorn supportlens.backend.api.app:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

From `frontend/`, run `npm run dev -- --hostname 127.0.0.1 --port 3000`. For a production preview, use `npm run build` then `npm run start -- --hostname 127.0.0.1 --port 3000`. Keep private credentials in ignored configuration and never use a production database with these commands.

```powershell
$env:ENVIRONMENT = 'test'
$env:SUPPORTLENS_RUN_DB_TESTS = '1'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -B -m supportlens.backend.knowledge.evaluate --top-k 3
.\.venv\Scripts\python.exe -B -m supportlens.ml.cli verify
.\.venv\Scripts\python.exe -B -m pip check
```

The evaluator runs the actual database and ONNX code on the fixed `evaluation/m4_b/fixtures.json` corpus inside a rollback transaction. `evaluation/m4_b/results.json` records per-query rankings and macro Recall@k, MRR@k and graded nDCG@k over seven queries with relevance labels. The one unmatched query is measured separately. Filtering, edit freshness and unpublication are asserted. These small synthetic fixtures verify plumbing and illustrate ranking differences. They cannot establish production relevance, a safe response-reuse threshold, latency or load capacity. Before tuning or enabling production content, evaluate representative approved queries and adversarial no-match examples.

| Method | Recall@3 | MRR@3 | nDCG@3 | Unmatched queries with no result |
| --- | ---: | ---: | ---: | ---: |
| Lexical | 0.8571 | 0.8571 | 0.8571 | 1/1 |
| Semantic | 0.9286 | 1.0000 | 0.9882 | 1/1 |
| Hybrid | 1.0000 | 1.0000 | 1.0000 | 1/1 |

Frontend checks are `npm run lint`, `npm run typecheck`, `npm test`, `npm run build` and `npm run test:e2e`. The browser matrix tests keyboard use, responsive layout and axe rules. Real Firebase verification separately uses approved identities and matches the persisted article/revision evidence.

The original M4-A development-toolchain audit reported 14 moderate entries. The M4-B audit returned nine moderate entries mapped to the same minimatch advisory and zero production-dependency findings. The published ESLint 10.15.0 dependency ranges would resolve its branch with patched minimatch. The latest TypeScript ESLint 8.72.2 still requires minimatch 10 through `typescript-estree`, so no complete compatible remediation was verified across both branches. The npm manifests and lockfile remain at the reviewed M4-A versions. [Minimatch maintainer advisory](https://github.com/isaacs/minimatch/security/advisories/GHSA-g3hv-75wg-m249)

### Local verification record

Local `main`, `origin/main` and the GitHub branch matched the manually pushed M4-A commit `17ccbe05c1e95dab5ec123464012579154193c92` before implementation. Preflight passed 349 offline backend cases and 94 frontend cases. All 48 protected M1 paths matched the prior recorded hashes. The original OpenAPI routes and schema definitions match the checkpoint by parsed equality.

Both guarded Neon databases applied `20261114_0002`, with zero reflected schema differences and all nine guards verified, including the five original guards. Real Firebase CUSTOMER and AGENT sessions verified publication, indexing, all three retrieval methods, exact evidence and source links, edit, reindex by publishing revision 2, and unpublication. Live verification retained one synthetic draft article, two immutable revisions, two chunks, two vectors and five lifecycle events. The existing synthetic ticket was reused. There were no new tickets, account changes or citations. The live harness resumed from journaled writes after render waits, without repeating confirmed mutations. Separately, test-database fixtures verified archive, concurrent editing, lease cancellation/recovery, vector reuse, and the original citation FK. The concurrency test intentionally retains its synthetic archive.

The final localhost preview runs with synthetic publication disabled. The frontend and backend health and readiness returned 200, an unauthenticated knowledge request returned 401, and both apps returned 404 for the private ONNX artifact path. The real-account check took no screenshots and exported no Firebase token. The production browser build and 96 frontend unit tests passed. All 42 browser cases passed across the three existing viewports, including the original 36 cases. The initial frontend unit run encountered worker startup timeouts and passed on a single-worker retry. One initial browser run timed out in an existing desktop regression and the complete subsequent run passed with no skips or retries.

The complete backend run passed 389 cases, including all 364 prior cases and 18 live PostgreSQL cases, with no failures or skips. The one warning is the existing Starlette TestClient deprecation. The final lease correction rechecks the joined revision in a fresh statement under the catalog lock. After this correction all four live knowledge tests passed again, including a separately collected two-session edit and indexing claim race. The full run and focused rerun together cover all 390 unique cases, including 19 live PostgreSQL cases.

Final row fingerprints matched every preexisting row in both databases. The guarded test runs retained three synthetic archives with six revisions and ten knowledge lifecycle events. Across all retained test fixtures, the test database added nine users, two tickets, six ticket audit events and three classification runs. Evaluation and rollback-scoped fixtures left no rows.

## M4-E Semantic Cache Integration Design

Use the existing `Embedder.encode` interface and `EmbeddingIdentity` for a compatible query embedding. Keep knowledge and approved response candidates in separate stores and search paths. A future candidate store needs its own IDs and namespace, canonical reusable guidance, approval status and approver/timestamps, eligible intent and policy scope, locale/context schema, model key/dimension, dependency revisions and hashes, expiry, revocation time and audit trail. Never store the original customer turn in a reusable cache record.

Determine hard eligibility before searching. Start with general guidance explicitly approved for reuse. Exclude customer identifiers, account state, personal or payment details and instructions to act on an account. For compromise, billing or authentication, require a reviewed security-specific allowlist for general guidance and otherwise bypass. Require compatible intent, policy, locale, product context and answer scope. M4-A's uncertain intent is an advisory signal. Score calibration must use labeled equivalent and hard-negative requests, including similar wording with different resolutions. Evaluate false-positive reuse per risk tier. Similarity alone grants no permission, and missing or ambiguous context should produce a cache miss.

Validate a candidate's approval, absolute expiry, policy version, embedding identity and every referenced published knowledge revision at use time. Superseding, archiving, unpublishing or revoking evidence invalidates dependent responses. Maintain explicit dependency edges for targeted invalidation and check them on the read path so background work cannot expose a stale answer. Separate model versions and namespaces during index rebuilds.

For duplicate generation, use a short per-key database reservation or lease tied to the eligible normalized guidance request and policy context. Compute outside the transaction, use fencing to reject stale completions and ensure only approved reusable content becomes a candidate. On misses, rejected hits or cache outages, fall back to current evidence and the future Gemini workflow. Preserve agent review and explicit sending throughout.

Track hit, miss, rejection category, invalidation and fallback counts without ticket text. Compare estimated avoided provider calls/tokens and measured latency with uncached controls. Evaluate approval/freshness coverage, precision at the reuse threshold, unsafe false hits, privacy leakage and duplicate-generation races before rollout. M4-B introduces no response-cache table, endpoint, runtime reuse or Gemini call.

## Exact staging preview

From the repository root, preview the reviewed scope before a manual checkpoint. The commands below leave Git state alone. Review the diff and keep `Decisions.md`, `frontend/AGENTS.md`, private environments, model binaries, data, browser output and all ignored local verification journals out of staging.

```powershell
$m4bPaths = @(
  '.env.example',
  'alembic/env.py',
  'alembic/versions/20261114_0002_knowledge_retrieval.py',
  'docs/M4_B_RETRIEVAL.md',
  'evaluation/m4_b/fixtures.json',
  'evaluation/m4_b/results.json',
  'frontend/contracts/openapi.json',
  'frontend/src/app/(agent)/agent/knowledge/[articleId]/page.tsx',
  'frontend/src/app/(agent)/agent/knowledge/new/page.tsx',
  'frontend/src/app/(agent)/agent/knowledge/page.tsx',
  'frontend/src/app/workspace.css',
  'frontend/src/components/agent.tsx',
  'frontend/src/components/knowledge-evidence.tsx',
  'frontend/src/components/knowledge.tsx',
  'frontend/src/components/shell.tsx',
  'frontend/src/lib/api/client.ts',
  'frontend/src/lib/api/schema.d.ts',
  'frontend/tests/e2e/knowledge.spec.ts',
  'frontend/tests/knowledge-api.test.ts',
  'pyproject.toml',
  'src/supportlens/backend/api/app.py',
  'src/supportlens/backend/api/config.py',
  'src/supportlens/backend/db/cli.py',
  'src/supportlens/backend/db/migrations.py',
  'src/supportlens/backend/knowledge/__init__.py',
  'src/supportlens/backend/knowledge/catalog.py',
  'src/supportlens/backend/knowledge/chunking.py',
  'src/supportlens/backend/knowledge/embedding.py',
  'src/supportlens/backend/knowledge/evaluate.py',
  'src/supportlens/backend/knowledge/indexing.py',
  'src/supportlens/backend/knowledge/models.py',
  'src/supportlens/backend/knowledge/retrieval.py',
  'src/supportlens/backend/knowledge/routes.py',
  'src/supportlens/backend/knowledge/schemas.py',
  'tests/backend/test_knowledge.py',
  'tests/backend/test_knowledge_postgresql_integration.py',
  'tests/backend/test_schema_migrations.py',
  'tests/backend/test_tickets_postgresql_integration.py'
)
git --literal-pathspecs add --dry-run -- @m4bPaths
git diff --cached --name-only
git diff --cached --check
```
