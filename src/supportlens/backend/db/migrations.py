"""Initial upgrade only: verified identities, empty inventory, one transaction."""
from pathlib import Path
import re

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, MetaData, inspect, text

from ..config import ConfigurationError, Environment, Settings
from .base import Base
from . import models  # register tables
from .preflight import (
    PreflightBlocked, ensure_empty, inspect_connection, preflight,
)
from .schema_guards import EXPECTED_TRIGGERS, GUARD_DDL
from .session import build_engine

REVISION = "20261027_0001"
KNOWLEDGE_REVISION = "20261114_0002"
CACHE_REVISION = "20261116_0003"
CACHE_TABLES = frozenset({"response_cache_entries", "response_cache_evidence"})
FOUNDATION_TABLES = frozenset({"users", "tickets", "ticket_messages", "ticket_events",
                              "ai_runs", "knowledge_articles", "knowledge_citations"})


def foundation_metadata():
    metadata = MetaData(naming_convention=Base.metadata.naming_convention)
    for name in sorted(FOUNDATION_TABLES):
        Base.metadata.tables[name].to_metadata(metadata)
    return metadata


def knowledge_metadata():
    metadata = MetaData(naming_convention=Base.metadata.naming_convention)
    for name, table in Base.metadata.tables.items():
        if name not in CACHE_TABLES:
            table.to_metadata(metadata)
    return metadata


def include_application_object_name(name, type_, parent_names):
    # Alembic 1.16 reflects the default schema as None, while our version table
    # is explicitly public. Exclude only its bookkeeping table; verify the
    # revision independently and retain every other reflected application object.
    return not (type_ == "table" and name == "alembic_version"
                and parent_names.get("schema_name") in (None, "public"))


def verify_schema(connection, *, required_revision=None) -> dict:
    from ..knowledge import models as knowledge_models  # register additive tables
    from ..response_cache import models as cache_models

    context = MigrationContext.configure(connection, opts={"compare_type": True, "compare_server_default": True, "version_table_schema": "public", "include_name": include_application_object_name})
    current_revision = context.get_current_revision()
    if current_revision not in {REVISION, KNOWLEDGE_REVISION, CACHE_REVISION} or (required_revision and current_revision != required_revision):
        raise PreflightBlocked("The expected SupportLens migration revision is not installed.")
    metadata = ({REVISION: foundation_metadata, KNOWLEDGE_REVISION: knowledge_metadata,
                 CACHE_REVISION: lambda: Base.metadata}[current_revision])()
    difference_count = len(compare_metadata(context, metadata))
    trigger_rows = list(connection.execute(text("""
        SELECT t.tgname, c.relname, p.proname, t.tgtype, t.tgenabled FROM pg_trigger t
        JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_proc p ON p.oid=t.tgfoid
        WHERE NOT t.tgisinternal AND n.nspname='public'
    """)))
    expected_bindings = {
        ("supportlens_ticket_guard", "tickets", "supportlens_guard_ticket", 23, "O"),
        ("supportlens_internal_message_guard", "ticket_messages", "supportlens_guard_internal_message", 23, "O"),
        ("supportlens_user_role_guard", "users", "supportlens_guard_user_role", 19, "O"),
        ("supportlens_knowledge_revision_guard", "knowledge_articles", "supportlens_guard_knowledge_revision", 19, "O"),
        ("supportlens_audit_append_only_guard", "ticket_events", "supportlens_audit_append_only", 27, "O"),
    }
    expected_functions = {}
    for ddl in GUARD_DDL:
        name = re.search(r"CREATE FUNCTION public\.(\w+)", ddl)
        if name:
            expected_functions[name.group(1)] = ddl.split("AS $$", 1)[1].rsplit("$$", 1)[0].strip()
    revisions = []
    if current_revision in {KNOWLEDGE_REVISION, CACHE_REVISION}:
        expected_bindings.update({
            ("supportlens_knowledge_event_append_only", "knowledge_entry_events", "supportlens_audit_append_only", 27, "O"),
            ("supportlens_knowledge_chunk_append_only", "knowledge_chunks", "supportlens_audit_append_only", 27, "O"),
            ("supportlens_knowledge_embedding_append_only", "knowledge_embeddings", "supportlens_audit_append_only", 27, "O"),
            ("supportlens_knowledge_provenance_guard", "knowledge_revisions", "supportlens_guard_knowledge_provenance", 27, "O"),
        })
        revisions.append(KNOWLEDGE_REVISION)
    if current_revision == CACHE_REVISION:
        expected_bindings.update({
            ("supportlens_response_cache_guard", "response_cache_entries", "supportlens_guard_response_cache", 27, "O"),
            ("supportlens_response_cache_evidence_guard", "response_cache_evidence", "supportlens_audit_append_only", 27, "O"),
            ("supportlens_response_cache_invalidate", "knowledge_entries", "supportlens_invalidate_response_cache", 17, "O"),
        })
        revisions.append(CACHE_REVISION)
    for revision in revisions:
        root = Path(__file__).resolve().parents[4]
        migration = ScriptDirectory.from_config(Config(str(root / "alembic.ini"))).get_revision(revision).module
        for ddl in migration.DDL:
            name = re.search(r"CREATE FUNCTION public\.(\w+)", ddl)
            if name:
                expected_functions[name.group(1)] = ddl.split("AS $$", 1)[1].rsplit("$$", 1)[0].strip()
    actual_functions = dict((row[0], row[1]) for row in connection.execute(text("""
        SELECT p.proname, p.prosrc FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public'
    """)))
    actual_functions = {name: body.strip() for name, body in actual_functions.items()}
    inspector = inspect(connection)
    checks_match = all(
        {str(c.name) for c in table.constraints if isinstance(c, CheckConstraint)}
        == {c["name"] for c in inspector.get_check_constraints(name, schema="public")}
        for name, table in metadata.tables.items()
    )
    if (difference_count or set(map(tuple, trigger_rows)) != expected_bindings
            or actual_functions != expected_functions or not checks_match):
        raise PreflightBlocked("Live schema, trigger inventory or migration revision differs from the approved foundation.")
    return {"revision": current_revision, "metadata_difference_count": difference_count,
            "guard_triggers_verified": len(trigger_rows), "guard_functions_verified": len(actual_functions),
            "check_constraint_names_verified": True, "tables": sorted(metadata.tables)}


