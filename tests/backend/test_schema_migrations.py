import importlib.util
from collections import Counter
from pathlib import Path
import subprocess
import sys

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable
from sqlalchemy.sql.elements import conv

from supportlens.backend.config import ConfigurationError, Environment
from supportlens.backend.db import migrations
from supportlens.backend.db.base import Base, NAMING_CONVENTION
from supportlens.backend.db.models import User, Ticket, TicketMessage, AIRun, KnowledgeCitation
from supportlens.backend.db.preflight import PreflightBlocked
from supportlens.backend.db.schema_guards import GUARD_DDL
from supportlens.backend.domain.enums import UserRole, TicketStatus
from supportlens.backend.domain.transitions import validate_transition

ROOT = Path(__file__).resolve().parents[2]


def load_revision():
    spec = importlib.util.spec_from_file_location("initial_revision", ROOT / "alembic/versions/20261027_0001_database_foundation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exact_minimum_table_inventory():
    assert set(Base.metadata.tables) == {"users", "tickets", "ticket_messages", "ticket_events", "ai_runs", "knowledge_articles", "knowledge_citations"}


def test_foreign_keys_preserve_audit_history():
    fks = [fk for table in Base.metadata.tables.values() for fk in table.foreign_key_constraints]
    assert len(fks) == 9
    assert all(fk.ondelete == "RESTRICT" for fk in fks)
    assert any(len(fk.column_keys) == 2 for fk in KnowledgeCitation.__table__.foreign_key_constraints)


def test_uuid_and_utc_timestamp_defaults():
    for table in Base.metadata.tables.values():
        assert table.c.id.primary_key and isinstance(table.c.id.type, sa.Uuid)
        assert str(table.c.id.server_default.arg) == "gen_random_uuid()"
        for column in table.columns:
            if isinstance(column.type, sa.DateTime): assert column.type.timezone
        if "created_at" in table.c:
            assert table.c.created_at.server_default is not None and not table.c.created_at.nullable


def test_unique_firebase_uid_and_safe_defaults():
    assert any(isinstance(c, sa.UniqueConstraint) and list(c.columns.keys()) == ["firebase_uid"] for c in User.__table__.constraints)
    assert User.__table__.c.role.server_default.arg == "CUSTOMER"
    assert Ticket.__table__.c.priority.server_default.arg == "NORMAL"
    assert str(AIRun.__table__.c.requires_human_review.server_default.arg) == "true"


def test_enum_values_are_checked_in_ddl_and_binding():
    ddl = str(CreateTable(Ticket.__table__).compile(dialect=postgresql.dialect()))
    assert "CHECK" in ddl and "REOPENED" in ddl and "NORMAL" in ddl
    with pytest.raises(LookupError):
        User.__table__.c.role.type.bind_processor(postgresql.dialect())("ADMIN")


def test_cross_row_role_and_visibility_guards_are_present():
    combined = "\n".join(GUARD_DDL)
    assert "FOR SHARE" in combined and "NEW.visibility='INTERNAL'" in combined
    assert "customer_role <> 'CUSTOMER'" in combined and "agent_role <> 'AGENT'" in combined
    assert "NEW.role IS DISTINCT FROM OLD.role" in combined
    assert "append-only" in combined


@pytest.mark.parametrize("ddl", [statement for statement in GUARD_DDL if "CREATE FUNCTION" in statement], ids=["ticket", "internal_message", "user_role", "knowledge_revision", "audit"])
def test_plpgsql_blocks_have_required_terminator(ddl):
    body = ddl.split("AS $$", 1)[1].rsplit("$$", 1)[0].strip()
    assert body.startswith("DECLARE") or body.startswith("BEGIN")
    assert body.endswith("END;")


class CaptureOperations:
    def __init__(self): self.metadata = sa.MetaData(naming_convention=NAMING_CONVENTION); self.executed = []
    def f(self, name): return conv(name)
    def create_table(self, name, *elements, **kwargs): return sa.Table(name, self.metadata, *elements, **kwargs)
    def create_index(self, name, table, columns, **kwargs):
        sa.Index(name, *[self.metadata.tables[table].c[c] for c in columns], **kwargs)
    def execute(self, statement): self.executed.append(str(statement))


def test_frozen_migration_schema_matches_models_without_database():
    revision = load_revision()
    capture = CaptureOperations()
    revision.op = capture
    revision.upgrade()
    assert revision.revision == migrations.REVISION and revision.down_revision is None
    assert capture.executed == list(GUARD_DDL)
    assert set(capture.metadata.tables) == set(Base.metadata.tables)
    for name, actual in Base.metadata.tables.items():
        frozen = capture.metadata.tables[name]
        # Constraint declaration order has no semantic effect; retain multiplicity.
        def normalized_ddl(table):
            return Counter(" ".join(line.strip().rstrip(",").split()) for line in str(CreateTable(table).compile(dialect=postgresql.dialect())).splitlines() if line.strip())
        left = normalized_ddl(actual)
        right = normalized_ddl(frozen)
        assert left == right
        assert {(i.name, tuple(i.columns.keys()), i.unique) for i in frozen.indexes} == {(i.name, tuple(i.columns.keys()), i.unique) for i in actual.indexes}


def test_downgrade_is_explicitly_disabled():
    with pytest.raises(RuntimeError, match="disabled"): load_revision().downgrade()


def test_migration_blocks_before_ddl_on_failed_preflight(settings_factory, monkeypatch):
    def blocked(*a, **kw): raise PreflightBlocked("Unexpected test objects")
    monkeypatch.setattr(migrations, "preflight", blocked)
    monkeypatch.setattr(migrations, "build_engine", lambda *a, **kw: pytest.fail("DDL engine constructed"))
    with pytest.raises(PreflightBlocked): migrations.migrate(settings_factory(), Environment.DEVELOPMENT, ROOT)


def test_production_migration_cannot_even_preflight(settings_factory, monkeypatch):
    monkeypatch.setattr(migrations, "preflight", lambda *a, **kw: pytest.fail("Production inspected"))
    with pytest.raises(ConfigurationError): migrations.migrate(settings_factory(environment="production"), Environment.TEST, ROOT)


def test_backend_import_does_not_import_ml():
    result = subprocess.run([sys.executable, "-B", "-c", "import sys; import supportlens.backend.db.models; assert not any(m.startswith('supportlens.ml') for m in sys.modules)"], capture_output=True)
    assert result.returncode == 0


def test_direct_alembic_commands_require_wrapper():
    from alembic.config import Config
    from alembic import command
    with pytest.raises(RuntimeError, match="wrapper|supportlens-db"):
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head", sql=True)


def test_ticket_transition_vocabulary():
    validate_transition(TicketStatus.RESOLVED, TicketStatus.REOPENED)
    with pytest.raises(ValueError): validate_transition(TicketStatus.RESOLVED, TicketStatus.OPEN)


def test_schema_comparison_excludes_only_own_public_version_bookkeeping():
    include = migrations.include_application_object_name
    assert not include("alembic_version", "table", {"schema_name": None})
    assert not include("alembic_version", "table", {"schema_name": "public"})
    assert include("alembic_version", "table", {"schema_name": "unrelated_schema"})
    assert include("unexpected_table", "table", {"schema_name": None})
    assert include("tickets", "table", {"schema_name": "public"})


@pytest.mark.parametrize("drift", [None, "trigger", "function", "check", "metadata"])
def test_live_schema_verifier_detects_drift_without_network(monkeypatch, drift):
    import re
    bindings = [
        ("supportlens_ticket_guard", "tickets", "supportlens_guard_ticket", 23, "O"),
        ("supportlens_internal_message_guard", "ticket_messages", "supportlens_guard_internal_message", 23, "O"),
        ("supportlens_user_role_guard", "users", "supportlens_guard_user_role", 19, "O"),
        ("supportlens_knowledge_revision_guard", "knowledge_articles", "supportlens_guard_knowledge_revision", 19, "O"),
        ("supportlens_audit_append_only_guard", "ticket_events", "supportlens_audit_append_only", 27, "O"),
    ]
    functions = [(re.search(r"CREATE FUNCTION public\.(\w+)", ddl).group(1), ddl.split("AS $$",1)[1].rsplit("$$",1)[0]) for ddl in GUARD_DDL if "CREATE FUNCTION" in ddl]
    if drift == "trigger": bindings = bindings[:-1]
    if drift == "function": functions[0] = (functions[0][0], "BEGIN RETURN NEW; END")
    class Connection:
        def execute(self, statement):
            return bindings if "pg_trigger" in str(statement) else functions
    class Context:
        def get_current_revision(self): return migrations.REVISION
    class Inspector:
        def get_check_constraints(self, name, schema):
            return [] if drift == "check" else [{"name": str(c.name)} for c in Base.metadata.tables[name].constraints if isinstance(c, sa.CheckConstraint)]
    monkeypatch.setattr(migrations.MigrationContext, "configure", lambda *a, **kw: Context())
    monkeypatch.setattr(migrations, "compare_metadata", lambda *a: ["drift"] if drift == "metadata" else [])
    monkeypatch.setattr(migrations, "inspect", lambda *a: Inspector())
    if drift:
        with pytest.raises(PreflightBlocked): migrations.verify_schema(Connection())
    else:
        assert migrations.verify_schema(Connection())["guard_functions_verified"] == 5
