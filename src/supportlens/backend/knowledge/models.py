"""Additive knowledge catalog around the existing immutable revision store."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    ARRAY, REAL, Boolean, CheckConstraint, DateTime, ForeignKey,
    ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, text as sql_text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, CreatedAt, Timestamps, UUIDPrimaryKey


class KnowledgeEntry(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "knowledge_entries"

    current_version: Mapped[int] = mapped_column(Integer, nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    changed_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))

    __table_args__ = (
        ForeignKeyConstraint(
            ["id", "current_version"], ["knowledge_revisions.entry_id", "knowledge_revisions.version"],
            name="fk_knowledge_entries_current_revision", use_alter=True,
            deferrable=True, initially="DEFERRED", ondelete="RESTRICT",
        ),
        CheckConstraint("current_version >= 1 AND generation >= current_version", name="version_positive"),
        CheckConstraint("status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')", name="status_valid"),
        Index("ix_knowledge_entries_status_updated", "status", "updated_at", "id"),
    )


class KnowledgeRevision(Base):
    __tablename__ = "knowledge_revisions"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    entry_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_entries.id", ondelete="RESTRICT"))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    editor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    source_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(2048))
    rights_reference: Mapped[str] = mapped_column(String(500), nullable=False)
    index_state: Mapped[str] = mapped_column(String(16), nullable=False)
    index_model: Mapped[str | None] = mapped_column(String(120))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_token: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["id", "version"], ["knowledge_articles.id", "knowledge_articles.version"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("entry_id", "version", name="uq_knowledge_revisions_entry_version"),
        CheckConstraint("version >= 1 AND attempts BETWEEN 0 AND 3", name="bounds"),
        CheckConstraint(
            "source_type IN ('APPROVED_REFERENCE', 'INTERNAL_PROCEDURE', 'SYNTHETIC_TEST')",
            name="source_type_valid",
        ),
        CheckConstraint(
            "index_state IN ('IDLE', 'PENDING', 'RUNNING', 'READY', 'LEXICAL_ONLY', 'FAILED')",
            name="index_state_valid",
        ),
        CheckConstraint(
            "(index_state = 'RUNNING' AND lease_token IS NOT NULL AND lease_until IS NOT NULL)"
            " OR (index_state <> 'RUNNING' AND lease_token IS NULL AND lease_until IS NULL)",
            name="lease_consistent",
        ),
        Index("ix_knowledge_revisions_job", "index_state", "lease_until"),
    )


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    revision_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_revisions.id", ondelete="RESTRICT"))
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    search_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("revision_id", "ordinal", name="uq_knowledge_chunks_revision_ordinal"),
        UniqueConstraint("revision_id", "content_hash", name="uq_knowledge_chunks_revision_hash"),
        CheckConstraint("ordinal >= 0 AND length(text) BETWEEN 1 AND 1000", name="bounds"),
    )


class KnowledgeEmbedding(Base):
    __tablename__ = "knowledge_embeddings"

    content_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_key: Mapped[str] = mapped_column(String(120), primary_key=True)
    vector: Mapped[list[float]] = mapped_column(ARRAY(REAL), nullable=False)

    __table_args__ = (
        CheckConstraint("array_ndims(vector) = 1 AND cardinality(vector) = 384", name="dimension"),
    )


class KnowledgeEntryEvent(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "knowledge_entry_events"

    entry_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_entries.id", ondelete="RESTRICT"))
    revision_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_revisions.id", ondelete="RESTRICT"))
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)

    __table_args__ = (
        UniqueConstraint("entry_id", "generation", name="uq_knowledge_events_entry_generation"),
        CheckConstraint("action IN ('CREATE', 'EDIT', 'PUBLISH', 'UNPUBLISH', 'ARCHIVE', 'REINDEX')", name="action_valid"),
        CheckConstraint("status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')", name="status_valid"),
    )


search_index = Index("ix_knowledge_chunks_search", sql_text("to_tsvector('english'::regconfig, search_text)"),
                     postgresql_using="gin")
KnowledgeChunk.__table__.append_constraint(search_index)
