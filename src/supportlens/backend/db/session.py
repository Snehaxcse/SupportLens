"""Explicit engine/session factories; no global engine or implicit connection."""
from contextlib import contextmanager
from dataclasses import replace

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from ..config import Environment, Settings


def build_engine(settings: Settings, target: Environment, *, readonly=False, migration=False, probe=False):
    spec = settings.spec(target, migration=migration, readonly=readonly)
    if probe:
        if not readonly or migration:
            raise ValueError("Dependency probes require a read-only runtime connection.")
        spec = probe_spec(spec)
    # PgBouncer rejects search_path and other safety GUCs in startup options.
    # Apply transaction-local policy on every BEGIN, while its backend is pinned.
    engine = create_engine(
        spec.url.set(drivername="postgresql+psycopg", query={}),
        connect_args={"sslmode": "verify-full", "sslrootcert": spec.ssl_root_cert,
                      "channel_binding": "require", "connect_timeout": spec.connect_timeout,
                      "prepare_threshold": None},
        poolclass=NullPool, hide_parameters=True, echo=False,
    )
    def initialize_transaction(connection):
        # Fixed SQL and validated numeric settings only; no credentials/user input.
        if probe:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY; SET LOCAL TIME ZONE 'UTC'; "
                                       "SET LOCAL search_path TO public; "
                                       f"SET LOCAL statement_timeout = {spec.statement_timeout}")
            return
        connection.exec_driver_sql("SET TRANSACTION READ ONLY" if readonly else "SET TRANSACTION READ WRITE")
        connection.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")
        connection.exec_driver_sql("SET LOCAL search_path TO public")
        connection.exec_driver_sql(f"SET LOCAL statement_timeout = {spec.statement_timeout}")

    event.listen(engine, "begin", initialize_transaction)
    return engine


def probe_spec(spec):
    return replace(spec, connect_timeout=2, statement_timeout=750)


def session_factory(engine):
    """Future callers must preflight the selected engine before obtaining sessions."""
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@contextmanager
def session_scope(factory):
    with factory() as session:
        with session.begin():
            yield session
