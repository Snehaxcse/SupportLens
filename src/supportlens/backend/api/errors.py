"""Safe errors contain fixed public messages, never driver/SDK details."""
from dataclasses import dataclass


@dataclass
class APIError(Exception):
    status: int
    code: str
    message: str


def unauthorized():
    return APIError(401, "unauthorized", "A valid Firebase ID token is required.")


def unavailable():
    return APIError(503, "dependency_unavailable", "A required service is unavailable.")
