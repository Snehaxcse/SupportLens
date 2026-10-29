"""Atomic ticket/audit operations; PostgreSQL row locks serialize mutations."""
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import DBAPIError, IntegrityError

from ..api.errors import APIError
from ..db.models import Ticket, TicketEvent, TicketMessage, User
from ..domain.enums import Priority, TicketEventType, TicketStatus, UserRole, Visibility
from ..domain.transitions import validate_transition
from .schemas import (
    AgentDetail, AgentEvent, AgentMessage, AgentTicket, AssignableAgent, CustomerDetail,
    CustomerTicket, Page, PageQuery, PublicAuthor, PublicEvent, PublicMessage, TicketAction,
)


def not_found():
    return APIError(404, "ticket_not_found", "The ticket was not found.")


class TicketRepository:
    def __init__(self, session, principal, required_role, *, write=False):
        self.session, self.write = session, write
        query = select(User).where(User.id == principal.id)
        if write:
            query = query.with_for_update(read=True)
        self.actor = session.scalars(query).one_or_none()
        if self.actor is None or not self.actor.is_active:
            raise APIError(403, "account_inactive", "This account is inactive.")
        if self.actor.role != required_role:
            raise APIError(403, "forbidden", "This operation is not permitted.")

    def ticket(self, identifier):
        query = select(Ticket).where(Ticket.id == identifier)
        if self.actor.role == UserRole.CUSTOMER:
            query = query.where(Ticket.customer_id == self.actor.id)
        if self.write:
            query = query.with_for_update()
        ticket = self.session.scalars(query).one_or_none()
        if ticket is None:
            raise not_found()
        return ticket

    def now(self):
        # Actual operation time, after lock acquisition, rather than transaction-start time.
        return self.session.scalar(select(func.clock_timestamp()))

    def event(self, ticket, kind, action, moment, **metadata):
        self.session.add(TicketEvent(ticket_id=ticket.id, actor_id=self.actor.id, event_type=kind,
            created_at=moment, event_metadata={"action": action.value, **metadata}))

    def summary(self, ticket):
        values = {name: getattr(ticket, name) for name in
                  ("id", "subject", "status", "priority", "created_at", "updated_at", "resolved_at")}
        if self.actor.role == UserRole.AGENT:
            return AgentTicket(**values, customer_id=ticket.customer_id, assigned_agent_id=ticket.assigned_agent_id)
        return CustomerTicket(**values)

    def paged(self, query, page, page_size, convert, *, order):
        total = self.session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
        rows = self.session.execute(query.order_by(*order).offset((page-1)*page_size).limit(page_size))
        return {"items": [convert(row) for row in rows], "total": total, "page": page, "page_size": page_size}

    def listing(self, options):
        query = select(Ticket)
        oldest = False
        if self.actor.role == UserRole.CUSTOMER:
            query = query.where(Ticket.customer_id == self.actor.id)
        else:
            for field in ("status", "priority", "assigned_agent_id"):
                value = getattr(options, field)
                if value is not None:
                    query = query.where(getattr(Ticket, field) == value)
            if options.unassigned_only:
                query = query.where(Ticket.assigned_agent_id.is_(None))
            if options.search:
                condition = Ticket.subject.icontains(options.search, autoescape=True)
                try:
                    condition = or_(condition, Ticket.id == UUID(options.search))
                except ValueError:
                    pass
                query = query.where(condition)
            oldest = options.order == "oldest"
        order = (Ticket.created_at.asc(), Ticket.id.asc()) if oldest else (Ticket.created_at.desc(), Ticket.id.desc())
        model = Page[AgentTicket] if self.actor.role == UserRole.AGENT else Page[CustomerTicket]
        return model(**self.paged(query, options.page, options.page_size, lambda row: self.summary(row[0]), order=order))

    def message_view(self, ticket, message, display_name):
        author = PublicAuthor(display_name=display_name,
                              kind=UserRole.CUSTOMER if message.author_id == ticket.customer_id else UserRole.AGENT)
        common = {"id": message.id, "body": message.body, "visibility": message.visibility,
                  "author": author, "created_at": message.created_at}
        if self.actor.role == UserRole.AGENT:
            return AgentMessage(**common, author_id=message.author_id)
        return PublicMessage(**common)

    def messages(self, ticket, page, page_size):
        query = select(TicketMessage, User.display_name).join(User, User.id == TicketMessage.author_id).where(TicketMessage.ticket_id == ticket.id)
        agent = self.actor.role == UserRole.AGENT
        if not agent:
            query = query.where(TicketMessage.visibility == Visibility.PUBLIC)
        model = Page[AgentMessage] if agent else Page[PublicMessage]
        return model(**self.paged(query, page, page_size, lambda row: self.message_view(ticket, row[0], row[1]),
                                order=(TicketMessage.created_at.asc(), TicketMessage.id.asc())))

    def event_view(self, event):
        metadata = event.event_metadata
        action = metadata.get("action", "TICKET_CREATED" if event.event_type == TicketEventType.TICKET_CREATED else "LEGACY_EVENT")
        if self.actor.role == UserRole.AGENT:
            if action not in TicketAction._value2member_map_:
                action = TicketAction.LEGACY_EVENT
            return AgentEvent(id=event.id, event_type=event.event_type, action=action, actor_id=event.actor_id,
                              metadata=metadata, created_at=event.created_at)
        before, after = metadata.get("before", {}), metadata.get("after", {})
        return PublicEvent(id=event.id, action=action, created_at=event.created_at,
            before_status=before.get("status"), after_status=after.get("status"),
            before_priority=before.get("priority"), after_priority=after.get("priority"),
            message_id=metadata.get("message_id"))

    def events(self, ticket, page, page_size):
        query = select(TicketEvent).where(TicketEvent.ticket_id == ticket.id)
        agent = self.actor.role == UserRole.AGENT
        if not agent:
            action = TicketEvent.event_metadata["action"].astext
            visibility = TicketEvent.event_metadata["visibility"].astext
            query = query.where(or_(TicketEvent.event_type == TicketEventType.TICKET_CREATED,
                and_(TicketEvent.event_type == TicketEventType.STATUS_CHANGED,
                     action.in_(["STATUS_CHANGED", "REOPENED", "PRIORITY_CHANGED"])),
                and_(TicketEvent.event_type == TicketEventType.REPLY_ADDED, visibility == "PUBLIC",
                     action.in_(["CUSTOMER_REPLY", "AGENT_REPLY"]))))
        model = Page[AgentEvent] if agent else Page[PublicEvent]
        return model(**self.paged(query, page, page_size, lambda row: self.event_view(row[0]),
                                order=(TicketEvent.created_at.asc(), TicketEvent.id.asc())))

    def detail(self, identifier, options):
        ticket = self.ticket(identifier)
        common = self.summary(ticket).model_dump() | {"description": ticket.description,
            "messages": self.messages(ticket, options.messages_page, options.page_size)}
        if self.actor.role == UserRole.AGENT:
            return AgentDetail(**common, events=self.events(ticket, options.events_page, options.page_size))
        return CustomerDetail(**common, history=self.events(ticket, options.history_page, options.page_size))

    def event_listing(self, identifier, options):
        return self.events(self.ticket(identifier), options.page, options.page_size)

    def create(self, payload):
        moment = self.now()
        ticket = Ticket(customer_id=self.actor.id, subject=payload.subject, description=payload.description,
                        status=TicketStatus.OPEN, priority=Priority.NORMAL, created_at=moment, updated_at=moment)
        self.session.add(ticket)
        self.session.flush()
        self.event(ticket, TicketEventType.TICKET_CREATED, TicketAction.TICKET_CREATED, moment,
                   after={"status": "OPEN", "priority": "NORMAL"})
        self.session.flush()
        return self.summary(ticket)

    def claim(self, identifier):
        ticket = self.ticket(identifier)  # SELECT FOR UPDATE serializes all competing claims.
        if ticket.assigned_agent_id is not None:
            raise APIError(409, "ticket_already_assigned", "The ticket is already assigned.")
        return self.assign_ticket(ticket, self.actor.id)

    def assign_ticket(self, ticket, target):
        if target is not None:
            candidate = self.session.scalars(select(User).where(User.id == target).with_for_update(read=True)).one_or_none()
            if candidate is None or not candidate.is_active or candidate.role != UserRole.AGENT:
                raise APIError(422, "invalid_assignee", "Assignment requires an active agent.")
        if target == ticket.assigned_agent_id:
            return self.summary(ticket)
        previous, moment = ticket.assigned_agent_id, self.now()
        ticket.assigned_agent_id, ticket.updated_at = target, moment
        self.event(ticket, TicketEventType.ASSIGNMENT_CHANGED, TicketAction.ASSIGNMENT_CHANGED, moment,
            before={"assigned_agent_id": str(previous) if previous else None},
            after={"assigned_agent_id": str(target) if target else None})
        self.session.flush()
        return self.summary(ticket)

    def assignment(self, identifier, payload):
        return self.assign_ticket(self.ticket(identifier), payload.assigned_agent_id)

    def transition(self, identifier, target):
        ticket = self.ticket(identifier)
        try:
            validate_transition(ticket.status, target)
        except ValueError:
            raise APIError(409, "invalid_transition", "The requested ticket status transition is not allowed.") from None
        previous, moment = ticket.status, self.now()
        ticket.status, ticket.updated_at = target, moment
        ticket.resolved_at = moment if target == TicketStatus.RESOLVED else None
        action = TicketAction.REOPENED if target == TicketStatus.REOPENED else TicketAction.STATUS_CHANGED
        self.event(ticket, TicketEventType.STATUS_CHANGED, action, moment,
                   before={"status": previous.value}, after={"status": target.value})
        self.session.flush()
        return self.summary(ticket)

    def priority(self, identifier, payload):
        ticket = self.ticket(identifier)
        if ticket.priority == payload.priority:
            return self.summary(ticket)
        previous, moment = ticket.priority, self.now()
        ticket.priority, ticket.updated_at = payload.priority, moment
        # Existing STATUS_CHANGED is the state-change bucket. The trusted action
        # and explicit before/after field distinguish priority from lifecycle changes.
        self.event(ticket, TicketEventType.STATUS_CHANGED, TicketAction.PRIORITY_CHANGED, moment,
                   before={"priority": previous.value}, after={"priority": payload.priority.value})
        self.session.flush()
        return self.summary(ticket)

    def message(self, identifier, payload):
        ticket = self.ticket(identifier)
        agent = self.actor.role == UserRole.AGENT
        visibility = payload.visibility if agent else Visibility.PUBLIC
        if visibility == Visibility.PUBLIC and ticket.status == TicketStatus.RESOLVED:
            raise APIError(409, "ticket_resolved", "Reopen the ticket before sending a public reply.")
        moment = self.now()
        message = TicketMessage(ticket_id=ticket.id, author_id=self.actor.id, body=payload.body,
                                visibility=visibility, created_at=moment)
        self.session.add(message)
        self.session.flush()
        action = TicketAction.INTERNAL_NOTE if visibility == Visibility.INTERNAL else TicketAction.AGENT_REPLY if agent else TicketAction.CUSTOMER_REPLY
        self.event(ticket, TicketEventType.REPLY_ADDED, action, moment,
                   message_id=str(message.id), visibility=visibility.value)
        if visibility == Visibility.PUBLIC:
            ticket.updated_at = moment
        self.session.flush()
        return self.message_view(ticket, message, self.actor.display_name)

    def assignable_agents(self, options):
        query = select(User).where(User.role == UserRole.AGENT, User.is_active.is_(True))
        return Page[AssignableAgent](**self.paged(query, options.page, options.page_size,
            lambda row: AssignableAgent(id=row[0].id, display_name=row[0].display_name), order=(User.id.asc(),)))


