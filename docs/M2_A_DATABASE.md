# SupportLens M2-A database foundation

M2-A adds configuration, PostgreSQL persistence contracts, guarded migrations and tests. It does not implement FastAPI routes, Firebase verification, ticket APIs, ML execution, retrieval, Gemini, a frontend or deployment.

## Current verification status

M2-A live verification completed on 2026-10-29. Both approved database names and distinct OIDs were verified through pooled and direct endpoints, with empty pre-migration inventories, verified client TLS, UTC and configured timeouts. The original migration committed on both databases. All 132 tests passed with no skips, including all six live PostgreSQL cases and 65 M1 regression tests. The final evidence is summarized below.

The approved database identity allowlist is `SupportLens_dev` and `SupportLens_test`. Configuration validates URL database selections, and preflight independently queries `current_database()` and database OIDs. Variable names alone never establish identity. M2-A refuses all production-selected commands before connection creation. Neon project/branch ownership must still be checked by the owner when obtaining URLs; this repository has no Neon management credentials or API integration.

Live evidence, captured locally without connection strings:

| Target | Database | Queried OID | Pre-migration objects (application/direct) | Committed revision |
|---|---|---|---|---|
| Development | `SupportLens_dev` | `16564` | `0 / 0` | `20261027_0001` |
| Test | `SupportLens_test` | `16567` | `0 / 0` | `20261027_0001` |

Both endpoints verified encrypted client TLS with `verify-full`, UTC, a ten-second connection timeout and a 10,000 ms statement timeout. Independent post-commit reflection found zero model differences, matching CHECK names, five verified guard functions and five enabled guard triggers in each database. Database OIDs are local catalog identities, not portable deployment configuration. Evidence files remain under ignored `artifacts/m2_a/live_20261028/`.

## Install in the existing environment

From the repository root in Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip --disable-pip-version-check install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip --disable-pip-version-check check
```

Backend packages are optional project dependencies installed by the development requirements. `constraints-backend.txt` pins their versions. The original `constraints.txt`, ML dependencies, model version, source, tests, data and evaluation artifacts are preserved; no M1 retraining is required.

## Configure locally without exposing credentials

If `.env` does not exist, copy `.env.example` locally. Never replace an existing environment file without reviewing it. Edit the ignored file on this computer; do not paste URLs into chat, public documentation, command arguments or Git.

Required values:

| Variable | Local value |
|---|---|
| `ENVIRONMENT` | `development` initially; `test` for opted-in integration tests |
| `DATABASE_URL` | Neon application URL selecting `SupportLens_dev` |
| `TEST_DATABASE_URL` | Distinct application URL selecting `SupportLens_test` |
| `MIGRATION_DATABASE_URL` | Matching direct dev endpoint if the application endpoint uses `-pooler` |
| `TEST_MIGRATION_DATABASE_URL` | Matching direct test endpoint if its application endpoint uses `-pooler` |
| `EXPECTED_DEV_DATABASE_NAME` | `SupportLens_dev` |
| `EXPECTED_TEST_DATABASE_NAME` | `SupportLens_test` |
| `DB_CONNECT_TIMEOUT_SECONDS` | `10`, bounded 1–30 |
| `DB_STATEMENT_TIMEOUT_MS` | `10000`, bounded 100–120000 |
| `DB_SSL_ROOT_CERT` | `system` or an existing absolute trusted CA bundle path |

Use `sslmode=verify-full`, including when a copied provider URL initially specifies `require`. Preserve the real hostname so certificate hostname verification works. Unverified modes, conflicting trust settings, unsafe query options and placeholder credentials are rejected. The driver enforces `verify-full`, required channel binding, certificate trust, connection timeout and disabled prepared-statement caching. A transaction-begin hook enforces transaction mode, UTC, public search path and statement timeout. A missing CA trust configuration causes a connection failure, never a verification downgrade.

On this Windows installation, libpq/OpenSSL's default CA file and directory were absent. An explicit PEM bundle exported from Windows ROOT certificates trusted for TLS server authentication resolved chain validation. The bundle stays in ignored `artifacts/`, and its absolute path is configured privately as `DB_SSL_ROOT_CERT`. This retains certificate and hostname verification; never trust a server leaf certificate merely to force a connection. A root-store export can be reproduced locally with Python:

```python
import ssl
from pathlib import Path

roots = sorted({cert for cert, encoding, trust in ssl.enum_certificates("ROOT")
                if encoding == "x509_asn"
                and (trust is True or "1.3.6.1.5.5.7.3.1" in trust)})
