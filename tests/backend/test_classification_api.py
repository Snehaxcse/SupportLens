"""No external identity calls; classification remains strictly agent-only."""
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from supportlens.backend.api.app import create_app
from supportlens.backend.api.errors import APIError
from supportlens.backend.classification.constants import CORE_LABELS, MODEL_LIMITATION, MODEL_VERSION, TRUSTED_SHA256, UNSUPPORTED
from supportlens.backend.classification.schemas import ClassificationResponse, ClassificationRun, ClassificationState
from supportlens.backend.classification.service import ClassificationService
from supportlens.backend.domain.enums import UserRole
from .test_auth_api import api_settings, runtime, identity, user, HEADERS

TICKET = uuid4()
BASE = f"/api/v1/agent/tickets/{TICKET}/classification"


def pending_run():
    return ClassificationRun(id=uuid4(), ticket_id=TICKET, generation=1, status=ClassificationState.PENDING,
        model_version=MODEL_VERSION, artifact_sha256=TRUSTED_SHA256["model.joblib"], artifact_verified=False,
        predicted_intent=None, confidence=None, low_score=None, advisory_threshold=.5, input_truncated=False,
        created_at=datetime.now(timezone.utc), completed_at=None, duration_ms=None, failure_category=None)


@pytest.fixture
def classification_client(api_settings, runtime):
    runtime.classifications = Mock(spec=ClassificationService)
    with TestClient(create_app(api_settings, runtime=runtime)) as client:
        yield client


@pytest.mark.parametrize("method,suffix,body", [("GET", "", None), ("POST", "/retry", {"expected_run_id": None})])
def test_unauthenticated_and_customer_requests_never_reach_classification(classification_client, runtime, method, suffix, body):
    assert classification_client.request(method, BASE+suffix, json=body).status_code == 401
    assert classification_client.request(method, BASE+suffix, json=body, headers=HEADERS).status_code == 403
    assert not runtime.classifications.mock_calls


@pytest.mark.parametrize("method,suffix", [("GET", ""), ("POST", "/retry")])
def test_inactive_user_is_denied_before_classification(classification_client, runtime, method, suffix):
    runtime.users.current.side_effect = APIError(403, "account_inactive", "This account is inactive.")
    assert classification_client.request(method, BASE+suffix, json={"expected_run_id": None}, headers=HEADERS).status_code == 403
    assert not runtime.classifications.mock_calls


def test_agent_get_has_explicit_safe_contract(classification_client, runtime, user):
    runtime.users.current.return_value = replace(user, role=UserRole.AGENT)
    run = pending_run()
    runtime.classifications.get.return_value = ClassificationResponse(ticket_id=TICKET, status=run.status, current=run,
        recent_runs=[run], total_runs=1, supported_intents=list(CORE_LABELS), unsupported_intents=list(UNSUPPORTED),
        model_limitation=MODEL_LIMITATION, retry_allowed=False, retry_available_at=None)
    response = classification_client.get(BASE, headers=HEADERS)
    assert response.status_code == 200 and response.json()["current"]["status"] == "PENDING"
    assert response.json()["requires_human_review"] is True and response.json()["automated_actions_allowed"] is False
    assert not {"score_metadata", "lease_token", "subject", "description", "model_path"} & set(response.json()["current"])
    assert runtime.classifications.get.call_args.args[1] == TICKET


def test_agent_retry_requires_observed_parent_and_returns_accepted_run(classification_client, runtime, user):
    runtime.users.current.return_value = replace(user, role=UserRole.AGENT)
    run, parent = pending_run(), uuid4()
    runtime.classifications.retry.return_value = run
    response = classification_client.post(BASE+"/retry", headers=HEADERS, json={"expected_run_id": str(parent)})
    assert response.status_code == 202 and response.json()["id"] == str(run.id)
    assert runtime.classifications.retry.call_args.args[1:] == (TICKET, parent)


@pytest.mark.parametrize("body", [{}, {"expected_run_id": "bad"}, {"expected_run_id": None, "model_path": "DO_NOT_ECHO_PATH"},
    {"expected_run_id": None, "model_version": "unapproved"}, {"expected_run_id": None, "requires_human_review": False}])
def test_retry_rejects_mass_assignment_and_model_selection(classification_client, runtime, user, body):
    runtime.users.current.return_value = replace(user, role=UserRole.AGENT)
    response = classification_client.post(BASE+"/retry", headers=HEADERS, json=body)
    assert response.status_code == 422 and "DO_NOT_ECHO_PATH" not in response.text
    runtime.classifications.retry.assert_not_called()


@pytest.mark.parametrize("status,code", [(404, "ticket_not_found"), (409, "classification_changed"),
    (429, "classification_retry_rate_limited"), (503, "classification_unavailable")])
def test_retry_preserves_safe_error_semantics(classification_client, runtime, user, status, code):
    runtime.users.current.return_value = replace(user, role=UserRole.AGENT)
    runtime.classifications.retry.side_effect = APIError(status, code, "Safe fixed message.")
    response = classification_client.post(BASE+"/retry", headers=HEADERS, json={"expected_run_id": None})
    assert response.status_code == status and response.json()["error"]["code"] == code
