from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from ..knowledge.schemas import Evidence, Input
from ..response_cache.schemas import CacheOrigin, CacheSummary
from ..tickets.schemas import AgentMessage


class GenerateDraft(Input):
    request_id: UUID
    expected_run_id: UUID | None


class ReviewDraft(Input):
    request_id: UUID
    expected_version: int = Field(ge=1, strict=True)
    action: Literal["EDIT", "APPROVE", "REJECT"]
    text: str | None = Field(default=None, min_length=1, max_length=1600)


class SendDraft(Input):
    request_id: UUID
    expected_version: int = Field(ge=1, strict=True)
    confirm_public_send: Literal[True]

    @field_validator("confirm_public_send", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("Explicit public send confirmation is required.")
        return value


class DraftDelivery(BaseModel):
    ticket_id: UUID
    draft_run_id: UUID
    message: AgentMessage


class DraftRun(BaseModel):
    id: UUID
    ticket_id: UUID
    state: Literal["PENDING", "RUNNING", "DRAFT", "APPROVED", "REJECTED", "ABSTAINED", "FAILED"]
    model: str
    prompt_version: str
    text: str | None
    evidence: list[Evidence]
    synthetic: bool
    validation: str | None
    failure: str | None
    retrieval_method: str | None
    provider_calls: int
    usage: dict[str, int]
    latency_ms: int
    version: int
    created_at: datetime
    completed_at: datetime | None
    reviewed_by: UUID | None
    reviewed_at: datetime | None
    send_candidate: bool = False
    sent_message_id: UUID | None = None
    cache_origin: CacheOrigin | None = None
    cache_lookup: Literal["HIT", "MISS"] | None = None
    reuse_approval: CacheSummary | None = None


class DraftList(BaseModel):
    ticket_id: UUID
    generation_enabled: bool
    total: int
    page: int
    items: list[DraftRun]
    reuse_enabled: bool | None = None
