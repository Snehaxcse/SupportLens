"""Exercise the real SDK's cryptographic checks using in-memory synthetic keys.

No Firebase account, service-account key, or live token is used. Only public-key
fetching and the remote user/revocation record are replaced with local fixtures.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import firebase_admin
from firebase_admin import _auth_client
from google.auth.credentials import AnonymousCredentials
import google.oauth2.id_token
import jwt
import pytest

from supportlens.backend.api.config import APISettings
from supportlens.backend.api.errors import APIError
from supportlens.backend.auth.firebase import FirebaseVerifier

PROJECT = "supportlens-unit"


@pytest.fixture(scope="module")
def synthetic_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def real_sdk(settings_factory, synthetic_key, monkeypatch):
    monkeypatch.delenv("FIREBASE_AUTH_EMULATOR_HOST", raising=False)
    settings = APISettings(_env_file=None, **settings_factory().model_dump(), firebase_project_id=PROJECT,
                           google_application_credentials=None, cors_allowed_origins=())
    verifier = FirebaseVerifier(settings)
    verifier._app = firebase_admin.initialize_app(AnonymousCredentials(), options={"projectId": PROJECT},
                                                name="synthetic-unit-" + uuid.uuid4().hex)
    public_key = synthetic_key.public_key().public_bytes(serialization.Encoding.PEM,
                                                        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    monkeypatch.setattr(google.oauth2.id_token, "_fetch_certs", Mock(return_value={"synthetic-key": public_key}))
    remote_user = SimpleNamespace(disabled=False, tokens_valid_after_timestamp=0)
    lookup = Mock(return_value=remote_user)
    monkeypatch.setattr(_auth_client.Client, "get_user", lookup)
    try:
        yield verifier, remote_user, lookup
    finally:
        verifier.close()


def signed_token(key, **updates):
    now = int(datetime.now(timezone.utc).timestamp())
    payload = {"sub": "synthetic-sdk-uid", "aud": PROJECT, "iss": "https://securetoken.google.com/" + PROJECT,
               "iat": now - 60, "exp": now + 600, "auth_time": now - 60, "email_verified": True,
               "email": "synthetic@example.invalid", **updates}
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": "synthetic-key"})


def test_real_sdk_accepts_valid_signature_and_checks_user_revocation(real_sdk, synthetic_key):
    verifier, remote_user, lookup = real_sdk
    assert verifier.verify(signed_token(synthetic_key)).uid == "synthetic-sdk-uid"
    lookup.assert_called_once_with("synthetic-sdk-uid")


@pytest.mark.parametrize("failure", ["signature", "audience", "issuer", "expired", "future", "revoked", "disabled"])
def test_real_sdk_rejects_invalid_synthetic_tokens(real_sdk, synthetic_key, failure):
    verifier, remote_user, lookup = real_sdk
    now = int(datetime.now(timezone.utc).timestamp())
    updates = {}
    key = synthetic_key
    if failure == "signature":
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    elif failure == "audience":
        updates["aud"] = "wrong-project"
    elif failure == "issuer":
        updates["iss"] = "https://untrusted.example.invalid/"
    elif failure == "expired":
        updates.update(iat=now-1000, exp=now-500)
    elif failure == "future":
        updates["iat"] = now+600
    elif failure == "revoked":
        remote_user.tokens_valid_after_timestamp = now * 1000
    elif failure == "disabled":
        remote_user.disabled = True
    with pytest.raises(APIError) as error:
        verifier.verify(signed_token(key, **updates))
    assert error.value.status == 401
