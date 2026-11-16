"""Acquire authorized context and eligible evidence before using a reserved provider call."""

from time import sleep

from sqlalchemy import select

from ..api.errors import APIError
from ..db.models import TicketMessage
from ..domain.enums import UserRole, Visibility
from ..knowledge.retrieval import ticket_query
from ..knowledge.schemas import RetrievalOptions, SourceType
from ..tickets.service import TicketRepository
from .grounding import context_policy, fingerprint, InvalidGrounding, prompt, scrub, sentences, validate_selection
from .service import MAX_ATTEMPTS, lease_actor


class DraftProcessor:
    def __init__(self, service, retrieval, *, backoff=sleep):
        self.service = service
        self.retrieval = retrieval
        self.backoff = backoff

    def context(self, lease, actor):
        with self.service.database.transaction(readonly=True) as session:
            repo = TicketRepository(session, actor, UserRole.AGENT)
            ticket = repo.ticket(lease.ticket_id)
            replies = list(session.scalars(select(TicketMessage.body).where(
                TicketMessage.ticket_id == ticket.id, TicketMessage.visibility == Visibility.PUBLIC,
            ).order_by(TicketMessage.created_at.desc(), TicketMessage.id.desc()).limit(2)))
            if lease.synthetic and not ticket.subject.casefold().startswith(("synthetic", "m4 synthetic", "m4-c synthetic")):
                return "", "SYNTHETIC_SCOPE"
            raw = "\n".join([ticket.subject, ticket.description, *replies])
            return ticket_query(ticket.subject, ticket.description, replies), context_policy(raw)

    def process_once(self):
        lease = self.service.claim()
        if lease is None:
            return False
        if lease is True:
            return True
        try:
            self.process(lease)
        except APIError as error:
            if error.status not in (403, 404):
                raise
            self.service.complete(lease, "ABSTAINED", "AUTHORIZATION_CHANGED")
        return True

    def process(self, lease):
        actor = lease_actor(lease)
        context, unsafe = self.context(lease, actor)
        if unsafe:
            self.service.complete(lease, "ABSTAINED", unsafe)
            return
        reduced = scrub(context)[:1800]
        digest = fingerprint(reduced.casefold())
        options = RetrievalOptions(top_k=4, source_type=SourceType.SYNTHETIC_TEST if lease.synthetic else None)
        result = self.retrieval.search(actor, reduced, options, ticket_id=lease.ticket_id)
        evidence = [item for item in result.items
                    if (item.source_type == SourceType.SYNTHETIC_TEST) == lease.synthetic]
        catalog = sentences(evidence)
        provenance = {"retrieval_method": result.method.value, "issue_hash": digest}
        if not catalog:
            self.service.complete(lease, "ABSTAINED", "INSUFFICIENT_EVIDENCE", **provenance)
            return
        for attempt in range(MAX_ATTEMPTS):
            denial = self.service.reserve(lease, evidence)
            if denial:
                if denial != "LEASE_LOST":
                    state = "ABSTAINED" if denial == "EVIDENCE_CHANGED" else "FAILED"
                    self.service.complete(lease, state, denial, **provenance)
                return
            response = self.service.provider.generate(prompt(reduced, catalog))
            if not self.service.record_usage(lease, response):
                return
            if response.category == "PROVIDER_TEMPORARY" and attempt + 1 < MAX_ATTEMPTS:
                self.backoff(0.5 * 2 ** attempt)
                continue
            if response.category:
                self.service.complete(lease, "FAILED", response.category, **provenance)
                return
            try:
                disposition, draft, citations = validate_selection(response.output, catalog)
            except InvalidGrounding:
                self.service.complete(lease, "ABSTAINED", "INVALID_GROUNDING", **provenance)
                return
            if disposition != "draft":
                self.service.complete(lease, "ABSTAINED",
                                      "MODEL_ESCALATION" if disposition == "escalate" else "INSUFFICIENT_EVIDENCE",
                                      **provenance)
                return
            self.service.complete(lease, "DRAFT", draft=draft, evidence=citations, **provenance)
            return
