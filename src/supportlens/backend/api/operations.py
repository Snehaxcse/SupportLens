"""Bounded operational counters. Route templates and fixed outcome names only."""
from collections import Counter
from threading import Lock

from sqlalchemy import func, select

from ..classification.constants import KIND as CLASSIFICATION_KIND
from ..db.models import AIRun
from ..drafts.service import KIND as DRAFT_KIND, DraftService
from ..knowledge.models import KnowledgeRevision


class Operations:
    def __init__(self):
        self.requests = Counter()
        self.latencies = Counter()
        self.events = Counter()
        self.lock = Lock()

    def request(self, route, status, milliseconds):
        with self.lock:
            self.requests[route, status // 100] += 1
            bucket = next((bound for bound in (50, 250, 1000, 5000) if milliseconds <= bound), "over_5000")
            self.latencies[str(bucket)] += 1

    def event(self, name):
        with self.lock:
            self.events[name] += 1

    def snapshot(self):
        with self.lock:
            return {"requests_by_route_and_status_class": {
                        f"{route} {status}xx": count for (route, status), count in self.requests.items()},
                    "latency_buckets_ms": dict(self.latencies), "events": dict(self.events)}


def queue_snapshot(database):
    with database.transaction(readonly=True) as session:
        kind = AIRun.score_metadata["kind"].astext
        runs = session.execute(select(kind, AIRun.status, func.count())
                               .where(kind.in_((CLASSIFICATION_KIND, DRAFT_KIND))).group_by(kind, AIRun.status))
        outcomes = {"classification": {}, "draft": {}}
        for run_kind, status, count in runs:
            outcomes["classification" if run_kind == CLASSIFICATION_KIND else "draft"][status.value] = count
        revisions = session.execute(select(KnowledgeRevision.index_state, func.count())
                                    .group_by(KnowledgeRevision.index_state))
        outcomes["knowledge_index"] = dict(revisions.tuples().all())
        _, reservations, exhausted = DraftService._budget(session, session.scalar(select(func.clock_timestamp())))
        outcomes["gemini"] = {"reservations_today": reservations, "provider_quota_exhausted": exhausted}
    return outcomes
