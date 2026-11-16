"""Short database transactions for queueing, leasing and publishing advisory results."""
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import Float, and_, case, cast, func, or_, select
from sqlalchemy.exc import DBAPIError

from ..api.errors import APIError
from ..db.models import AIRun, Ticket, TicketEvent
from ..domain.enums import AIRunStatus, TicketEventType, UserRole
from ..tickets.schemas import TicketAction
from ..tickets.service import TicketRepository
from .constants import CORE_LABELS, INPUT_SCHEME, KIND, MODEL_LIMITATION, MODEL_VERSION, SCORE_TYPE, TRUSTED_SHA256, UNSUPPORTED
from .queue import new_run
from .schemas import ClassificationResponse, ClassificationRun, ClassificationState, FailureCategory, InferenceResult, UNAVAILABLE_CATEGORIES


@dataclass(frozen=True)
class ClassificationOptions:
    threshold: float = 0.5
    lease_seconds: int = 300
    max_executions: int = 3
    retry_cooldown_seconds: int = 60
    retry_limit_per_minute: int = 10

    @classmethod
    def from_settings(cls, settings):
        return cls(**{name: getattr(settings, "classification_" + variable, default) for name, variable, default in (
            ("threshold", "low_score_threshold", 0.5), ("lease_seconds", "lease_seconds", 300),
            ("max_executions", "max_executions", 3), ("retry_cooldown_seconds", "retry_cooldown_seconds", 60),
            ("retry_limit_per_minute", "retry_limit_per_minute", 10))})


@dataclass(frozen=True)
class ClassificationJob:
    id: UUID
    ticket_id: UUID
    lease_token: str
    subject: str
    description: str
    recovery_exhausted: bool = False


def classification_unavailable():
    return APIError(503, "classification_unavailable", "Classification data is temporarily unavailable.")


def run_query(ticket_id=None):
    query = select(AIRun).where(AIRun.score_metadata["kind"].astext == KIND)
    return query.where(AIRun.ticket_id == ticket_id) if ticket_id is not None else query


def recent_query(ticket_id):
    return run_query(ticket_id).order_by(AIRun.score_metadata["generation"].as_integer().desc(), AIRun.created_at.desc(), AIRun.id.desc())


