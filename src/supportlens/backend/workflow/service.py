"""Resolve and audit workflow policy using the existing ticket and ML stores."""

from dataclasses import dataclass
import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from ..api.errors import APIError
from ..db.models import TicketEvent, TicketMessage, User
from ..domain.enums import Intent, TicketEventType, UserRole, Visibility
from ..drafts.grounding import context_policy, fingerprint, scrub, sentences
from ..knowledge.retrieval import ticket_query
from ..knowledge.schemas import RetrievalOptions, SourceType
from ..tickets.schemas import TicketAction
from ..tickets.service import TicketRepository
from .policy import VERSION, Decision, DecisionCode, decide


class WorkflowStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket_id: UUID
    decision: DecisionCode
    reason: str
    explanation: str
    risk: Literal["STANDARD", "ELEVATED", "UNASSESSED"]
    intent: Intent | None
    classification_run_id: UUID | None
    evidence_count: int | None
    requires_agent_review: Literal[True] = True


@dataclass(frozen=True)
class Snapshot:
    decision: Decision
    classification_id: UUID | None
    intent: Intent | None
    context_hash: str
    query: str

    def metadata(self, decision=None):
        decision = decision or self.decision
        return {
            "version": VERSION, "decision": decision.code.value, "reason": decision.reason, "risk": decision.risk,
            "classification_run_id": str(self.classification_id) if self.classification_id else None,
            "intent": self.intent.value if self.intent else None, "context_hash": self.context_hash,
        }

    def matches(self, previous):
        return self.decision.eligible and self.metadata() == previous


class Workflow:
    def __init__(self, database, classifications, retrieval, *, threshold=0.7, synthetic_customer_uid=None):
        self.database, self.classifications, self.retrieval = database, classifications, retrieval
        self.threshold = threshold
        self.synthetic_customer_uid = synthetic_customer_uid

    def inspect(self, repo, ticket):
        session = repo.session
        public = select(TicketMessage).where(TicketMessage.ticket_id == ticket.id,
                                            TicketMessage.visibility == Visibility.PUBLIC)
        replies = list(session.scalars(public.order_by(TicketMessage.created_at.desc(), TicketMessage.id.desc()).limit(2)))
        follow_up = session.scalar(public.where(TicketMessage.author_id == ticket.customer_id)
                                   .with_only_columns(TicketMessage.id).limit(1)) is not None
        classification, invalid = None, False
        try:
            classification = self.classifications.current(session, ticket.id)
        except APIError as error:
            if error.code != "classification_unavailable":
                raise
            invalid = True
        texts = (ticket.subject, ticket.description, *(reply.body for reply in replies))
        content_risk = context_policy("\n".join(texts))
        policy = decide(status=ticket.status, priority=ticket.priority, content_risk=content_risk,
                        customer_follow_up=follow_up, classification=classification, threshold=self.threshold,
                        invalid_classification=invalid)
        digest = fingerprint(json.dumps([ticket.subject, ticket.description, ticket.status.value, ticket.priority.value,
                                         [str(reply.id) for reply in replies]]))
        return Snapshot(policy, classification.id if classification else None,
                        classification.predicted_intent if classification else None, digest,
                        scrub(ticket_query(ticket.subject, ticket.description, [reply.body for reply in replies]))[:1800])

    def status(self, actor, ticket_id, drafts):
        with self.database.transaction(readonly=True) as session:
            repo = TicketRepository(session, actor, UserRole.AGENT)
            snapshot = self.inspect(repo, repo.ticket(ticket_id))
            capacity_block = None
            if snapshot.decision.eligible and drafts.enabled(ticket_id):
                capacity_block = drafts.budget_block(session) or drafts.queue_block(session)
        decision, evidence_count = snapshot.decision, None
        if decision.eligible:
            synthetic = ticket_id == drafts.synthetic_ticket
            options = RetrievalOptions(top_k=4, source_type=SourceType.SYNTHETIC_TEST if synthetic else None)
            retrieved = self.retrieval.search(actor, snapshot.query, options, ticket_id=ticket_id)
            usable = sentences([item for item in retrieved.items
                                if (item.source_type == SourceType.SYNTHETIC_TEST) == synthetic])
            evidence_count = len({item.chunk_id for _, item in usable.values()})
            if not evidence_count:
                decision = Decision(DecisionCode.INSUFFICIENT_EVIDENCE, "NO_SAFE_EVIDENCE")
            elif not drafts.enabled(ticket_id) or capacity_block:
                decision = Decision(DecisionCode.GENERATION_UNAVAILABLE, capacity_block or "PROVIDER_DISABLED")
        return WorkflowStatus(ticket_id=ticket_id, decision=decision.code, reason=decision.reason,
                              explanation=decision.message, risk=decision.risk, intent=snapshot.intent,
                              classification_run_id=snapshot.classification_id, evidence_count=evidence_count)

    def synthetic_send_allowed(self, repo, ticket):
        if not self.synthetic_customer_uid:
            return False
        return repo.session.scalar(select(User.id).where(User.id == ticket.customer_id,
            User.firebase_uid == self.synthetic_customer_uid, User.role == UserRole.CUSTOMER,
            User.is_active.is_(True)).with_for_update(read=True)) == ticket.customer_id

    @staticmethod
    def audit(repo, ticket, snapshot, decision=None):
        policy = snapshot.metadata(decision)
        # Collapse repeated observations of the same decision while holding the ticket lock.
        latest = repo.session.scalar(select(TicketEvent.event_metadata).where(TicketEvent.ticket_id == ticket.id,
            TicketEvent.event_type == TicketEventType.AI_DECISION_RECORDED,
            TicketEvent.event_metadata["action"].astext == TicketAction.WORKFLOW_POLICY_EVALUATED.value)
            .order_by(TicketEvent.created_at.desc(), TicketEvent.id.desc()).limit(1))
        if not latest or latest.get("policy") != policy:
            repo.event(ticket, TicketEventType.AI_DECISION_RECORDED, TicketAction.WORKFLOW_POLICY_EVALUATED,
                       repo.now(), policy=policy)
