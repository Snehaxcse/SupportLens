"""Durable cache records, apart from the knowledge vector store."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import ARRAY, REAL, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, CreatedAt, UUIDPrimaryKey


class ResponseCacheEntry(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "response_cache_entries"

    source_run_id: Mapped[UUID] = mapped_column(ForeignKey("ai_runs.id", ondelete="RESTRICT"), unique=True)
    approved_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    approval_request_id: Mapped[UUID] = mapped_column(unique=True)
    source_draft_version: Mapped[int] = mapped_column(Integer)
    issue_key: Mapped[str] = mapped_column(String(48))
    canonical_issue: Mapped[str] = mapped_column(String(180))
    intent: Mapped[str] = mapped_column(String(24))
    risk: Mapped[str] = mapped_column(String(16))
    scope: Mapped[str] = mapped_column(String(16))
    policy_version: Mapped[str] = mapped_column(String(64))
    match_version: Mapped[str] = mapped_column(String(40))
    embedding_model: Mapped[str] = mapped_column(String(120))
    vector: Mapped[list[float]] = mapped_column(ARRAY(REAL))
    content: Mapped[str] = mapped_column(Text)
    duplicate_key: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidation_reason: Mapped[str | None] = mapped_column(String(24))
    revoked_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    revocation_request_id: Mapped[UUID | None] = mapped_column(unique=True)
    hit_count: Mapped[int] = mapped_column(Integer)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("array_ndims(vector) = 1 AND cardinality(vector) = 384", name="dimension"),
        CheckConstraint("scope IN ('GENERAL', 'SYNTHETIC') AND risk = 'STANDARD'", name="scope_risk"),
        CheckConstraint("intent IN ('APP_TECHNICAL', 'CONTENT', 'FEATURE')", name="intent"),
        CheckConstraint("length(content) BETWEEN 1 AND 1600 AND source_draft_version > 0 AND version > 0", name="bounds"),
        CheckConstraint("expires_at > created_at AND expires_at <= created_at + interval '30 days'", name="ttl"),
        CheckConstraint("hit_count >= 0 AND (hit_count = 0) = (last_hit_at IS NULL)", name="usage"),
        CheckConstraint("(state = 'ACTIVE' AND invalidated_at IS NULL AND invalidation_reason IS NULL) OR "
                        "(state = 'INACTIVE' AND invalidated_at IS NOT NULL AND invalidation_reason IN "
                        "('EVIDENCE_CHANGED', 'REVOKED', 'EXPIRED') AND invalidation_reason IS NOT NULL)", name="state"),
        CheckConstraint("(revoked_by IS NOT NULL) = coalesce(invalidation_reason = 'REVOKED', false) AND "
                        "(revocation_request_id IS NOT NULL) = (revoked_by IS NOT NULL)", name="revocation"),
        Index("ix_response_cache_candidates", "scope", "intent", "issue_key", "expires_at", postgresql_where=text("state = 'ACTIVE'")),
        Index("uq_response_cache_active_content", "duplicate_key", unique=True, postgresql_where=text("state = 'ACTIVE'")),
    )


class ResponseCacheEvidence(Base):
    __tablename__ = "response_cache_evidence"

    cache_id: Mapped[UUID] = mapped_column(ForeignKey("response_cache_entries.id", ondelete="RESTRICT"), primary_key=True)
    chunk_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_chunks.id", ondelete="RESTRICT"), primary_key=True)
    article_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_entries.id", ondelete="RESTRICT"))
    revision_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_revisions.id", ondelete="RESTRICT"))
    content_hash: Mapped[str] = mapped_column(String(64))

    __table_args__ = (Index("ix_response_cache_evidence_article", "article_id"),)
