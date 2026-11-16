"""Real PostgreSQL/FKs with controlled provider results and rolled-back fixtures."""

from datetime import timedelta
import os
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from supportlens.backend.auth.users import snapshot
from supportlens.backend.db.models import AIRun, KnowledgeCitation, TicketMessage
from supportlens.backend.drafts.grounding import OPENING
from supportlens.backend.drafts.provider import ProviderResult
from supportlens.backend.drafts.service import DraftService
from supportlens.backend.drafts.worker import DraftProcessor
from supportlens.backend.knowledge.catalog import Catalog
from supportlens.backend.knowledge.embedding import LocalEmbedder
from supportlens.backend.knowledge.indexing import Indexer
from supportlens.backend.knowledge.retrieval import RetrievalService
from supportlens.backend.knowledge.schemas import ArticleInput, LifecycleInput
from tests.backend.test_tickets_postgresql_integration import ticket_database, ticket_http

pytestmark = [
    pytest.mark.db_integration,
    pytest.mark.skipif(os.environ.get("SUPPORTLENS_RUN_DB_TESTS") != "1", reason="Verified PostgreSQL opt-in is absent."),
]


def test_live_draft_decisions_citations_quota_and_customer_boundary(ticket_http):
    client = TestClient(ticket_http.app)
    agent = ticket_http.headers("agent")
    owner = ticket_http.headers("owner")
    actor = snapshot(ticket_http.actors["agent"])
    runtime = ticket_http.app.state.runtime
    ticket = client.post("/api/v1/tickets", headers=owner, json={
        "subject": "Synthetic playback pauses", "description": "Fictional player playback trace.",
    }).json()
    identity = UUID(ticket["id"])
    ticket_path = "/api/v1/agent/tickets/" + ticket["id"]
    path = ticket_path + "/drafts"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=owner).status_code == 403
    assert client.post(path, headers=owner, json={}).status_code == 403
    assert client.get(path, headers=ticket_http.headers("inactive_agent")).status_code == 403
    embedder = LocalEmbedder()
    catalog = Catalog(runtime.database, allow_synthetic=True)
    article = catalog.create(actor, ArticleInput(
        title="Fictional playback trace guidance", body="The fictional player trace displays a playback pause.",
        source_name="Synthetic draft test", source_type="SYNTHETIC_TEST", rights_reference="Original fixture.",
    ))
    published = catalog.transition(actor, article.id, LifecycleInput(expected_generation=1, operation="PUBLISH"))
    assert Indexer(runtime.database, embedder, allow_synthetic=True).process_once()
    calls = []
    def generate(payload):
        assert "INTERNAL_SENTINEL" not in payload
        calls.append(True)
        return ProviderResult(output='{"disposition":"draft","sentence_ids":["e1s1"]}', usage={"promptTokenCount": 45})
    provider = SimpleNamespace(model="gemini-3.1-flash-lite", available=True, generate=generate)
    runtime.drafts = DraftService(runtime.database, provider, daily_limit=1, synthetic_ticket=identity)
    worker = DraftProcessor(runtime.drafts, RetrievalService(runtime.database, embedder, allow_synthetic=True))
    with runtime.database.transaction() as session:
        session.add(TicketMessage(ticket_id=identity, author_id=actor.id, visibility="INTERNAL", body="INTERNAL_SENTINEL"))
    payload = {"request_id": str(uuid4()), "expected_run_id": None}
    first = client.post(path, headers=agent, json=payload)
    assert first.status_code == 202
    run_id = first.json()["id"]
    assert client.post(path, headers=agent, json=payload).json()["id"] == run_id
    assert client.post(path, headers=agent, json=payload | {"request_id": str(uuid4())}).status_code == 409
    assert worker.process_once()
    draft = client.get(path, headers=agent).json()["items"][0]
    assert draft["state"] == "DRAFT" and draft["usage"] == {"promptTokenCount": 45}
    assert draft["evidence"][0]["revision_id"] == str(published.revision_id)
    review_path = path + "/" + run_id + "/review"
    edit = {"request_id": str(uuid4()), "expected_version": 1, "action": "EDIT", "text": draft["text"]}
    assert client.post(review_path, headers=agent, json=edit | {"text": OPENING + "\nWe completed the change."}).status_code == 422
    assert client.post(review_path, headers=agent, json=edit).json()["version"] == 2
    assert client.post(review_path, headers=agent, json=edit).json()["version"] == 2
    assert client.post(review_path, headers=agent, json={
        "request_id": str(uuid4()), "expected_version": 2, "action": "APPROVE",
    }).json()["state"] == "APPROVED"
    public = client.get("/api/v1/tickets/" + ticket["id"], headers=owner).json()
    assert public["messages"]["total"] == 0 and public["history"]["total"] == 1
    with runtime.database.transaction() as session:
        assert session.scalar(select(func.count()).select_from(KnowledgeCitation).where(
            KnowledgeCitation.ai_run_id == UUID(run_id))) == 1
        row = session.get(AIRun, UUID(run_id))
        # A delayed run can reserve today even if it was queued several days ago.
        # The quota follows the attempt day, not the queue timestamp.
        row.created_at -= timedelta(days=3)
    assert client.post(path, headers=agent, json={
        "request_id": str(uuid4()), "expected_run_id": run_id,
    }).status_code == 202
    assert worker.process_once()
    assert client.get(path, headers=agent).json()["items"][0]["failure"] == "DAILY_LIMIT"
    assert len(calls) == 1


