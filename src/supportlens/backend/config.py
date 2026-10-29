"""Typed secret-safe configuration; no settings are loaded at import time."""
import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url


class ConfigurationError(ValueError):
    """Safe operational message; never include connection strings or driver errors."""


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


def parse_database_url(secret: SecretStr) -> URL:
    try:
        url = make_url(secret.get_secret_value())
        if (url.drivername not in {"postgresql", "postgresql+psycopg"}
                or not url.host or not url.username or not url.password or not url.database
                or not re.fullmatch(r"[A-Za-z0-9_]{1,63}", url.database)
                or "," in url.host or "/" in url.host):
            raise ValueError
        if any("REPLACE_" in str(v) for v in (url.host, url.username, url.password)):
            raise ValueError
        allowed = {"sslmode", "sslrootcert", "channel_binding"}
        if set(url.query) - allowed or any(not isinstance(v, str) for v in url.query.values()):
            raise ValueError
        if url.query.get("sslmode", "verify-full") != "verify-full":
            raise ValueError
        if url.query.get("channel_binding", "require") != "require":
            raise ValueError
        return url
    except Exception:
        raise ValueError("Invalid PostgreSQL URL or unsafe TLS options.") from None


@dataclass(frozen=True, repr=False)
class ConnectionSpec:
    url: URL
    expected_database: str
    target: Environment
    connect_timeout: int
    statement_timeout: int
    ssl_root_cert: str

    def __repr__(self):
        return "ConnectionSpec(credentials=REDACTED)"

    @property
    def endpoint_fingerprint(self) -> str:
        return hashlib.sha256(f"{self.url.host}:{self.url.port or 5432}".encode()).hexdigest()[:16]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore",
        hide_input_in_errors=True, frozen=True,
    )
    environment: Environment
    database_url: SecretStr = Field(repr=False)
    test_database_url: SecretStr = Field(repr=False)
    migration_database_url: SecretStr | None = Field(default=None, repr=False)
    test_migration_database_url: SecretStr | None = Field(default=None, repr=False)
    expected_dev_database_name: str = "SupportLens_dev"
    expected_test_database_name: str = "SupportLens_test"
    db_connect_timeout_seconds: int = Field(default=10, ge=1, le=30)
    db_statement_timeout_ms: int = Field(default=10_000, ge=100, le=120_000)
    db_ssl_root_cert: str = "system"

    @field_validator("migration_database_url", "test_migration_database_url", mode="before")
    @classmethod
    def empty_optional_url(cls, value):
        return None if value == "" else value

    @field_validator("database_url", "test_database_url", "migration_database_url", "test_migration_database_url")
    @classmethod
    def validate_url(cls, value):
        if value is not None:
            parse_database_url(value)
        return value

    @field_validator("db_ssl_root_cert")
    @classmethod
    def trusted_ca(cls, value):
        if value == "system":
            return value
        path = Path(value)
        if not path.is_absolute() or not path.is_file():
            raise ValueError("CA bundle must be system trust or an existing absolute file path.")
        return str(path)

    @model_validator(mode="after")
    def distinct_databases(self):
        # Names below are an explicit allowlist from the approved project setup.
        if (self.expected_dev_database_name != "SupportLens_dev"
                or self.expected_test_database_name != "SupportLens_test"):
            raise ValueError("Database identity allowlist requires a reviewed configuration change.")
        dev, test = parse_database_url(self.database_url), parse_database_url(self.test_database_url)
        if dev.database != self.expected_dev_database_name or test.database != self.expected_test_database_name:
            raise ValueError("Configured database names do not match the approved identity allowlist.")
        if dev.database == test.database:
            raise ValueError("Development and test databases must be distinct.")
        for value, expected in ((self.migration_database_url, dev.database), (self.test_migration_database_url, test.database)):
            if value:
                url = parse_database_url(value)
                if url.database != expected or "-pooler." in url.host:
                    raise ValueError("Migration URL must select the expected database through a direct endpoint.")
                application = dev if expected == dev.database else test
                if (url.host != application.host.replace("-pooler.", ".")
                        or (url.port or 5432) != (application.port or 5432)):
                    raise ValueError("Direct migration endpoint does not match its application endpoint.")
        for url in (dev, test):
            if url.query.get("sslrootcert", self.db_ssl_root_cert) != self.db_ssl_root_cert:
                raise ValueError("URL trust configuration conflicts with DB_SSL_ROOT_CERT.")
        return self

    def spec(self, target: Environment, *, migration: bool = False, readonly: bool = False) -> ConnectionSpec:
        if not isinstance(target, Environment):
            raise ConfigurationError("An explicit typed database target is required.")
        if self.environment == Environment.PRODUCTION or target == Environment.PRODUCTION:
            raise ConfigurationError("M2-A commands never access production databases.")
        if self.environment == Environment.TEST and target != Environment.TEST and not readonly:
            raise ConfigurationError("A test-selected process cannot create a development session.")
        if target == Environment.DEVELOPMENT:
            value = self.migration_database_url if migration else self.database_url
            value = value or self.database_url
            expected = self.expected_dev_database_name
        else:
            value = self.test_migration_database_url if migration else self.test_database_url
            value = value or self.test_database_url
            expected = self.expected_test_database_name
        url = parse_database_url(value)
        if migration and "-pooler." in url.host:
            raise ConfigurationError("A direct migration URL is required for this pooled endpoint.")
        return ConnectionSpec(url, expected, target, self.db_connect_timeout_seconds,
                              self.db_statement_timeout_ms, self.db_ssl_root_cert)


def load_settings() -> Settings:
    try:
        return Settings()
    except (ValidationError, ValueError):
        raise ConfigurationError(
            "Configuration blocked. Set ENVIRONMENT, DATABASE_URL and TEST_DATABASE_URL in ignored .env; "
            "use approved database names and verified TLS. Values are never logged."
        ) from None
