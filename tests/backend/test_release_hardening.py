"""Launch profile, readiness and abuse checks with deliberately fictional identities."""
import asyncio
from threading import Event
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from supportlens.backend.api.abuse import AbuseLimits
from supportlens.backend.api.app import RequestBodyLimit, create_app
from supportlens.backend.api.config import APISettings
from supportlens.backend.api.demo import TICKETS
from supportlens.backend.api.errors import APIError
from supportlens.backend.api.readiness import Readiness
from supportlens.backend.auth.firebase import VerifiedIdentity
from supportlens.backend.config import ConfigurationError, Environment
from supportlens.backend.domain.enums import UserRole


HEADERS = {"Authorization": "Bearer fictional.token.value"}
DEMO_ORIGIN = "https://demo-ui.example.invalid"
DEMO_API = "https://demo-api.example.invalid"


@pytest.fixture
def demo_options(tmp_path):
    credential = tmp_path / "synthetic-credential.json"
    credential.write_text("{}", encoding="utf-8")
    return {"_env_file": None, "environment": "synthetic_demo",
            "demo_database_url": "postgresql://supportlens_demo_app:fiction@db.invalid/SupportLens_demo",
            "demo_firebase_project_id": "fictional-demo-suite", "demo_customer_uid": "customer_fixture",
            "demo_agent_uid": "agent_fixture", "google_application_credentials": str(credential),
            "cors_allowed_origins": (DEMO_ORIGIN,), "demo_trusted_hosts": ("demo-api.example.invalid",)}


@pytest.fixture
def runtime():
    identity = VerifiedIdentity("customer_fixture", "fixture@example.invalid")
    account = SimpleNamespace(id=uuid4(), email="fixture@example.invalid", role=UserRole.CUSTOMER)
    return SimpleNamespace(database=Mock(), verifier=Mock(verify=Mock(return_value=identity)),
                           users=Mock(current=Mock(return_value=account)), tickets=Mock())


@pytest.mark.parametrize("override", [
    {"database_url": "postgresql://local:fiction@db.invalid/SupportLens_dev"},
    {"demo_database_url": "postgresql://supportlens_demo_app:fiction@db.invalid/SupportLens_test"},
    {"demo_database_url": "postgresql://administrator:fiction@db.invalid/SupportLens_demo"},
    {"firebase_project_id": "fictional-developer-project"},
    {"demo_firebase_project_id": "supportlens-fc39b"},
    {"demo_agent_uid": "customer_fixture"},
    {"gemini_free_tier_confirmed": True},
    {"gemini_api_key": "fictional-private-value"},
    {"gemini_allow_reviewed_context": True},
    {"response_cache_enabled": True},
    {"workflow_min_confidence": 0.6},
    {"classification_low_score_threshold": 0.4},
    {"cors_allowed_origins": ("http://localhost:3000",)},
    {"demo_trusted_hosts": ("*",)},
])
def test_demo_environment_rejects_unsafe_configuration(demo_options, override):
    with pytest.raises(ValidationError):
        APISettings(**(demo_options | override))


def test_demo_settings_target_only_its_distinct_database(demo_options):
    settings = APISettings(**demo_options)
    assert settings.spec(Environment.SYNTHETIC_DEMO).expected_database == "SupportLens_demo"
    assert not settings.response_cache_enabled
    for target in (Environment.TEST, Environment.DEVELOPMENT, Environment.PRODUCTION):
        with pytest.raises(ConfigurationError):
            settings.spec(target, readonly=True)


def test_demo_rejects_onboarding_unknown_identities_and_unapproved_text(demo_options, runtime):
    app = create_app(APISettings(**demo_options), runtime=runtime)
    with TestClient(app, base_url=DEMO_API) as client:
        assert client.post("/api/v1/auth/onboard", headers=HEADERS, json={}).status_code == 403
        runtime.users.onboard.assert_not_called()
        response = client.post("/api/v1/tickets", headers=HEADERS, json={
            "subject": "Personal content", "description": "FICTIONAL_PRIVATE_SENTINEL"})
        assert response.status_code == 422 and "FICTIONAL_PRIVATE_SENTINEL" not in response.text
        runtime.tickets.create.assert_not_called()
        runtime.tickets.create.side_effect = APIError(409, "test_boundary", "Validated fixture.")
        response = client.post("/api/v1/tickets", headers=HEADERS, json={
            "subject": TICKETS[0][0], "description": TICKETS[0][1]})
        assert response.status_code == 409
        runtime.tickets.create.assert_called_once()
        runtime.users.current.reset_mock()
        runtime.verifier.verify.return_value = VerifiedIdentity("unknown_fixture", "other@example.invalid")
        assert client.get("/api/v1/me", headers=HEADERS).status_code == 403
        runtime.users.current.assert_not_called()
        assert client.get("/health", headers={"Host": "unapproved.example.invalid"}).status_code == 400


