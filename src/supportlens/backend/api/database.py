"""Existing TLS/session infrastructure, with identity checks before every transaction."""
from contextlib import contextmanager
import threading

from sqlalchemy.exc import SQLAlchemyError

from ..config import ConfigurationError
from .errors import unavailable

from ..db.migrations import verify_schema
from ..db.preflight import inspect_connection
from ..db.session import build_engine, session_factory


class Database:
    def __init__(self, settings):
        self.settings = settings
        self.target = settings.environment
        settings.spec(self.target)  # Reject production before any connection creation.
        self._engines = {}
        self._lock = threading.Lock()

    def engine(self, readonly):
        with self._lock:
            if readonly not in self._engines:
                self._engines[readonly] = build_engine(self.settings, self.target, readonly=readonly)
            return self._engines[readonly]

    @contextmanager
    def transaction(self, *, readonly=False):
        engine = self.engine(readonly)
        try:
            with engine.connect() as connection:
                with connection.begin():
                    inspect_connection(connection, self.settings.spec(self.target, readonly=readonly), require_readonly=readonly)
                    with session_factory(connection)() as session:
                        yield session
                        if not readonly:
                            session.flush()
        except (SQLAlchemyError, ConfigurationError):
            raise unavailable() from None

    def ready(self):
        with self.engine(True).connect() as connection:
            inspect_connection(connection, self.settings.spec(self.target, readonly=True))
            verify_schema(connection)

    def close(self):
        for engine in self._engines.values():
            engine.dispose()
