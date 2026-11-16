"""Deterministic eligibility to ask for a draft, never permission to send."""

from dataclasses import dataclass
from enum import StrEnum

from ..classification.schemas import ClassificationState
from ..domain.enums import Intent, Priority, TicketStatus

VERSION = "supportlens-draft-policy-v1"
SAFE_INTENTS = frozenset({Intent.APP_TECHNICAL, Intent.CONTENT, Intent.FEATURE})


class DecisionCode(StrEnum):
    AI_DRAFT_ELIGIBLE = "AI_DRAFT_ELIGIBLE"
    NEEDS_AGENT = "NEEDS_AGENT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CLASSIFICATION_PENDING = "CLASSIFICATION_PENDING"
    CLASSIFICATION_UNCERTAIN = "CLASSIFICATION_UNCERTAIN"
    GENERATION_UNAVAILABLE = "GENERATION_UNAVAILABLE"


MESSAGES = {
    "ELIGIBLE": "A draft can be requested. Review and public send require separate agent actions.",
    "TICKET_RESOLVED": "Reopen this ticket before drafting or sending a new reply.",
    "HIGH_PRIORITY": "High-priority tickets need manual handling.",
    "SENSITIVE_CASE": "Handle this sensitive issue with a manual reply.",
    "PROMPT_INJECTION": "The conversation contains instructions directed at the drafting system. Reply manually.",
    "CLASSIFICATION_PENDING": "Wait for the current classification, or continue with a manual reply.",
    "CLASSIFICATION_MISSING": "Request classification in the intent panel before drafting.",
    "CLASSIFICATION_FAILED": "Classification could not establish an eligible intent. Reply manually or retry classification.",
    "CLASSIFICATION_TRUNCATED": "The classifier did not read the full request. Review the ticket manually.",
    "CUSTOMER_FOLLOW_UP": "A customer follow-up is outside the classifier input. Review the latest conversation manually.",
    "INTENT_REQUIRES_AGENT": "This intent requires a manual reply.",
    "LOW_CONFIDENCE": "The classifier score is below the drafting threshold. Review the ticket manually.",
    "NO_SAFE_EVIDENCE": "No eligible published guidance supports a draft. Continue with a manual reply.",
    "PROVIDER_DISABLED": "Gemini drafting is disabled for this ticket. The manual reply workflow is available.",
    "PROVIDER_QUOTA": "Gemini quota is exhausted for the current Pacific day. Continue with a manual reply.",
    "DAILY_LIMIT": "The daily generation limit is exhausted. Continue with a manual reply.",
    "QUEUE_FULL": "The draft queue is full. Try again later or continue with a manual reply.",
}


@dataclass(frozen=True)
class Decision:
    code: DecisionCode
    reason: str
    risk: str = "STANDARD"

    @property
    def eligible(self):
        return self.code == DecisionCode.AI_DRAFT_ELIGIBLE

    @property
    def message(self):
        return MESSAGES[self.reason]


def decide(*, status, priority, content_risk, customer_follow_up, classification, threshold, invalid_classification=False):
    if content_risk:
        return Decision(DecisionCode.NEEDS_AGENT, content_risk, "ELEVATED")
    if status == TicketStatus.RESOLVED:
        return Decision(DecisionCode.NEEDS_AGENT, "TICKET_RESOLVED")
    if priority == Priority.HIGH:
        return Decision(DecisionCode.NEEDS_AGENT, "HIGH_PRIORITY", "ELEVATED")
    if invalid_classification:
        return Decision(DecisionCode.CLASSIFICATION_UNCERTAIN, "CLASSIFICATION_FAILED", "UNASSESSED")
    if classification is None:
        return Decision(DecisionCode.CLASSIFICATION_PENDING, "CLASSIFICATION_MISSING", "UNASSESSED")
    if classification.status in (ClassificationState.PENDING, ClassificationState.RUNNING):
        return Decision(DecisionCode.CLASSIFICATION_PENDING, "CLASSIFICATION_PENDING", "UNASSESSED")
    if classification.status != ClassificationState.SUCCEEDED or classification.confidence is None:
        return Decision(DecisionCode.CLASSIFICATION_UNCERTAIN, "CLASSIFICATION_FAILED", "UNASSESSED")
    if classification.predicted_intent not in SAFE_INTENTS:
        return Decision(DecisionCode.NEEDS_AGENT, "INTENT_REQUIRES_AGENT", "ELEVATED")
    if customer_follow_up:
        return Decision(DecisionCode.CLASSIFICATION_UNCERTAIN, "CUSTOMER_FOLLOW_UP", "UNASSESSED")
    if classification.input_truncated:
        return Decision(DecisionCode.CLASSIFICATION_UNCERTAIN, "CLASSIFICATION_TRUNCATED", "UNASSESSED")
    if classification.confidence < max(threshold, classification.advisory_threshold):
        return Decision(DecisionCode.CLASSIFICATION_UNCERTAIN, "LOW_CONFIDENCE", "UNASSESSED")
    return Decision(DecisionCode.AI_DRAFT_ELIGIBLE, "ELIGIBLE")