class TicketService:
    def __init__(self, database):
        self.database = database

    def run(self, principal, role, method, *args, write=False):
        with self.database.transaction(readonly=not write) as session:
            try:
                repository = TicketRepository(session, principal, role, write=write)
                result = getattr(repository, method)(*args)  # Internal fixed method names only.
                if write:
                    session.flush()
            except IntegrityError:
                raise APIError(409, "ticket_conflict", "The ticket change conflicts with database integrity rules.") from None
            except DBAPIError as error:
                if getattr(error.orig, "sqlstate", None) in {"40001", "40P01", "55P03"}:
                    raise APIError(409, "retry_conflict", "A concurrent change prevented this operation; retry the request.") from None
                raise
        return result  # Owning transaction must commit before any HTTP response.

    def customer_list(self, actor, options): return self.run(actor, UserRole.CUSTOMER, "listing", options)
    def customer_detail(self, actor, identifier, options): return self.run(actor, UserRole.CUSTOMER, "detail", identifier, options)
    def create(self, actor, payload): return self.run(actor, UserRole.CUSTOMER, "create", payload, write=True)
    def customer_message(self, actor, identifier, payload): return self.run(actor, UserRole.CUSTOMER, "message", identifier, payload, write=True)
    def reopen(self, actor, identifier): return self.run(actor, UserRole.CUSTOMER, "transition", identifier, TicketStatus.REOPENED, write=True)
    def agent_list(self, actor, options): return self.run(actor, UserRole.AGENT, "listing", options)
    def agent_detail(self, actor, identifier, options): return self.run(actor, UserRole.AGENT, "detail", identifier, options)
    def claim(self, actor, identifier): return self.run(actor, UserRole.AGENT, "claim", identifier, write=True)
    def assignment(self, actor, identifier, payload): return self.run(actor, UserRole.AGENT, "assignment", identifier, payload, write=True)
    def status(self, actor, identifier, payload): return self.run(actor, UserRole.AGENT, "transition", identifier, payload.status, write=True)
    def priority(self, actor, identifier, payload): return self.run(actor, UserRole.AGENT, "priority", identifier, payload, write=True)
    def agent_message(self, actor, identifier, payload): return self.run(actor, UserRole.AGENT, "message", identifier, payload, write=True)
    def agent_events(self, actor, identifier, options):
        return self.run(actor, UserRole.AGENT, "event_listing", identifier, options)
    def agents(self, actor, options): return self.run(actor, UserRole.AGENT, "assignable_agents", options)
