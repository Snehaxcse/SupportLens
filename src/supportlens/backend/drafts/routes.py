from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from ..auth.dependencies import agent_only
from .schemas import DraftList, DraftRun, GenerateDraft, ReviewDraft

router = APIRouter(prefix="/api/v1/agent/tickets", tags=["Agent drafts"])


@router.get("/{ticket_id}/drafts", response_model=DraftList)
def list_drafts(request: Request, ticket_id: UUID, page: Annotated[int, Query(ge=1, le=1000)] = 1,
                actor=Depends(agent_only)):
    return request.app.state.runtime.drafts.listing(actor, ticket_id, page)


@router.post("/{ticket_id}/drafts", response_model=DraftRun, status_code=202)
def generate_draft(request: Request, ticket_id: UUID, body: GenerateDraft, actor=Depends(agent_only)):
    runtime = request.app.state.runtime
    run = runtime.drafts.enqueue(actor, ticket_id, body)
    worker = getattr(runtime, "draft_worker", None)
    if worker is not None:
        worker.wake()
    return run


@router.post("/{ticket_id}/drafts/{run_id}/review", response_model=DraftRun)
def review_draft(request: Request, ticket_id: UUID, run_id: UUID, body: ReviewDraft, actor=Depends(agent_only)):
    return request.app.state.runtime.drafts.review(actor, ticket_id, run_id, body)
