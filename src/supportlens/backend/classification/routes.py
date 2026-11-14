"""Agent-only classification access with fixed model identity and retry preconditions."""
from uuid import UUID
from fastapi import APIRouter, Depends, Request, Response

from ..auth.dependencies import agent_only
from ..tickets.schemas import ErrorResponse
from .schemas import ClassificationResponse, ClassificationRetryRequest, ClassificationRun, ClassificationState

router = APIRouter(prefix="/api/v1/agent/tickets", tags=["Agent classification"],
    responses={status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 422, 429, 500, 503)})


@router.get("/{ticket_id}/classification", response_model=ClassificationResponse)
def get_classification(ticket_id: UUID, request: Request, actor=Depends(agent_only)):
    return request.app.state.runtime.classifications.get(actor, ticket_id)


@router.post("/{ticket_id}/classification/retry", response_model=ClassificationRun, status_code=202,
             responses={200: {"model": ClassificationRun, "description": "Completed attempt returned by durable replay."}})
def retry_classification(ticket_id: UUID, payload: ClassificationRetryRequest, request: Request, response: Response,
                         actor=Depends(agent_only)):
    runtime = request.app.state.runtime
    run = runtime.classifications.retry(actor, ticket_id, payload.expected_run_id)
    response.status_code = 202 if run.status in {ClassificationState.PENDING, ClassificationState.RUNNING} else 200
    worker = getattr(runtime, "classification_worker", None)
    if worker is not None:
        try:
            worker.wake()
        except Exception:
            pass  # The committed outbox remains recoverable by polling/startup/CLI.
    return run
