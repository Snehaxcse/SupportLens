"""Real PostgreSQL/ONNX tests. All new fixture rows roll back."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
from threading import Barrier
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from supportlens.backend.api.errors import APIError
from supportlens.backend.api.database import Database
from supportlens.backend.auth.users import snapshot
from supportlens.backend.db.models import AIRun, KnowledgeCitation, TicketMessage
from supportlens.backend.domain.enums import Visibility
from supportlens.backend.domain.enums import UserRole
from supportlens.backend.knowledge.catalog import Catalog
from supportlens.backend.knowledge.embedding import LocalEmbedder
from supportlens.backend.knowledge.indexing import Indexer
from supportlens.backend.knowledge.models import KnowledgeChunk, KnowledgeEmbedding, KnowledgeEntryEvent, KnowledgeRevision
from supportlens.backend.knowledge.retrieval import RetrievalService
from supportlens.backend.knowledge.schemas import ArticleEdit, ArticleInput, LifecycleInput, PreviewInput
from tests.backend.test_knowledge import article
from tests.backend.test_tickets_postgresql_integration import synthetic_user, ticket_database, ticket_http

pytestmark = [
    pytest.mark.db_integration,
    pytest.mark.skipif(os.environ.get("SUPPORTLENS_RUN_DB_TESTS") != "1", reason="Verified PostgreSQL opt-in is absent."),
]


@pytest.fixture
def knowledge(ticket_http):
    runtime = ticket_http.app.state.runtime
    embedder = LocalEmbedder()
    runtime.knowledge = Catalog(runtime.database, allow_synthetic=True)
    runtime.retrieval = RetrievalService(runtime.database, embedder, allow_synthetic=True)
    return ticket_http, Indexer(runtime.database, embedder, allow_synthetic=True)


def test_live_knowledge_lifecycle_provenance_and_retrieval(knowledge):
    fixture, indexer = knowledge
    client = TestClient(fixture.app)
    agent = fixture.headers("agent")
    root = "/api/v1/agent/knowledge"
    title = "Synthetic interruption fixture " + uuid4().hex
    content = "# Playback interruption\nMusic playback stops in the fictional player. This synthetic fixture is not support guidance."
    body = article(title=title, body=content)
    created = client.post(root, headers=agent, json=body)
    assert created.status_code == 201
    first = created.json()
    path = root + "/" + first["id"]
    assert first["status"] == "DRAFT" and first["revision"] == 1
    assert client.get(root, headers=agent, params={"q": title, "status": "DRAFT"}).json()["total"] == 1
    assert client.get(path, headers=fixture.headers("inactive_agent")).status_code == 403
    assert client.get(path, headers=fixture.headers("owner")).status_code == 403

    def transition(record, operation):
        result = client.post(path + "/lifecycle", headers=agent,
                             json={"expected_generation": record["generation"], "operation": operation})
        assert result.status_code == 200
        return result.json()

    def preview(method, source="SYNTHETIC_TEST", query="music playback interruption"):
        result = client.post(root + "/retrieval-preview", headers=agent,
                             json={"method": method, "query": query, "source_type": source})
        assert result.status_code == 200
        return result.json()

    assert not preview("lexical")["items"]
    published = transition(first, "PUBLISH")
    assert published["index_state"] == "PENDING" and not published["retrieval_eligible"]
    assert indexer.process_once()
    for method in ("lexical", "semantic", "hybrid"):
        result = preview(method)
        assert result["method"] == method
        hit = result["items"][0]
        assert hit["article_id"] == first["id"] and hit["revision_id"] == first["revision_id"]
        assert hit["source_name"] == first["source_name"] and hit["revision"] == 1
        assert "Music playback stops" in hit["excerpt"]
    assert not preview("hybrid", "APPROVED_REFERENCE")["items"]
    assert not preview("lexical", query="zyqjvx unknownterm981")["items"]
    ticket = client.post("/api/v1/tickets", headers=fixture.headers("owner"),
                         json={"subject": "Fictional player", "description": "Synthetic device question"}).json()
    customer_path = "/api/v1/tickets/" + ticket["id"]
    assert client.post(customer_path + "/messages", headers=fixture.headers("owner"),
                       json={"body": "Music playback interruption"}).status_code == 201
    evidence_path = "/api/v1/agent/tickets/" + ticket["id"] + "/knowledge"
    assert client.get(evidence_path, headers=fixture.headers("owner")).status_code == 403
    evidence = client.get(evidence_path, headers=agent, params={"method": "hybrid"}).json()
    assert evidence["ticket_id"] == ticket["id"] and evidence["items"][0]["revision_id"] == first["revision_id"]

    # The pre-existing citation FK points to the immutable revision, not the catalog's current pointer.
    with fixture.app.state.runtime.database.transaction() as session:
        run = session.scalar(select(AIRun).where(AIRun.ticket_id == UUID(ticket["id"])))
        assert run is not None
        session.add(KnowledgeCitation(ai_run_id=run.id, knowledge_article_id=UUID(first["revision_id"]),
                                       article_version=1, citation_metadata={"test_fixture": True}))
    updated = client.patch(path, headers=agent, json=body | {"body": content + "\nNew fictional revision.",
                                                           "expected_generation": published["generation"]})
    assert updated.status_code == 200
    second = updated.json()
    assert second["revision"] == 2 and second["status"] == "DRAFT"
    assert client.patch(path, headers=agent, json=body | {"expected_generation": published["generation"]}).status_code == 409
    assert not preview("hybrid")["items"]
    second = transition(second, "PUBLISH")
    assert indexer.process_once()
    assert preview("hybrid")["items"][0]["revision_id"] == second["revision_id"]
    assert client.get(path + "?revision=1", headers=agent).json()["body"] == content
    revisions = client.get(path + "/revisions", headers=agent).json()
    assert [row["revision"] for row in revisions["items"]] == [2, 1]
    second = transition(second, "REINDEX")
    reuse_job = indexer.claim()
    chunks, new_vectors, model = indexer.prepare(reuse_job)
    assert chunks and not new_vectors and model == indexer.embedder.identity.key
    assert indexer.complete(reuse_job, chunks, new_vectors, model)
    draft = transition(second, "UNPUBLISH")
    assert not preview("hybrid")["items"]
    archived = transition(draft, "ARCHIVE")
    assert archived["status"] == "ARCHIVED"
    with fixture.app.state.runtime.database.transaction(readonly=True) as session:
        retained = session.scalar(select(KnowledgeCitation).where(KnowledgeCitation.knowledge_article_id == UUID(first["revision_id"])))
        assert retained.article_version == 1
        assert session.scalar(select(func.count()).select_from(KnowledgeEntryEvent).where(
            KnowledgeEntryEvent.entry_id == UUID(first["id"]))) == 7
        assert session.scalar(select(func.count()).select_from(KnowledgeChunk).where(
            KnowledgeChunk.revision_id == UUID(first["revision_id"]))) == 1


def test_live_index_recovery_fences_stale_leases_and_publication(knowledge):
    fixture, indexer = knowledge
    actor = snapshot(fixture.actors["agent"])
    runtime = fixture.app.state.runtime
    draft = runtime.knowledge.create(actor, ArticleInput.model_validate(article()))
    with pytest.raises(APIError) as denied:
        Catalog(runtime.database).transition(actor, draft.id, LifecycleInput(expected_generation=1, operation="PUBLISH"))
    assert denied.value.code == "knowledge_synthetic"
    state = runtime.knowledge.transition(actor, draft.id, LifecycleInput(expected_generation=1, operation="PUBLISH"))
    first = indexer.claim()
    assert first.revision_id == draft.revision_id
    with runtime.database.transaction() as session:
        row = session.get(KnowledgeRevision, draft.revision_id)
        row.lease_until = session.scalar(select(func.clock_timestamp())) - timedelta(seconds=1)
    second = indexer.claim()
    assert first.lease != second.lease
    prepared = indexer.prepare(second)
    assert not indexer.complete(first, *prepared)
    runtime.knowledge.transition(actor, state.id, LifecycleInput(expected_generation=state.generation, operation="UNPUBLISH"))
    assert not indexer.complete(second, *prepared) and indexer.claim() is None
    with runtime.database.transaction(readonly=True) as session:
        keys = [item.embedding_hash for item in prepared[0]]
        assert session.scalar(select(func.count()).select_from(KnowledgeEmbedding).where(
            KnowledgeEmbedding.content_hash.in_(keys))) == 0


def test_live_embedding_unavailable_preserves_lexical_and_excludes_internal_notes(knowledge):
    fixture, _ = knowledge
    runtime = fixture.app.state.runtime
    actor = snapshot(fixture.actors["agent"])
    draft = runtime.knowledge.create(actor, ArticleInput.model_validate(article(
        title="Zivcronfrindle", body="Zivcronfrindle appears only in the private note.",
    )))
    runtime.knowledge.transition(actor, draft.id, LifecycleInput(expected_generation=1, operation="PUBLISH"))
    disabled = LocalEmbedder(False)
    assert Indexer(runtime.database, disabled, allow_synthetic=True).process_once()
    lookup = RetrievalService(runtime.database, disabled, allow_synthetic=True)
    result = lookup.preview(actor, PreviewInput(query="Zivcronfrindle", method="hybrid"))
    assert result.method == "lexical" and result.semantic_state == "UNAVAILABLE"
    assert result.items[0].article_id == draft.id
    client = TestClient(fixture.app)
    ticket = client.post("/api/v1/tickets", headers=fixture.headers("owner"),
                         json={"subject": "Unrelated", "description": "Ordinary question"}).json()
    with runtime.database.transaction() as session:
        session.add(TicketMessage(ticket_id=UUID(ticket["id"]), author_id=actor.id,
                                  visibility=Visibility.INTERNAL, body="Zivcronfrindle"))
    path = f"/api/v1/agent/tickets/{ticket['id']}/knowledge"
    assert not client.get(path, headers=fixture.headers("agent"), params={"method": "lexical"}).json()["items"]
    assert client.get(f"/api/v1/agent/tickets/{uuid4()}/knowledge", headers=fixture.headers("agent")).status_code == 404


def test_live_concurrent_article_edits_keep_one_revision(ticket_database):
    settings, _ = ticket_database
    database = Database(settings)
    catalog = Catalog(database, allow_synthetic=True)
    try:
        with database.transaction() as session:
            actor = snapshot(synthetic_user(session, "M4-B retained edit race", UserRole.AGENT))
        initial = article(title="Synthetic retained M4-B edit race " + uuid4().hex)
        draft = catalog.create(actor, ArticleInput(**initial))
        race = Barrier(2)

        def edit(body):
            race.wait(timeout=20)
            try:
                return catalog.edit(actor, draft.id, ArticleEdit(
                    **(initial | {"body": body}), expected_generation=draft.generation,
                ))
            except APIError as error:
                assert error.status == 409 and error.code == "knowledge_changed"
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            winners = [result for result in pool.map(edit, ["Synthetic edit A.", "Synthetic edit B."]) if result]
        assert len(winners) == 1 and winners[0].revision == 2
        published = catalog.transition(actor, draft.id, LifecycleInput(
            expected_generation=winners[0].generation, operation="PUBLISH",
        ))
        indexer = Indexer(database, LocalEmbedder(), allow_synthetic=True)
        claim_race = Barrier(2)

        def claim():
            claim_race.wait(timeout=20)
            return indexer.claim(entry_id=draft.id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            attempts = [pool.submit(claim) for _ in range(2)]
            jobs = [job for job in (attempt.result(timeout=90) for attempt in attempts) if job]
        assert len(jobs) == 1 and jobs[0].revision_id == published.revision_id
        archived = catalog.transition(actor, draft.id, LifecycleInput(
            expected_generation=published.generation, operation="ARCHIVE",
        ))
        assert archived.status == "ARCHIVED"
        assert not indexer.complete(jobs[0], [], {}, None)
        assert catalog.revisions(actor, draft.id, 1, 20).total == 2
    finally:
        database.close()
