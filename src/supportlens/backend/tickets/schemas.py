"""Explicit HTTP allowlists; customer DTOs cannot carry internal audit metadata."""
from datetime import datetime
from enum import StrEnum
from typing import Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..domain.enums import Priority, TicketEventType, TicketStatus, UserRole, Visibility


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyRequest(StrictModel):
    pass


class ErrorDetail(StrictModel):
    code: str
    message: str


class ErrorResponse(StrictModel):
    error: ErrorDetail
    request_id: str


class TicketCreate(StrictModel):
    subject: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=20000)

    @field_validator("subject", "description")
    @classmethod
    def clean_text(cls, value):
        return clean_text(value)


def clean_text(value):
    value = value.strip()
    if not value or any(ord(c) < 32 and c not in "\n\r\t" for c in value):
        raise ValueError("Nonempty plain text without unsafe control characters is required.")
    return value


class CustomerMessageCreate(StrictModel):
    body: str = Field(min_length=1, max_length=20000)

    @field_validator("body")
    @classmethod
    def clean_body(cls, value):
        return clean_text(value)


class AgentMessageCreate(CustomerMessageCreate):
    visibility: Visibility = Visibility.PUBLIC


class AssignmentChange(StrictModel):
    assigned_agent_id: UUID | None  # Explicit null means unassign; omission is invalid.


class StatusChange(StrictModel):
    status: TicketStatus


class PriorityChange(StrictModel):
    priority: Priority


class PageQuery(StrictModel):
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=25, ge=1, le=100)


class InboxQuery(PageQuery):
    status: TicketStatus | None = None
    priority: Priority | None = None
    assigned_agent_id: UUID | None = None
    unassigned_only: bool = False
    search: str | None = Field(default=None, min_length=1, max_length=200)
    order: Literal["newest", "oldest"] = "newest"

    @field_validator("search")
    @classmethod
    def clean_search(cls, value):
        return clean_text(value) if value is not None else None

    @model_validator(mode="after")
    def compatible_assignment_filters(self):
        if self.unassigned_only and self.assigned_agent_id is not None:
            raise ValueError("Assignment and unassigned-only filters cannot be combined.")
        return self


class CustomerDetailQuery(StrictModel):
    messages_page: int = Field(default=1, ge=1, le=10000)
    history_page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=25, ge=1, le=100)


class AgentDetailQuery(StrictModel):
    messages_page: int = Field(default=1, ge=1, le=10000)
    events_page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=25, ge=1, le=100)


T = TypeVar("T")


class Page(StrictModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class CustomerTicket(StrictModel):
    id: UUID
    subject: str
    status: TicketStatus
    priority: Priority
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None


class AgentTicket(CustomerTicket):
    customer_id: UUID
    assigned_agent_id: UUID | None


class PublicAuthor(StrictModel):
    display_name: str | None
    kind: UserRole


class PublicMessage(StrictModel):
    id: UUID
    body: str
    visibility: Literal["PUBLIC"] = "PUBLIC"
    author: PublicAuthor
    created_at: datetime


class AgentMessage(StrictModel):
    id: UUID
    body: str
    visibility: Visibility
    author_id: UUID
    author: PublicAuthor
    created_at: datetime


class TicketAction(StrEnum):
    TICKET_CREATED = "TICKET_CREATED"
    CUSTOMER_REPLY = "CUSTOMER_REPLY"
    AGENT_REPLY = "AGENT_REPLY"
    INTERNAL_NOTE = "INTERNAL_NOTE"
    ASSIGNMENT_CHANGED = "ASSIGNMENT_CHANGED"
    STATUS_CHANGED = "STATUS_CHANGED"
    REOPENED = "REOPENED"
    PRIORITY_CHANGED = "PRIORITY_CHANGED"
    LEGACY_EVENT = "LEGACY_EVENT"


class PublicEvent(StrictModel):
    id: UUID
    action: Literal["TICKET_CREATED", "CUSTOMER_REPLY", "AGENT_REPLY", "STATUS_CHANGED", "REOPENED", "PRIORITY_CHANGED"]
    created_at: datetime
    before_status: TicketStatus | None = None
    after_status: TicketStatus | None = None
    before_priority: Priority | None = None
    after_priority: Priority | None = None
    message_id: UUID | None = None


class AgentEvent(StrictModel):
    id: UUID
    event_type: TicketEventType
    action: TicketAction
    actor_id: UUID | None
    metadata: dict[str, Any]
    created_at: datetime


class CustomerDetail(CustomerTicket):
    description: str
    messages: Page[PublicMessage]
    history: Page[PublicEvent]


class AgentDetail(AgentTicket):
    description: str
    messages: Page[AgentMessage]
    events: Page[AgentEvent]


class AssignableAgent(StrictModel):
    id: UUID
    display_name: str | None
