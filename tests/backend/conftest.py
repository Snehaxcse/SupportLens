from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import URL

from supportlens.backend.config import Settings
from supportlens.backend.db.preflight import IDENTITY_SQL, TLS_SQL, OBJECTS_SQL


def synthetic_url(database, host="db.example.invalid", **query):
    return URL.create("postgresql", username="synthetic", password="not-a-real-password",
                      host=host, database=database,
                      query={"sslmode": "verify-full", **query}).render_as_string(hide_password=False)


@pytest.fixture
def settings_factory():
    def create(**overrides):
        values = {"environment": "development", "database_url": synthetic_url("SupportLens_dev"),
                  "test_database_url": synthetic_url("SupportLens_test"), "migration_database_url": None,
                  "test_migration_database_url": None, "expected_dev_database_name": "SupportLens_dev",
                  "expected_test_database_name": "SupportLens_test", "db_connect_timeout_seconds": 10,
                  "db_statement_timeout_ms": 10000, "db_ssl_root_cert": "system"}
        values.update(overrides)
        return Settings(_env_file=None, **values)
    return create


class FakeResult:
    def __init__(self, value): self.value = value
    def mappings(self): return self
    def one(self): return self.value
    def one_or_none(self): return self.value
    def __iter__(self): return iter(self.value)


class FakeConnection:
    def __init__(self, database="SupportLens_dev", objects=(), **changes):
        self.statements = []
        self.closed = False
        self.row = {"database_name": database, "schema_name": "public", "server_version": "17.0",
                    "server_version_num": 170000, "timezone": "UTC", "statement_timeout": "10s",
                    "transaction_read_only": "on", "database_oid": 101 if database.endswith("dev") else 102,
                    "server_now": datetime.now(timezone.utc)}
        self.row.update(changes)
        self.objects = objects
        self.parameters = {"sslmode": "verify-full", "connect_timeout": "10"}
        self.driver = SimpleNamespace(info=SimpleNamespace(get_parameters=lambda: self.parameters),
                                      pgconn=SimpleNamespace(ssl_in_use=True))
        self.connection = SimpleNamespace(driver_connection=self.driver)
    def execute(self, statement):
        query = str(statement)
        self.statements.append(query)
        if query == IDENTITY_SQL: return FakeResult(self.row)
        if query == TLS_SQL: return FakeResult({"ssl": True, "protocol": "TLSv1.3"})
        if query == OBJECTS_SQL: return FakeResult(self.objects)
        raise AssertionError("Unexpected preflight SQL")
    def __enter__(self): return self
    def __exit__(self, *args): self.closed = True


class FakeEngine:
    def __init__(self, connection): self.connection = connection; self.disposed = False
    def connect(self): return self.connection
    def dispose(self): self.disposed = True
