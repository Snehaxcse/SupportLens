"""Knowledge management and retrieval are entirely inside the agent API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from ..auth.dependencies import agent_only
from .schemas import (
    ArticleDetail, ArticleEdit, ArticleFilters, ArticleInput, ArticlePage,
    LifecycleInput, Method, PreviewInput, RetrievalOptions, RetrievalResult, RevisionPage, SourceType,
)

router = APIRouter(prefix="/api/v1/agent", tags=["Agent knowledge"], dependencies=[Depends(agent_only)])


@router.get("/knowledge", response_model=ArticlePage)
def list_articles(request: Request, filters: Annotated[ArticleFilters, Query()], actor=Depends(agent_only)):
    return request.app.state.runtime.knowledge.list(actor, filters)


@router.post("/knowledge", response_model=ArticleDetail, status_code=201)
def create_article(request: Request, payload: ArticleInput, actor=Depends(agent_only)):
    return request.app.state.runtime.knowledge.create(actor, payload)


@router.post("/knowledge/retrieval-preview", response_model=RetrievalResult)
def retrieval_preview(request: Request, payload: PreviewInput, actor=Depends(agent_only)):
    return request.app.state.runtime.retrieval.preview(actor, payload)


@router.get("/knowledge/{article_id}", response_model=ArticleDetail)
def article_detail(request: Request, article_id: UUID, revision: Annotated[int | None, Query(ge=1)] = None,
                   actor=Depends(agent_only)):
    return request.app.state.runtime.knowledge.detail(actor, article_id, revision)


@router.patch("/knowledge/{article_id}", response_model=ArticleDetail)
def edit_article(request: Request, article_id: UUID, payload: ArticleEdit, actor=Depends(agent_only)):
    return request.app.state.runtime.knowledge.edit(actor, article_id, payload)


@router.post("/knowledge/{article_id}/lifecycle", response_model=ArticleDetail)
def article_lifecycle(request: Request, article_id: UUID, payload: LifecycleInput, actor=Depends(agent_only)):
    return request.app.state.runtime.knowledge.transition(actor, article_id, payload)


@router.get("/knowledge/{article_id}/revisions", response_model=RevisionPage)
def list_revisions(request: Request, article_id: UUID,
                   page: Annotated[int, Query(ge=1, le=1000)] = 1,
                   page_size: Annotated[int, Query(ge=1, le=50)] = 20, actor=Depends(agent_only)):
    return request.app.state.runtime.knowledge.revisions(actor, article_id, page, page_size)


@router.get("/tickets/{ticket_id}/knowledge", response_model=RetrievalResult)
def ticket_evidence(request: Request, ticket_id: UUID, method: Method = Method.HYBRID,
                    top_k: Annotated[int, Query(ge=1, le=10)] = 5, source_type: SourceType | None = None,
                    actor=Depends(agent_only)):
    return request.app.state.runtime.retrieval.for_ticket(
        actor, ticket_id, RetrievalOptions(method=method, top_k=top_k, source_type=source_type),
    )