def test_live_draft_lease_recovery_and_uncertain_call_is_not_replayed(ticket_http):
    client = TestClient(ticket_http.app)
    actor = snapshot(ticket_http.actors["agent"])
    runtime = ticket_http.app.state.runtime
    ticket = client.post("/api/v1/tickets", headers=ticket_http.headers("owner"), json={
        "subject": "Synthetic lease test", "description": "Fictional draft queue.",
    }).json()
    identity = UUID(ticket["id"])
    runtime.drafts = DraftService(runtime.database, SimpleNamespace(model="gemini-3.1-flash-lite", available=True),
                                  synthetic_ticket=identity)
    path = f"/api/v1/agent/tickets/{identity}/drafts"
    run_id = UUID(client.post(path, headers=ticket_http.headers("agent"), json={
        "request_id": str(uuid4()), "expected_run_id": None,
    }).json()["id"])
    first = runtime.drafts.claim()
    assert first.run_id == run_id and runtime.drafts.claim() is None
    with runtime.database.transaction() as session:
        row = session.get(AIRun, run_id)
        row.score_metadata = row.score_metadata | {"lease_until": "2000-01-01T00:00:00+00:00"}
    second = runtime.drafts.claim()
    assert second.run_id == run_id and second.token != first.token
    assert not runtime.drafts.complete(first, "FAILED", "stale")
    with runtime.database.transaction() as session:
        row = session.get(AIRun, run_id)
        row.score_metadata = row.score_metadata | {"lease_until": "2000-01-01T00:00:00+00:00",
                                                  "attempts": [{"day": "2000-01-01"}]}
    assert runtime.drafts.claim() is True
    outcome = runtime.drafts.listing(actor, identity).items[0]
    assert outcome.state == "FAILED" and outcome.failure == "INTERRUPTED" and outcome.provider_calls == 1


def test_live_stale_evidence_prevents_approval_and_keeps_citation(ticket_http):
    client = TestClient(ticket_http.app)
    actor = snapshot(ticket_http.actors["agent"])
    runtime = ticket_http.app.state.runtime
    ticket = client.post("/api/v1/tickets", headers=ticket_http.headers("owner"), json={
        "subject": "Synthetic stale playback trace", "description": "Fictional player pauses while testing.",
    }).json()
    identity = UUID(ticket["id"])
    embedder = LocalEmbedder()
    catalog = Catalog(runtime.database, allow_synthetic=True)
    article = catalog.create(actor, ArticleInput(
        title="Fictional playback trace guidance", body="The fictional player trace displays a playback pause.",
        source_name="Synthetic draft test", source_type="SYNTHETIC_TEST", rights_reference="Original fixture.",
    ))
    published = catalog.transition(actor, article.id, LifecycleInput(expected_generation=1, operation="PUBLISH"))
    assert Indexer(runtime.database, embedder, allow_synthetic=True).process_once()
    provider = SimpleNamespace(
        model="gemini-3.1-flash-lite", available=True,
        generate=lambda _: ProviderResult(output='{"disposition":"draft","sentence_ids":["e1s1"]}'),
    )
    runtime.drafts = DraftService(runtime.database, provider, synthetic_ticket=identity)
    worker = DraftProcessor(runtime.drafts, RetrievalService(runtime.database, embedder, allow_synthetic=True))
    path = f"/api/v1/agent/tickets/{identity}/drafts"
    agent = ticket_http.headers("agent")
    run = client.post(path, headers=agent, json={"request_id": str(uuid4()), "expected_run_id": None}).json()
    assert worker.process_once()
    catalog.transition(actor, article.id,
                       LifecycleInput(expected_generation=published.generation, operation="UNPUBLISH"))
    review_path = path + "/" + run["id"] + "/review"
    failed = client.post(review_path, headers=agent, json={
        "request_id": str(uuid4()), "expected_version": 1, "action": "APPROVE",
    })
    assert failed.status_code == 409 and failed.json()["error"]["code"] == "draft_stale_evidence"
    assert client.post(review_path, headers=agent, json={
        "request_id": str(uuid4()), "expected_version": 1, "action": "REJECT",
    }).json()["state"] == "REJECTED"
    with runtime.database.transaction(readonly=True) as session:
        citation = session.scalar(select(KnowledgeCitation).where(KnowledgeCitation.ai_run_id == UUID(run["id"])))
        assert citation.knowledge_article_id == published.revision_id
