from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from ..knowledge.schemas import Input


class ReuseApproval(Input):
    request_id: UUID
    expected_draft_version: int = Field(ge=1, strict=True)
    confirm_general_reuse: Literal[True]

    @field_validator("confirm_general_reuse", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("Explicit approval for cross-ticket guidance is required.")
        return value


class ReuseRevocation(Input):
    request_id: UUID
    expected_version: int = Field(ge=1, strict=True)


class CacheSummary(BaseModel):
    id: UUID
    version: int
    status: Literal["ACTIVE", "INVALIDATED", "EXPIRED", "INCOMPATIBLE"]
    reason: str | None
    expires_at: datetime
    hit_count: int


class CacheOrigin(BaseModel):
    id: UUID
    version: int
    expires_at: datetime
    status: Literal["ACTIVE", "INVALIDATED", "EXPIRED", "INCOMPATIBLE"] | None = None


class CacheMetrics(BaseModel):
    entries: int
    active_entries: int
    invalidations: int
    expired_entries: int
    incompatible_entries: int
    lookups: int
    hits: int
    misses: int
    rejected_candidates: int
    incompatible_candidates: int
    average_lookup_ms: float | None
    gemini_generation_bypasses: int
    gemini_requests_attempted: int
    gemini_responses_observed: int
    pacific_day_reservations: int
