"""M2-B settings extend, rather than change, the verified database contract."""
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator

from ..config import ConfigurationError, Environment, Settings
from ..response_cache.matching import MIN_SIMILARITY


class APISettings(Settings):
    demo_firebase_project_id: str | None = None
    demo_customer_uid: str | None = Field(default=None, repr=False, exclude=True)
    demo_agent_uid: str | None = Field(default=None, repr=False, exclude=True)
    demo_cache_evaluation: bool = False
    demo_cache_ticket_id: UUID | None = None
    demo_ticket_limit: int = Field(default=250, ge=1, le=1000)
    demo_trusted_hosts: tuple[str, ...] = ()
    readiness_timeout_seconds: float = Field(default=4.0, ge=1, le=4.5)
    request_body_timeout_seconds: float = Field(default=10, ge=1, le=60)
    api_ip_requests_per_minute: int = Field(default=180, ge=1, le=1200)
    api_actor_requests_per_minute: int = Field(default=120, ge=1, le=1200)
    response_cache_enabled: bool = False
    response_cache_min_similarity: float = Field(default=MIN_SIMILARITY, ge=MIN_SIMILARITY, le=1, allow_inf_nan=False)
    response_cache_ttl_days: int = Field(default=7, ge=1, le=30)
    workflow_min_confidence: float = Field(default=0.7, ge=0, le=1, allow_inf_nan=False)
    workflow_draft_queue_limit: int = Field(default=32, ge=1, le=100)
    workflow_synthetic_customer_uid: str | None = Field(default=None, max_length=128, repr=False, exclude=True)
    gemini_api_key: SecretStr | None = Field(default=None, repr=False, exclude=True)
    gemini_free_tier_confirmed: bool = False
    gemini_model: str = "gemini-3.1-flash-lite"
    gemini_daily_limit: int = Field(default=8, ge=0, le=20)
    gemini_timeout_seconds: int = Field(default=20, ge=5, le=30)
    gemini_synthetic_ticket_id: UUID | None = None
    gemini_allow_reviewed_context: bool = False
    draft_poll_seconds: int = Field(default=30, ge=1, le=3600)
    knowledge_worker_enabled: bool = True
    knowledge_embeddings_enabled: bool = True
    knowledge_allow_synthetic: bool = False
    knowledge_poll_seconds: int = Field(default=30, ge=1, le=3600)
    classification_enabled: bool = True
    classification_worker_enabled: bool = True
    classification_low_score_threshold: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    classification_poll_seconds: int = Field(default=30, ge=1, le=3600)
    classification_lease_seconds: int = Field(default=300, ge=30, le=3600)
    classification_max_executions: int = Field(default=3, ge=1, le=5)
    classification_retry_cooldown_seconds: int = Field(default=60, ge=10, le=3600)
    classification_retry_limit_per_minute: int = Field(default=10, ge=1, le=100)
    firebase_project_id: str | None = None
    google_application_credentials: str | None = Field(default=None, repr=False)
    cors_allowed_origins: tuple[str, ...] = ()

    @field_validator("firebase_project_id", "demo_firebase_project_id", "google_application_credentials",
                     "gemini_api_key", "gemini_synthetic_ticket_id", "workflow_synthetic_customer_uid",
                     "demo_customer_uid", "demo_agent_uid", "demo_cache_ticket_id", mode="before")
    @classmethod
    def empty_optional(cls, value):
        return None if value == "" else value

    @field_validator("firebase_project_id", "demo_firebase_project_id")
    @classmethod
    def project_id(cls, value):
        if value is not None and not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", value):
            raise ValueError("Expected an explicit Firebase project ID, not an API key.")
        return value

    @field_validator("google_application_credentials")
    @classmethod
    def private_credentials_file(cls, value):
        if value is not None and (not Path(value).is_absolute() or not Path(value).is_file()):
            raise ValueError("GOOGLE_APPLICATION_CREDENTIALS must be an existing absolute private file path.")
        return value

    @field_validator("cors_allowed_origins")
    @classmethod
    def explicit_origins(cls, values):
        for value in values:
            try:
                url = urlsplit(value)
                port = url.port
                if (not url.hostname or url.username or url.password or url.query or url.fragment
                        or url.path or "*" in value or url.scheme not in {"http", "https"}
                        or (url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"})
                        or (port is not None and not 1 <= port <= 65535)):
                    raise ValueError
            except ValueError:
                raise ValueError("CORS requires exact HTTPS origins or explicit local HTTP origins.") from None
        return tuple(dict.fromkeys(values))

    @model_validator(mode="after")
    def isolated_demo(self):
        if self.environment != Environment.SYNTHETIC_DEMO:
            if any((self.demo_firebase_project_id, self.demo_customer_uid, self.demo_agent_uid,
                    self.demo_cache_evaluation, self.demo_cache_ticket_id, self.demo_trusted_hosts)):
                raise ValueError("Demo API settings require the synthetic demo environment.")
            return self
        if (self.firebase_project_id is not None or not self.demo_firebase_project_id
                or self.demo_firebase_project_id == "supportlens-fc39b" or not self.google_application_credentials):
            raise ValueError("Use a dedicated synthetic Firebase project and its explicit private credential source.")
        users = (self.demo_customer_uid, self.demo_agent_uid)
        if any(not uid or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", uid) for uid in users) or users[0] == users[1]:
            raise ValueError("Two separate approved synthetic Firebase identities are required.")
        if (not self.cors_allowed_origins or any(not origin.startswith("https://") for origin in self.cors_allowed_origins)
                or not self.demo_trusted_hosts or any(
                    not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host)
                    for host in self.demo_trusted_hosts)):
            raise ValueError("Synthetic demo requires exact HTTPS origins and explicit backend hostnames.")
        if any((self.gemini_api_key, self.gemini_free_tier_confirmed, self.gemini_synthetic_ticket_id,
                self.gemini_allow_reviewed_context)):
            raise ValueError("Gemini settings are forbidden in the synthetic demo.")
        if (self.workflow_synthetic_customer_uid or self.response_cache_enabled != self.demo_cache_evaluation
                or bool(self.demo_cache_ticket_id) != self.demo_cache_evaluation):
            raise ValueError("Synthetic reuse requires the explicit evaluation opt-in and uses only the approved customer.")
        if self.workflow_min_confidence < 0.7 or self.classification_low_score_threshold < 0.5:
            raise ValueError("Synthetic demo cannot lower the established confidence floors.")
        if self.demo_migration_database_url:
            raise ValueError("A runtime process must not carry the demo migration credential.")
        return self

    @property
    def auth_project_id(self):
        return self.demo_firebase_project_id if self.environment == Environment.SYNTHETIC_DEMO else self.firebase_project_id


def load_api_settings():
    try:
        return APISettings()
    except (ValidationError, ValueError):
        raise ConfigurationError("API configuration is invalid. Review the environment-specific database, Firebase and CORS settings; values are suppressed.") from None
