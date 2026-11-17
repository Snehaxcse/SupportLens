"""Supported Admin SDK verification with revocation checks and explicit project binding."""
from dataclasses import dataclass
import os
import threading
import uuid

import firebase_admin
from firebase_admin import auth, credentials

from ..api.errors import unauthorized, unavailable
from ..config import Environment


@dataclass(frozen=True, repr=False)
class VerifiedIdentity:
    uid: str
    email: str | None = None
    display_name: str | None = None


def safe_profile_text(value, maximum):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(character) < 32 or ord(character) == 127 for character in value)):
        return None
    return value.strip()


class FirebaseVerifier:
    def __init__(self, settings):
        self.settings = settings
        self._app = None
        self._lock = threading.Lock()

    def _ensure_app(self):
        # The emulator intentionally accepts unsigned tokens. It is never valid here.
        if "FIREBASE_AUTH_EMULATOR_HOST" in os.environ or not self.settings.auth_project_id:
            raise unavailable()
        with self._lock:
            if self._app is None:
                try:
                    source = (credentials.Certificate(self.settings.google_application_credentials)
                              if self.settings.google_application_credentials else credentials.ApplicationDefault())
                    source.get_credential()  # Detect missing ADC before claiming readiness.
                    demo = self.settings.environment == Environment.SYNTHETIC_DEMO
                    if demo and source.project_id != self.settings.auth_project_id:
                        raise ValueError
                    self._app = firebase_admin.initialize_app(source, options={
                        "projectId": self.settings.auth_project_id, "httpTimeout": 2 if demo else 10,
                    }, name="supportlens-" + uuid.uuid4().hex)
                except Exception:
                    raise unavailable() from None
        return self._app

    def ready(self):
        self._ensure_app()

    def readiness_probe(self):
        app = self._ensure_app()
        if self.settings.environment == Environment.SYNTHETIC_DEMO:
            probe_user = auth.get_user(self.settings.demo_customer_uid, app=app)
            if probe_user.disabled or not probe_user.email_verified:
                raise unavailable()
        else:
            auth.list_users(max_results=1, app=app)

    def verify(self, token: str) -> VerifiedIdentity:
        app = self._ensure_app()
        try:
            claims = auth.verify_id_token(token, app=app, check_revoked=True, clock_skew_seconds=0)
        except (auth.InvalidIdTokenError, auth.ExpiredIdTokenError, auth.RevokedIdTokenError,
                auth.UserDisabledError, auth.UserNotFoundError, ValueError):
            raise unauthorized() from None
        except Exception:
            # Network/key-fetch/IAM failures deny access without leaking SDK messages.
            raise unavailable() from None
        project = self.settings.auth_project_id
        uid = claims.get("uid")
        firebase_claims = claims.get("firebase", {})
        if (not isinstance(uid, str) or not uid.strip() or len(uid) > 128
                or claims.get("sub") != uid or claims.get("aud") != project
                or claims.get("iss") != "https://securetoken.google.com/" + project
                or not isinstance(firebase_claims, dict) or firebase_claims.get("tenant")):
            raise unauthorized()
        email = safe_profile_text(claims.get("email"), 320) if claims.get("email_verified") is True else None
        if email and (email.count("@") != 1 or any(c.isspace() for c in email)
                      or not all(email.split("@"))):
            email = None
        # Name/email are profile data only; roles/custom claims never grant permissions.
        return VerifiedIdentity(uid, email, safe_profile_text(claims.get("name"), 120))

    def close(self):
        if not self._lock.acquire(timeout=0.25):
            return
        try:
            if self._app is not None:
                firebase_admin.delete_app(self._app)
                self._app = None
        finally:
            self._lock.release()
