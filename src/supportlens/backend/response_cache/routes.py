from uuid import UUID

from fastapi import APIRouter, Depends, Request

from ..api.errors import APIError
from ..auth.dependencies import agent_only
from .schemas import CacheMetrics, CacheSummary, ReuseApproval, ReuseRevocation

router = APIRouter(prefix="/api/v1/agent", tags=["Response cache"])


@router.post("/tickets/{ticket_id}/drafts/{run_id}/reuse", response_model=CacheSummary)
def approve_reuse(request: Request, ticket_id: UUID, run_id: UUID, body: ReuseApproval, actor=Depends(agent_only)):
    runtime = request.app.state.runtime
    return runtime.response_cache.approve(runtime.drafts, actor, ticket_id, run_id, body)


@router.post("/tickets/{ticket_id}/drafts/{run_id}/reuse/{cache_id}/revoke", response_model=CacheSummary)
def revoke_reuse(request: Request, ticket_id: UUID, run_id: UUID, cache_id: UUID, body: ReuseRevocation,
                 actor=Depends(agent_only)):
    runtime = request.app.state.runtime
    if not runtime.response_cache.enabled:
        raise APIError(503, "cache_disabled", "Response reuse is disabled.")
    return runtime.response_cache.revoke(actor, ticket_id, run_id, cache_id, body)


@router.get("/response-cache/metrics", response_model=CacheMetrics)
def cache_metrics(request: Request, actor=Depends(agent_only)):
    cache = request.app.state.runtime.response_cache
    if not cache.enabled:
        raise APIError(503, "cache_disabled", "Response reuse is disabled.")
    return cache.metrics(actor)