def migrate(settings: Settings, target: Environment, project_root: Path) -> dict:
    # This check occurs before even read-only inspection: production is never contacted.
    spec = settings.spec(target, migration=True)
    identities = preflight(settings, require_empty_test=True)
    ensure_empty(identities[target])
    engine = build_engine(settings, target, migration=True)
    try:
        with engine.begin() as connection:
            direct = inspect_connection(connection, spec, require_readonly=False)
            if direct.database_oid != identities[target].database_oid:
                raise PreflightBlocked("Direct and application database identities differ.")
            ensure_empty(direct)
            connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('supportlens_m2a_initial'))"))
            ensure_empty(inspect_connection(connection, spec, require_readonly=False))
            cfg = Config(str(project_root / "alembic.ini"))
            cfg.attributes["verified_connection"] = connection
            cfg.attributes["operation"] = "m2a-initial-upgrade"
            command.upgrade(cfg, REVISION)
            report = verify_schema(connection)
            # Commit only when schema verification also passes.
        return {"target": target.value, "database_name": spec.expected_database, "migration_applied": True, **report}
    except ConfigurationError:
        raise
    except Exception:
        raise PreflightBlocked("Migration failed and its transaction was rolled back. Driver details are suppressed.") from None
    finally:
        engine.dispose()


def verify_databases(settings: Settings) -> dict:
    identities = preflight(settings)
    reports = {}
    for target in (Environment.DEVELOPMENT, Environment.TEST):
        engine = build_engine(settings, target, readonly=True)
        try:
            with engine.connect() as connection:
                inspect_connection(connection, settings.spec(target, readonly=True))
                reports[target.value] = verify_schema(connection)
        finally:
            engine.dispose()
    return {"identities": {target.value: identity.public_report() for target, identity in identities.items()}, "schemas": reports}


def migrate_knowledge(settings: Settings, target: Environment, project_root: Path) -> dict:
    return migrate_additive(settings, target, project_root, REVISION, KNOWLEDGE_REVISION, "m4b-knowledge-upgrade")


def migrate_cache(settings: Settings, target: Environment, project_root: Path) -> dict:
    return migrate_additive(settings, target, project_root, KNOWLEDGE_REVISION, CACHE_REVISION, "m4e-cache-upgrade")


def migrate_additive(settings, target, project_root, predecessor, revision, operation):
    spec = settings.spec(target, migration=True)
    identities = preflight(settings)
    engine = build_engine(settings, target, migration=True)
    try:
        with engine.begin() as connection:
            direct = inspect_connection(connection, spec, require_readonly=False)
            if direct.database_oid != identities[target].database_oid:
                raise PreflightBlocked("Direct and application database identities differ.")
            connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('supportlens_additive_migration'))"))
            before = verify_schema(connection)
            if before["revision"] in {revision, CACHE_REVISION}:
                return {"migration_applied": False, **before}
            if before["revision"] != predecessor:
                raise PreflightBlocked("The additive migration prerequisite is not installed.")
            cfg = Config(str(project_root / "alembic.ini"))
            cfg.attributes.update(verified_connection=connection, operation=operation)
            command.upgrade(cfg, revision)
            return {"migration_applied": True, **verify_schema(connection, required_revision=revision)}
    except ConfigurationError:
        raise
    except Exception:
        raise PreflightBlocked("The additive migration failed and rolled back. Driver details are suppressed.") from None
    finally:
        engine.dispose()
