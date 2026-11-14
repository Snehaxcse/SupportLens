"""Recovery orchestration never computes within a database operation."""
from unittest.mock import Mock
import pytest
from pydantic import ValidationError
from supportlens.backend.api.config import APISettings
from .test_auth_api import api_settings
from uuid import uuid4

from supportlens.backend.classification.schemas import ClassificationState, FailureCategory, InferenceResult
from supportlens.backend.classification.service import ClassificationJob
from supportlens.backend.classification.worker import ClassificationWorker
from supportlens.backend.tickets.service import TicketService


def job(exhausted=False):
    return ClassificationJob(uuid4(), uuid4(), str(uuid4()), "Synthetic", "Synthetic description", exhausted)


def test_worker_claims_then_computes_then_publishes_without_transaction():
    sequence = []
    store = Mock()
    store.claim_next.side_effect = lambda **kwargs: (sequence.append("committed_claim"), job())[1]
    def classify(*args):
        assert sequence == ["committed_claim"]
        sequence.append("inference")
        return InferenceResult(status=ClassificationState.UNAVAILABLE, failure_category=FailureCategory.MODEL_MISSING, duration_ms=0)
    store.complete.side_effect = lambda *args: sequence.append("publish")
    assert ClassificationWorker(store, Mock(classify=classify)).process_once()
    assert sequence == ["committed_claim", "inference", "publish"]


def test_empty_queue_does_not_load_model_or_publish():
    store, inference = Mock(), Mock()
    store.claim_next.return_value = None
    assert not ClassificationWorker(store, inference).process_once()
    inference.classify.assert_not_called()
    store.complete.assert_not_called()


def test_exhausted_recovery_becomes_failure_without_more_model_execution():
    store, inference = Mock(), Mock()
    store.claim_next.return_value = job(True)
    assert ClassificationWorker(store, inference).process_once()
    inference.classify.assert_not_called()
    outcome = store.complete.call_args.args[1]
    assert outcome.failure_category == FailureCategory.RECOVERY_EXHAUSTED and outcome.requires_human_review


def test_wake_failure_cannot_undo_committed_ticket():
    service = TicketService(object(), on_created=Mock(side_effect=RuntimeError("synthetic wake failure")))
    retained = object()
    service.run = Mock(return_value=retained)
    assert service.create(object(), object()) is retained
    service.run.assert_called_once()


def test_failed_ticket_transaction_does_not_wake_worker():
    wake = Mock()
    service = TicketService(object(), on_created=wake)
    service.run = Mock(side_effect=RuntimeError("synthetic rollback"))
    try:
        service.create(object(), object())
    except RuntimeError:
        pass
    wake.assert_not_called()


@pytest.mark.parametrize("field,value", [("classification_low_score_threshold", float("nan")),
    ("classification_low_score_threshold", 1.1), ("classification_poll_seconds", 0),
    ("classification_lease_seconds", 0), ("classification_max_executions", 0),
    ("classification_retry_cooldown_seconds", 0), ("classification_retry_limit_per_minute", 0)])
def test_classification_configuration_is_bounded(api_settings, field, value):
    with pytest.raises(ValidationError):
        APISettings(_env_file=None, **(api_settings.model_dump() | {field: value}))
