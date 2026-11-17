"""Minimal API factory: health, readiness, current user and authenticated onboarding."""
from contextlib import asynccontextmanager
import asyncio
from datetime import datetime
import json
import logging
from time import perf_counter
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse

from ..auth.dependencies import agent_only, current_user, verified_identity
from ..auth.firebase import FirebaseVerifier
from ..auth.users import UserService
from ..domain.enums import UserRole
from ..tickets.routes import install_ticket_routes
from ..tickets.service import TicketService
from ..classification.inference import InferenceService, load_trusted_classifier
from ..classification.service import ClassificationOptions, ClassificationService
from ..classification.worker import ClassificationWorker
from ..classification.routes import router as classification_router
from ..knowledge.catalog import Catalog
from ..knowledge.embedding import FILES, LocalEmbedder, verified_bytes
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
from .abuse import AbuseLimits
from .config import load_api_settings
from .database import Database
from .demo import SyntheticDemo
from .errors import APIError
from .operations import Operations, queue_snapshot
from .readiness import Readiness

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


def error_response(request, status, code, message, *, retry_after=None):
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else {}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message},
        "request_id": getattr(request.state, "request_id", "unavailable")}, headers=headers)


class RequestBodyLimit:
    def __init__(self, app, *, timeout=10):
        self.app = app
        self.timeout = timeout

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

        try:
            async with asyncio.timeout(self.timeout):
                body = await self.read_body(receive)
        except TimeoutError:
            response = error_response(request, 408, "request_timeout", "The request body was not received in time.")
            await response(scope, receive, send)
            return
        except APIError:
            await self.reject(request, receive, send)
            return
        if body is None:
            return
        initial = {"type": "http.request", "body": body, "more_body": False}

        async def replay():
            nonlocal initial
            if initial is not None:
                message, initial = initial, None
                return message
            return await receive()

        await self.app(scope, replay, send)

    @staticmethod
    async def read_body(receive):
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return None
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > MAX_REQUEST_BODY_BYTES:
                raise APIError(413, "request_too_large", "Request body exceeds the allowed size.")
            if not body and not message.get("more_body", False):
                return chunk
            body.extend(chunk)
            if not message.get("more_body", False):
                return bytes(body)

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
    demo_mode = settings.environment == Environment.SYNTHETIC_DEMO
    if owned_runtime:
        database = Database(settings)
        runtime = SimpleNamespace(database=database, verifier=FirebaseVerifier(settings), users=UserService(database))
    runtime.demo = SyntheticDemo(settings) if demo_mode else None
    runtime.abuse = AbuseLimits(settings.api_ip_requests_per_minute, settings.api_actor_requests_per_minute)
    runtime.operations = Operations()
    if not hasattr(runtime, "classifications"):
        runtime.classifications = ClassificationService(runtime.database, ClassificationOptions.from_settings(settings))
    if owned_runtime and settings.classification_enabled and settings.classification_worker_enabled and settings.auth_project_id:
        inference = InferenceService()
        if demo_mode:
            model = load_trusted_classifier()
            inference = InferenceService(loader=lambda: model)
        runtime.classification_worker = ClassificationWorker(runtime.classifications, inference,
                                                              poll_seconds=settings.classification_poll_seconds)
    if not hasattr(runtime, "tickets"):
        worker = getattr(runtime, "classification_worker", None)
        runtime.tickets = TicketService(runtime.database, on_created=worker.wake if worker else None,
                                       inventory_limit=settings.demo_ticket_limit if demo_mode else None)
    if owned_runtime and demo_mode and settings.knowledge_embeddings_enabled:
        for name in FILES:
            verified_bytes(name)
    embedder = LocalEmbedder(getattr(settings, "knowledge_embeddings_enabled", True))
    synthetic = demo_mode or settings.knowledge_allow_synthetic
    workers = {}
    if getattr(runtime, "classification_worker", None) is not None:
        workers["classification"] = runtime.classification_worker
    knowledge_worker = None
    if owned_runtime and settings.knowledge_worker_enabled and settings.auth_project_id:
        indexer = Indexer(runtime.database, embedder, allow_synthetic=synthetic)
        indexer.observe = runtime.operations.event
        knowledge_worker = IndexWorker(indexer, settings.knowledge_poll_seconds)
        workers["knowledge"] = knowledge_worker
    if not hasattr(runtime, "knowledge"):
        runtime.knowledge = Catalog(runtime.database, allow_synthetic=synthetic,
                                    wake=knowledge_worker.wake if knowledge_worker else None)
    if not hasattr(runtime, "retrieval"):
        runtime.retrieval = RetrievalService(runtime.database, embedder, allow_synthetic=synthetic)
    if not hasattr(runtime, "workflow"):
        runtime.workflow = Workflow(runtime.database, runtime.classifications, runtime.retrieval,
                                    threshold=settings.workflow_min_confidence,
                                    synthetic_customer_uid=settings.demo_customer_uid if demo_mode
                                    else settings.workflow_synthetic_customer_uid)
    if not hasattr(runtime, "response_cache"):
        runtime.response_cache = ResponseCache(runtime.database, embedder, enabled=settings.response_cache_enabled,
                                               threshold=settings.response_cache_min_similarity,
                                               ttl_days=settings.response_cache_ttl_days)
    if not hasattr(runtime, "drafts"):
        provider = Gemini(settings)
        # The opt-in PostgreSQL test process must never make real API calls.
        if settings.environment != Environment.DEVELOPMENT:
            provider.available = False
        runtime.drafts = DraftService(runtime.database, provider, daily_limit=0 if demo_mode else settings.gemini_daily_limit,
                                      synthetic_ticket=settings.demo_cache_ticket_id if demo_mode
                                      else settings.gemini_synthetic_ticket_id,
                                      allow_reviewed=settings.gemini_allow_reviewed_context,
                                      workflow=runtime.workflow, queue_limit=settings.workflow_draft_queue_limit,
                                      cache=runtime.response_cache)
        if owned_runtime and settings.auth_project_id and (not demo_mode or settings.demo_cache_evaluation):
            runtime.draft_worker = IndexWorker(DraftProcessor(runtime.drafts, runtime.retrieval),
                                               settings.draft_poll_seconds, name="draft", delay_event="draft_worker_delay")
            workers["draft"] = runtime.draft_worker
    if isinstance(runtime.drafts, DraftService):
        runtime.drafts.observe = runtime.operations.event

    def database_probe():
        if owned_runtime:
            required = CACHE_REVISION if demo_mode or runtime.response_cache.enabled else None
            runtime.database.readiness_probe(required_revision=required)
        else:
            runtime.database.ready()

    def firebase_probe():
        if owned_runtime:
            runtime.verifier.readiness_probe()
        else:
            runtime.verifier.ready()

    readiness = Readiness({"firebase": firebase_probe, "database": database_probe},
                          settings.readiness_timeout_seconds)

    @asynccontextmanager
    async def lifespan(app):
        # No database or Firebase connection is required for process health.
        try:
            for worker in workers.values():
                worker.observe = runtime.operations.event
                worker.start()
            yield
        finally:
            readiness.stop()
            await asyncio.gather(*(asyncio.to_thread(worker.stop) for worker in workers.values()))
            runtime.database.close()
            runtime.verifier.close()

    app = FastAPI(title="SupportLens", debug=False, docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.state.runtime = runtime
    app.add_middleware(RequestBodyLimit, timeout=settings.request_body_timeout_seconds)
    if demo_mode:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.demo_trusted_hosts), www_redirect=False)
    app.add_middleware(JSONCORSMiddleware, allow_origins=list(settings.cors_allowed_origins),
        allow_credentials=False, allow_methods=["GET", "POST", "PATCH"], allow_headers=["Authorization", "Content-Type"],
        expose_headers=["X-Request-ID", "Retry-After"], max_age=600)

    @app.middleware("http")
    async def request_boundary(request, call_next):
        request.state.request_id = str(uuid4())  # Ignore untrusted incoming correlation IDs.
        started = perf_counter()
        try:
            if request.url.path.startswith("/api/") and request.method != "OPTIONS":
                runtime.abuse.peer(request)
            elif request.url.path == "/ready":
                runtime.abuse.readiness(request)
            response = await call_next(request)
        except APIError as exc:
            response = error_response(request, exc.status, exc.code, exc.message, retry_after=exc.retry_after)
            origin = request.headers.get("origin")
            if origin in settings.cors_allowed_origins:
                response.headers.update({"Access-Control-Allow-Origin": origin, "Vary": "Origin",
                                         "Access-Control-Expose-Headers": "X-Request-ID, Retry-After"})
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
        route_path = getattr(route, "path", "unmatched")
        duration = round((perf_counter() - started) * 1000, 2)
        runtime.operations.request(route_path, response.status_code, duration)
        logger.info(json.dumps({"event": "http_request", "request_id": request.state.request_id,
            "method": request.method if request.method in {"GET", "POST", "OPTIONS", "PUT", "PATCH", "DELETE", "HEAD"} else "OTHER",
            "route": route_path, "status": response.status_code, "duration_ms": duration}))
        return response

    @app.exception_handler(APIError)
    async def api_error(request, exc):
        return error_response(request, exc.status, exc.code, exc.message, retry_after=exc.retry_after)

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
    async def ready():
        dependencies_ready = (not owned_runtime or bool(settings.auth_project_id)) and await readiness.verify()
        if not dependencies_ready or any(not worker.running for worker in workers.values()):
            raise APIError(503, "not_ready", "Required dependencies are not ready.")
        return {"status": "ready"}

    @app.get("/api/v1/agent/operations", include_in_schema=False)
    def operations(actor=Depends(agent_only)):
        return {"http": runtime.operations.snapshot(), "dependencies": dict(readiness.last),
                "workers": {name: worker.running for name, worker in workers.items()},
                "response_cache_enabled": runtime.response_cache.enabled,
                "queue_states": queue_snapshot(runtime.database)}

    @app.get("/api/v1/me", response_model=UserResponse)
    def me(user=Depends(current_user)):
        return UserResponse.model_validate(user)

    @app.post("/api/v1/auth/onboard", response_model=UserResponse)
    def onboard(payload: OnboardingRequest, identity=Depends(verified_identity)):
        if demo_mode:
            raise APIError(403, "demo_signup_disabled", "Use a provisioned fictional demo account.")
        return UserResponse.model_validate(runtime.users.onboard(identity))

    install_ticket_routes(app)
    app.include_router(classification_router)
    app.include_router(knowledge_router)
    app.include_router(draft_router)
    app.include_router(cache_router)
    return app
