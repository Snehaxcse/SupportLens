"""Read-only identity/TLS/inventory checks. No table creation or application data reads."""
from dataclasses import asdict, dataclass
import re

from sqlalchemy import text

from ..config import ConfigurationError, ConnectionSpec, Environment, Settings
from .session import build_engine


class PreflightBlocked(ConfigurationError):
    pass


IDENTITY_SQL = """
SELECT current_database() AS database_name, current_schema() AS schema_name,
       current_setting('server_version') AS server_version,
       current_setting('server_version_num')::integer AS server_version_num,
       current_setting('TimeZone') AS timezone,
       current_setting('statement_timeout') AS statement_timeout,
       current_setting('transaction_read_only') AS transaction_read_only,
       (SELECT oid::bigint FROM pg_database WHERE datname=current_database()) AS database_oid,
       now() AS server_now
"""
TLS_SQL = "SELECT ssl, version AS protocol FROM pg_stat_ssl WHERE pid=pg_backend_pid()"
OBJECTS_SQL = """
SELECT n.nspname AS schema_name, c.relname AS object_name, c.relkind::text AS object_type
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
UNION ALL
SELECT n.nspname, p.proname, 'function' FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
UNION ALL
SELECT n.nspname, t.typname, 'enum' FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
WHERE t.typtype IN ('e','d') AND n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
UNION ALL
SELECT nspname, nspname, 'schema' FROM pg_namespace
WHERE nspname NOT LIKE 'pg_%' AND nspname NOT IN ('public','information_schema')
UNION ALL
SELECT n.nspname, e.extname, 'extension' FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace
WHERE e.extname <> 'plpgsql'
ORDER BY 1, 2, 3
"""


@dataclass(frozen=True)
class DatabaseIdentity:
    target: str
    database_name: str
    database_oid: int
    schema_name: str
    server_version: str
    timezone: str
    statement_timeout_ms: int
    connect_timeout_seconds: int
    verified_client_tls: bool
    tls_mode: str
    backend_tls: bool
    tls_protocol: str | None
    read_only: bool
    endpoint_fingerprint: str
    objects: tuple[tuple[str, str, str], ...]

    def public_report(self):
        return asdict(self)


def milliseconds(value: str) -> int:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|min)?", value)
    if not match:
        raise PreflightBlocked("Unrecognized server statement timeout.")
    scale = {None: 1, "ms": 1, "s": 1000, "min": 60000}[match.group(2)]
    return int(float(match.group(1)) * scale)


def client_security(connection) -> tuple[bool, str, int]:
    try:
        driver = connection.connection.driver_connection
        parameters = driver.info.get_parameters()
        return bool(driver.pgconn.ssl_in_use), parameters.get("sslmode", ""), int(parameters.get("connect_timeout", "0"))
    except Exception:
        raise PreflightBlocked("Could not attest the client TLS/timeout configuration.") from None


def inspect_connection(connection, spec: ConnectionSpec, *, require_readonly=True) -> DatabaseIdentity:
    row = connection.execute(text(IDENTITY_SQL)).mappings().one()
    if row["database_name"] != spec.expected_database:
        raise PreflightBlocked("Queried database identity differs from the approved target.")
    if row["schema_name"] != "public" or row["timezone"] != "UTC":
        raise PreflightBlocked("Expected public schema and UTC session were not established.")
    if row["server_version_num"] < 130000:
        raise PreflightBlocked("PostgreSQL 13 or newer is required by this migration.")
    now = row["server_now"]
    if now.tzinfo is None or now.utcoffset().total_seconds() != 0:
        raise PreflightBlocked("UTC-aware server timestamp behavior could not be verified.")
    timeout = milliseconds(row["statement_timeout"])
    if timeout != spec.statement_timeout:
        raise PreflightBlocked("Server statement timeout does not match configuration.")
    readonly = row["transaction_read_only"] == "on"
    if require_readonly and not readonly:
        raise PreflightBlocked("Read-only transaction protection was not established.")
    encrypted, mode, connect_timeout = client_security(connection)
    if not encrypted or mode != "verify-full" or connect_timeout != spec.connect_timeout:
        raise PreflightBlocked("Verified client TLS or connection timeout checks failed.")
    tls = connection.execute(text(TLS_SQL)).mappings().one_or_none()
    objects = tuple(tuple(row) for row in connection.execute(text(OBJECTS_SQL)))
    return DatabaseIdentity(
        target=spec.target.value, database_name=row["database_name"], database_oid=int(row["database_oid"]),
        schema_name=row["schema_name"], server_version=row["server_version"], timezone=row["timezone"],
        statement_timeout_ms=timeout, connect_timeout_seconds=connect_timeout,
        verified_client_tls=True, tls_mode=mode, backend_tls=bool(tls and tls["ssl"]),
        tls_protocol=tls["protocol"] if tls else None, read_only=readonly,
        endpoint_fingerprint=spec.endpoint_fingerprint, objects=objects,
    )


def ensure_empty(identity: DatabaseIdentity) -> None:
    if identity.objects:
        raise PreflightBlocked("Target contains existing objects. Stop; owner review is required before migration.")


def verify_distinct(development: DatabaseIdentity, test: DatabaseIdentity) -> None:
    if development.database_name == test.database_name:
        raise PreflightBlocked("Development and test resolve to the same database identity.")
    if (development.endpoint_fingerprint == test.endpoint_fingerprint
            and development.database_oid == test.database_oid):
        raise PreflightBlocked("Development and test share the same endpoint/database OID.")
    if development.target != "development" or test.target != "test":
        raise PreflightBlocked("Identity target mapping is invalid.")


def preflight(settings: Settings, *, require_empty_test=False) -> dict[Environment, DatabaseIdentity]:
    results = {}
    for target in (Environment.DEVELOPMENT, Environment.TEST):
        spec = settings.spec(target, readonly=True)
        engine = build_engine(settings, target, readonly=True)
        try:
            with engine.connect() as connection:
                results[target] = inspect_connection(connection, spec)
                # No commit; connection close rolls the read-only transaction back.
        except ConfigurationError:
            raise
        except Exception:
            raise PreflightBlocked("Database connection/preflight failed. Driver details are suppressed to protect credentials.") from None
        finally:
            engine.dispose()
    verify_distinct(results[Environment.DEVELOPMENT], results[Environment.TEST])
    if require_empty_test:
        ensure_empty(results[Environment.TEST])
    return results
