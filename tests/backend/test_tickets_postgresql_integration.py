"""Guarded real-PG workflows. Rollback fixtures plus one retained, auditable claim race."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
from queue import Queue
import socket
import threading
import time
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
import requests
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session
import uvicorn

from supportlens.backend.api.app import create_app
from supportlens.backend.api.config import load_api_settings
from supportlens.backend.api.database import Database
from supportlens.backend.api.errors import APIError, unauthorized
from supportlens.backend.auth.firebase import VerifiedIdentity
from supportlens.backend.auth.users import UserService, snapshot
from supportlens.backend.config import Environment
from supportlens.backend.db.migrations import verify_databases, verify_schema
from supportlens.backend.db.models import Ticket, TicketEvent, TicketMessage, User
from supportlens.backend.db.preflight import inspect_connection
from supportlens.backend.db.session import build_engine
from supportlens.backend.db.testing import require_test_opt_in
from supportlens.backend.domain.enums import TicketEventType, UserRole
from supportlens.backend.tickets.schemas import TicketCreate
from supportlens.backend.tickets.service import TicketRepository, TicketService

pytestmark = [pytest.mark.db_integration, pytest.mark.skipif(
    os.environ.get("SUPPORTLENS_RUN_DB_TESTS") != "1", reason="Explicit verified PostgreSQL test opt-in is absent.")]


@pytest.fixture(scope="module")
def ticket_database():
    settings = load_api_settings()
    require_test_opt_in(settings)
    verify_databases(settings)
    engine = build_engine(settings, Environment.TEST)
    try:
        yield settings, engine
    finally:
        engine.dispose()


def synthetic_user(session, label, role=UserRole.CUSTOMER, active=True):
    user = User(firebase_uid="synthetic_m2c_"+uuid4().hex, email="synthetic@example.invalid",
                display_name="Synthetic "+label, role=role, is_active=active)
    session.add(user)
    session.flush()
    return user


@pytest.fixture
def ticket_http(ticket_database):
    settings, engine = ticket_database
    with engine.connect() as connection:
        transaction = connection.begin()
        inspect_connection(connection, settings.spec(Environment.TEST), require_readonly=False)
        verify_schema(connection)
        with Session(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False) as session:
            actors = {"owner": synthetic_user(session, "Owner"), "other": synthetic_user(session, "Other"),
                      "agent": synthetic_user(session, "Agent", UserRole.AGENT),
                      "agent2": synthetic_user(session, "Agent2", UserRole.AGENT),
                      "inactive": synthetic_user(session, "Inactive", active=False),
                      "inactive_agent": synthetic_user(session, "InactiveAgent", UserRole.AGENT, False)}
            session.commit()  # Releases only the savepoint; fixture's outer transaction rolls back.
        lock = threading.RLock()  # Synchronizes this single test connection only, never production claims.
        class BoundDatabase:
            @contextmanager
            def transaction(self, *, readonly=False):
                with lock:
                    with Session(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False) as unit:
                        with unit.begin():
                            yield unit
            def ready(self):
                with lock:
                    inspect_connection(connection, settings.spec(Environment.TEST), require_readonly=False)
            def close(self): pass
        class SyntheticVerifier:
            def verify(self, token):
                for label, actor in actors.items():
                    if token == "synthetic."+label+".signature":
                        return VerifiedIdentity(actor.firebase_uid)
                raise unauthorized()
            def ready(self): pass
            def close(self): pass
        database = BoundDatabase()
        runtime = SimpleNamespace(database=database, users=UserService(database), verifier=SyntheticVerifier(), tickets=TicketService(database))
        app = create_app(settings, runtime=runtime)
        headers = lambda label: {"Authorization": "Bearer synthetic."+label+".signature"}
        try:
            yield SimpleNamespace(app=app, connection=connection, actors=actors, headers=headers)
        finally:
            transaction.rollback()


def test_live_ticket_customer_agent_lifecycle_over_local_http(ticket_http):
    fixture = ticket_http
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(fixture.app, host="127.0.0.1", port=port, log_config=None, access_log=False))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    statuses = []
    def call(method, route, label="owner", expected=200, **kwargs):
        response = requests.request(method, base+route, headers=fixture.headers(label), timeout=60, **kwargs)
        assert response.status_code == expected, (method, route, response.status_code)
        statuses.append({"method": method, "route": route, "status": response.status_code})
        return response.json()
    try:
        deadline = time.monotonic()+15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        assert call("GET", "/health")["status"] == "ok"
        assert call("GET", "/ready")["status"] == "ready"
        ticket = call("POST", "/api/v1/tickets", expected=201,
                      json={"subject": "Synthetic lifecycle", "description": "Synthetic support issue"})
        identifier = ticket["id"]
        customer = "/api/v1/tickets/"+identifier
        agent = "/api/v1/agent/tickets/"+identifier
        assert ticket["status"] == "OPEN" and ticket["priority"] == "NORMAL"
        assert "customer_id" not in ticket and "assigned_agent_id" not in ticket
        assert call("GET", "/api/v1/tickets")["total"] == 1
        assert call("GET", "/api/v1/tickets", "other")["total"] == 0
        for route, method, body in ((customer, "GET", None), (customer+"/messages", "POST", {"body": "Forged"}), (customer+"/reopen", "POST", {})):
            result = call(method, route, "other", expected=404, **({"json": body} if body is not None else {}))
            assert result["error"]["code"] == "ticket_not_found"
        missing = call("GET", "/api/v1/tickets/"+str(uuid4()), "other", expected=404)
        assert missing["error"]["message"] == "The ticket was not found."
        reply = call("POST", customer+"/messages", expected=201, json={"body": "Synthetic customer reply"})
        assert reply["visibility"] == "PUBLIC" and "author_id" not in reply
        claimed = call("POST", agent+"/claim", "agent", json={})
        assert claimed["assigned_agent_id"] == str(fixture.actors["agent"].id) and claimed["status"] == "OPEN"
        call("POST", agent+"/claim", "agent2", expected=409, json={})
        for invalid in (fixture.actors["owner"].id, fixture.actors["inactive_agent"].id, uuid4()):
            call("PATCH", agent+"/assignment", "agent", expected=422, json={"assigned_agent_id": str(invalid)})
        call("PATCH", agent+"/assignment", "agent", json={"assigned_agent_id": str(fixture.actors["agent2"].id)})
        call("PATCH", agent+"/status", "agent2", json={"status": "IN_PROGRESS"})
        call("PATCH", agent+"/priority", "agent2", json={"priority": "HIGH"})
        call("POST", agent+"/messages", "agent2", expected=201, json={"body": "Synthetic agent public reply", "visibility": "PUBLIC"})
        note = call("POST", agent+"/messages", "agent2", expected=201, json={"body": "PRIVATE_NOTE_SENTINEL", "visibility": "INTERNAL"})
        public = call("GET", customer)
        serialized = json.dumps(public)
        assert "PRIVATE_NOTE_SENTINEL" not in serialized and note["id"] not in serialized
        assert "actor_id" not in serialized and "assigned_agent_id" not in serialized and "metadata" not in serialized
        assert public["messages"]["total"] == 2
        public_actions = {event["action"] for event in public["history"]["items"]}
        assert "PRIORITY_CHANGED" in public_actions and "INTERNAL_NOTE" not in public_actions and "ASSIGNMENT_CHANGED" not in public_actions
        detail = call("GET", agent, "agent")
        assert detail["messages"]["total"] == 3 and any(m["body"] == "PRIVATE_NOTE_SENTINEL" for m in detail["messages"]["items"])
        resolved = call("PATCH", agent+"/status", "agent", json={"status": "RESOLVED"})
        assert resolved["resolved_at"] is not None
        call("POST", customer+"/messages", expected=409, json={"body": "Must reopen first"})
        call("POST", agent+"/messages", "agent", expected=409, json={"body": "Must reopen first", "visibility": "PUBLIC"})
        call("POST", agent+"/messages", "agent", expected=201, json={"body": "Resolved internal note", "visibility": "INTERNAL"})
        call("PATCH", agent+"/status", "agent", expected=409, json={"status": "OPEN"})
        reopened = call("POST", customer+"/reopen", json={})
        assert reopened["status"] == "REOPENED" and reopened["resolved_at"] is None
        call("POST", customer+"/reopen", expected=409, json={})
        call("POST", customer+"/messages", expected=201, json={"body": "Synthetic follow-up"})
        call("PATCH", agent+"/status", "agent", json={"status": "IN_PROGRESS"})
        call("PATCH", agent+"/status", "agent", json={"status": "RESOLVED"})
        call("PATCH", agent+"/status", "agent", json={"status": "REOPENED"})
        events = call("GET", agent+"/events?page_size=100", "agent")
        assert events["items"][0]["action"] == "TICKET_CREATED"
        assert {"ASSIGNMENT_CHANGED", "STATUS_CHANGED", "PRIORITY_CHANGED", "CUSTOMER_REPLY", "AGENT_REPLY", "INTERNAL_NOTE", "REOPENED"} <= {e["action"] for e in events["items"]}
        priority = next(e for e in events["items"] if e["action"] == "PRIORITY_CHANGED")
        assert priority["metadata"]["before"] == {"priority": "NORMAL"} and priority["metadata"]["after"] == {"priority": "HIGH"}
        assert all("body" not in e["metadata"] and "email" not in e["metadata"] for e in events["items"])
        call("PATCH", agent+"/status", "agent", json={"status": "RESOLVED"})  # REOPENED -> RESOLVED
        extra = call("POST", "/api/v1/tickets", expected=201, json={"subject": "Synthetic direct resolution", "description": "Synthetic"})
        call("PATCH", "/api/v1/agent/tickets/"+extra["id"]+"/status", "agent", json={"status": "RESOLVED"})  # OPEN -> RESOLVED
        schema = fixture.app.openapi()
        assert len([p for p in schema["paths"] if p.startswith("/api/v1/agent")]) == 9
        Path("artifacts/m2_c/runtime_ticket_lifecycle.json").write_text(json.dumps({
            "url": base, "real_tcp_http": True, "real_postgresql": True, "firebase_identity": "synthetic verifier only in isolated fixture",
            "write_target": "SupportLens_test, outer transaction rolled back", "checks": statuses,
            "openapi_generated": True, "public_openapi_endpoint": "disabled, preserving M2-B"}, indent=2)+"\n")
    finally:
        server.should_exit = True
        thread.join(15)
        listener.close()
        assert not thread.is_alive(), "Owned synthetic HTTP server must stop."


def test_live_ticket_inbox_filters_pagination_and_noops(ticket_http):
    fixture = ticket_http
    marker = "M2C " + uuid4().hex
    with TestClient(fixture.app) as client:
        def call(method, path, label="owner", expected=200, **kwargs):
            response = client.request(method, path, headers=fixture.headers(label), **kwargs)
            assert response.status_code == expected
            return response.json()
        tickets = [call("POST", "/api/v1/tickets", expected=201, json={"subject": marker+suffix, "description": "Synthetic"}) for suffix in (" A", " B", " %_literal")]
        a, b = ("/api/v1/agent/tickets/"+t["id"] for t in tickets[:2])
        call("PATCH", a+"/assignment", "agent", json={"assigned_agent_id": str(fixture.actors["agent"].id)})
        call("PATCH", a+"/priority", "agent", json={"priority": "LOW"})
        call("PATCH", a+"/status", "agent", json={"status": "IN_PROGRESS"})
        call("PATCH", b+"/priority", "agent", json={"priority": "HIGH"})
        def inbox(**params):
            return call("GET", "/api/v1/agent/tickets", "agent", params={"search": marker, **params})
        assert inbox()["total"] == 3
        assert inbox(status="IN_PROGRESS")["total"] == 1
        assert inbox(priority="HIGH")["items"][0]["id"] == tickets[1]["id"]
        assert inbox(assigned_agent_id=str(fixture.actors["agent"].id))["total"] == 1
        assert inbox(unassigned_only="true")["total"] == 2
        oldest = inbox(order="oldest", page_size=1)
        second = inbox(order="oldest", page_size=1, page=2)
        assert oldest["total"] == 3 and oldest["items"][0]["id"] == tickets[0]["id"]
        assert second["items"][0]["id"] == tickets[1]["id"]
        assert inbox(order="newest")["items"][0]["id"] == tickets[2]["id"]
        assert inbox(page=100)["items"] == []
        assert call("GET", "/api/v1/agent/tickets", "agent", params={"search": tickets[0]["id"]})["total"] == 1
        assert call("GET", "/api/v1/agent/tickets", "agent", params={"search": marker+" %_"})["total"] == 1
        assert call("GET", "/api/v1/agent/tickets", "agent", params={"search": "' OR 1=1 --"})["total"] == 0
        before = call("GET", a+"/events", "agent")["total"]
        call("PATCH", a+"/assignment", "agent", json={"assigned_agent_id": str(fixture.actors["agent"].id)})
        call("PATCH", a+"/priority", "agent", json={"priority": "LOW"})
        assert call("GET", a+"/events", "agent")["total"] == before
        call("PATCH", a+"/assignment", "agent", json={"assigned_agent_id": None})
        assert inbox(unassigned_only="true")["total"] == 3
        agents = call("GET", "/api/v1/agent/users?page_size=100", "agent")
        identifiers = {item["id"] for item in agents["items"]}
        assert str(fixture.actors["agent"].id) in identifiers and str(fixture.actors["inactive_agent"].id) not in identifiers
        assert all(set(item) == {"id", "display_name"} for item in agents["items"])
        call("GET", "/api/v1/tickets", "inactive", expected=403)
        call("POST", "/api/v1/agent/tickets/"+tickets[0]["id"]+"/claim", "inactive_agent", expected=403)
        for number in range(3):
            call("POST", "/api/v1/tickets/"+tickets[0]["id"]+"/messages", expected=201, json={"body": "Synthetic "+str(number)})
        one = call("GET", "/api/v1/tickets/"+tickets[0]["id"]+"?page_size=1")
        two = call("GET", "/api/v1/tickets/"+tickets[0]["id"]+"?messages_page=2&history_page=2&page_size=1")
        assert one["messages"]["total"] == 3 and one["messages"]["items"][0]["body"] == "Synthetic 0"
        assert two["messages"]["items"][0]["body"] == "Synthetic 1"
        assert two["history"]["total"] == 6  # Creation, priority, status and three public replies; assignments hidden.
        identifiers = [UUID(ticket["id"]) for ticket in tickets]
        tied = fixture.connection.scalar(select(func.min(Ticket.created_at)).where(Ticket.id.in_(identifiers)))
        fixture.connection.execute(update(Ticket).where(Ticket.id.in_(identifiers)).values(created_at=tied, updated_at=func.clock_timestamp()))
        first_page, last_page = inbox(order="oldest", page_size=2), inbox(order="oldest", page_size=2, page=2)
        assert [item["id"] for item in first_page["items"] + last_page["items"]] == sorted(ticket["id"] for ticket in tickets)


def test_live_ticket_mutations_rollback_when_audit_fails(ticket_http, monkeypatch):
    fixture = ticket_http
    with TestClient(fixture.app) as client:
        created = client.post("/api/v1/tickets", headers=fixture.headers("owner"), json={"subject": "Synthetic atomicity", "description": "Synthetic"})
        identifier = created.json()["id"]
        original_count = fixture.connection.scalar(select(func.count()).select_from(TicketEvent).where(TicketEvent.ticket_id == UUID(identifier)))
        def fail_audit(*args, **kwargs):
            raise RuntimeError("SYNTHETIC_AUDIT_FAILURE_NO_PRIVATE_DETAILS")
        with monkeypatch.context() as patch:
            patch.setattr(TicketRepository, "event", fail_audit)
            for route, method, label, payload in (("/api/v1/tickets", "POST", "owner", {"subject": "Must rollback", "description": "Synthetic"}),
                ("/api/v1/tickets/"+identifier+"/messages", "POST", "owner", {"body": "Must rollback"}),
                ("/api/v1/agent/tickets/"+identifier+"/assignment", "PATCH", "agent", {"assigned_agent_id": str(fixture.actors["agent"].id)}),
                ("/api/v1/agent/tickets/"+identifier+"/status", "PATCH", "agent", {"status": "RESOLVED"}),
                ("/api/v1/agent/tickets/"+identifier+"/priority", "PATCH", "agent", {"priority": "HIGH"})):
                response = client.request(method, route, headers=fixture.headers(label), json=payload)
                assert response.status_code == 500 and "SYNTHETIC_AUDIT_FAILURE" not in response.text
        row = fixture.connection.execute(select(Ticket).where(Ticket.id == UUID(identifier))).mappings().one()
        assert row["status"].value == "OPEN" and row["priority"].value == "NORMAL" and row["assigned_agent_id"] is None and row["resolved_at"] is None
        assert fixture.connection.scalar(select(func.count()).select_from(Ticket).where(Ticket.customer_id == fixture.actors["owner"].id)) == 1
        assert fixture.connection.scalar(select(func.count()).select_from(TicketMessage).where(TicketMessage.ticket_id == UUID(identifier))) == 0
        assert fixture.connection.scalar(select(func.count()).select_from(TicketEvent).where(TicketEvent.ticket_id == UUID(identifier))) == original_count


def test_live_concurrent_claim_committed_winner_and_single_audit_event(ticket_database):
    settings, engine = ticket_database
    marker = uuid4().hex
    # A committed, uniquely marked synthetic fixture is necessary for two truly
    # independent PostgreSQL transactions to see the same claimable ticket.
    # It and its append-only audit history remain in SupportLens_test, never reset/deleted.
    with engine.begin() as connection:
        inspect_connection(connection, settings.spec(Environment.TEST), require_readonly=False)
        verify_schema(connection)
        with Session(bind=connection, join_transaction_mode="rollback_only", expire_on_commit=False) as session:
            owner = synthetic_user(session, "ClaimOwner "+marker)
            first = synthetic_user(session, "ClaimAgent1 "+marker, UserRole.AGENT)
            second = synthetic_user(session, "ClaimAgent2 "+marker, UserRole.AGENT)
            owner_snapshot, a, b = snapshot(owner), snapshot(first), snapshot(second)
            ticket = TicketRepository(session, owner_snapshot, UserRole.CUSTOMER, write=True).create(
                TicketCreate(subject="M2C retained claim "+marker, description="Synthetic concurrency fixture; retain audit history."))
            identifier = ticket.id
    record = {"marker": marker, "ticket_id": str(identifier), "user_ids": [str(owner_snapshot.id), str(a.id), str(b.id)],
              "database": "SupportLens_test", "retained_for_audit": True}
    with Path("artifacts/m2_c/retained_concurrency_fixtures.jsonl").open("a") as output:
        output.write(json.dumps(record)+"\n")
    contender_pid = Queue()
    class ObservedDatabase(Database):
        @contextmanager
        def transaction(self, *, readonly=False):
            with super().transaction(readonly=readonly) as session:
                contender_pid.put(session.scalar(select(func.pg_backend_pid())))
                yield session
    database = ObservedDatabase(settings)
    service = TicketService(database)
    pool = ThreadPoolExecutor(max_workers=1)
    def contender():
        try:
            service.claim(b, identifier)
            return 200, "claimed"
        except APIError as error:
            return error.status, error.code
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            session = Session(bind=connection, join_transaction_mode="rollback_only", expire_on_commit=False)
            try:
                inspect_connection(connection, settings.spec(Environment.TEST), require_readonly=False)
                winner_pid = connection.scalar(select(func.pg_backend_pid()))
                claimed = TicketRepository(session, a, UserRole.AGENT, write=True).claim(identifier)
                assert claimed.assigned_agent_id == a.id
                future = pool.submit(contender)
                waiting_pid = contender_pid.get(timeout=30)
                blocked = False
                deadline = time.monotonic()+7
                while time.monotonic() < deadline:
                    blocked = connection.execute(text("SELECT :winner = ANY(pg_blocking_pids(:contender))"),
                        {"winner": winner_pid, "contender": waiting_pid}).scalar_one()
                    if blocked: break
                    time.sleep(0.05)
                assert blocked, "Second agent must wait on the PostgreSQL ticket row lock."
                session.close()
                transaction.commit()
                assert future.result(timeout=30) == (409, "ticket_already_assigned")
                assert connection.scalar(select(Ticket.assigned_agent_id).where(Ticket.id == identifier)) == a.id
                events = connection.execute(select(TicketEvent.__table__).where(TicketEvent.ticket_id == identifier)).mappings().all()
                assert len(events) == 2 and sum(row["event_type"] == TicketEventType.ASSIGNMENT_CHANGED for row in events) == 1
                assignment = next(row for row in events if row["event_type"] == TicketEventType.ASSIGNMENT_CHANGED)
                assert assignment["actor_id"] == a.id and assignment["metadata"]["before"] == {"assigned_agent_id": None}
                assert assignment["metadata"]["after"] == {"assigned_agent_id": str(a.id)}
                Path("artifacts/m2_c/concurrent_claim_result.json").write_text(json.dumps({
                    "real_row_lock_blocking_observed": True, "winner_committed": True, "loser_status": 409,
                    "assignment_audit_event_count": 1, "fixture_marker": marker, "test_fixture_retained": True}, indent=2)+"\n")
            finally:
                session.close()
                if transaction.is_active:
                    transaction.rollback()
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        database.close()
