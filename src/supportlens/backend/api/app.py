"""Minimal API factory: health, readiness, current user and authenticated onboarding."""
from contextlib import asynccontextmanager
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
from .config import load_api_settings
from .database import Database
from .errors import APIError

logger = logging.getLogger("supportlens.http")


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
    if runtime is None:
        database = Database(settings)
        runtime = SimpleNamespace(database=database, verifier=FirebaseVerifier(settings), users=UserService(database))
    if not hasattr(runtime, "tickets"):
        runtime.tickets = TicketService(runtime.database)

    @asynccontextmanager
    async def lifespan(app):
        # No database or Firebase connection is required for process health.
        try:
            yield
        finally:
            runtime.database.close()
            runtime.verifier.close()

    app = FastAPI(title="SupportLens", debug=False, docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.state.runtime = runtime
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
    return app
