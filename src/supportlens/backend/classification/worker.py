"""Recoverable queue processor. Inference is outside transactions and the event loop."""
import argparse
import json
import logging
import threading

from .inference import InferenceService
from .schemas import ClassificationState, FailureCategory, InferenceResult
from .service import ClassificationOptions, ClassificationService

logger = logging.getLogger("supportlens.classification")


class ClassificationWorker:
    def __init__(self, store, inference, *, poll_seconds=30):
        self.store, self.inference, self.poll_seconds = store, inference, poll_seconds
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread = None
        self._start_lock = threading.Lock()
        self.observe = lambda event: None

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive() and not self._stopping.is_set())

    def process_once(self, *, ticket_id=None):
        job = self.store.claim_next(ticket_id=ticket_id)
        if job is None:
            return False
        # claim_next has committed and released its session/locks before returning.
        result = (InferenceResult(status=ClassificationState.FAILED, failure_category=FailureCategory.RECOVERY_EXHAUSTED,
                                  duration_ms=0)
                  if job.recovery_exhausted else self.inference.classify(job.subject, job.description))
        if self.store.complete(job, result):
            self.observe("classification_" + result.status.value.lower())
            if job.recovery_exhausted:
                self.observe("classification_recovery_exhausted")
            elif job.recovered_lease:
                self.observe("classification_lease_recovered")
        return True

    def wake(self):
        self._wake.set()

    def start(self):
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stopping.clear()
            self._thread = threading.Thread(target=self._loop, name="supportlens-classification", daemon=True)
            self._thread.start()

    def stop(self):
        self._stopping.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
        # Process death/in-flight interruption leaves a durable lease, recovered on restart.

    def _loop(self):
        while not self._stopping.is_set():
            self._wake.clear()
            try:
                if self.process_once():
                    continue
            except Exception:
                self.observe("classification_worker_failure")
                # Driver/model errors and ticket text must never enter logs.
                logger.warning(json.dumps({"event": "classification_worker_retry", "category": "PROCESSING_UNAVAILABLE"}))
            self._wake.wait(self.poll_seconds)


def main(argv=None):
    from ..api.config import load_api_settings
    from ..api.database import Database
    from ..config import Environment
    from ..db.testing import require_test_opt_in
    parser = argparse.ArgumentParser(description="Process durable advisory classification jobs; no retraining.")
    parser.add_argument("--target", choices=("development", "test"), required=True)
    parser.add_argument("--max-jobs", type=int, default=100)
    args = parser.parse_args(argv)
    database = None
    try:
        settings = load_api_settings()
        target = Environment(args.target)
        assert settings.environment == target and 1 <= args.max_jobs <= 1000
        settings.spec(target)
        if target == Environment.TEST:
            require_test_opt_in(settings)
        database = Database(settings)
        database.ready()
        worker = ClassificationWorker(ClassificationService(database, ClassificationOptions.from_settings(settings)),
            InferenceService(enabled=settings.classification_enabled))
        processed = 0
        while processed < args.max_jobs and worker.process_once():
            processed += 1
        print(json.dumps({"status": "completed", "processed_jobs": processed, "target": target.value}))
        return 0
    except Exception:
        print(json.dumps({"status": "unavailable", "details_suppressed": True}))
        return 1
    finally:
        if database is not None:
            database.close()


if __name__ == "__main__":
    raise SystemExit(main())
