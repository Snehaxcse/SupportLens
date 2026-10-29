"""Offline HTTP/SDK boundary checks: no real token, credential, or socket required."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

from fastapi import Depends
from fastapi.testclient import TestClient
from firebase_admin import auth
from pydantic import ValidationError
import pytest

from supportlens.backend.api.app import create_app
from supportlens.backend.api.config import APISettings
from supportlens.backend.api.errors import APIError, unavailable
from supportlens.backend.auth.dependencies import agent_only, customer_only
from supportlens.backend.auth.firebase import FirebaseVerifier, VerifiedIdentity
from supportlens.backend.auth.users import CurrentUser, UserService, require_active
from supportlens.backend.domain.enums import UserRole

TOKEN = "synthetic.payload.signature"
HEADERS = {"Authorization": "Bearer " + TOKEN}
PROJECT = "supportlens-unit"


@pytest.fixture
def api_settings(settings_factory):
    values = settings_factory().model_dump()
    return APISettings(_env_file=None, **values, firebase_project_id=PROJECT,
                       google_application_credentials=None, cors_allowed_origins=("http://localhost:3000",))


@pytest.fixture
def identity():
    return VerifiedIdentity("synthetic-unit-uid", "synthetic@example.invalid", "Synthetic")


@pytest.fixture
def user():
    return CurrentUser(uuid.uuid4(), "synthetic@example.invalid", "Synthetic", UserRole.CUSTOMER,
                       True, datetime.now(timezone.utc))


@pytest.fixture
def runtime(identity, user):
    return SimpleNamespace(verifier=SimpleNamespace(verify=Mock(return_value=identity), ready=Mock(), close=Mock()),
        users=SimpleNamespace(current=Mock(return_value=user), onboard=Mock(return_value=user)),
        database=SimpleNamespace(ready=Mock(), close=Mock()))


@pytest.fixture
def client(api_settings, runtime):
    with TestClient(create_app(api_settings, runtime=runtime)) as instance:
        yield instance


@pytest.mark.parametrize("headers", [{}, {"Authorization": ""}, {"Authorization": "Basic synthetic"},
    {"Authorization": "Bearer opaque"}, {"Authorization": "Bearer a.b.c extra"},
    {"Authorization": "Bearer a..c"}, {"Authorization": "Bearer " + "a" * 17000 + ".b.c"},
    [("authorization", "Bearer a.b.c"), ("authorization", "Bearer d.e.f")]])
def test_missing_and_malformed_authorization(client, runtime, headers):
    response = client.get("/api/v1/me", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    runtime.verifier.verify.assert_not_called()
    runtime.users.current.assert_not_called()


def test_me_uses_verified_uid_and_database_role(client, runtime):
    response = client.get("/api/v1/me", headers=HEADERS)
    assert response.status_code == 200 and response.json()["role"] == "CUSTOMER"
    assert "firebase_uid" not in response.json()
    runtime.verifier.verify.assert_called_once_with(TOKEN)
    runtime.users.current.assert_called_once_with("synthetic-unit-uid")


def test_unknown_user_fails_closed(client, runtime):
    runtime.users.current.side_effect = APIError(403, "onboarding_required", "Complete onboarding.")
    assert client.get("/api/v1/me", headers=HEADERS).status_code == 403


@pytest.mark.parametrize("route", ["/api/v1/me", "/api/v1/auth/onboard"])
def test_invalid_identity_cannot_reach_storage(client, runtime, route):
    runtime.verifier.verify.side_effect = APIError(401, "unauthorized", "Invalid credentials.")
    response = client.request("POST" if route.endswith("onboard") else "GET", route, headers=HEADERS, json={})
    assert response.status_code == 401
    runtime.users.current.assert_not_called()
    runtime.users.onboard.assert_not_called()


def test_onboarding_accepts_no_client_identity_or_role(client, runtime):
    assert client.post("/api/v1/auth/onboard", headers=HEADERS, json={}).status_code == 200
    assert runtime.users.onboard.call_args.args[0].uid == "synthetic-unit-uid"
    runtime.users.onboard.reset_mock()
    for body in ({"role": "AGENT"}, {"firebase_uid": "other"}, {"is_active": True}, {"email": "other@example.invalid"}):
        response = client.post("/api/v1/auth/onboard", headers=HEADERS, json=body)
        assert response.status_code == 422 and "other@example.invalid" not in response.text
    runtime.users.onboard.assert_not_called()


@pytest.mark.parametrize("path", ["/api/v1/users/role", "/api/v1/admin/promote", "/docs", "/openapi.json", "/tickets"])
def test_no_public_promotion_or_unrequested_endpoints(client, path):
    assert client.post(path, headers=HEADERS, json={"role": "AGENT"}).status_code == 404


@pytest.mark.parametrize("role,customer_status,agent_status", [(UserRole.CUSTOMER, 200, 403), (UserRole.AGENT, 403, 200)])
def test_reusable_database_role_dependencies(api_settings, runtime, user, role, customer_status, agent_status):
    runtime.users.current.return_value = replace(user, role=role)
    app = create_app(api_settings, runtime=runtime)
    app.get("/test/customer")(lambda user=Depends(customer_only): {"ok": True})
    app.get("/test/agent")(lambda user=Depends(agent_only): {"ok": True})
    with TestClient(app) as client:
        assert client.get("/test/customer", headers=HEADERS).status_code == customer_status
        assert client.get("/test/agent", headers=HEADERS).status_code == agent_status


def test_inactive_database_user_is_rejected(user):
    with pytest.raises(APIError) as error:
        require_active(replace(user, is_active=False))
    assert error.value.status == 403 and error.value.code == "account_inactive"


def test_health_does_not_probe_dependencies(client, runtime):
    assert client.get("/health").json() == {"status": "ok"}
    runtime.database.ready.assert_not_called()
    runtime.verifier.ready.assert_not_called()


@pytest.mark.parametrize("dependency", ["database", "verifier", None])
def test_ready_status_hides_dependency_details(client, runtime, dependency):
    if dependency:
        getattr(runtime, dependency).ready.side_effect = RuntimeError("SYNTHETIC_PRIVATE_DETAIL")
    response = client.get("/ready")
    assert response.status_code == (503 if dependency else 200)
    assert "SYNTHETIC_PRIVATE_DETAIL" not in response.text


def test_safe_logging_error_envelope_and_no_cache(client, runtime, caplog):
    caplog.set_level("INFO", logger="supportlens.http")
    runtime.users.current.side_effect = RuntimeError("SYNTHETIC_PRIVATE_DETAIL")
    response = client.get("/api/v1/me?private=SYNTHETIC_QUERY", headers=HEADERS | {"X-Request-ID": "UNTRUSTED"})
    assert response.status_code == 500 and response.json()["error"]["code"] == "internal_error"
    assert response.json()["request_id"] == response.headers["x-request-id"] != "UNTRUSTED"
    assert response.headers["cache-control"] == "no-store"
    records = [json.loads(record.message) for record in caplog.records if record.name == "supportlens.http"]
    assert records and records[-1]["route"] == "/api/v1/me"
    assert all(secret not in json.dumps(records) + response.text for secret in (TOKEN, "SYNTHETIC_QUERY", "SYNTHETIC_PRIVATE_DETAIL"))


def test_cors_explicit_origins_and_json_rejection(client):
    allowed = client.options("/api/v1/me", headers={"Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "Authorization"})
    assert allowed.status_code == 200 and allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "access-control-allow-credentials" not in allowed.headers
    denied = client.options("/api/v1/me", headers={"Origin": "https://untrusted.example.invalid", "Access-Control-Request-Method": "GET"})
    assert denied.status_code == 400 and denied.json()["error"]["code"] == "cors_rejected"
    assert "access-control-allow-origin" not in denied.headers
    assert int(denied.headers["content-length"]) == len(denied.content)


@pytest.mark.parametrize("origins", [("*",), ("http://public.example.invalid",), ("https://user:pass@example.invalid",),
    ("https://example.invalid/",), ("https://example.invalid?x=1",), ("https://example.invalid:*",)])
def test_cors_rejects_unsafe_configuration(api_settings, origins):
    with pytest.raises(ValidationError):
        APISettings(_env_file=None, **(api_settings.model_dump() | {"cors_allowed_origins": origins}))


@pytest.fixture
def sdk_verifier(api_settings, monkeypatch):
    monkeypatch.delenv("FIREBASE_AUTH_EMULATOR_HOST", raising=False)
    verifier = FirebaseVerifier(api_settings)
    verifier._app = object()  # Unit boundary: no private ADC discovery/network.
    return verifier


def claims(**updates):
    return {"uid": "synthetic-unit-uid", "sub": "synthetic-unit-uid", "aud": PROJECT,
        "iss": "https://securetoken.google.com/" + PROJECT, "email": "synthetic@example.invalid",
        "email_verified": True, "name": "Synthetic", "firebase": {}, **updates}


def test_sdk_receives_project_app_and_mandatory_revocation_check(sdk_verifier, monkeypatch):
    verify = Mock(return_value=claims(role="AGENT", admin=True))
    monkeypatch.setattr(auth, "verify_id_token", verify)
    identity = sdk_verifier.verify(TOKEN)
    verify.assert_called_once_with(TOKEN, app=sdk_verifier._app, check_revoked=True, clock_skew_seconds=0)
    assert identity.uid == "synthetic-unit-uid" and not hasattr(identity, "role")
    assert TOKEN not in repr(identity) and identity.email not in repr(identity)


@pytest.mark.parametrize("exception", [auth.InvalidIdTokenError, auth.ExpiredIdTokenError,
    auth.RevokedIdTokenError, auth.UserDisabledError, auth.UserNotFoundError, ValueError])
def test_sdk_invalid_expired_revoked_and_disabled_rejected(sdk_verifier, monkeypatch, exception):
    failure = exception("SYNTHETIC_SDK_SECRET", None) if exception is auth.ExpiredIdTokenError else exception("SYNTHETIC_SDK_SECRET")
    monkeypatch.setattr(auth, "verify_id_token", Mock(side_effect=failure))
    with pytest.raises(APIError) as error:
        sdk_verifier.verify(TOKEN)
    assert error.value.status == 401 and "SYNTHETIC_SDK_SECRET" not in str(error.value)


@pytest.mark.parametrize("updates", [{"aud": "wrong-project"}, {"iss": "https://untrusted.invalid"},
    {"sub": "other"}, {"uid": ""}, {"firebase": {"tenant": "other"}}, {"firebase": "invalid"}])
def test_wrong_project_and_identity_binding_rejected(sdk_verifier, monkeypatch, updates):
    monkeypatch.setattr(auth, "verify_id_token", Mock(return_value=claims(**updates)))
    with pytest.raises(APIError) as error:
        sdk_verifier.verify(TOKEN)
    assert error.value.status == 401


def test_sdk_network_failure_is_safe_unavailable(sdk_verifier, monkeypatch):
    monkeypatch.setattr(auth, "verify_id_token", Mock(side_effect=RuntimeError("SYNTHETIC_PRIVATE_DETAIL")))
    with pytest.raises(APIError) as error:
        sdk_verifier.verify(TOKEN)
    assert error.value.status == 503 and "SYNTHETIC_PRIVATE_DETAIL" not in str(error.value)


def test_emulator_and_missing_project_cannot_bypass_sdk(api_settings, monkeypatch):
    monkeypatch.setenv("FIREBASE_AUTH_EMULATOR_HOST", "localhost:9099")
    verifier = FirebaseVerifier(api_settings)
    with pytest.raises(APIError):
        verifier.ready()
    monkeypatch.setenv("FIREBASE_AUTH_EMULATOR_HOST", "")
    with pytest.raises(APIError):
        verifier.ready()
    monkeypatch.delenv("FIREBASE_AUTH_EMULATOR_HOST")
    verifier = FirebaseVerifier(api_settings.model_copy(update={"firebase_project_id": None}))
    with pytest.raises(APIError):
        verifier.verify(TOKEN)


def test_only_verified_bounded_profile_fields_are_synchronized(sdk_verifier, monkeypatch):
    monkeypatch.setattr(auth, "verify_id_token", Mock(return_value=claims(email_verified=False, name="x" * 121)))
    identity = sdk_verifier.verify(TOKEN)
    assert identity.email is None and identity.display_name is None


def test_service_returns_only_after_successful_commit(identity, user):
    database = Mock()
    @contextmanager
    def fails_on_commit():
        yield Mock()
        raise RuntimeError("synthetic commit failure")
    database.transaction = fails_on_commit
    service = UserService(database)
    from unittest.mock import patch
    with patch("supportlens.backend.auth.users.onboard_user", return_value=user):
        with pytest.raises(RuntimeError):
            service.onboard(identity)


@pytest.mark.parametrize("explicit_file", [False, True])
def test_sdk_initialization_uses_supported_credentials_and_explicit_project(api_settings, monkeypatch, explicit_file):
    from supportlens.backend.auth import firebase as module
    monkeypatch.delenv("FIREBASE_AUTH_EMULATOR_HOST", raising=False)
    settings = api_settings.model_copy(update={"google_application_credentials": "synthetic-private-file" if explicit_file else None})
    source = Mock()
    certificate = Mock(return_value=source)
    adc = Mock(return_value=source)
    initialize = Mock(return_value=object())
    delete = Mock()
    monkeypatch.setattr(module.credentials, "Certificate", certificate)
    monkeypatch.setattr(module.credentials, "ApplicationDefault", adc)
    monkeypatch.setattr(module.firebase_admin, "initialize_app", initialize)
    monkeypatch.setattr(module.firebase_admin, "delete_app", delete)
    verifier = FirebaseVerifier(settings)
    verifier.ready()
    verifier.ready()
    assert initialize.call_count == 1
    assert initialize.call_args.kwargs["options"] == {"projectId": PROJECT, "httpTimeout": 10}
    source.get_credential.assert_called_once_with()
    assert certificate.call_count == int(explicit_file) and adc.call_count == int(not explicit_file)
    verifier.close()
    delete.assert_called_once()


def test_real_factory_health_survives_missing_firebase_configuration(api_settings, monkeypatch):
    settings = api_settings.model_copy(update={"firebase_project_id": None})
    from supportlens.backend.api.database import Database
    connect = Mock(side_effect=AssertionError("Health must not connect"))
    monkeypatch.setattr(Database, "engine", connect)
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/ready").status_code == 503
    connect.assert_not_called()
