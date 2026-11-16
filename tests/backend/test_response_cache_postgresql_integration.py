"""Guarded cache, replay, revocation and provider-fallback scenarios."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import os
from threading import Barrier
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from supportlens.backend.api.database import Database
from supportlens.backend.auth.users import snapshot
from supportlens.backend.classification.service import ClassificationService
from supportlens.backend.domain.enums import UserRole
from supportlens.backend.drafts.grounding import scrub
from supportlens.backend.drafts.provider import ProviderResult
from supportlens.backend.drafts.schemas import GenerateDraft, ReviewDraft
from supportlens.backend.drafts.service import DraftService, lease_actor
from supportlens.backend.drafts.worker import DraftProcessor
from supportlens.backend.knowledge.catalog import Catalog
from supportlens.backend.knowledge.embedding import LocalEmbedder
from supportlens.backend.knowledge.indexing import Indexer
from supportlens.backend.knowledge.retrieval import RetrievalService
from supportlens.backend.knowledge.schemas import ArticleInput, LifecycleInput, RetrievalOptions, SourceType
from supportlens.backend.response_cache.models import ResponseCacheEntry
from supportlens.backend.response_cache.schemas import ReuseApproval, ReuseRevocation
from supportlens.backend.response_cache.service import ResponseCache
from supportlens.backend.tickets.schemas import TicketCreate
from supportlens.backend.tickets.service import TicketService
from supportlens.backend.workflow.service import Workflow
from tests.backend.test_tickets_postgresql_integration import synthetic_user, ticket_database, ticket_http
from tests.backend.test_workflow_postgresql_integration import Scenario, classify

pytestmark = [pytest.mark.db_integration, pytest.mark.skipif(
    os.environ.get("SUPPORTLENS_RUN_DB_TESTS") != "1", reason="Verified PostgreSQL opt-in is absent.")]

SOURCE = {"subject": "Playback pauses", "description": "The fictional audio player pauses during playback."}
TARGET = {"subject": "Songs stop mid-track", "description": "The fictional music stops while playing a song."}
BODY = "The fictional audio player displays a playback trace. The fictional trace records when the music pauses or stops."


class CacheScenario(Scenario):
    def __init__(self, ticket_http):
        super().__init__(ticket_http)
        self.cache = ResponseCache(self.runtime.database, LocalEmbedder(), enabled=True)
        self.runtime.response_cache = self.cache
        self.fixture = ticket_http

    def select_ticket(self, payload, party="owner"):
        response = self.client.post("/api/v1/tickets", headers=self.fixture.headers(party), json=payload)
        assert response.status_code == 201
        self.id = UUID(response.json()["id"])
        self.customer_path = f"/api/v1/tickets/{self.id}"
        self.path = f"/api/v1/agent/tickets/{self.id}"
        self.runtime.drafts = DraftService(self.runtime.database, self.provider, allow_reviewed=True,
                                          workflow=self.runtime.workflow, cache=self.cache, daily_limit=8)
        self.worker = DraftProcessor(self.runtime.drafts, self.runtime.retrieval)
        classify(self.runtime.classifications, self.id)
        return self.id

    def reference(self, body=BODY):
        item = self.catalog.create(self.actor, ArticleInput(title="Fictional audio playback stops or pauses", body=body,
           source_name="Controlled approved reference fixture", source_type="APPROVED_REFERENCE",
           source_url="https://example.invalid/support", rights_reference="Original, fictional transaction fixture."))
        published = self.catalog.transition(self.actor, item.id, LifecycleInput(expected_generation=1, operation="PUBLISH"))
        assert Indexer(self.runtime.database, self.embedder).process_once()
        return published

    def approved(self):
        assert self.request().status_code == 202
        assert self.worker.process_once()
        latest = self.latest()
        assert latest["state"] == "DRAFT"
        approved = self.review(latest, "APPROVE")
        assert approved.status_code == 200
        return approved.json()

    def reuse(self, run, *, key=None, headers=None):
        return self.client.post(self.path + f"/drafts/{run['id']}/reuse", headers=headers or self.agent, json={
            "request_id": str(key or uuid4()), "expected_draft_version": run["version"], "confirm_general_reuse": True})


def test_live_separate_approval_paraphrase_hit_without_budget_and_single_public_send(ticket_http):
    s = CacheScenario(ticket_http)
    s.select_ticket(SOURCE)
    article = s.reference()
    source_path = s.path
    source = s.approved()
    assert source["cache_lookup"] == "MISS" and source["reuse_approval"] is None and len(s.calls) == 1
    assert s.send(source, uuid4()).status_code == 200  # The later explicit approval may ignore this exact delivery only.
    assert s.reuse(source, headers=s.owner).status_code == 403
    assert s.client.get("/api/v1/agent/response-cache/metrics").status_code == 401
    assert s.client.get("/api/v1/agent/response-cache/metrics", headers=s.owner).status_code == 403
    key = uuid4()
    approved = s.reuse(source, key=key)
    assert approved.status_code == 200 and approved.json()["status"] == "ACTIVE"
    cache_id = approved.json()["id"]
    assert s.reuse(source, key=key).json() == approved.json()
    assert s.reuse(source).status_code == 409
    s.select_ticket(TARGET, party="other")
    assert s.reuse(source).status_code == 404
    s.runtime.drafts.daily_limit = 0
    assert s.policy()["decision"] == "AI_DRAFT_ELIGIBLE"
    note = s.client.post(s.path + "/messages", headers=s.agent, json={"body":"INTERNAL_WORKFLOW_SENTINEL", "visibility":"INTERNAL"})
    assert note.status_code == 201
    target = s.approved()
    assert target["id"] != source["id"] and target["cache_lookup"] == "HIT" and target["provider_calls"] == 0
    assert target["cache_origin"]["id"] == cache_id and target["evidence"][0]["revision_id"] == str(article.revision_id)
    assert s.reuse(target).status_code == 409 and len(s.calls) == 1
    assert s.client.get(s.customer_path, headers=s.owner).status_code == 404
    owner = ticket_http.headers("other")
    assert s.client.get(s.path + "/drafts", headers=owner).status_code == 403
    assert s.client.get(s.customer_path, headers=owner).json()["messages"]["total"] == 0
    s.cache.threshold = 1.0
    assert s.send(target, uuid4()).json()["error"]["code"] == "cache_stale"
    s.cache.threshold = 0.90
    send_key = uuid4()
    delivery = s.send(target, send_key)
    assert delivery.status_code == 200 and s.send(target, send_key).json() == delivery.json()
    public = s.client.get(s.customer_path, headers=owner).json()
    assert public["messages"]["total"] == 1
    assert source["id"] not in str(public) and cache_id not in str(public) and "INTERNAL_WORKFLOW_SENTINEL" not in str(public)
    metrics = s.client.get("/api/v1/agent/response-cache/metrics", headers=s.agent).json()
    assert metrics["hits"] >= 1 and metrics["gemini_generation_bypasses"] == metrics["hits"]
    reused = s.client.get(source_path + "/drafts", headers=s.agent).json()["items"][0]["reuse_approval"]
    assert reused["hit_count"] == 1 and reused["id"] == cache_id


def test_live_unpublish_invalidates_and_new_ticket_falls_back(ticket_http):
    s = CacheScenario(ticket_http)
    s.select_ticket(SOURCE)
    article = s.reference()
    source = s.approved()
    cache_id = UUID(s.reuse(source).json()["id"])
    s.select_ticket(TARGET, party="other")
    hit = s.approved()
    assert hit["cache_lookup"] == "HIT" and len(s.calls) == 1
    s.catalog.transition(s.actor, article.id, LifecycleInput(expected_generation=article.generation, operation="UNPUBLISH"))
    with s.runtime.database.transaction(readonly=True) as session:
        entry = session.get(ResponseCacheEntry, cache_id)
        assert entry.state == "INACTIVE" and entry.version == 2 and entry.invalidation_reason == "EVIDENCE_CHANGED"
    assert s.send(hit, uuid4()).status_code == 409
    s.reference("The fictional player records the playback pause. The sample playback trace can help describe the issue.")
    s.select_ticket(TARGET)
    miss = s.approved()
    assert miss["cache_lookup"] == "MISS" and miss["provider_calls"] == 1 and len(s.calls) == 2


def test_two_sessions_serialize_revocation_and_hit_publication(ticket_database):
    settings, _ = ticket_database
    db = Database(settings)
    journal = Path("artifacts/m4_e/retained_cache_race.jsonl")
    journal.parent.mkdir(parents=True, exist_ok=True)
    catalog = Catalog(db, allow_synthetic=True)
    article = None
    try:
        with db.transaction() as session:
            customer_row = synthetic_user(session, "Synthetic cache owner")
            owner, uid = snapshot(customer_row), customer_row.firebase_uid
            agent = snapshot(synthetic_user(session, "Synthetic cache agent", UserRole.AGENT))
        tickets = TicketService(db)
        first = tickets.create(owner, TicketCreate(subject="Synthetic " + SOURCE["subject"], description=SOURCE["description"]))
        second = tickets.create(owner, TicketCreate(subject="Synthetic " + TARGET["subject"], description=TARGET["description"]))
        with journal.open("a", encoding="utf-8") as out:
            out.write(json.dumps({"source": str(first.id), "target": str(second.id), "environment": "test"}) + "\n")
        classifications = ClassificationService(db)
        classify(classifications, first.id)
        classify(classifications, second.id)
        lexical = LocalEmbedder(False)
        retrieval = RetrievalService(db, lexical, allow_synthetic=True)
        cache = ResponseCache(db, LocalEmbedder(), enabled=True)
        workflow = Workflow(db, classifications, retrieval, synthetic_customer_uid=uid)
        provider = SimpleNamespace(model="gemini-3.1-flash-lite", available=True, generate=lambda _: ProviderResult(
            output='{"disposition":"draft","sentence_ids":["e1s1"]}'))
        source = DraftService(db, provider, synthetic_ticket=first.id, workflow=workflow, cache=cache, daily_limit=20)
        target = DraftService(db, provider, synthetic_ticket=second.id, workflow=workflow, cache=cache, daily_limit=0)
        created = catalog.create(agent, ArticleInput(title="Fictional playback stops or pauses", body=BODY,
            source_name="Synthetic concurrency fixture", source_type="SYNTHETIC_TEST", rights_reference="Original test fixture."))
        article = catalog.transition(agent, created.id, LifecycleInput(expected_generation=1, operation="PUBLISH"))
        assert Indexer(db, lexical, allow_synthetic=True).process_once()
        a = source.enqueue(agent, first.id, GenerateDraft(request_id=uuid4(), expected_run_id=None))
        assert DraftProcessor(source, retrieval).process_once()
        approved = source.review(agent, first.id, a.id, ReviewDraft(request_id=uuid4(), expected_version=1, action="APPROVE"))
        entry = cache.approve(source, agent, first.id, a.id, ReuseApproval(request_id=uuid4(),
            expected_draft_version=approved.version, confirm_general_reuse=True))
        b = target.enqueue(agent, second.id, GenerateDraft(request_id=uuid4(), expected_run_id=None))
        lease = target.claim()
        assert lease.run_id == b.id
        actor = lease_actor(lease)
        processor = DraftProcessor(target, retrieval)
        context, unsafe = processor.context(lease, actor)
        assert not unsafe
        evidence = retrieval.search(actor, scrub(context), RetrievalOptions(source_type=SourceType.SYNTHETIC_TEST), ticket_id=second.id).items
        lookup = cache.lookup(target, actor, lease, evidence)
        assert lookup.candidate is not None
        barrier = Barrier(2, timeout=30)
        revoke = ReuseRevocation(request_id=uuid4(), expected_version=entry.version)

        def revoke_entry():
            barrier.wait()
            return cache.revoke(agent, first.id, a.id, entry.id, revoke)

        def publish_hit():
            barrier.wait()
            return target.complete(lease, "DRAFT", draft=lookup.candidate.text, evidence=lookup.evidence, cache_match=lookup)

        with ThreadPoolExecutor(max_workers=2) as pool:
            revoking, using = pool.submit(revoke_entry), pool.submit(publish_hit)
            status, won_before_revocation = revoking.result(), using.result()
        assert status.status == "INVALIDATED" and cache.revoke(agent, first.id, a.id, entry.id, revoke) == status
        if won_before_revocation:
            with pytest.raises(Exception, match="Reuse approval"):
                target.review(agent, second.id, b.id, ReviewDraft(request_id=uuid4(), expected_version=1, action="APPROVE"))
        else:
            assert target.complete(lease, "FAILED", "PROVIDER_DISABLED")
        with db.transaction(readonly=True) as session:
            assert session.scalar(select(ResponseCacheEntry.hit_count).where(ResponseCacheEntry.id == entry.id)) == int(won_before_revocation)
    finally:
        try:
            if article is not None:
                catalog.transition(agent, article.id, LifecycleInput(expected_generation=article.generation, operation="UNPUBLISH"))
        finally:
            db.close()