class ClassificationService:
    def __init__(self, database, options=None):
        self.database = database
        self.options = options or ClassificationOptions()

    def _metadata(self, row):
        meta = row.score_metadata
        if (not isinstance(meta, dict) or meta.get("kind") != KIND or row.model_version != MODEL_VERSION
                or row.requires_human_review is not True or meta.get("artifact_sha256") != TRUSTED_SHA256["model.joblib"]
                or meta.get("score_type") != SCORE_TYPE or meta.get("input_scheme") != INPUT_SCHEME
                or type(meta.get("generation")) is not int or not 1 <= meta["generation"] <= 2_147_483_647):
            raise classification_unavailable()
        return meta

    def _view(self, row):
        try:
            meta = self._metadata(row)
            category = FailureCategory(meta["failure_category"]) if meta.get("failure_category") else None
            state = ClassificationState(row.status.value)
            if state == ClassificationState.FAILED and category in UNAVAILABLE_CATEGORIES:
                state = ClassificationState.UNAVAILABLE
            terminal = state in {ClassificationState.SUCCEEDED, ClassificationState.FAILED, ClassificationState.UNAVAILABLE}
            confidence = meta.get("confidence")
            threshold = meta.get("advisory_threshold", self.options.threshold)
            if state == ClassificationState.SUCCEEDED:
                if (row.predicted_intent not in CORE_LABELS or confidence is None or category is not None
                        or meta.get("artifact_verified") is not True or meta.get("low_score") != (confidence < threshold)):
                    raise ValueError
            elif row.predicted_intent is not None or confidence is not None or (terminal and category is None):
                raise ValueError
            if terminal and (not meta.get("completed_at") or meta.get("duration_ms") is None):
                raise ValueError
            return ClassificationRun(id=row.id, ticket_id=row.ticket_id, generation=meta["generation"], status=state,
                model_version=row.model_version, artifact_sha256=meta["artifact_sha256"],
                artifact_verified=meta.get("artifact_verified", False), predicted_intent=row.predicted_intent,
                confidence=confidence, low_score=meta.get("low_score"), advisory_threshold=threshold,
                input_truncated=meta.get("input_truncated", False), created_at=row.created_at,
                completed_at=meta.get("completed_at"), duration_ms=meta.get("duration_ms"), failure_category=category)
        except APIError:
            raise
        except Exception:
            raise classification_unavailable() from None

    def get(self, actor, ticket_id):
        with self.database.transaction(readonly=True) as session:
            repository = TicketRepository(session, actor, UserRole.AGENT)
            repository.ticket(ticket_id)
            rows = list(session.scalars(recent_query(ticket_id).limit(5)))
            total = session.scalar(select(func.count()).select_from(run_query(ticket_id).subquery()))
            recent = [self._view(row) for row in rows]
            current = recent[0] if recent else None
            now = repository.now()
            active = current is not None and current.status in {ClassificationState.PENDING, ClassificationState.RUNNING}
            available_at = current.created_at + timedelta(seconds=self.options.retry_cooldown_seconds) if current and not active else None
            return ClassificationResponse(ticket_id=ticket_id, status=current.status if current else ClassificationState.NOT_REQUESTED,
                current=current, recent_runs=recent, total_runs=total, supported_intents=list(CORE_LABELS),
                unsupported_intents=list(UNSUPPORTED), model_limitation=MODEL_LIMITATION,
                retry_allowed=not active and (available_at is None or now >= available_at), retry_available_at=available_at)

    def current(self, session, ticket_id):
        row = session.scalar(recent_query(ticket_id).limit(1))
        return self._view(row) if row else None

    def retry(self, actor, ticket_id, expected_run_id):
        try:
            with self.database.transaction() as session:
                return self._retry(session, actor, ticket_id, expected_run_id)
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) in {"40001", "40P01", "55P03"}:
                raise APIError(409, "retry_conflict", "A concurrent change prevented classification retry.") from None
            raise

    def _retry(self, session, actor, ticket_id, expected_run_id):
        repository = TicketRepository(session, actor, UserRole.AGENT, write=True)
        # Serialize an actor's cross-ticket budget. Keys are bound parameters; locks end with this transaction.
        session.execute(select(func.pg_advisory_xact_lock(1397506892, func.hashtext(str(actor.id)))))
        ticket = repository.ticket(ticket_id)  # Serializes all new generations for this ticket.
        parent = AIRun.score_metadata["retry_parent"].astext
        child = session.scalars(recent_query(ticket_id).where(
            parent == str(expected_run_id) if expected_run_id else parent.is_(None)).limit(1)).first()
        if child is not None:
            return self._view(child)  # Durable replay, including completed/historical requests.
        current = session.scalars(recent_query(ticket_id).limit(1)).first()
        if current is not None and current.status in {AIRunStatus.PENDING, AIRunStatus.RUNNING}:
            return self._view(current)
        if (current.id if current else None) != expected_run_id:
            raise APIError(409, "classification_changed", "Classification changed; refresh before requesting another run.")
        now = repository.now()
        if current is not None and now < current.created_at + timedelta(seconds=self.options.retry_cooldown_seconds):
            raise APIError(429, "classification_retry_rate_limited", "Wait for the classification retry cooldown, then refresh.")
        count = session.scalar(select(func.count()).select_from(run_query().where(
            AIRun.score_metadata["requested_by"].astext == str(actor.id), AIRun.created_at >= now-timedelta(minutes=1)).subquery()))
        if count >= self.options.retry_limit_per_minute:
            raise APIError(429, "classification_retry_rate_limited", "The classification retry budget is temporarily exhausted.")
        generation = self._metadata(current)["generation"]+1 if current else 1
        if generation > 2_147_483_647:
            raise classification_unavailable()
        row = new_run(session, ticket.id, now, generation=generation, parent=expected_run_id, requested_by=actor.id)
        session.add(TicketEvent(ticket_id=ticket.id, actor_id=actor.id, event_type=TicketEventType.AI_DECISION_RECORDED,
            created_at=now, event_metadata={"action": TicketAction.CLASSIFICATION_REQUESTED.value,
                "run_id": str(row.id), "model_version": MODEL_VERSION, "requires_human_review": True}))
        session.flush()
        return self._view(row)

    def claim_next(self, *, ticket_id=None):
        with self.database.transaction() as session:
            now = session.scalar(select(func.clock_timestamp()))
            raw_lease = AIRun.score_metadata["lease_expires_at"]
            expiry = case((func.jsonb_typeof(raw_lease) == "number", cast(raw_lease.astext, Float)), else_=0.0)
            eligible = or_(AIRun.status == AIRunStatus.PENDING,
                and_(AIRun.status == AIRunStatus.RUNNING, expiry <= now.timestamp()))
            row = session.scalars(run_query(ticket_id).where(eligible).order_by(AIRun.created_at, AIRun.id)
                                  .limit(1).with_for_update(skip_locked=True)).first()
            if row is None:
                return None
            meta = self._metadata(row)
            executions = meta.get("execution_count", 0)
            if type(executions) is not int or executions < 0:
                raise classification_unavailable()
            exhausted = executions >= self.options.max_executions
            token = str(uuid4())
            row.status = AIRunStatus.RUNNING
            row.score_metadata = {**meta, "lease_token": token, "lease_expires_at": now.timestamp()+self.options.lease_seconds,
                "execution_count": executions if exhausted else executions+1, "advisory_threshold": self.options.threshold}
            ticket = session.execute(select(Ticket.subject, Ticket.description).where(Ticket.id == row.ticket_id)).one()
            return ClassificationJob(row.id, row.ticket_id, token, ticket.subject, ticket.description, exhausted)
        # The owning transaction commits before the caller can perform inference.

    def complete(self, job, outcome):
        outcome = InferenceResult.model_validate(outcome)
        with self.database.transaction() as session:
            row = session.scalars(select(AIRun).where(AIRun.id == job.id, AIRun.ticket_id == job.ticket_id)
                                  .with_for_update()).one_or_none()
            if (row is None or row.status != AIRunStatus.RUNNING
                    or row.score_metadata.get("lease_token") != job.lease_token):
                return False  # A newer lease or committed result fences out stale workers.
            meta = self._metadata(row)
            now = session.scalar(select(func.clock_timestamp()))
            successful = outcome.status == ClassificationState.SUCCEEDED
            threshold = meta["advisory_threshold"]
            row.status = AIRunStatus.SUCCEEDED if successful else AIRunStatus.FAILED
            row.predicted_intent = outcome.predicted_intent
            row.score_metadata = {key: value for key, value in meta.items() if key not in {"lease_token", "lease_expires_at"}} | {
                "confidence": outcome.confidence, "class_scores": outcome.class_scores,
                "low_score": outcome.confidence < threshold if successful else None,
                "completed_at": now.isoformat(), "duration_ms": outcome.duration_ms,
                "failure_category": outcome.failure_category.value if outcome.failure_category else None,
                "input_characters": outcome.input_characters, "input_truncated": outcome.input_truncated,
                "artifact_verified": outcome.artifact_verified}
            session.add(TicketEvent(ticket_id=row.ticket_id, actor_id=None, event_type=TicketEventType.AI_DECISION_RECORDED,
                created_at=now, event_metadata={"action": TicketAction.CLASSIFICATION_COMPLETED.value,
                    "run_id": str(row.id), "status": outcome.status.value, "model_version": row.model_version,
                    "requires_human_review": True}))
            session.flush()
            return True
