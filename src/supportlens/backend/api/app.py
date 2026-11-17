"""Minimal API factory: health, readiness, current user and authenticated onboarding."""
from contextlib import asynccontextmanager
import asyncio
from datetime import datetime
import json
import logging
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from ..auth.dependencies import current_user, verified_identity
from ..auth.firebase import FirebaseVerifier
from ..auth.users import UserService
from ..domain.enums import UserRole
from ..tickets.routes import install_ticket_routes
from ..tickets.service import TicketService
from ..classification.inference import InferenceService
from ..classification.service import ClassificationOptions, ClassificationService
from ..classification.worker import ClassificationWorker
from ..classification.routes import router as classification_router
from ..knowledge.catalog import Catalog
from ..knowledge.embedding import LocalEmbedder
from ..knowledge.indexing import Indexer, IndexWorker
from ..knowledge.retrieval import RetrievalService
from ..knowledge.routes import router as knowledge_router
from ..drafts.provider import Gemini
from ..drafts.routes import router as draft_router
from ..drafts.service import DraftService
from ..drafts.worker import DraftProcessor
from ..workflow.service import Workflow
from ..response_cache.routes import router as cache_router
from ..response_cache.service import ResponseCache
from ..db.migrations import CACHE_REVISION
from ..config import Environment
from .config import load_api_settings
from .database import Database
from .errors import APIError

logger = logging.getLogger("supportlens.http")

# Allows a 100,000-character knowledge article encoded as JSON surrogate pairs,
# plus its title, provenance fields and JSON structure.
MAX_REQUEST_BODY_BYTES = 1280 * 1024


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: str
    display_name: str | None
    role: UserRole
    is_active: bool
    created_at: datetime


class OnboardingRequest(BaseModel):
    # Profile/role/UID come exclusively from the verified token and database.
    model_config = ConfigDict(extra="forbid")


def error_response(request, status, code, message):
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else {}
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message},
        "request_id": getattr(request.state, "request_id", "unavailable")}, headers=headers)


class RequestBodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        length = request.headers.get("content-length", "").lstrip("0")
        if length.isascii() and length.isdecimal() and (
            len(length) > len(str(MAX_REQUEST_BODY_BYTES)) or int(length) > MAX_REQUEST_BODY_BYTES
        ):
            await self.reject(request, receive, send)
            return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > MAX_REQUEST_BODY_BYTES:
                await self.reject(request, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        initial = {"type": "http.request", "body": bytes(body), "more_body": False}
        body.clear()

        async def replay():
            nonlocal initial
            if initial is not None:
                message, initial = initial, None
                return message
            return await receive()

        await self.app(scope, replay, send)

    @staticmethod
    async def reject(request, receive, send):
        response = error_response(request, 413, "request_too_large", "Request body exceeds the allowed size.")
        await response(request.scope, receive, send)


class JSONCORSMiddleware(CORSMiddleware):
    def preflight_response(self, request_headers):
        response = super().preflight_response(request_headers)
        if response.status_code >= 400:
            return JSONResponse(status_code=400, content={"error": {
                "code": "cors_rejected", "message": "Cross-origin request is not allowed."}},
                headers={key: value for key, value in response.headers.items()
                         if key not in {"content-type", "content-length"}})
        return response


def create_app(settings=None, *, runtime=None):
    settings = settings if settings is not None else load_api_settings()
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    owned_runtime = runtime is None
    if owned_runtime:
        database = Database(settings)
        runtime = SimpleNamespace(database=database, verifier=FirebaseVerifier(settings), users=UserService(database))
    if not hasattr(runtime, "classifications"):
        runtime.classifications = ClassificationService(runtime.database, ClassificationOptions.from_settings(settings))
    if owned_runtime and settings.classification_worker_enabled and settings.firebase_project_id:
        runtime.classification_worker = ClassificationWorker(runtime.classifications,
            InferenceService(enabled=settings.classification_enabled), poll_seconds=settings.classification_poll_seconds)
    if not hasattr(runtime, "tickets"):
        worker = getattr(runtime, "classification_worker", None)
        runtime.tickets = TicketService(runtime.database, on_created=worker.wake if worker else None)
    embedder = LocalEmbedder(getattr(settings, "knowledge_embeddings_enabled", True))
    synthetic = getattr(settings, "knowledge_allow_synthetic", False)
    workers = [worker for worker in (getattr(runtime, "classification_worker", None),) if worker is not None]
    knowledge_worker = None
    if owned_runtime and settings.knowledge_worker_enabled and settings.firebase_project_id:
        knowledge_worker = IndexWorker(Indexer(runtime.database, embedder, allow_synthetic=synthetic),
                                       settings.knowledge_poll_seconds)
        workers.append(knowledge_worker)
    if not hasattr(runtime, "knowledge"):
        runtime.knowledge = Catalog(runtime.database, allow_synthetic=synthetic,
                                    wake=knowledge_worker.wake if knowledge_worker else None)
    if not hasattr(runtime, "retrieval"):
        runtime.retrieval = RetrievalService(runtime.database, embedder, allow_synthetic=synthetic)
    if not hasattr(runtime, "workflow"):
        runtime.workflow = Workflow(runtime.database, runtime.classifications, runtime.retrieval,
                                    threshold=settings.workflow_min_confidence,
                                    synthetic_customer_uid=settings.workflow_synthetic_customer_uid)
    if not hasattr(runtime, "response_cache"):
        runtime.response_cache = ResponseCache(runtime.database, embedder, enabled=settings.response_cache_enabled,
                                               threshold=settings.response_cache_min_similarity,
                                               ttl_days=settings.response_cache_ttl_days)
    if not hasattr(runtime, "drafts"):
        provider = Gemini(settings)
        # The opt-in PostgreSQL test process must never make real API calls.
        if settings.environment != Environment.DEVELOPMENT:
            provider.available = False
        runtime.drafts = DraftService(runtime.database, provider, daily_limit=settings.gemini_daily_limit,
                                      synthetic_ticket=settings.gemini_synthetic_ticket_id,
                                      allow_reviewed=settings.gemini_allow_reviewed_context,
                                      workflow=runtime.workflow, queue_limit=settings.workflow_draft_queue_limit,
                                      cache=runtime.response_cache)
        if owned_runtime and settings.firebase_project_id:
            runtime.draft_worker = IndexWorker(DraftProcessor(runtime.drafts, runtime.retrieval),
                                               settings.draft_poll_seconds, name="draft", delay_event="draft_worker_delay")
            workers.append(runtime.draft_worker)

    @asynccontextmanager
    async def lifespan(app):
        # No database or Firebase connection is required for process health.
        try:
            for worker in workers:
                worker.start()
            yield
        finally:
            await asyncio.gather(*(asyncio.to_thread(worker.stop) for worker in workers))
            runtime.database.close()
            runtime.verifier.close()

    app = FastAPI(title="SupportLens", debug=False, docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.state.runtime = runtime
    app.add_middleware(RequestBodyLimit)
    app.add_middleware(JSONCORSMiddleware, allow_origins=list(settings.cors_allowed_origins),
        allow_credentials=False, allow_methods=["GET", "POST", "PATCH"], allow_headers=["Authorization", "Content-Type"],
        expose_headers=["X-Request-ID"], max_age=600)

    @app.middleware("http")
    async def request_boundary(request, call_next):
        request.state.request_id = str(uuid4())  # Ignore untrusted incoming correlation IDs.
        try:
            response = await call_next(request)
        except Exception:
            response = error_response(request, 500, "internal_error", "An unexpected error occurred.")
            origin = request.headers.get("origin")
            if origin in settings.cors_allowed_origins:
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Vary"] = "Origin"
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        route = request.scope.get("route")
        logger.info(json.dumps({"event": "http_request", "request_id": request.state.request_id,
            "method": request.method if request.method in {"GET", "POST", "OPTIONS", "PUT", "PATCH", "DELETE", "HEAD"} else "OTHER",
            "route": getattr(route, "path", "unmatched"), "status": response.status_code}))
        return response

    @app.exception_handler(APIError)
    async def api_error(request, exc):
        return error_response(request, exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Pydantic error details can include submitted secrets; do not return/log them.
        return error_response(request, 422, "invalid_request", "Request validation failed.")

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error_response(request, exc.status_code, "http_error", "The request could not be completed.")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/ready")
    def ready():
        try:
            runtime.verifier.ready()
            if owned_runtime and runtime.response_cache.enabled:
                runtime.database.ready(required_revision=CACHE_REVISION)
            else:
                runtime.database.ready()
        except Exception:
            raise APIError(503, "not_ready", "Required dependencies are not ready.") from None
        return {"status": "ready"}

    @app.get("/api/v1/me", response_model=UserResponse)
    def me(user=Depends(current_user)):
        return UserResponse.model_validate(user)

    @app.post("/api/v1/auth/onboard", response_model=UserResponse)
    def onboard(payload: OnboardingRequest, identity=Depends(verified_identity)):
        return UserResponse.model_validate(runtime.users.onboard(identity))

    install_ticket_routes(app)
    app.include_router(classification_router)
    app.include_router(knowledge_router)
    app.include_router(draft_router)
    app.include_router(cache_router)
    return app
