"""Cache admission, explicit review, and read-time provenance enforcement."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
import json
from time import monotonic
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import update, func, select, text

from ..api.errors import APIError
from ..db.models import AIRun, TicketMessage
from ..domain.enums import TicketEventType, UserRole, Visibility
from ..drafts.grounding import fingerprint, validate_edit
from ..drafts.service import KIND, evidence_current, order, runs
from ..knowledge.embedding import EmbeddingUnavailable
from ..knowledge.schemas import Evidence
from ..tickets.schemas import TicketAction
from ..tickets.service import TicketRepository
from ..workflow.policy import VERSION as WORKFLOW_VERSION
from .matching import MAX_CANDIDATES, MIN_SIMILARITY, VERSION, Candidate, choose, classify_issue, evidence_key, general_guidance
from .models import ResponseCacheEntry as Entry, ResponseCacheEvidence as Dependency
from .schemas import CacheMetrics, CacheSummary


def scope(synthetic):
    return "SYNTHETIC" if synthetic else "GENERAL"


def stale():
    return APIError(409, "cache_stale", "Reuse approval or its evidence is no longer current. Request a fresh draft.")


@dataclass(frozen=True)
class Lookup:
    reason: str
    latency_ms: int
    rejected: int = 0
    incompatible: int = 0
    candidate: Candidate | None = None
    evidence: tuple = ()
    miss_key: str | None = None
    similarity: float | None = None

    def metadata(self, *, used=False, discarded=False):
        return {"status": "HIT" if used else "MISS", "reason": "STALE_CANDIDATE" if discarded else self.reason,
                "latency_ms": self.latency_ms, "rejected_candidates": self.rejected + int(discarded),
                "incompatible_candidates": self.incompatible}


class ResponseCache:
    def __init__(self, database, embedder, *, enabled=False, threshold=MIN_SIMILARITY, ttl_days=7):
        self.database, self.embedder = database, embedder
        self.enabled, self.threshold, self.ttl_days = enabled, threshold, ttl_days

    def compatible(self, row):
        return (row.embedding_model == self.embedder.identity.key and row.match_version == VERSION
                and row.policy_version == WORKFLOW_VERSION)

    def summary(self, row, now):
        status, reason = "ACTIVE", None
        if row.state == "INACTIVE":
            status = "EXPIRED" if row.invalidation_reason == "EXPIRED" else "INVALIDATED"
            reason = row.invalidation_reason
        elif now >= row.expires_at:
            status, reason = "EXPIRED", "EXPIRED"
        elif not self.compatible(row):
            status, reason = "INCOMPATIBLE", "VERSION_CHANGED"
        return CacheSummary(id=row.id, version=row.version, status=status, reason=reason,
                            expires_at=row.expires_at, hit_count=row.hit_count)

    def metrics(self, actor):
        with self.database.transaction(readonly=True) as session:
            repo = TicketRepository(session, actor, UserRole.AGENT)
            now = repo.now()
            day = now.astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat()
            active = Entry.state == "ACTIVE"
            compatible = self._compatible_query()
            entries = session.execute(select(
                func.count().label("entries"),
                func.count().filter(active, Entry.expires_at > now, compatible).label("active_entries"),
                func.count().filter(Entry.state == "INACTIVE").label("invalidations"),
                func.count().filter(Entry.expires_at <= now).label("expired_entries"),
                func.count().filter(active, Entry.expires_at > now, ~compatible).label("incompatible_entries"),
            ).select_from(Entry)).mappings().one()
            lookups = session.execute(text("""
                SELECT count(*) AS lookups, count(*) FILTER (WHERE item->>'status'='HIT') AS hits,
                       count(*) FILTER (WHERE item->>'status'='MISS') AS misses,
                       coalesce(sum((item->>'rejected_candidates')::integer),0) AS rejected_candidates,
                       coalesce(sum((item->>'incompatible_candidates')::integer),0) AS incompatible_candidates,
                       avg((item->>'latency_ms')::integer) AS average_lookup_ms
                FROM (SELECT score_metadata->'cache_lookup' AS item FROM ai_runs
                      WHERE score_metadata->>'kind'=:kind AND score_metadata ? 'cache_lookup') lookups
            """), {"kind": KIND}).mappings().one()
            usage = session.execute(text("""
                SELECT count(*) FILTER (WHERE item->>'external_attempt'='true') AS gemini_requests_attempted,
                       count(*) FILTER (WHERE item->>'external_response'='true') AS gemini_responses_observed,
                       count(*) FILTER (WHERE item->>'day'=:day) AS pacific_day_reservations
                FROM ai_runs CROSS JOIN LATERAL jsonb_array_elements(
                    CASE WHEN score_metadata->>'kind'=:kind THEN score_metadata->'attempts' ELSE '[]'::jsonb END
                ) attempts(item)
            """), {"kind": KIND, "day": day}).mappings().one()
            return CacheMetrics(**entries, **lookups, **usage, gemini_generation_bypasses=lookups["hits"])

    def candidates_exist(self, repo, ticket, drafts, snapshot):
        issue = self._issue(repo, ticket, drafts, snapshot)
        if issue is None:
            return False
        return repo.session.scalar(self._query(issue, snapshot.intent, ticket.id == drafts.synthetic_ticket, repo.now())
                                   .where(self._compatible_query()).with_only_columns(Entry.id).limit(1)) is not None

    @staticmethod
    def _issue(repo, ticket, drafts, snapshot, *, ignored=None):
        if not snapshot.decision.eligible or snapshot.decision.risk != "STANDARD" or not drafts.in_scope(ticket.id):
            return None
        if ticket.id == drafts.synthetic_ticket and not drafts.workflow.synthetic_send_allowed(repo, ticket):
            return None
        public = select(TicketMessage.id).where(TicketMessage.ticket_id == ticket.id, TicketMessage.visibility == Visibility.PUBLIC)
        if ignored is not None:
            public = public.where(TicketMessage.id != ignored)
        return classify_issue(ticket.subject, ticket.description, snapshot.intent,
                              public_context=repo.session.scalar(public.limit(1)) is not None)

    @staticmethod
    def _query(issue, intent, synthetic, now):
        return select(Entry).where(Entry.scope == scope(synthetic), Entry.risk == "STANDARD", Entry.intent == intent.value,
                                   Entry.issue_key == issue.key, Entry.state == "ACTIVE", Entry.expires_at > now)

    def _compatible_query(self):
        return ((Entry.policy_version == WORKFLOW_VERSION) & (Entry.match_version == VERSION)
                & (Entry.embedding_model == self.embedder.identity.key))

    def lookup(self, drafts, actor, lease, evidence):
        start = monotonic()
        elapsed = lambda: round((monotonic() - start) * 1000)
        evidence_map = {evidence_key(item): item for item in evidence}
        with self.database.transaction(readonly=True) as session:
            repo = TicketRepository(session, actor, UserRole.AGENT)
            ticket = repo.ticket(lease.ticket_id)
            snapshot = drafts.workflow.inspect(repo, ticket)
            current = session.scalar(runs(ticket.id).where(AIRun.id == lease.run_id))
            if current is None or not snapshot.matches(current.score_metadata.get("workflow")):
                return Lookup("POLICY_CHANGED", elapsed())
            issue = self._issue(repo, ticket, drafts, snapshot)
            if issue is None:
                return Lookup("UNSUPPORTED_CONTEXT", elapsed())
            query = self._query(issue, snapshot.intent, lease.synthetic, repo.now())
            shared_key = fingerprint(json.dumps([scope(lease.synthetic), VERSION, WORKFLOW_VERSION, snapshot.intent.value,
                                                  issue.key, sorted(evidence_map)]))
            compatible = self._compatible_query()
            incompatible = session.scalar(select(func.count()).select_from(query.where(~compatible).subquery()))
            entries = list(session.scalars(query.where(compatible).order_by(Entry.id).limit(MAX_CANDIDATES + 1)))
            if not entries or len(entries) > MAX_CANDIDATES:
                return Lookup("NO_CANDIDATES" if not entries else "CAPACITY", elapsed(), incompatible=incompatible,
                              miss_key=shared_key)
            dependencies = defaultdict(list)
            for link in session.scalars(select(Dependency).where(Dependency.cache_id.in_([row.id for row in entries]))):
                dependencies[link.cache_id].append(evidence_key(link))
            candidates = [Candidate(row.id, row.version, row.vector, row.content, tuple(dependencies[row.id])) for row in entries]
        try:
            vector = self.embedder.encode([issue.phrase])[0]
        except EmbeddingUnavailable:
            return Lookup("EMBEDDING_UNAVAILABLE", elapsed(), incompatible=incompatible, miss_key=shared_key)
        match, reason, rejected = choose(vector, candidates, evidence_map, threshold=self.threshold, synthetic=lease.synthetic)
        if match is None:
            return Lookup(reason, elapsed(), rejected, incompatible, miss_key=shared_key)
        score, candidate, cited = match
        return Lookup(reason, elapsed(), rejected, incompatible, candidate, tuple(cited), shared_key, score)

    def recheck(self, repo, ticket, drafts, entry_id, version, content, evidence, similarity, *, usage=False):
        """Called after taking the catalog's shared evidence locks in the owning transaction."""
        if not self.enabled or type(similarity) not in {int, float} or not self.threshold <= similarity <= 1:
            return None
        snapshot = drafts.workflow.inspect(repo, ticket)
        issue = self._issue(repo, ticket, drafts, snapshot)
        if issue is None:
            return None
        synthetic = ticket.id == drafts.synthetic_ticket
        row = repo.session.scalar(select(Entry).where(Entry.id == UUID(str(entry_id))).with_for_update(read=not usage))
        if (row is None or row.version != version or self.summary(row, repo.now()).status != "ACTIVE"
                or row.scope != scope(synthetic) or row.intent != snapshot.intent.value or row.issue_key != issue.key
                or row.content != content or not general_guidance(content, evidence, synthetic)):
            return None
        dependencies = repo.session.scalars(select(Dependency).where(Dependency.cache_id == row.id))
        if {evidence_key(link) for link in dependencies} != {evidence_key(item) for item in evidence}:
            return None
        if usage:
            row.hit_count += 1
            row.last_hit_at = repo.now()
        return row

    def _source(self, repo, ticket, drafts, run, version):
        meta = run.score_metadata
        if (meta.get("decision") != "APPROVED" or meta.get("version") != version
                or meta.get("validation") != "SUPPORTED_EXTRACTIVE" or not meta.get("reviewed_by")
                or meta.get("response_provenance") != "GEMINI_EXTRACTIVE_SELECTION"
                or repo.session.scalar(order(runs(ticket.id)).with_only_columns(AIRun.id).limit(1)) != run.id
                or meta.get("synthetic") != (ticket.id == drafts.synthetic_ticket)):
            raise APIError(409, "cache_not_eligible", "Reuse requires the current reviewed Gemini draft.")
        ignored = UUID(meta["send"]["message_id"]) if meta.get("send") else None
        snapshot = drafts.workflow.inspect(repo, ticket, ignored_public_id=ignored)
        issue = self._issue(repo, ticket, drafts, snapshot, ignored=ignored)
        evidence = [Evidence.model_validate(item) for item in meta["evidence"]]
        if (issue is None or not snapshot.matches(meta.get("workflow"))
                or not meta.get("original") or not general_guidance(meta.get("text"), evidence, meta["synthetic"])
                or not validate_edit(meta["text"], meta["original"])):
            raise APIError(409, "cache_not_eligible", "The issue or guidance is outside the general reuse policy.")
        return issue, snapshot, evidence

    def approve(self, drafts, actor, ticket_id, run_id, payload):
        if not self.enabled or drafts.workflow is None:
            raise APIError(503, "cache_disabled", "Response reuse is disabled.")
        with self.database.transaction(readonly=True) as session:
            repo = TicketRepository(session, actor, UserRole.AGENT)
            ticket, run = repo.ticket(ticket_id), self._run(session, ticket_id, run_id)
            replay = self._approval_replay(repo, run_id, payload)
            if replay is not None:
                return replay
            issue, _, _ = self._source(repo, ticket, drafts, run, payload.expected_draft_version)
        try:
            vector = self.embedder.encode([issue.phrase])[0]
        except EmbeddingUnavailable:
            raise APIError(503, "cache_embedding_unavailable", "Local embeddings are unavailable.") from None
        with self.database.transaction() as session:
            repo = TicketRepository(session, actor, UserRole.AGENT, write=True)
            ticket = repo.ticket(ticket_id)
            run = self._run(session, ticket_id, run_id, lock=True)
            # The ticket lock covers concurrent approvals of this source. The advisory lock covers cross-ticket duplicates.
            replay = self._approval_replay(repo, run_id, payload)
            if replay is not None:
                return replay
            current, snapshot, evidence = self._source(repo, ticket, drafts, run, payload.expected_draft_version)
            synthetic = run.score_metadata["synthetic"]
            if current != issue or not evidence_current(session, evidence, synthetic):
                raise stale()
            content = run.score_metadata["text"]
            duplicate = fingerprint(json.dumps([scope(synthetic), VERSION, WORKFLOW_VERSION, self.embedder.identity.key,
                                                issue.key, snapshot.intent.value,
                                                content, sorted(evidence_key(item) for item in evidence)]))
            for lock in sorted((f"cache-guidance:{duplicate}", f"cache-approval:{payload.request_id}")):
                session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(lock, 0))))
            if session.scalar(select(Entry.id).where(Entry.approval_request_id == payload.request_id)) is not None:
                raise APIError(409, "cache_request_conflict", "This reuse request has already been used.")
            now = repo.now()
            session.execute(update(Entry).where(Entry.duplicate_key == duplicate, Entry.state == "ACTIVE", Entry.expires_at <= now)
                            .values(state="INACTIVE", version=Entry.version + 1, invalidated_at=now, invalidation_reason="EXPIRED"))
            if session.scalar(select(Entry.id).where(Entry.duplicate_key == duplicate, Entry.state == "ACTIVE")) is not None:
                raise APIError(409, "cache_duplicate", "This general guidance already has an active reuse approval.")
            row = Entry(source_run_id=run_id, approved_by=repo.actor.id, approval_request_id=payload.request_id,
                        source_draft_version=payload.expected_draft_version, issue_key=issue.key, canonical_issue=issue.phrase,
                        intent=snapshot.intent.value, risk="STANDARD", scope=scope(synthetic), policy_version=WORKFLOW_VERSION,
                        match_version=VERSION, embedding_model=self.embedder.identity.key, vector=vector, content=content,
                        duplicate_key=duplicate, state="ACTIVE", version=1, hit_count=0, created_at=now,
                        expires_at=now + timedelta(days=self.ttl_days))
            session.add(row)
            session.flush()
            session.add_all(Dependency(cache_id=row.id, chunk_id=item.chunk_id, article_id=item.article_id,
                                       revision_id=item.revision_id, content_hash=item.content_hash) for item in evidence)
            run.score_metadata = run.score_metadata | {"cache_eligible": True, "cache_approval_id": str(row.id)}
            repo.event(ticket, TicketEventType.AI_DECISION_RECORDED, TicketAction.CACHE_REUSE_APPROVED, now,
                       cache_id=str(row.id), draft_run_id=str(run_id), draft_version=payload.expected_draft_version)
            return self.summary(row, now)

    def _approval_replay(self, repo, run_id, payload):
        row = repo.session.scalar(select(Entry).where(Entry.source_run_id == run_id))
        if row is None:
            return None
        if (row.approved_by != repo.actor.id or row.approval_request_id != payload.request_id
                or row.source_draft_version != payload.expected_draft_version):
            raise APIError(409, "cache_already_reviewed", "This draft already has a reuse review. Use a fresh draft for new approval.")
        return self.summary(row, repo.now())

    @staticmethod
    def _run(session, ticket_id, run_id, *, lock=False):
        query = runs(ticket_id).where(AIRun.id == run_id)
        row = session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise APIError(404, "draft_not_found", "The draft was not found.")
        return row

    def revoke(self, actor, ticket_id, run_id, cache_id, payload):
        with self.database.transaction() as session:
            repo = TicketRepository(session, actor, UserRole.AGENT, write=True)
            ticket = repo.ticket(ticket_id)
            run = self._run(session, ticket_id, run_id, lock=True)
            row = session.scalar(select(Entry).where(Entry.id == cache_id, Entry.source_run_id == run_id).with_for_update())
            if row is None:
                raise APIError(404, "cache_not_found", "The reuse approval was not found.")
            if (row.revocation_request_id == payload.request_id and row.revoked_by == repo.actor.id
                    and row.version == payload.expected_version + 1):
                return self.summary(row, repo.now())
            if row.state != "ACTIVE" or row.version != payload.expected_version:
                raise stale()
            session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"cache-revoke:{payload.request_id}", 0))))
            if session.scalar(select(Entry.id).where(Entry.revocation_request_id == payload.request_id)) is not None:
                raise APIError(409, "cache_request_conflict", "This reuse request has already been used.")
            now = repo.now()
            row.state, row.version, row.invalidated_at, row.invalidation_reason = "INACTIVE", row.version + 1, now, "REVOKED"
            row.revoked_by, row.revocation_request_id = repo.actor.id, payload.request_id
            run.score_metadata = run.score_metadata | {"cache_eligible": False}
            repo.event(ticket, TicketEventType.AI_DECISION_RECORDED, TicketAction.CACHE_REUSE_REVOKED, now,
                       cache_id=str(row.id), draft_run_id=str(run_id))
            return self.summary(row, now)
