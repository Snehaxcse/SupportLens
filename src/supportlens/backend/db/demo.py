"""Initialize a newly provisioned synthetic database with fictional accounts."""
import argparse
import json
import os
from pathlib import Path
import re

from alembic import command
from alembic.config import Config
from sqlalchemy import insert, text

from ..config import ConfigurationError, Environment, load_settings
from ..domain.enums import UserRole
from .migrations import CACHE_REVISION, verify_schema
from .models import User
from .preflight import ensure_empty, inspect_connection
from .session import build_engine

RUNTIME_GRANTS = (
    "REVOKE CREATE ON SCHEMA public FROM PUBLIC",
    "GRANT USAGE ON SCHEMA public TO supportlens_demo_app",
    "GRANT SELECT ON ALL TABLES IN SCHEMA public TO supportlens_demo_app",
    "GRANT UPDATE (display_name) ON users TO supportlens_demo_app",
    "GRANT INSERT, UPDATE ON tickets, ai_runs, knowledge_entries, knowledge_revisions, "
    "response_cache_entries TO supportlens_demo_app",
    "GRANT INSERT ON ticket_messages, ticket_events, knowledge_articles, knowledge_citations, "
    "knowledge_chunks, knowledge_embeddings, knowledge_entry_events, response_cache_evidence TO supportlens_demo_app",
)


def initialize_demo(settings, identities, root):
    target = Environment.SYNTHETIC_DEMO
    runtime_spec = settings.spec(target, readonly=True)
    owner_spec = settings.spec(target, migration=True)
    if (not all(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", uid) for uid in identities)
            or len(set(identities)) != 2):
        raise ConfigurationError("Two distinct fictional identifiers are required.")
    runtime = build_engine(settings, target, readonly=True)
    try:
        with runtime.connect() as connection:
            identity = inspect_connection(connection, runtime_spec)
            ensure_empty(identity)
    finally:
        runtime.dispose()

    owner = build_engine(settings, target, migration=True)
    try:
        with owner.begin() as connection:
            direct = inspect_connection(connection, owner_spec, require_readonly=False)
            if direct.database_oid != identity.database_oid:
                raise ConfigurationError("Runtime and migration credentials select different databases.")
            connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('supportlens-demo-bootstrap'))"))
            ensure_empty(inspect_connection(connection, owner_spec, require_readonly=False))
            config = Config(str(root / "alembic.ini"))
            config.attributes.update(verified_connection=connection, operation="demo-empty-upgrade")
            command.upgrade(config, CACHE_REVISION)
            for table_grant in RUNTIME_GRANTS:
                connection.exec_driver_sql(table_grant)
            for uid, role in zip(identities, (UserRole.CUSTOMER, UserRole.AGENT), strict=True):
                name = f"Fictional demo {role.value.lower()}"
                connection.execute(insert(User).values(
                    firebase_uid=uid, role=role, email=f"{role.value.lower()}@example.invalid", display_name=name))
            return verify_schema(connection, required_revision=CACHE_REVISION)
    finally:
        owner.dispose()


def main():
    parser = argparse.ArgumentParser(description="Guarded bootstrap for an empty synthetic demo database.")
    parser.add_argument("--initialize-empty-demo", action="store_true", required=True)
    parser.parse_args()
    try:
        ids = (os.environ.get("DEMO_CUSTOMER_UID", ""), os.environ.get("DEMO_AGENT_UID", ""))
        report = initialize_demo(load_settings(), ids, Path.cwd())
    except Exception:
        print(json.dumps({"status": "blocked", "details_suppressed": True}))
        return 1
    print(json.dumps({"status": "initialized", "revision": report["revision"], "fictional_accounts": 2}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