bundle = Path("artifacts/local_trust/windows_trusted_roots.pem")
bundle.parent.mkdir(parents=True, exist_ok=True)
bundle.write_text("".join(ssl.DER_cert_to_PEM_cert(cert) for cert in roots), encoding="ascii")
# Configure the ignored .env using bundle.resolve(); do not commit local trust files.
```

Pooled application endpoints are supported. Direct migration URLs must match the normalized application endpoint and approved database name. Other host aliases require an explicit reviewed configuration change. No URLs or passwords are placed in `alembic.ini`.

Libpq TLS verification behavior: [PostgreSQL connection documentation](https://www.postgresql.org/docs/current/libpq-connect.html). SQLAlchemy passes the enforced connection arguments to psycopg: [engine configuration](https://docs.sqlalchemy.org/en/20/core/engines.html).

## Read-only preflight

```powershell
.\.venv\Scripts\python.exe -B -m supportlens.backend.db.cli config-check
.\.venv\Scripts\python.exe -B -m supportlens.backend.db.cli preflight
```

Preflight initializes every transaction with `SET TRANSACTION READ ONLY`, followed by transaction-local UTC, search-path and statement-timeout settings before its catalog/session SELECTs. No table or application data is changed. It queries database name/OID, server version, current schema, UTC-aware timestamp behavior, transaction mode, timeouts, TLS state and an object inventory. It attests client encryption and `verify-full` from the driver, rather than trusting URL text. Connections close without committing.

Neon's pooler rejected `search_path` in connection startup options during live verification. Per-transaction initialization corrects this: `SET LOCAL` settings apply while a backend is pinned to that transaction and do not depend on session state surviving pooler reuse. The same hook is reapplied on every BEGIN. Write-capable migration/test connections explicitly select READ WRITE; production remains forbidden.

The inventory includes user relations/indexes/sequences, functions, enums/domains, extra schemas and nonstandard extensions. Standard PostgreSQL system schemas and built-in plpgsql are excluded. Existing test objects block the initial-migration preflight, even if they appear harmless; do not drop or reset them to force a pass. Obtain owner review. The preflight can be called programmatically with `require_empty_test=False` for read-only inspection of populated databases, but migration always enforces empty-target guards itself.

`backend_tls` describes the PostgreSQL backend connection; pooled endpoints may have a separate client TLS hop. The mandatory check is encrypted client transport with certificate/hostname verification. Reports contain safe database/catalog metadata, never credentials or connection strings. Driver exception details are suppressed at the CLI boundary.

## Initial migration, only after passing preflight

The following operations are authorized only for the verified empty development and dedicated test targets. Run development first, then test:

```powershell
.\.venv\Scripts\python.exe -B -m supportlens.backend.db.cli migrate --target development
.\.venv\Scripts\python.exe -B -m supportlens.backend.db.cli migrate --target test
.\.venv\Scripts\python.exe -B -m supportlens.backend.db.cli verify
```

Each migration rechecks both database identities, requires an empty test database and empty selected target, verifies the direct connection, takes a transaction-scoped advisory lock, rechecks inventory, and upgrades to `20261027_0001` in one transaction. Schema verification occurs before commit. Failed migrations roll back their own transaction. They never reset a database, drop unknown objects or perform automatic downgrades.

After both schemas exist, use `verify`, not the initial empty-database preflight. Re-running initial migration against a populated target deliberately blocks; it is not a reset or repair command.

Direct `alembic upgrade`, `downgrade`, `stamp`, and offline SQL execution through `env.py` are blocked without the wrapper's verified connection. The initial revision is an explicit frozen snapshot, independent of later ORM changes. Its downgrade raises an error. Future schema changes require reviewed forward migrations. The wrapper follows Alembic's [shared-connection transaction pattern](https://alembic.sqlalchemy.org/en/latest/cookbook.html).

Live verification checks migration revision, SQLAlchemy metadata differences, check-constraint names, trigger bindings/enabled state and stored guard-function bodies. Alembic metadata comparison alone does not establish runtime constraint behavior; the PostgreSQL tests below are also required. No claim of live schema success is made without executing them.

Live verification also exposed an Alembic 1.16 default-schema alias issue: reflection uses `None` for the default schema while the bookkeeping table is explicitly `public.alembic_version`. The verifier now excludes only that exact bookkeeping table from the application-model comparison and still verifies its revision independently. Unknown application tables remain differences. The schema definitions, initial revision and guard SQL were not rewritten. Verification failures rolled back before the corrected migration committed.

## Persistence contracts and integrity

Seven tables are defined: users, tickets, ticket messages, ticket events, AI runs, knowledge articles and knowledge citations. UUIDs have Python and database generation defaults. Timestamps use timezone-aware PostgreSQL types and server defaults. ORM/Core updates use `now()` for updated timestamps; direct SQL writers must also maintain `updated_at` explicitly.

- Firebase UID is unique; users default to CUSTOMER. Agent provisioning is trusted server work. Future signup code must ignore client-supplied roles. No authentication is implemented here.
- Ticket owners must be CUSTOMER and assigned users must be AGENT. Database guards preserve these role references during later role changes.
- Internal messages require AGENT authors. Future APIs must additionally enforce who may read internal notes and access each ticket.
- Statuses and priorities use database CHECK constraints. Resolved tickets require a resolved timestamp; other states require it null. The explicit transition vocabulary is in `domain/transitions.py` and mirrored by the ticket guard.
- Events use object-shaped JSON metadata and are append-only. A null actor is reserved for trusted system events; future APIs must never trust a client's actor field.
- AI runs are persistence only and default to human review. No inference or automated customer actions occur.
- Knowledge content/source/title/version are immutable after insertion. Create a new UUID/version row to revise an article; active status may change. Citation foreign keys reference the exact article/version pair.
- Foreign keys restrict deletion throughout the history graph. There are no ORM delete cascades or automatic cleanup operations.

Database guards are integrity rules, not a substitute for M2-B authentication/authorization.

## Tests

Offline tests use synthetic fixtures and never connect to a database:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider tests/backend -q
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider tests/ml -q
.\.venv\Scripts\python.exe -B -m supportlens.ml.cli verify
```

