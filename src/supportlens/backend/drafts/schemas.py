from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from ..knowledge.schemas import Evidence, Input


class GenerateDraft(Input):
    request_id: UUID
    expected_run_id: UUID | None


class ReviewDraft(Input):
    request_id: UUID
    expected_version: int = Field(ge=1, strict=True)
    action: Literal["EDIT", "APPROVE", "REJECT"]
    text: str | None = Field(default=None, min_length=1, max_length=1600)


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


class DraftList(BaseModel):
    ticket_id: UUID
    generation_enabled: bool
    total: int
    page: int
    items: list[DraftRun]
