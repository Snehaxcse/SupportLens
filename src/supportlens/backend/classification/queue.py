"""Lightweight transactional outbox construction; no model imports or inference."""
from uuid import uuid4

from ..db.models import AIRun
from ..domain.enums import AIRunStatus
from .constants import INPUT_SCHEME, KIND, MODEL_VERSION, SCORE_TYPE, TRUSTED_SHA256


def new_run(session, ticket_id, moment, *, generation=1, parent=None, requested_by=None):
    row = AIRun(id=uuid4(), ticket_id=ticket_id, created_at=moment, model_version=MODEL_VERSION,
        status=AIRunStatus.PENDING, predicted_intent=None, requires_human_review=True,
        score_metadata={"kind": KIND, "generation": generation, "retry_parent": str(parent) if parent else None,
            "requested_by": str(requested_by) if requested_by else None,
            "request_source": "AGENT_RETRY" if requested_by else "TICKET_CREATION",
            "artifact_sha256": TRUSTED_SHA256["model.joblib"], "score_type": SCORE_TYPE,
            "input_scheme": INPUT_SCHEME, "execution_count": 0})
    session.add(row)
    return row
