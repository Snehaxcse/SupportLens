"""HTTP allowlists, RBAC, bounded output, and error contracts; all identities synthetic."""
from dataclasses import replace
from datetime import datetime, timezone
import json
from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from supportlens.backend.api.app import create_app
from supportlens.backend.api.errors import APIError
from supportlens.backend.domain.enums import UserRole
from supportlens.backend.tickets.schemas import (
    AgentMessageCreate, AssignmentChange, CustomerMessageCreate, CustomerTicket, InboxQuery,
    Page, PriorityChange, PublicEvent, StatusChange, TicketCreate,
)
from supportlens.backend.tickets.service import TicketService
from .test_auth_api import api_settings, runtime, identity, user, HEADERS

IDENTIFIER = str(uuid4())
CUSTOMER_ROUTES = [("POST", "/api/v1/tickets"), ("GET", "/api/v1/tickets"),
    ("GET", f"/api/v1/tickets/{IDENTIFIER}"), ("POST", f"/api/v1/tickets/{IDENTIFIER}/messages"),
    ("POST", f"/api/v1/tickets/{IDENTIFIER}/reopen")]
AGENT_ROUTES = [("GET", "/api/v1/agent/tickets"), ("GET", f"/api/v1/agent/tickets/{IDENTIFIER}"),
    ("POST", f"/api/v1/agent/tickets/{IDENTIFIER}/claim"), ("PATCH", f"/api/v1/agent/tickets/{IDENTIFIER}/assignment"),
    ("PATCH", f"/api/v1/agent/tickets/{IDENTIFIER}/status"), ("PATCH", f"/api/v1/agent/tickets/{IDENTIFIER}/priority"),
    ("POST", f"/api/v1/agent/tickets/{IDENTIFIER}/messages"), ("GET", f"/api/v1/agent/tickets/{IDENTIFIER}/events"),
    ("GET", "/api/v1/agent/users")]


@pytest.fixture
def ticket_client(api_settings, runtime):
    runtime.tickets = Mock(spec=TicketService)
    with TestClient(create_app(api_settings, runtime=runtime)) as client:
        yield client


def summary():
    moment = datetime.now(timezone.utc)
    return CustomerTicket(id=IDENTIFIER, subject="Synthetic support request", status="OPEN", priority="NORMAL",
                          created_at=moment, updated_at=moment, resolved_at=None)


@pytest.mark.parametrize("method,path", CUSTOMER_ROUTES + AGENT_ROUTES)
def test_all_ticket_routes_require_authentication(ticket_client, runtime, method, path):
    assert ticket_client.request(method, path, json={}).status_code == 401
    assert not runtime.tickets.mock_calls


@pytest.mark.parametrize("method,path", AGENT_ROUTES)
def test_customer_cannot_call_agent_routes(ticket_client, runtime, method, path):
    assert ticket_client.request(method, path, headers=HEADERS, json={}).status_code == 403
    assert not runtime.tickets.mock_calls


@pytest.mark.parametrize("method,path", CUSTOMER_ROUTES)
def test_agent_cannot_impersonate_customer_route(ticket_client, runtime, user, method, path):
    runtime.users.current.return_value = replace(user, role=UserRole.AGENT)
    assert ticket_client.request(method, path, headers=HEADERS, json={}).status_code == 403
    assert not runtime.tickets.mock_calls


@pytest.mark.parametrize("method,path", CUSTOMER_ROUTES + AGENT_ROUTES)
def test_inactive_accounts_are_blocked_before_service(ticket_client, runtime, method, path):
    runtime.users.current.side_effect = APIError(403, "account_inactive", "This account is inactive.")
    assert ticket_client.request(method, path, headers=HEADERS, json={}).status_code == 403
    assert not runtime.tickets.mock_calls


def test_create_forwards_only_validated_text_and_authenticated_actor(ticket_client, runtime, user):
    runtime.tickets.create.return_value = summary()
    response = ticket_client.post("/api/v1/tickets", headers=HEADERS,
        json={"subject": " Synthetic support request ", "description": " A synthetic issue. "})
    assert response.status_code == 201 and response.json()["priority"] == "NORMAL"
    actor, payload = runtime.tickets.create.call_args.args
    assert actor.id == user.id and payload.subject == "Synthetic support request" and payload.description == "A synthetic issue."


def test_invalid_token_cannot_reach_ticket_mutation(ticket_client, runtime):
    runtime.verifier.verify.side_effect = APIError(401, "unauthorized", "A valid Firebase ID token is required.")
    response = ticket_client.post("/api/v1/tickets", headers=HEADERS,
        json={"subject": "Synthetic", "description": "Synthetic"})
    assert response.status_code == 401
    assert not runtime.tickets.mock_calls


@pytest.mark.parametrize("field,value", [("customer_id", IDENTIFIER), ("assigned_agent_id", IDENTIFIER),
    ("priority", "HIGH"), ("status", "RESOLVED"), ("actor_id", IDENTIFIER), ("metadata", {"token": "SYNTHETIC"})])
def test_ticket_creation_rejects_mass_assignment(ticket_client, runtime, field, value):
    response = ticket_client.post("/api/v1/tickets", headers=HEADERS,
        json={"subject": "Synthetic", "description": "Synthetic", field: value})
    assert response.status_code == 422
    runtime.tickets.create.assert_not_called()


