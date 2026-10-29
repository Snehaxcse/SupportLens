"""Explicit integration-test gate. No database selection from an implicit default."""
import os

from ..config import ConfigurationError, Environment, Settings


def require_test_opt_in(settings: Settings) -> None:
    if os.environ.get("SUPPORTLENS_RUN_DB_TESTS") != "1":
        raise ConfigurationError("Database integration tests require explicit local opt-in.")
    if settings.environment != Environment.TEST:
        raise ConfigurationError("Database integration tests require ENVIRONMENT=test.")
    # Resolves the approved test identity and rejects unsafe or production configuration.
    settings.spec(Environment.TEST)
