"""Durable AI runs, strict agent review, and reservations before every remote call."""

from dataclasses import dataclass
from datetime import timedelta
from secrets import token_hex
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select, text

from ..api.errors import APIError
from ..db.models import AIRun, KnowledgeCitation, TicketEvent
from ..domain.enums import AIRunStatus, TicketEventType, UserRole
from ..knowledge.models import KnowledgeChunk, KnowledgeEntry
from ..knowledge.retrieval import evidence_rows
from ..knowledge.schemas import Evidence, RetrievalOptions
from ..tickets.service import TicketRepository
from .grounding import PROMPT_VERSION, fingerprint, validate_edit
from .schemas import DraftList, DraftRun

KIND = "supportlens.grounded-draft.v1"
ACTIVE = (AIRunStatus.PENDING, AIRunStatus.RUNNING)
MAX_ATTEMPTS = 2
PAGE_SIZE = 10


@dataclass(frozen=True)
class Lease:
    run_id: UUID
    ticket_id: UUID
    actor_id: UUID
    token: str
    synthetic: bool
    model: str


def runs(ticket_id=None):
    query = select(AIRun).where(AIRun.score_metadata["kind"].astext == KIND)
    return query.where(AIRun.ticket_id == ticket_id) if ticket_id is not None else query


def order(query):
    return query.order_by(AIRun.score_metadata["sequence"].as_integer().desc(), AIRun.id)


def view(row):
    meta = row.score_metadata
    attempts = meta["attempts"]
    usage = {}
    for attempt in attempts:
        for key, value in attempt.get("usage", {}).items():
            usage[key] = usage.get(key, 0) + value
    return DraftRun(id=row.id, ticket_id=row.ticket_id, model=row.model_version,
                    state=meta.get("decision") or meta.get("state") or row.status.value,
                    prompt_version=meta["prompt_version"], text=meta.get("text"),
                    evidence=meta.get("evidence", []), synthetic=meta["synthetic"], validation=meta.get("validation"),
                    failure=meta.get("failure"), retrieval_method=meta.get("retrieval_method"),
                    provider_calls=len(attempts), usage=usage,
                    latency_ms=sum(attempt.get("latency_ms", 0) for attempt in attempts),
                    version=meta["version"], created_at=row.created_at, completed_at=meta.get("completed_at"),
                    reviewed_by=meta.get("reviewed_by"), reviewed_at=meta.get("reviewed_at"))


def event(session, row, actor, action, now):
    session.add(TicketEvent(ticket_id=row.ticket_id, actor_id=actor, event_type=TicketEventType.AI_DECISION_RECORDED,
                           created_at=now, event_metadata={"action": action, "draft_run_id": str(row.id),
                                                          "requires_human_review": True}))


def evidence_current(session, evidence, synthetic):
    if not evidence:
        return False
    entry_ids = sorted({item.article_id for item in evidence})
    # Writers in the knowledge catalog acquire this same entry lock before changing eligibility.
    session.execute(select(KnowledgeEntry.id).where(KnowledgeEntry.id.in_(entry_ids))
                    .order_by(KnowledgeEntry.id).with_for_update(read=True)).all()
    found = session.execute(evidence_rows(RetrievalOptions(), synthetic)
                            .where(KnowledgeChunk.id.in_([item.chunk_id for item in evidence]))).all()
    actual = {(item.chunk_id, item.article_id, item.revision_id, item.revision, item.content_hash, item.excerpt,
               item.source_type) for item in found}
    expected = {(item.chunk_id, item.article_id, item.revision_id, item.revision, item.content_hash, item.excerpt,
                 item.source_type) for item in evidence}
    return actual == expected and all((item.source_type == "SYNTHETIC_TEST") == synthetic for item in evidence)