Live tests are skipped by default. After both migrations and live verification pass, run these in a dedicated PowerShell session:

```powershell
$env:ENVIRONMENT = 'test'
$env:SUPPORTLENS_RUN_DB_TESTS = '1'
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider tests/backend/test_postgresql_integration.py -q
# Full regression run with the same explicit live-test opt-in:
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -q
```

Close that dedicated shell afterward. An `.env` entry alone does not enable database tests; explicit process opt-in is required. The test gate rejects development/production write environments. The fixture re-verifies both schemas and the selected test identity, then writes only synthetic rows in an outer transaction that always rolls back. Constraint failures use savepoints. It never deletes unrelated rows, drops tables, truncates or resets databases. No migration is run by pytest.

The full default suite combines M1 and backend tests; skipped live tests must be reported separately, never counted as passed.

The first live run passed five cases and exposed an invalid enum-test fixture: its ten-character invalid priority exceeded the six-character column width before reaching the CHECK constraint. The fixture now uses a shorter invalid value and verifies PostgreSQL SQLSTATE `23514`, so it specifically exercises the enum CHECK. The column and migration are unchanged.

## Safe manual Git checkpoint

No staging, commit, branch rename, remote change or push is automatic. `Decisions.md` remains deliberately excluded, as do `.env`, private data, model files and local verification artifacts.

Review this explicit dry run:

```powershell
git add --dry-run -- .env.example pyproject.toml requirements-dev.txt constraints-backend.txt alembic.ini alembic src/supportlens/backend tests/backend docs/M2_A_DATABASE.md
```

After reviewing those exact files, the owner may stage and inspect them:

```powershell
git add -- .env.example pyproject.toml requirements-dev.txt constraints-backend.txt alembic.ini alembic src/supportlens/backend tests/backend docs/M2_A_DATABASE.md
git diff --cached --stat
git diff --cached --check
git diff --cached --name-only
```

Proposed commit message, only after all live gates pass:

```text
feat(db): establish guarded PostgreSQL database foundation
```

## Live completion: 2026-10-29

The complete explicitly opted-in suite passed: **132 passed, zero failures, zero skips** (65 M1, 61 offline backend, six live PostgreSQL). The final run includes the corrected enum fixture. All live writes used synthetic data on the verified test database and rolled back. A fresh read-only post-test check confirmed zero rows in all seven application tables in both databases.

`pip check` found no broken requirements. `supportlens.ml.cli verify` confirmed the saved artifact loads, gold and protected hashes are unchanged, split leakage checks pass and human review remains the default. All 48 protected M1 files match the pre-M2 hash baseline. The M2-A models, original migration and guard SQL also match their pre-live hashes.

| Acceptance gate | Result | Evidence |
|---|---|---|
| A: Repository safety | PASS | M1 hashes preserved; no credentials/private datasets tracked or staged |
| B: Environment | PASS | Typed configuration; verified certificate chain/hostname; UTC/timeouts |
| C: Database isolation | PASS | Approved names and distinct OIDs; empty initial inventories; only dev/test connected |
| D: Schema | PASS | Original revision committed twice; zero metadata differences; guards verified |
| E: Testing | PASS | 132 passed; no skips; post-test application row counts all zero |
| F: Git readiness | PASS | Explicit 28-file dry run; nothing staged, committed or pushed |

Private local evidence is under ignored `artifacts/m2_a/live_20261028/`: `pre_migration_inventory.json`, `final_schema.json`, `regression_tests.json`, `regression_tests_redacted.txt`, `post_test_counts.json`, `source_integrity.json`, `git_safety.json` and `completion.json`. The initial five-pass/one-failure live run remains recorded separately for traceability. `artifacts/m2_a/verification.json` contains the final summary; its prior offline result is retained alongside the live evidence.

There are no remaining M2-A blockers. No M2-B authentication or ticket APIs were implemented. Stop here until the owner approves M2-B.
