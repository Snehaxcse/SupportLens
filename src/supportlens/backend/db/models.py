"""PostgreSQL persistence contracts; no ML imports or database connections."""
from datetime import datetime
import uuid

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, Enum, ForeignKey, ForeignKeyConstraint,
    Index, Integer, String, Text, UniqueConstraint, Uuid, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..domain.enums import AIRunStatus, Intent, Priority, TicketEventType, TicketStatus, UserRole, Visibility
from .base import Base, CreatedAt, Timestamps, UUIDPrimaryKey


def checked_enum(enum, name):
    return Enum(enum, name=name, native_enum=False, create_constraint=True,
                validate_strings=True, values_callable=lambda cls: [v.value for v in cls])


class User(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "users"
    firebase_uid: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120))
    role: Mapped[UserRole] = mapped_column(checked_enum(UserRole, "user_role"), nullable=False, server_default="CUSTOMER")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    __table_args__ = (
        CheckConstraint("length(btrim(firebase_uid)) > 0", name="firebase_uid_nonblank"),
        CheckConstraint("length(btrim(email)) > 0", name="email_nonblank"),
        CheckConstraint("display_name IS NULL OR length(btrim(display_name)) > 0", name="display_name_nonblank"),
        CheckConstraint("updated_at >= created_at", name="timestamp_order"),
    )


class Ticket(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "tickets"
    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    assigned_agent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"))
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[TicketStatus] = mapped_column(checked_enum(TicketStatus, "ticket_status"), nullable=False, server_default="OPEN")
    priority: Mapped[Priority] = mapped_column(checked_enum(Priority, "ticket_priority"), nullable=False, server_default="NORMAL")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("length(btrim(subject)) > 0", name="subject_nonblank"),
        CheckConstraint("length(btrim(description)) BETWEEN 1 AND 20000", name="description_length"),
        CheckConstraint("(status = 'RESOLVED') = (resolved_at IS NOT NULL)", name="resolution_consistent"),
        CheckConstraint("resolved_at IS NULL OR resolved_at >= created_at", name="resolution_order"),
        CheckConstraint("updated_at >= created_at", name="timestamp_order"),
        Index("ix_tickets_customer_created", "customer_id", "created_at"),
        Index("ix_tickets_agent_status", "assigned_agent_id", "status"),
    )


class TicketMessage(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "ticket_messages"
    ticket_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tickets.id", ondelete="RESTRICT"), nullable=False)
    author_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    visibility: Mapped[Visibility] = mapped_column(checked_enum(Visibility, "message_visibility"), nullable=False, server_default="PUBLIC")
    __table_args__ = (
        CheckConstraint("length(btrim(body)) BETWEEN 1 AND 20000", name="body_length"),
        Index("ix_ticket_messages_ticket_created", "ticket_id", "created_at"),
    )


class TicketEvent(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "ticket_events"
    ticket_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tickets.id", ondelete="RESTRICT"), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"))
    event_type: Mapped[TicketEventType] = mapped_column(checked_enum(TicketEventType, "ticket_event_type"), nullable=False)
    event_metadata: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    __table_args__ = (
        CheckConstraint("jsonb_typeof(metadata) = 'object'", name="metadata_object"),
        Index("ix_ticket_events_ticket_created", "ticket_id", "created_at"),
    )


class AIRun(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "ai_runs"
    ticket_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tickets.id", ondelete="RESTRICT"), nullable=False)
    model_version: Mapped[str] = mapped_column(String(120), nullable=False)
    predicted_intent: Mapped[Intent | None] = mapped_column(checked_enum(Intent, "predicted_intent"))
    score_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    requires_human_review: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    status: Mapped[AIRunStatus] = mapped_column(checked_enum(AIRunStatus, "ai_run_status"), nullable=False, server_default="PENDING")
    __table_args__ = (
        CheckConstraint("length(btrim(model_version)) > 0", name="model_version_nonblank"),
        CheckConstraint("jsonb_typeof(score_metadata) = 'object'", name="score_metadata_object"),
        Index("ix_ai_runs_ticket_created", "ticket_id", "created_at"),
    )


class KnowledgeArticle(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "knowledge_articles"
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_reference: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    __table_args__ = (
        UniqueConstraint("id", "version", name="uq_knowledge_articles_id_version"),
        CheckConstraint("length(btrim(title)) > 0", name="title_nonblank"),
        CheckConstraint("length(btrim(content)) BETWEEN 1 AND 100000", name="content_length"),
        CheckConstraint("length(btrim(source_reference)) > 0", name="source_nonblank"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("updated_at >= created_at", name="timestamp_order"),
    )


class KnowledgeCitation(UUIDPrimaryKey, Base):
    __tablename__ = "knowledge_citations"
    ai_run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("ai_runs.id", ondelete="RESTRICT"), nullable=False)
    knowledge_article_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    article_version: Mapped[int] = mapped_column(Integer, nullable=False)
    citation_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    __table_args__ = (
        ForeignKeyConstraint(["knowledge_article_id", "article_version"], ["knowledge_articles.id", "knowledge_articles.version"], ondelete="RESTRICT"),
        UniqueConstraint("ai_run_id", "knowledge_article_id", "article_version", name="uq_knowledge_citations_run_article_version"),
        CheckConstraint("article_version >= 1", name="article_version_positive"),
        CheckConstraint("jsonb_typeof(citation_metadata) = 'object'", name="citation_metadata_object"),
        Index("ix_knowledge_citations_article_version", "knowledge_article_id", "article_version"),
    )