@pytest.mark.parametrize("body", [{}, {"subject": "", "description": "x"}, {"subject": "x", "description": "  \n "},
    {"subject": "x"*201, "description": "x"}, {"subject": "x", "description": "x"*20001},
    {"subject": "x\u0000", "description": "x"}])
def test_create_required_fields_and_lengths(ticket_client, runtime, body):
    assert ticket_client.post("/api/v1/tickets", headers=HEADERS, json=body).status_code == 422
    runtime.tickets.create.assert_not_called()


@pytest.mark.parametrize("extra", [{"visibility": "INTERNAL"}, {"visibility": "PUBLIC"}, {"author_id": IDENTIFIER}])
def test_customer_reply_cannot_supply_visibility_or_author(ticket_client, runtime, extra):
    response = ticket_client.post(f"/api/v1/tickets/{IDENTIFIER}/messages", headers=HEADERS, json={"body": "Synthetic", **extra})
    assert response.status_code == 422
    runtime.tickets.customer_message.assert_not_called()


@pytest.mark.parametrize("query", ["page=0", "page=10001", "page_size=101", "page_size=0", "visibility=INTERNAL"])
def test_customer_listing_is_bounded_and_has_no_privileged_filters(ticket_client, runtime, query):
    assert ticket_client.get("/api/v1/tickets?"+query, headers=HEADERS).status_code == 422
    runtime.tickets.customer_list.assert_not_called()


@pytest.mark.parametrize("query", ["status=BAD", "priority=CRITICAL", "order=arbitrary_sql", "page_size=101",
    "unassigned_only=true&assigned_agent_id="+IDENTIFIER, "search=", "unexpected=1"])
def test_inbox_rejects_invalid_or_conflicting_filters(ticket_client, runtime, user, query):
    runtime.users.current.return_value = replace(user, role=UserRole.AGENT)
    assert ticket_client.get("/api/v1/agent/tickets?"+query, headers=HEADERS).status_code == 422
    runtime.tickets.agent_list.assert_not_called()


def test_customer_response_fails_closed_on_internal_message(ticket_client, runtime):
    value = summary().model_dump() | {"description": "Synthetic", "messages": {"items": [{
        "id": str(uuid4()), "body": "PRIVATE_NOTE_SENTINEL", "visibility": "INTERNAL",
        "author": {"display_name": "Synthetic", "kind": "AGENT"}, "created_at": datetime.now(timezone.utc)}],
        "total": 1, "page": 1, "page_size": 25}, "history": {"items": [], "total": 0, "page": 1, "page_size": 25}}
    runtime.tickets.customer_detail.return_value = value
    response = ticket_client.get(f"/api/v1/tickets/{IDENTIFIER}", headers=HEADERS)
    assert response.status_code == 500 and "PRIVATE_NOTE_SENTINEL" not in response.text


def test_safe_404_for_cross_owner_or_missing_ticket(ticket_client, runtime):
    runtime.tickets.customer_detail.side_effect = APIError(404, "ticket_not_found", "The ticket was not found.")
    response = ticket_client.get(f"/api/v1/tickets/{IDENTIFIER}", headers=HEADERS)
    assert response.status_code == 404 and response.json()["error"]["code"] == "ticket_not_found"


def test_patch_cors_and_safe_route_template_logging(ticket_client, runtime, caplog):
    caplog.set_level("INFO", logger="supportlens.http")
    response = ticket_client.options(f"/api/v1/agent/tickets/{IDENTIFIER}/status", headers={
        "Origin": "http://localhost:3000", "Access-Control-Request-Method": "PATCH",
        "Access-Control-Request-Headers": "Authorization,Content-Type"})
    assert response.status_code == 200 and "PATCH" in response.headers["access-control-allow-methods"]
    ticket_client.get(f"/api/v1/tickets/{IDENTIFIER}?search=PRIVATE_SEARCH_SENTINEL", headers=HEADERS)
    logs = [json.loads(record.message) for record in caplog.records if record.name == "supportlens.http"]
    assert "PRIVATE_SEARCH_SENTINEL" not in json.dumps(logs) and IDENTIFIER not in json.dumps(logs)


def test_openapi_available_without_public_debug_routes(api_settings, runtime):
    app = create_app(api_settings, runtime=runtime)
    schema = app.openapi()
    assert "/api/v1/tickets" in schema["paths"] and "/api/v1/agent/tickets/{ticket_id}/priority" in schema["paths"]
    assert "customer_id" not in schema["components"]["schemas"]["TicketCreate"]["properties"]
    assert schema["components"]["schemas"]["CustomerMessageCreate"]["additionalProperties"] is False
    assert "metadata" not in schema["components"]["schemas"]["PublicEvent"]["properties"]
    operation = schema["paths"]["/api/v1/tickets"]["post"]
    assert operation["security"] == [{"FirebaseIDToken": []}]
    assert schema["components"]["securitySchemes"]["FirebaseIDToken"]["scheme"] == "bearer"
    assert operation["responses"]["422"]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorResponse")


@pytest.mark.parametrize("model,data", [(CustomerMessageCreate, {"body": " "}), (CustomerMessageCreate, {"body": "x"*20001}),
    (AgentMessageCreate, {"body": "x", "visibility": "SECRET"}), (AssignmentChange, {}),
    (StatusChange, {"status": "BAD"}), (PriorityChange, {"priority": "URGENT"})])
def test_domain_request_allowlists(model, data):
    with pytest.raises(ValidationError):
        model(**data)
