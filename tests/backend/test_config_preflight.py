from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from supportlens.backend.config import ConfigurationError, Environment, Settings
from supportlens.backend.db import preflight as module
from supportlens.backend.db import session
from supportlens.backend.db.cli import main
from supportlens.backend.db.preflight import PreflightBlocked, ensure_empty, inspect_connection, preflight, verify_distinct
from .conftest import FakeConnection, FakeEngine, synthetic_url


def test_missing_required_variables(monkeypatch):
    for key in ("ENVIRONMENT", "DATABASE_URL", "TEST_DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("value", ["prod", "staging", "", "TEST"])
def test_unsafe_environment_selection(settings_factory, value):
    with pytest.raises(ValidationError): settings_factory(environment=value)


def test_production_refused_before_engine_creation(settings_factory, monkeypatch):
    settings = settings_factory(environment="production")
    monkeypatch.setattr(module, "build_engine", lambda *a, **kw: pytest.fail("Production connection attempted"))
    with pytest.raises(ConfigurationError, match="production"):
        preflight(settings)


def test_test_process_cannot_open_development_write_session(settings_factory):
    settings = settings_factory(environment="test")
    with pytest.raises(ConfigurationError): settings.spec(Environment.DEVELOPMENT)
    assert settings.spec(Environment.DEVELOPMENT, readonly=True).target == Environment.DEVELOPMENT


@pytest.mark.parametrize("mode", ["disable", "allow", "prefer", "require", "verify-ca"])
def test_unverified_tls_modes_rejected(settings_factory, mode):
    with pytest.raises(ValidationError): settings_factory(database_url=synthetic_url("SupportLens_dev", sslmode=mode))


def test_database_aliases_and_mismatched_names_rejected(settings_factory):
    with pytest.raises(ValidationError): settings_factory(test_database_url=synthetic_url("SupportLens_dev"))
    with pytest.raises(ValidationError): settings_factory(database_url=synthetic_url("postgres"))


def test_url_options_cannot_override_safety(settings_factory):
    with pytest.raises(ValidationError): settings_factory(database_url=synthetic_url("SupportLens_dev", options="unsafe"))
    with pytest.raises(ValidationError): settings_factory(database_url=synthetic_url("SupportLens_dev", channel_binding="disable"))


def test_secret_repr_and_validation_errors_are_redacted(settings_factory):
    settings = settings_factory()
    assert "not-a-real-password" not in repr(settings)
    assert "not-a-real-password" not in repr(settings.spec(Environment.DEVELOPMENT))
    with pytest.raises(ValidationError) as error:
        settings_factory(database_url=synthetic_url("SupportLens_dev", sslmode="disable"))
    assert "not-a-real-password" not in str(error.value)


def test_pooler_requires_matching_direct_migration_endpoint(settings_factory):
    settings = settings_factory(database_url=synthetic_url("SupportLens_dev", host="branch-pooler.example.invalid"))
    with pytest.raises(ConfigurationError, match="direct"):
        settings.spec(Environment.DEVELOPMENT, migration=True)
    direct = settings_factory(database_url=synthetic_url("SupportLens_dev", host="branch-pooler.example.invalid"),
                              migration_database_url=synthetic_url("SupportLens_dev", host="branch.example.invalid"))
    assert direct.spec(Environment.DEVELOPMENT, migration=True).url.host == "branch.example.invalid"
    with pytest.raises(ValidationError): settings_factory(migration_database_url=synthetic_url("SupportLens_dev", host="different.example.invalid"))


@pytest.mark.parametrize("overrides", [{"db_connect_timeout_seconds": 0}, {"db_statement_timeout_ms": 0}, {"db_ssl_root_cert": "missing.crt"}])
def test_invalid_timeouts_and_ca_rejected(settings_factory, overrides):
    with pytest.raises(ValidationError): settings_factory(**overrides)


def test_driver_arguments_enforce_tls_utc_and_timeouts(settings_factory, monkeypatch):
    captured = {}
    engine = object()
    def create_engine(url, **kw):
        captured.update(url=url, **kw)
        return engine
    monkeypatch.setattr(session, "create_engine", create_engine)
    def listen(target, name, callback):
        assert target is engine and name == "begin"
        captured["begin_callback"] = callback
    monkeypatch.setattr(session.event, "listen", listen)
    session.build_engine(settings_factory(), Environment.TEST, readonly=True)
    assert captured["hide_parameters"] is True and captured["echo"] is False
    args = captured["connect_args"]
    assert args["sslmode"] == "verify-full" and args["sslrootcert"] == "system"
    assert args["channel_binding"] == "require" and args["connect_timeout"] == 10
    assert "options" not in args
    assert args["prepare_threshold"] is None
    class Connection:
        statements = []
        def exec_driver_sql(self, sql): self.statements.append(sql)
    connection = Connection()
    captured["begin_callback"](connection)
    expected = ["SET TRANSACTION READ ONLY", "SET LOCAL TIME ZONE 'UTC'", "SET LOCAL search_path TO public", "SET LOCAL statement_timeout = 10000"]
    assert connection.statements == expected
    captured["begin_callback"](connection)
    assert connection.statements == expected * 2


def test_preflight_catalog_queries_only_select_and_close_without_commit(settings_factory, monkeypatch):
    connections = [FakeConnection(), FakeConnection("SupportLens_test")]
    engines = [FakeEngine(c) for c in connections]
    iterator = iter(engines)
    monkeypatch.setattr(module, "build_engine", lambda *a, **kw: next(iterator))
    results = preflight(settings_factory(), require_empty_test=True)
    assert results[Environment.TEST].verified_client_tls
    assert all(c.closed for c in connections) and all(e.disposed for e in engines)
    assert all(query.lstrip().upper().startswith("SELECT") for c in connections for query in c.statements)
    assert sum(len(c.statements) for c in connections) == 6


@pytest.mark.parametrize("changes", [{"database_name": "unexpected"}, {"timezone": "Europe/London"},
    {"schema_name": "unexpected"}, {"statement_timeout": "0"}, {"transaction_read_only": "off"},
    {"server_now": datetime(2026,1,1)}, {"server_version_num": 120000}])
def test_server_identity_and_session_mismatches_block(settings_factory, changes):
    with pytest.raises(PreflightBlocked): inspect_connection(FakeConnection(**changes), settings_factory().spec(Environment.DEVELOPMENT))


def test_client_tls_failure_blocks(settings_factory):
    connection = FakeConnection()
    connection.driver.pgconn.ssl_in_use = False
    with pytest.raises(PreflightBlocked): inspect_connection(connection, settings_factory().spec(Environment.DEVELOPMENT))


def test_actual_database_identity_isolation(settings_factory):
    settings = settings_factory()
    dev = inspect_connection(FakeConnection(), settings.spec(Environment.DEVELOPMENT))
    with pytest.raises(PreflightBlocked): verify_distinct(dev, replace(dev, target="test"))
    with pytest.raises(PreflightBlocked): verify_distinct(dev, replace(dev, target="test", database_name="SupportLens_test"))


def test_unexpected_objects_block_migration_inventory(settings_factory):
    identity = inspect_connection(FakeConnection(objects=(("public", "unrelated_table", "r"),)), settings_factory().spec(Environment.DEVELOPMENT))
    with pytest.raises(PreflightBlocked, match="owner review"): ensure_empty(identity)


def test_cli_suppresses_driver_credentials(settings_factory, monkeypatch, capsys):
    import supportlens.backend.db.cli as cli
    monkeypatch.setattr(cli, "load_settings", lambda: settings_factory())
    def fail(*a, **kw): raise RuntimeError("not-a-real-password")
    monkeypatch.setattr(cli, "preflight", fail)
    assert main(["preflight"]) == 2
    assert "not-a-real-password" not in capsys.readouterr().out


@pytest.mark.parametrize("command", ["reset", "downgrade", "drop"])
def test_destructive_cli_commands_do_not_exist(command):
    with pytest.raises(SystemExit) as error: main([command])
    assert error.value.code == 2


def test_test_writes_require_explicit_opt_in_and_test_environment(settings_factory, monkeypatch):
    from supportlens.backend.db.testing import require_test_opt_in
    monkeypatch.delenv("SUPPORTLENS_RUN_DB_TESTS", raising=False)
    with pytest.raises(ConfigurationError): require_test_opt_in(settings_factory(environment="test"))
    monkeypatch.setenv("SUPPORTLENS_RUN_DB_TESTS", "1")
    with pytest.raises(ConfigurationError): require_test_opt_in(settings_factory(environment="development"))
    require_test_opt_in(settings_factory(environment="test"))


def test_untyped_target_rejected(settings_factory):
    with pytest.raises(ConfigurationError): settings_factory().spec("unexpected")