def test_limit_uses_connection_peer_and_preserves_cors(demo_options, runtime):
    app = create_app(APISettings(**demo_options, api_ip_requests_per_minute=1), runtime=runtime)
    with TestClient(app, base_url=DEMO_API) as client:
        assert client.get("/api/v1/me", headers={"X-Forwarded-For": "192.0.2.1"}).status_code == 401
        result = client.get("/api/v1/me", headers={
            "X-Forwarded-For": "192.0.2.2", "Origin": DEMO_ORIGIN})
        assert result.status_code == 429 and result.json()["error"]["code"] == "rate_limited"
        assert result.headers["Retry-After"] == "60"
        assert result.headers["Access-Control-Allow-Origin"] == DEMO_ORIGIN
        assert client.get("/health").status_code == 200
        runtime.verifier.verify.assert_not_called()


def test_verified_actor_cannot_reset_budget_by_changing_peers(demo_options, runtime):
    app = create_app(APISettings(**demo_options, api_actor_requests_per_minute=1), runtime=runtime)
    with TestClient(app, base_url=DEMO_API, client=("192.0.2.10", 1000)) as first:
        assert first.post("/api/v1/auth/onboard", headers=HEADERS, json={}).status_code == 403
    with TestClient(app, base_url=DEMO_API, client=("192.0.2.20", 2000)) as second:
        assert second.post("/api/v1/auth/onboard", headers=HEADERS, json={}).status_code == 429
    runtime.users.onboard.assert_not_called()


def test_slow_body_returns_408_before_dispatch():
    async def not_dispatched(*args):
        pytest.fail("Slow body dispatched to the application.")

    async def receive():
        await asyncio.sleep(2)

    sent = []

    async def send(message):
        sent.append(message)

    bounded = RequestBodyLimit(not_dispatched, timeout=0.025)
    asyncio.run(bounded({"type": "http", "headers": [], "state": {}}, receive, send))
    assert sent[0]["status"] == 408
    assert b"request_timeout" in sent[1]["body"]


def test_readiness_has_separate_peer_budget_and_keeps_liveness_available(demo_options, runtime):
    app = create_app(APISettings(**demo_options), runtime=runtime)
    with TestClient(app, base_url=DEMO_API) as client:
        for _ in range(20):
            assert client.get("/ready").status_code == 200
        failure = client.get("/ready")
        assert failure.status_code == 429 and int(failure.headers["Retry-After"]) > 0
        assert client.get("/health").status_code == 200
    assert runtime.database.ready.call_count == 20

def test_windows_expire_and_store_fails_closed_at_capacity():
    clock = Mock(return_value=0)
    limiter = AbuseLimits(1, 1, capacity=1, clock=clock)
    limiter.consume(("peer", "one"), 1)
    for key in ("one", "two"):
        with pytest.raises(APIError, match="Too many") as error:
            limiter.consume(("peer", key), 1)
        assert error.value.retry_after == 60
    clock.return_value = 61
    limiter.consume(("peer", "two"), 1)
    assert len(limiter.windows) == 1


def test_readiness_times_out_without_unbounded_probe_threads():
    stop = Event()
    network = Mock(side_effect=lambda: stop.wait(timeout=10))
    readiness = Readiness({"database": network, "firebase": lambda: None}, timeout=0.025)

    async def verify():
        started = perf_counter()
        assert await asyncio.gather(readiness.verify(), readiness.verify()) == [False, False]
        assert perf_counter() - started < 0.5
        assert readiness.last == {"database": "timeout", "firebase": "ready"}
        network.assert_called_once()

    try:
        asyncio.run(verify())
    finally:
        stop.set()


def test_readiness_does_not_use_a_stale_success():
    network = Mock(side_effect=[None, RuntimeError("PRIVATE")])
    readiness = Readiness({"database": network}, timeout=0.3)

    async def verify():
        assert await readiness.verify()
        assert not await readiness.verify()
        assert readiness.last == {"database": "unavailable"}

    asyncio.run(verify())
