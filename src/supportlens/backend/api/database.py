"""Existing TLS/session infrastructure, with identity checks before every transaction."""
from contextlib import contextmanager
import threading

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ..config import ConfigurationError, Environment
from .errors import unavailable

from ..db.migrations import CACHE_REVISION, KNOWLEDGE_REVISION, REVISION, verify_schema
from ..db.preflight import inspect_connection
from ..db.session import build_engine, probe_spec, session_factory


DEMO_ROLE_SQL = text("""
    SELECT NOT (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls)
           AND NOT has_schema_privilege(current_user, 'public', 'CREATE')
           AND NOT pg_has_role(current_user, 'supportlens_demo_migrate', 'MEMBER')
      FROM pg_roles r
     WHERE r.rolname = current_user AND r.rolname = 'supportlens_demo_app'
""")
RELEASE_SCHEMA_SQL = text("""
    SELECT to_regclass('public.users') IS NOT NULL
       AND to_regclass('public.tickets') IS NOT NULL
       AND to_regclass('public.ticket_messages') IS NOT NULL
       AND to_regclass('public.ticket_events') IS NOT NULL
       AND to_regclass('public.ai_runs') IS NOT NULL
       AND to_regclass('public.knowledge_revisions') IS NOT NULL
       AND to_regclass('public.response_cache_entries') IS NOT NULL
""")


class Database:
    def __init__(self, settings):
        self.settings = settings
        self.target = settings.environment
        settings.spec(self.target)  # Reject production before any connection creation.
        self._engines = {}
        self._lock = threading.Lock()

    def engine(self, readonly, *, probe=False):
        key = (readonly, probe)
        with self._lock:
            if key not in self._engines:
                self._engines[key] = build_engine(self.settings, self.target, readonly=readonly, **({"probe": True} if probe else {}))
            return self._engines[key]

    @contextmanager
    def transaction(self, *, readonly=False):
        engine = self.engine(readonly)
        try:
            with engine.connect() as connection:
                with connection.begin():
                    inspect_connection(connection, self.settings.spec(self.target, readonly=readonly),
                                       require_readonly=readonly, include_inventory=False)
                    self.verify_runtime_role(connection)
                    with session_factory(connection)() as session:
                        yield session
                        if not readonly:
                            session.flush()
        except (SQLAlchemyError, ConfigurationError):
            raise unavailable() from None

    def ready(self, *, required_revision=None):
        with self.engine(True).connect() as connection:
            inspect_connection(connection, self.settings.spec(self.target, readonly=True))
            verify_schema(connection, required_revision=required_revision)

    def verify_runtime_role(self, connection):
        if self.target == Environment.SYNTHETIC_DEMO and connection.scalar(DEMO_ROLE_SQL) is not True:
            raise ConfigurationError("Synthetic demo runtime role failed its privilege guard.")

    def readiness_probe(self, *, required_revision=None):
        spec = probe_spec(self.settings.spec(self.target, readonly=True))
        with self.engine(True, probe=True).connect() as connection:
            inspect_connection(connection, spec, include_inventory=False)
            self.verify_runtime_role(connection)
            versions = connection.execute(text("SELECT version_num FROM public.alembic_version LIMIT 2")).scalars().all()
            allowed = {required_revision} if required_revision else {REVISION, KNOWLEDGE_REVISION, CACHE_REVISION}
            if len(versions) != 1 or versions[0] not in allowed:
                raise ConfigurationError("Database revision failed the runtime readiness check.")
            if required_revision == CACHE_REVISION and not connection.scalar(RELEASE_SCHEMA_SQL):
                raise ConfigurationError("Required release relations are missing.")

    def close(self):
        for engine in self._engines.values():
            engine.dispose()
