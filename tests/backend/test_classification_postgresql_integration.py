"""Explicit TEST-only verification: real transactions, recovery, replay and competing retries."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
from queue import Queue
import threading
import time
from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from supportlens.backend.api.database import Database
from supportlens.backend.auth.users import snapshot
from supportlens.backend.classification.constants import CORE_LABELS, MODEL_VERSION, TRUSTED_SHA256
from supportlens.backend.classification.inference import InferenceService
from supportlens.backend.classification.schemas import ClassificationState, InferenceResult
from supportlens.backend.classification.service import ClassificationJob, ClassificationOptions, ClassificationService
from supportlens.backend.classification.worker import ClassificationWorker
from supportlens.backend.config import Environment
from supportlens.backend.db.models import AIRun, Ticket, TicketEvent
from supportlens.backend.db.preflight import inspect_connection
from supportlens.backend.domain.enums import AIRunStatus, Intent, TicketEventType, UserRole
from supportlens.backend.tickets.schemas import TicketCreate
from supportlens.backend.tickets.service import TicketRepository
from .test_tickets_postgresql_integration import ticket_database, ticket_http, synthetic_user

pytestmark = [pytest.mark.db_integration, pytest.mark.skipif(os.environ.get("SUPPORTLENS_RUN_DB_TESTS") != "1",
    reason="Explicit verified PostgreSQL test opt-in is absent.")]


def successful():
    return InferenceResult(status=ClassificationState.SUCCEEDED, predicted_intent=Intent.FEATURE, confidence=.8,
        class_scores={label: (.8 if label == "FEATURE" else .2/6) for label in CORE_LABELS}, artifact_verified=True, duration_ms=1)


def test_live_classification_real_model_persistence_and_customer_projection(ticket_http):
    fixture = ticket_http
    store = fixture.app.state.runtime.classifications
    store.options = replace(store.options, retry_cooldown_seconds=0)
    with TestClient(fixture.app) as client:
        headers = fixture.headers("agent")
        created = client.post("/api/v1/tickets", headers=fixture.headers("owner"),
            json={"subject": "Synthetic M4 playback report", "description": "Synthetic music pauses on a test device."})
        assert created.status_code == 201
        ticket_id = created.json()["id"]
        path = f"/api/v1/agent/tickets/{ticket_id}/classification"
        before = dict(fixture.connection.execute(select(Ticket.__table__).where(Ticket.id == ticket_id)).mappings().one())
        initial = client.get(path, headers=headers).json()
        assert initial["status"] == "PENDING" and initial["total_runs"] == 1
        first = initial["current"]["id"]
        assert client.get(path).status_code == 401
        assert client.get(path, headers=fixture.headers("owner")).status_code == 403
        assert client.get(path, headers=fixture.headers("inactive_agent")).status_code == 403
        assert client.get(f"/api/v1/agent/tickets/{uuid4()}/classification", headers=headers).status_code == 404
        worker = ClassificationWorker(store, InferenceService())
        assert worker.process_once(ticket_id=ticket_id)
        data = client.get(path, headers=headers).json()
        assert data["status"] == "SUCCEEDED" and data["current"]["id"] == first
        run = data["current"]
        assert run["model_version"] == MODEL_VERSION and run["artifact_sha256"] == TRUSTED_SHA256["model.joblib"]
        assert run["requires_human_review"] and not run["automated_actions_allowed"]
        assert run["artifact_verified"] and run["low_score"] == (run["confidence"] < .5)
        assert run["completed_at"] and run["duration_ms"] >= 0 and run["threshold_validated"] is False
        public = client.get(f"/api/v1/tickets/{ticket_id}", headers=fixture.headers("owner"))
        assert public.status_code == 200 and public.json()["history"]["total"] == 1
        assert MODEL_VERSION not in public.text and TRUSTED_SHA256["model.joblib"] not in public.text
        assert "score_metadata" not in public.text and "CLASSIFICATION_COMPLETED" not in public.text
        body = {"expected_run_id": first}
        retried = client.post(path+"/retry", headers=headers, json=body)
        replay = client.post(path+"/retry", headers=headers, json=body)
        assert retried.status_code == replay.status_code == 202
        assert retried.json()["id"] == replay.json()["id"] != first
        assert retried.json()["generation"] == 2
        assert ClassificationWorker(store, InferenceService(enabled=False)).process_once(ticket_id=ticket_id)
        unavailable = client.get(path, headers=headers).json()
        assert unavailable["status"] == "UNAVAILABLE" and unavailable["current"]["confidence"] is None
        assert unavailable["total_runs"] == 2
        # Repeating the same observed-parent request returns the original accepted attempt, even once terminal.
        completed_replay = client.post(path+"/retry", headers=headers, json=body)
        assert completed_replay.status_code == 200 and completed_replay.json()["id"] == retried.json()["id"]
        assert client.get(path, headers=headers).json()["total_runs"] == 2
        assert client.post(path+"/retry", headers=fixture.headers("owner"), json=body).status_code == 403
        assert client.post(path+"/retry", headers=headers, json={"expected_run_id": str(uuid4())}).status_code == 409
        store.options = replace(store.options, retry_limit_per_minute=1)
        assert client.post(path+"/retry", headers=headers, json={"expected_run_id": retried.json()["id"]}).status_code == 429
        after = dict(fixture.connection.execute(select(Ticket.__table__).where(Ticket.id == ticket_id)).mappings().one())
        assert before == after  # No status, priority, assignment or timestamp side effects.
        assert client.get(f"/api/v1/tickets/{ticket_id}", headers=fixture.headers("owner")).json()["history"]["total"] == 1


def test_live_expired_leases_fencing_atomic_audit_and_bounded_recovery(ticket_http, monkeypatch):
    fixture = ticket_http
    store = fixture.app.state.runtime.classifications
    store.options = replace(store.options, retry_cooldown_seconds=0)
    with TestClient(fixture.app) as client:
        ticket_id = client.post("/api/v1/tickets", headers=fixture.headers("owner"),
            json={"subject": "Synthetic M4 recovery", "description": "Synthetic restart recovery fixture."}).json()["id"]
    def expire(identifier):
        with store.database.transaction() as session:
            row = session.get(AIRun, identifier)
            row.score_metadata = row.score_metadata | {"lease_expires_at": 0}
    original = store.claim_next(ticket_id=ticket_id)
    expire(original.id)
    recovered = store.claim_next(ticket_id=ticket_id)
    assert original.id == recovered.id and original.lease_token != recovered.lease_token
    assert not store.complete(original, successful())
    # Actual FK failure during audit insertion must roll back the result update in the same transaction.
    original_add = Session.add
    with monkeypatch.context() as patch:
        def reject_audit(session, instance, **kwargs):
            if isinstance(instance, TicketEvent) and instance.event_type == TicketEventType.AI_DECISION_RECORDED:
                instance.actor_id = uuid4()  # Deliberately absent synthetic FK, only inside rollback fixture.
            return original_add(session, instance, **kwargs)
        patch.setattr(Session, "add", reject_audit)
        with pytest.raises(IntegrityError):
            store.complete(recovered, successful())
    state = fixture.connection.execute(select(AIRun.__table__).where(AIRun.id == recovered.id)).mappings().one()
    assert state["status"] == AIRunStatus.RUNNING and state["predicted_intent"] is None
    assert store.complete(recovered, successful())
    assert not store.complete(recovered, successful())
    actor = snapshot(fixture.actors["agent"])
    next_run = store.retry(actor, ticket_id, recovered.id)
    for _ in range(store.options.max_executions):
        leased = store.claim_next(ticket_id=ticket_id)
        assert leased and not leased.recovery_exhausted
        expire(leased.id)
    inference = Mock()
    assert ClassificationWorker(store, inference).process_once(ticket_id=ticket_id)
    inference.classify.assert_not_called()
    result = store.get(actor, ticket_id)
    assert result.current.id == next_run.id and result.current.failure_category.value == "RECOVERY_EXHAUSTED"
    assert result.current.status == ClassificationState.FAILED and result.total_runs == 2
    assert fixture.connection.scalar(select(func.count()).select_from(TicketEvent).where(
        TicketEvent.ticket_id == ticket_id, TicketEvent.event_type == TicketEventType.AI_DECISION_RECORDED)) == 3
    wrong_ticket = replace(recovered, ticket_id=uuid4())
    assert not store.complete(wrong_ticket, successful())


def test_live_concurrent_generation_retry_and_claim_preserve_history(ticket_database):
    settings, engine = ticket_database
    marker = uuid4().hex
    with engine.begin() as connection:
        inspect_connection(connection, settings.spec(Environment.TEST), require_readonly=False)
        with Session(bind=connection, join_transaction_mode="rollback_only", expire_on_commit=False) as session:
            owner = synthetic_user(session, "M4Owner "+marker)
            a = snapshot(synthetic_user(session, "M4AgentA "+marker, UserRole.AGENT))
            b = snapshot(synthetic_user(session, "M4AgentB "+marker, UserRole.AGENT))
            ticket = TicketRepository(session, snapshot(owner), UserRole.CUSTOMER, write=True).create(
                TicketCreate(subject="M4 retained inference "+marker, description="Synthetic concurrency fixture; retain history."))
            ticket_id = ticket.id
    folder = Path("artifacts/m4_a")
    folder.mkdir(exist_ok=True)
    with (folder/"retained_concurrency_fixtures.jsonl").open("a") as output:
        output.write(json.dumps({"marker": marker, "ticket_id": str(ticket_id), "database": "SupportLens_test", "retained_for_audit": True})+"\n")
    contender_pids = Queue()
    class ObservedDatabase(Database):
        @contextmanager
        def transaction(self, *, readonly=False):
            with super().transaction(readonly=readonly) as session:
                if threading.current_thread() is not threading.main_thread():
                    contender_pids.put(session.scalar(select(func.pg_backend_pid())))
                yield session
    database = ObservedDatabase(settings)
    store = ClassificationService(database, ClassificationOptions(retry_cooldown_seconds=0))
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        initial_job = store.claim_next(ticket_id=ticket_id)
        assert initial_job is not None and store.complete(initial_job, successful())
        initial_id = initial_job.id
        with engine.connect() as connection:
            transaction = connection.begin()
            session = Session(bind=connection, join_transaction_mode="rollback_only", expire_on_commit=False)
            try:
                inspect_connection(connection, settings.spec(Environment.TEST), require_readonly=False)
                winner_pid = connection.scalar(select(func.pg_backend_pid()))
                accepted = store._retry(session, a, ticket_id, initial_id)
                future = pool.submit(store.retry, b, ticket_id, initial_id)
                waiting_pid = contender_pids.get(timeout=60)
                blocked = False
                deadline = time.monotonic()+7
                while time.monotonic() < deadline:
                    blocked = connection.execute(text("SELECT :winner = ANY(pg_blocking_pids(:contender))"),
                        {"winner": winner_pid, "contender": waiting_pid}).scalar_one()
                    if blocked:
                        break
                    time.sleep(.05)
                assert blocked, "Concurrent retry must wait for the ticket generation lock."
                session.close()
                transaction.commit()
                assert future.result(timeout=60).id == accepted.id
            finally:
                session.close()
                if transaction.is_active:
                    transaction.rollback()
        assert store.get(a, ticket_id).total_runs == 2
        # A different consumer must skip an eligible row while its claim lock is held.
        with engine.connect() as connection:
            transaction = connection.begin()
            inspect_connection(connection, settings.spec(Environment.TEST), require_readonly=False)
            connection.execute(select(AIRun.id).where(AIRun.id == accepted.id).with_for_update()).one()
            assert pool.submit(store.claim_next, ticket_id=ticket_id).result(timeout=60) is None
            transaction.rollback()
        lease = store.claim_next(ticket_id=ticket_id)
        assert lease.id == accepted.id and store.claim_next(ticket_id=ticket_id) is None
        assert store.complete(lease, successful()) and not store.complete(lease, successful())
        data = store.get(a, ticket_id)
        assert data.total_runs == 2 and data.current.generation == 2
        assert {row.id for row in data.recent_runs} == {initial_id, accepted.id}
        assert all(row.requires_human_review and row.status == ClassificationState.SUCCEEDED for row in data.recent_runs)
        with database.transaction(readonly=True) as session:
            ticket = session.get(Ticket, ticket_id)
            assert ticket.status.value == "OPEN" and ticket.priority.value == "NORMAL" and ticket.assigned_agent_id is None
            assert session.scalar(select(func.count()).select_from(TicketEvent).where(TicketEvent.ticket_id == ticket_id)) == 4
        (folder/"concurrent_classification_result.json").write_text(json.dumps({"status": "PASS", "marker": marker,
            "real_row_lock_blocking_observed": True, "skip_locked_observed": True, "single_retry_generation": True,
            "historical_runs_retained": 2, "stale_publication_rejected": True, "database": "SupportLens_test"}, indent=2)+"\n")
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        database.close()
