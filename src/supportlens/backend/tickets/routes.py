"""Thin HTTP boundary. Authentication/RBAC and bounded DTOs apply to every route."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query, Request

from ..auth.dependencies import agent_only, customer_only
from .schemas import (
    AgentDetail, AgentDetailQuery, AgentEvent, AgentMessage, AgentMessageCreate, AgentTicket,
    AssignableAgent, AssignmentChange, CustomerDetail, CustomerDetailQuery, CustomerMessageCreate,
    CustomerTicket, EmptyRequest, ErrorResponse, InboxQuery, Page, PageQuery, PriorityChange, PublicMessage,
    StatusChange, TicketCreate,
)

errors = {status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 422, 500, 503)}
customer = APIRouter(prefix="/api/v1/tickets", tags=["Customer tickets"], responses=errors)
agent = APIRouter(prefix="/api/v1/agent", tags=["Agent tickets"], responses=errors)


@customer.post("", response_model=CustomerTicket, status_code=201)
def create_ticket(payload: TicketCreate, request: Request, actor=Depends(customer_only)):
    """Create the customer's OPEN/NORMAL ticket and its initial audit event atomically."""
    return request.app.state.runtime.tickets.create(actor, payload)


@customer.get("", response_model=Page[CustomerTicket])
def customer_tickets(request: Request, options: Annotated[PageQuery, Query()], actor=Depends(customer_only)):
    return request.app.state.runtime.tickets.customer_list(actor, options)


@customer.get("/{ticket_id}", response_model=CustomerDetail)
def customer_detail(ticket_id: UUID, request: Request, options: Annotated[CustomerDetailQuery, Query()], actor=Depends(customer_only)):
    return request.app.state.runtime.tickets.customer_detail(actor, ticket_id, options)


@customer.post("/{ticket_id}/messages", response_model=PublicMessage, status_code=201)
def customer_reply(ticket_id: UUID, payload: CustomerMessageCreate, request: Request, actor=Depends(customer_only)):
    return request.app.state.runtime.tickets.customer_message(actor, ticket_id, payload)


@customer.post("/{ticket_id}/reopen", response_model=CustomerTicket)
def customer_reopen(ticket_id: UUID, request: Request, payload: EmptyRequest = Body(default=EmptyRequest()), actor=Depends(customer_only)):
    return request.app.state.runtime.tickets.reopen(actor, ticket_id)


@agent.get("/tickets", response_model=Page[AgentTicket])
def agent_inbox(request: Request, options: Annotated[InboxQuery, Query()], actor=Depends(agent_only)):
    return request.app.state.runtime.tickets.agent_list(actor, options)


@agent.get("/tickets/{ticket_id}", response_model=AgentDetail)
def agent_detail(ticket_id: UUID, request: Request, options: Annotated[AgentDetailQuery, Query()], actor=Depends(agent_only)):
    return request.app.state.runtime.tickets.agent_detail(actor, ticket_id, options)


@agent.post("/tickets/{ticket_id}/claim", response_model=AgentTicket)
def claim_ticket(ticket_id: UUID, request: Request, payload: EmptyRequest = Body(default=EmptyRequest()), actor=Depends(agent_only)):
    return request.app.state.runtime.tickets.claim(actor, ticket_id)


@agent.patch("/tickets/{ticket_id}/assignment", response_model=AgentTicket)
def assign_ticket(ticket_id: UUID, payload: AssignmentChange, request: Request, actor=Depends(agent_only)):
    return request.app.state.runtime.tickets.assignment(actor, ticket_id, payload)


@agent.patch("/tickets/{ticket_id}/status", response_model=AgentTicket)
def change_status(ticket_id: UUID, payload: StatusChange, request: Request, actor=Depends(agent_only)):
    return request.app.state.runtime.tickets.status(actor, ticket_id, payload)


@agent.patch("/tickets/{ticket_id}/priority", response_model=AgentTicket)
def change_priority(ticket_id: UUID, payload: PriorityChange, request: Request, actor=Depends(agent_only)):
    return request.app.state.runtime.tickets.priority(actor, ticket_id, payload)


@agent.post("/tickets/{ticket_id}/messages", response_model=AgentMessage, status_code=201)
def agent_reply(ticket_id: UUID, payload: AgentMessageCreate, request: Request, actor=Depends(agent_only)):
    return request.app.state.runtime.tickets.agent_message(actor, ticket_id, payload)


@agent.get("/tickets/{ticket_id}/events", response_model=Page[AgentEvent])
def agent_events(ticket_id: UUID, request: Request, options: Annotated[PageQuery, Query()], actor=Depends(agent_only)):
    return request.app.state.runtime.tickets.agent_events(actor, ticket_id, options)


@agent.get("/users", response_model=Page[AssignableAgent])
def active_agents(request: Request, options: Annotated[PageQuery, Query()], actor=Depends(agent_only)):
    return request.app.state.runtime.tickets.agents(actor, options)


def install_ticket_routes(app):
    app.include_router(customer)
    app.include_router(agent)
    original_openapi = app.openapi
    def authenticated_openapi():
        schema = original_openapi()
        schema.setdefault("components", {}).setdefault("securitySchemes", {})["FirebaseIDToken"] = {
            "type": "http", "scheme": "bearer", "bearerFormat": "Firebase ID token",
            "description": "Use a Firebase ID token; permissions come from the active PostgreSQL user."}
        for path, operations in schema["paths"].items():
            if path.startswith("/api/v1/"):
                for method, operation in operations.items():
                    if method in {"get", "post", "patch"}:
                        operation["security"] = [{"FirebaseIDToken": []}]
        return schema
    app.openapi = authenticated_openapi
