"""M2-B settings extend, rather than change, the verified database contract."""
from pathlib import Path
import re
from urllib.parse import urlsplit

from pydantic import Field, ValidationError, field_validator

from ..config import ConfigurationError, Settings


class APISettings(Settings):
    firebase_project_id: str | None = None
    google_application_credentials: str | None = Field(default=None, repr=False)
    cors_allowed_origins: tuple[str, ...] = ()

    @field_validator("firebase_project_id", "google_application_credentials", mode="before")
    @classmethod
    def empty_optional(cls, value):
        return None if value == "" else value

    @field_validator("firebase_project_id")
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


def load_api_settings():
    try:
        return APISettings()
    except (ValidationError, ValueError):
        raise ConfigurationError("API configuration is invalid. Review database, Firebase and CORS variable names in .env.example; values are suppressed.") from None
