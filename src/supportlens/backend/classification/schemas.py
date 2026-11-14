"""Whitelisted agent DTOs; queue ownership, raw text and internal errors are never exposed."""
from datetime import datetime
from enum import StrEnum
import math
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ..domain.enums import Intent
from .constants import CORE_LABELS, SCORE_TYPE


class ClassificationState(StrEnum):
    NOT_REQUESTED = "NOT_REQUESTED"
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


class FailureCategory(StrEnum):
    MODEL_MISSING = "MODEL_MISSING"
    ARTIFACT_INVALID = "ARTIFACT_INVALID"
    RUNTIME_INCOMPATIBLE = "RUNTIME_INCOMPATIBLE"
    MODEL_DISABLED = "MODEL_DISABLED"
    INVALID_INPUT = "INVALID_INPUT"
    INFERENCE_FAILED = "INFERENCE_FAILED"
    UNSUPPORTED_OUTPUT = "UNSUPPORTED_OUTPUT"
    RECOVERY_EXHAUSTED = "RECOVERY_EXHAUSTED"


UNAVAILABLE_CATEGORIES = frozenset({FailureCategory.MODEL_MISSING, FailureCategory.ARTIFACT_INVALID,
    FailureCategory.RUNTIME_INCOMPATIBLE, FailureCategory.MODEL_DISABLED})


class InferenceResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True, allow_inf_nan=False)
    status: Literal[ClassificationState.SUCCEEDED, ClassificationState.FAILED, ClassificationState.UNAVAILABLE]
    predicted_intent: Intent | None = None
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    class_scores: dict[str, float] = Field(default_factory=dict)
    failure_category: FailureCategory | None = None
    duration_ms: float = Field(ge=0, allow_inf_nan=False)
    input_truncated: bool = False
    input_characters: int = Field(default=0, ge=0, le=10000)
    artifact_verified: bool = False
    requires_human_review: Literal[True] = True
    automated_actions_allowed: Literal[False] = False

    @model_validator(mode="after")
    def valid_terminal_result(self):
        if self.status == ClassificationState.SUCCEEDED:
            if (self.predicted_intent not in CORE_LABELS or self.confidence is None
                    or self.failure_category is not None or not self.artifact_verified
                    or set(self.class_scores) != set(CORE_LABELS)
                    or any(not 0 <= score <= 1 for score in self.class_scores.values())
                    or not math.isclose(sum(self.class_scores.values()), 1, abs_tol=1e-6)
                    or not math.isclose(self.class_scores.get(self.predicted_intent, -1), self.confidence, abs_tol=1e-9)
                    or not math.isclose(max(self.class_scores.values()), self.confidence, abs_tol=1e-9)):
                raise ValueError("Invalid successful classification.")
        elif self.predicted_intent is not None or self.confidence is not None or self.failure_category is None:
            raise ValueError("Invalid unavailable or failed classification.")
        return self


class ClassificationRun(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    id: UUID
    ticket_id: UUID
    generation: int = Field(ge=1)
    status: ClassificationState
    model_version: str = Field(min_length=1, max_length=120)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_verified: bool
    predicted_intent: Intent | None
    confidence: float | None = Field(ge=0, le=1, allow_inf_nan=False)
    score_type: Literal["UNCALIBRATED_RAW_PROBABILITY"] = SCORE_TYPE
    requires_human_review: Literal[True] = True
    automated_actions_allowed: Literal[False] = False
    low_score: bool | None
    advisory_threshold: float = Field(ge=0, le=1, allow_inf_nan=False)
    threshold_validated: Literal[False] = False
    input_truncated: bool
    created_at: AwareDatetime
    completed_at: AwareDatetime | None
    duration_ms: float | None = Field(ge=0, allow_inf_nan=False)
    failure_category: FailureCategory | None


class ClassificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket_id: UUID
    status: ClassificationState
    current: ClassificationRun | None
    recent_runs: list[ClassificationRun] = Field(max_length=5)
    total_runs: int = Field(ge=0)
    supported_intents: list[str]
    unsupported_intents: list[str]
    model_limitation: str
    requires_human_review: Literal[True] = True
    automated_actions_allowed: Literal[False] = False
    retry_allowed: bool
    retry_available_at: AwareDatetime | None


class ClassificationRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_run_id: UUID | None  # Required: null means the observed absence of a run.
