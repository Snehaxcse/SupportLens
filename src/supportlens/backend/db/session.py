"""Explicit engine/session factories; no global engine or implicit connection."""
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from ..config import Environment, Settings


def build_engine(settings: Settings, target: Environment, *, readonly=False, migration=False):
    spec = settings.spec(target, migration=migration, readonly=readonly)
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
        connection.exec_driver_sql("SET TRANSACTION READ ONLY" if readonly else "SET TRANSACTION READ WRITE")
        connection.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")
        connection.exec_driver_sql("SET LOCAL search_path TO public")
        connection.exec_driver_sql(f"SET LOCAL statement_timeout = {spec.statement_timeout}")

    event.listen(engine, "begin", initialize_transaction)
    return engine


def session_factory(engine):
    """Future callers must preflight the selected engine before obtaining sessions."""
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@contextmanager
def session_scope(factory):
    with factory() as session:
        with session.begin():
            yield session