class DraftService:
    def __init__(self, database, provider, *, daily_limit=8, synthetic_ticket=None, allow_reviewed=False):
        self.database, self.provider = database, provider
        self.daily_limit = daily_limit
        self.synthetic_ticket = synthetic_ticket
        self.allow_reviewed = allow_reviewed

    def enabled(self, ticket_id):
        return self.provider.available and self.daily_limit > 0 and (
            ticket_id == self.synthetic_ticket or (self.allow_reviewed and self.synthetic_ticket is None)
        )

    def listing(self, actor, ticket_id, page=1):
        with self.database.transaction(readonly=True) as session:
            repository = TicketRepository(session, actor, UserRole.AGENT)
            repository.ticket(ticket_id)
            query = runs(ticket_id)
            total = session.scalar(select(func.count()).select_from(query.subquery()))
            rows = list(session.scalars(order(query).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)))
            return DraftList(ticket_id=ticket_id, generation_enabled=self.enabled(ticket_id),
                             total=total, page=page, items=[view(row) for row in rows])

    def enqueue(self, actor, ticket_id, payload):
        with self.database.transaction() as session:
            repo = TicketRepository(session, actor, UserRole.AGENT, write=True)
            ticket = repo.ticket(ticket_id)
            key = str(payload.request_id)
            query = runs(ticket_id)
            replay = session.scalar(query.where(AIRun.score_metadata["request_id"].astext == key))
            if replay is not None:
                previous = str(payload.expected_run_id) if payload.expected_run_id else None
                if replay.score_metadata["requester"] != str(actor.id) or replay.score_metadata["parent"] != previous:
                    raise APIError(409, "draft_changed", "The draft request key is already in use.")
                return view(replay)
            latest = session.scalar(order(query).limit(1))
            if latest is not None and latest.status in ACTIVE:
                raise APIError(409, "draft_active", "A draft is already in progress.")
            if (latest.id if latest else None) != payload.expected_run_id:
                raise APIError(409, "draft_changed", "Refresh the draft history before requesting another run.")
            if not self.enabled(ticket.id):
                raise APIError(503, "draft_disabled", "Gemini drafting is unavailable for this ticket.")
            now = repo.now()
            if latest is not None and now < latest.created_at + timedelta(seconds=15):
                raise APIError(429, "draft_rate_limited", "Wait before requesting another draft.")
            # Serializes a single agent's rate cap across tickets.
            session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"draft-agent:{actor.id}", 0))))
            count = session.scalar(select(func.count()).select_from(runs().where(
                AIRun.score_metadata["requester"].astext == str(actor.id),
                AIRun.created_at >= now - timedelta(minutes=1),
            ).subquery()))
            if count >= 3:
                raise APIError(429, "draft_rate_limited", "The agent draft request limit was reached.")
            row = AIRun(id=uuid4(), ticket_id=ticket_id, created_at=now, model_version=self.provider.model,
                        status=AIRunStatus.PENDING, requires_human_review=True, score_metadata={
                "kind": KIND, "prompt_version": PROMPT_VERSION, "request_id": key,
                "requester": str(actor.id), "parent": str(latest.id) if latest else None,
                "sequence": (latest.score_metadata["sequence"] + 1) if latest else 1,
                "synthetic": ticket_id == self.synthetic_ticket, "version": 1,
                "attempts": [], "claims": 0, "review_events": [], "cache_eligible": False,
            })
            session.add(row)
            event(session, row, actor.id, "DRAFT_REQUESTED", now)
            session.flush()
            return view(row)

    def claim(self):
        with self.database.transaction() as session:
            now = session.scalar(select(func.clock_timestamp()))
            # ISO timestamps are written in UTC by the same database clock.
            cutoff = now.isoformat()
            row = session.scalar(runs().where(or_(
                AIRun.status == AIRunStatus.PENDING,
                and_(AIRun.status == AIRunStatus.RUNNING, AIRun.score_metadata["lease_until"].astext <= cutoff),
            )).order_by(AIRun.created_at, AIRun.id).limit(1).with_for_update(skip_locked=True))
            if row is None:
                return None
            meta = row.score_metadata
            if (meta["claims"] >= 3
                    or any(item.get("outcome") != "PROVIDER_TEMPORARY" for item in meta["attempts"])):
                self._finish(session, row, now, "FAILED", "INTERRUPTED", {})
                return True
            token = token_hex(24)
            row.score_metadata = meta | {"lease": token, "lease_until": (now + timedelta(minutes=5)).isoformat(),
                                         "claims": meta["claims"] + 1}
            row.status = AIRunStatus.RUNNING
            return Lease(row.id, row.ticket_id, UUID(meta["requester"]), token, meta["synthetic"], row.model_version)

    @staticmethod
    def _owned(session, lease):
        row = session.scalar(runs(lease.ticket_id).where(AIRun.id == lease.run_id).with_for_update())
        if row is None or row.status != AIRunStatus.RUNNING or row.score_metadata.get("lease") != lease.token:
            return None
        return row

    def reserve(self, lease, evidence):
        with self.database.transaction() as session:
            session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended("supportlens-gemini-budget", 0))))
            row = self._owned(session, lease)
            if row is None:
                return "LEASE_LOST"
            if (not self.enabled(lease.ticket_id) or lease.model != self.provider.model
                    or lease.synthetic != (lease.ticket_id == self.synthetic_ticket)):
                return "PROVIDER_DISABLED"
            TicketRepository(session, lease_actor(lease), UserRole.AGENT, write=True)
            if not evidence_current(session, evidence, lease.synthetic):
                return "EVIDENCE_CHANGED"
            now = session.scalar(select(func.clock_timestamp()))
            pacific_day = now.astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat()
            count, quota = session.execute(text("""
                SELECT count(*), coalesce(bool_or(item->>'outcome' = 'PROVIDER_QUOTA'), false)
                FROM ai_runs
                CROSS JOIN LATERAL jsonb_array_elements(
                    CASE WHEN score_metadata->>'kind' = :kind THEN score_metadata->'attempts' ELSE '[]'::jsonb END
                ) AS attempts(item)
                WHERE score_metadata->>'kind' = :kind AND item->>'day' = :day
            """), {"kind": KIND, "day": pacific_day}).one()
            attempts = row.score_metadata["attempts"]
            if quota:
                return "PROVIDER_QUOTA"
            if len(attempts) >= MAX_ATTEMPTS:
                return "RETRY_EXHAUSTED"
            if count >= self.daily_limit:
                return "DAILY_LIMIT"
            row.score_metadata = row.score_metadata | {"attempts": [
                *attempts, {"day": pacific_day, "at": now.isoformat()},
            ]}
            return None

    def record_usage(self, lease, result):
        with self.database.transaction() as session:
            row = self._owned(session, lease)
            if row is None:
                return False
            attempts = row.score_metadata["attempts"]
            last = attempts[-1] | {"outcome": result.category or "RECEIVED", "usage": result.usage or {},
                                   "latency_ms": result.latency_ms}
            row.score_metadata = row.score_metadata | {"attempts": [*attempts[:-1], last]}
            return True

    @staticmethod
    def _finish(session, row, now, state, failure, values):
        meta = {key: value for key, value in row.score_metadata.items() if key not in ("lease", "lease_until")}
        row.score_metadata = meta | values | {"state": state, "failure": failure, "completed_at": now.isoformat()}
        row.status = AIRunStatus.SUCCEEDED if state == "DRAFT" else AIRunStatus.FAILED
        event(session, row, None, "DRAFT_COMPLETED", now)

    def complete(self, lease, state, failure=None, *, draft=None, evidence=(), retrieval_method=None, issue_hash=None):
        with self.database.transaction() as session:
            row = self._owned(session, lease)
            if row is None:
                return False
            if state == "DRAFT":
                if not evidence_current(session, evidence, lease.synthetic):
                    state, failure, draft, evidence = "ABSTAINED", "EVIDENCE_CHANGED", None, ()
                else:
                    for item in evidence:
                        session.add(KnowledgeCitation(ai_run_id=row.id, knowledge_article_id=item.revision_id,
                            article_version=item.revision, citation_metadata={
                                "article_id": str(item.article_id), "chunk_id": str(item.chunk_id),
                                "content_hash": item.content_hash, "rank": item.rank,
                            }))
            now = session.scalar(select(func.clock_timestamp()))
            self._finish(session, row, now, state, failure, {
                "text": draft, "original": draft, "evidence": [item.model_dump(mode="json") for item in evidence],
                "retrieval_method": retrieval_method, "issue_fingerprint": issue_hash,
                "validation": "SUPPORTED_EXTRACTIVE" if state == "DRAFT" else state,
                "safety_classification": "SYNTHETIC" if lease.synthetic else ("GENERAL_REVIEW" if state == "DRAFT" else "MANUAL"),
                "embedding_model": next((item.embedding_model for item in evidence if item.embedding_model), None),
                "response_provenance": "GEMINI_EXTRACTIVE_SELECTION" if state == "DRAFT" else None,
            })
            return True

    def review(self, actor, ticket_id, run_id, payload):
        with self.database.transaction() as session:
            repo = TicketRepository(session, actor, UserRole.AGENT, write=True)
            repo.ticket(ticket_id)
            row = session.scalar(runs(ticket_id).where(AIRun.id == run_id).with_for_update())
            if row is None:
                raise APIError(404, "draft_not_found", "The draft was not found.")
            meta = row.score_metadata
            key = str(payload.request_id)
            operation_hash = fingerprint(payload.model_dump_json())
            for entry in meta["review_events"]:
                if entry["key"] == key:
                    if entry["actor"] != str(actor.id) or entry["hash"] != operation_hash:
                        raise APIError(409, "draft_changed", "The review request key is already in use.")
                    return view(row)
            latest = session.scalar(order(runs(ticket_id)).limit(1))
            if (latest.id != row.id or meta["version"] != payload.expected_version
                    or meta.get("state") != "DRAFT" or meta.get("decision")):
                raise APIError(409, "draft_changed", "This draft is no longer available for review.")
            if len(meta["review_events"]) >= 12:
                raise APIError(409, "draft_review_limit", "Request a new draft to continue reviewing.")
            if (payload.action == "EDIT") != (payload.text is not None):
                raise APIError(422, "draft_invalid_edit", "Only an edit accepts text.")
            if payload.action == "EDIT" and not validate_edit(payload.text, meta["original"]):
                raise APIError(422, "draft_invalid_edit", "Keep the opening and use only complete original evidence lines.")
            if payload.action == "APPROVE":
                evidence = [Evidence.model_validate(item) for item in meta["evidence"]]
                if not evidence_current(session, evidence, meta["synthetic"]):
                    raise APIError(409, "draft_stale_evidence", "Evidence changed. Regenerate with current sources.")
            now = repo.now()
            decision = {"APPROVE": "APPROVED", "REJECT": "REJECTED"}.get(payload.action)
            content = payload.text if payload.action == "EDIT" else meta["text"]
            history = {"actor": str(actor.id), "key": key, "hash": operation_hash, "action": payload.action,
                       "at": now.isoformat(), "text": content if payload.action == "EDIT" else None}
            row.score_metadata = meta | {"text": content, "version": meta["version"] + 1, "decision": decision,
                                         "review_events": [*meta["review_events"], history],
                                         "reviewed_by": str(actor.id), "reviewed_at": now.isoformat()}
            event(session, row, actor.id, f"DRAFT_{payload.action}", now)
            return view(row)


@dataclass(frozen=True)
class Actor:
    id: UUID


def lease_actor(lease):
    return Actor(lease.actor_id)
