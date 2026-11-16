"""Recoverable jobs with publication fencing and a content-addressed vector store."""

from dataclasses import dataclass
from datetime import timedelta
import json
import logging
from threading import Event, Thread
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert

from ..db.models import KnowledgeArticle
from .chunking import chunks
from .embedding import EmbeddingUnavailable
from .models import KnowledgeChunk, KnowledgeEmbedding, KnowledgeEntry, KnowledgeRevision

logger = logging.getLogger("supportlens.knowledge")


@dataclass(frozen=True)
class IndexJob:
    entry_id: UUID
    revision_id: UUID
    version: int
    generation: int
    lease: UUID
    title: str
    body: str


class Indexer:
    def __init__(self, database, embedder, *, allow_synthetic=False):
        self.database = database
        self.embedder = embedder
        self.allow_synthetic = allow_synthetic

    def claim(self, *, entry_id=None):
        with self.database.transaction() as session:
            now = session.scalar(select(func.clock_timestamp()))
            query = select(KnowledgeEntry).join(
                KnowledgeRevision, and_(KnowledgeRevision.entry_id == KnowledgeEntry.id,
                                        KnowledgeRevision.version == KnowledgeEntry.current_version),
            ).where(
                KnowledgeEntry.status == "PUBLISHED",
                or_(KnowledgeRevision.index_state == "PENDING",
                    and_(KnowledgeRevision.index_state == "RUNNING", KnowledgeRevision.lease_until <= now)),
            )
            if not self.allow_synthetic:
                query = query.where(KnowledgeEntry.synthetic.is_(False))
            if entry_id is not None:
                query = query.where(KnowledgeEntry.id == entry_id)
            entry = session.scalar(query.order_by(KnowledgeEntry.updated_at, KnowledgeEntry.id)
                                   .limit(1).with_for_update(of=KnowledgeEntry, skip_locked=True))
            if entry is None:
                return None
            # A concurrent claim can commit its revision update before we acquire
            # the entry lock, without updating the entry itself. Recheck the
            # revision in a fresh READ COMMITTED statement under that lock.
            revision, article = self._locked_revision(session, entry)
            pending = revision.index_state == "PENDING"
            expired = revision.index_state == "RUNNING" and revision.lease_until <= now
            if not (pending or expired):
                return None
            if revision.attempts >= 3:
                revision.index_state = "FAILED"
                revision.lease_token = revision.lease_until = None
                return True
            lease = uuid4()
            revision.index_state = "RUNNING"
            revision.attempts += 1
            revision.lease_token, revision.lease_until = lease, now + timedelta(minutes=5)
            return IndexJob(entry.id, revision.id, revision.version, entry.generation, lease, article.title, article.content)

    @staticmethod
    def _locked_revision(session, entry):
        return session.execute(
            select(KnowledgeRevision, KnowledgeArticle)
            .join(KnowledgeArticle, KnowledgeArticle.id == KnowledgeRevision.id)
            .where(KnowledgeRevision.entry_id == entry.id, KnowledgeRevision.version == entry.current_version)
        ).one()

    def prepare(self, job):
        items = chunks(job.revision_id, job.title, job.body)
        model = self.embedder.identity.key
        inputs = {item.embedding_hash: item.search_text for item in items}
        with self.database.transaction(readonly=True) as session:
            known = set(session.scalars(select(KnowledgeEmbedding.content_hash).where(
                KnowledgeEmbedding.content_hash.in_(inputs), KnowledgeEmbedding.model_key == model,
            )))
        missing = [(key, value) for key, value in inputs.items() if key not in known]
        vectors = {}
        try:
            for offset in range(0, len(missing), 16):
                batch = missing[offset:offset + 16]
                embedded = self.embedder.encode([value for _, value in batch])
                vectors.update((key, vector) for (key, _), vector in zip(batch, embedded, strict=True))
        except EmbeddingUnavailable:
            return items, {}, None
        return items, vectors, model

    def complete(self, job, items, vectors, model):
        with self.database.transaction() as session:
            entry = session.scalar(select(KnowledgeEntry).where(KnowledgeEntry.id == job.entry_id).with_for_update())
            if (entry is None or entry.current_version != job.version or entry.generation != job.generation
                    or entry.status != "PUBLISHED"):
                return False
            revision = session.get(KnowledgeRevision, job.revision_id)
            if revision.index_state != "RUNNING" or revision.lease_token != job.lease:
                return False
            if vectors:
                session.execute(insert(KnowledgeEmbedding).values([
                    {"content_hash": key, "model_key": model, "vector": vector}
                    for key, vector in sorted(vectors.items())
                ]).on_conflict_do_nothing())
            if items:
                session.execute(insert(KnowledgeChunk).values([
                    {"id": item.id, "revision_id": revision.id, "ordinal": item.ordinal,
                     "text": item.text, "search_text": item.search_text, "content_hash": item.content_hash,
                     "embedding_hash": item.embedding_hash}
                    for item in items
                ]).on_conflict_do_nothing())
            revision.index_state = "READY" if model else "LEXICAL_ONLY"
            revision.index_model = model
            revision.lease_token = revision.lease_until = None
            return True

    def process_once(self):
        job = self.claim()
        if job is None:
            return False
        if job is not True:
            self.complete(job, *self.prepare(job))
        return True


class IndexWorker:
    def __init__(self, indexer, poll_seconds):
        self.indexer = indexer
        self.poll_seconds = poll_seconds
        self._wake = Event()
        self._stop = Event()
        self._thread = None

    def wake(self):
        self._wake.set()

    def start(self):
        self._stop.clear()
        self._thread = Thread(target=self._loop, name="supportlens-knowledge", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self.wake()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def _loop(self):
        while not self._stop.is_set():
            self._wake.clear()
            try:
                if self.indexer.process_once():
                    continue
            except Exception:
                logger.warning(json.dumps({"event": "knowledge_index_delay", "category": "PROCESSING_UNAVAILABLE"}))
            self._wake.wait(self.poll_seconds)
