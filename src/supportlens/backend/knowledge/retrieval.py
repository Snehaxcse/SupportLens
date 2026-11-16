"""Bounded PostgreSQL lexical search, exact dense search and rank fusion."""

from dataclasses import dataclass
import re
from uuid import UUID

from sqlalchemy import and_, func, literal_column, select

from ..db.models import KnowledgeArticle, TicketMessage
from ..domain.enums import UserRole, Visibility
from ..tickets.service import TicketRepository
from .embedding import EmbeddingUnavailable
from .models import KnowledgeChunk, KnowledgeEmbedding, KnowledgeEntry, KnowledgeRevision
from .schemas import Evidence, Method, RetrievalResult

MAX_DENSE_ROWS = 2000
MIN_COSINE = 0.32
RRF_K = 60
ENGLISH = literal_column("'english'::regconfig")


@dataclass(frozen=True)
class Candidate:
    chunk_id: UUID
    article_id: UUID
    score: float


def rank_evidence(lexical, semantic, method, top_k):
    lexical_ranks = {item.chunk_id: rank for rank, item in enumerate(lexical, 1)}
    semantic_ranks = {item.chunk_id: rank for rank, item in enumerate(semantic, 1)}
    if method == Method.HYBRID:
        combined = {item.chunk_id: item for item in [*lexical, *semantic]}
        candidates = [Candidate(key, item.article_id, sum(
            1 / (RRF_K + ranks[key]) for ranks in (lexical_ranks, semantic_ranks) if key in ranks
        )) for key, item in combined.items()]
        candidates.sort(key=lambda item: (-item.score, item.article_id, item.chunk_id))
    else:
        candidates = lexical if method == Method.LEXICAL else semantic
    selected = []
    seen = set()
    for item in candidates:
        if item.article_id not in seen:
            seen.add(item.article_id)
            selected.append((item, lexical_ranks.get(item.chunk_id), semantic_ranks.get(item.chunk_id)))
            if len(selected) == top_k:
                break
    return selected


def cosine_candidates(rows, query_vector):
    import numpy as np

    normalized = np.asarray(query_vector, dtype=np.float32)
    vectors = np.asarray([row.vector for row in rows], dtype=np.float32)
    if not len(vectors):
        return []
    lengths = np.linalg.norm(vectors, axis=1) * np.linalg.norm(normalized)
    scores = np.divide(vectors @ normalized, lengths, out=np.zeros_like(lengths), where=lengths > 0)
    items = [Candidate(row.chunk_id, row.article_id, float(score)) for row, score in zip(rows, scores, strict=True)
             if np.isfinite(score) and score >= MIN_COSINE]
    return sorted(items, key=lambda item: (-item.score, item.article_id, item.chunk_id))


def lexical_terms(text):
    terms = dict.fromkeys(word[:40] for word in re.findall(r"[^\W_]+", text.casefold()) if len(word) >= 2)
    return " OR ".join(f'"{word}"' for word in list(terms)[:48])


def ticket_query(subject, description, public_messages):
    # Bound long unbroken strings and preserve space for the latest public context.
    def words(text, limit):
        return " ".join(token[:40] for token in text.split()[:limit])

    return "\n".join([words(subject, 24), *(words(body[:480], 20) for body in public_messages),
                      words(description[:1200], 48)])[:4000]


def evidence_rows(options, allow_synthetic):
    query = (
        select(
            KnowledgeChunk.id.label("chunk_id"), KnowledgeEntry.id.label("article_id"),
            KnowledgeRevision.version.label("revision"), KnowledgeRevision.id.label("revision_id"),
            KnowledgeChunk.content_hash, KnowledgeChunk.text.label("excerpt"), KnowledgeArticle.title,
            KnowledgeArticle.source_reference.label("source_name"), KnowledgeRevision.source_type,
            KnowledgeRevision.source_url, KnowledgeRevision.index_model,
        ).select_from(KnowledgeChunk)
        .join(KnowledgeRevision, KnowledgeRevision.id == KnowledgeChunk.revision_id)
        .join(KnowledgeArticle, KnowledgeArticle.id == KnowledgeRevision.id)
        .join(KnowledgeEntry, and_(KnowledgeEntry.id == KnowledgeRevision.entry_id,
                                  KnowledgeEntry.current_version == KnowledgeRevision.version))
        .where(KnowledgeEntry.status == "PUBLISHED", KnowledgeRevision.index_state.in_(["READY", "LEXICAL_ONLY"]))
    )
    if not allow_synthetic:
        query = query.where(KnowledgeEntry.synthetic.is_(False))
    if options.source_type:
        query = query.where(KnowledgeRevision.source_type == options.source_type)
    return query


class RetrievalService:
    def __init__(self, database, embedder, *, allow_synthetic=False):
        self.database = database
        self.embedder = embedder
        self.allow_synthetic = allow_synthetic

    def for_ticket(self, actor, ticket_id, options):
        with self.database.transaction(readonly=True) as session:
            repository = TicketRepository(session, actor, UserRole.AGENT)
            ticket = repository.ticket(ticket_id)
            recent = list(session.scalars(
                select(TicketMessage.body).where(TicketMessage.ticket_id == ticket_id,
                                                TicketMessage.visibility == Visibility.PUBLIC)
                .order_by(TicketMessage.created_at.desc(), TicketMessage.id.desc()).limit(2)
            ))
            query = ticket_query(ticket.subject, ticket.description, recent)
        return self.search(actor, query, options, ticket_id=ticket_id)

    def preview(self, actor, options):
        # Authorize before spending CPU, then recheck authorization when reading evidence.
        with self.database.transaction(readonly=True) as session:
            TicketRepository(session, actor, UserRole.AGENT)
        return self.search(actor, options.query, options)

    def search(self, actor, query, options, *, ticket_id=None):
        requested = options.method
        state = "NOT_REQUESTED"
        query_vector = None
        if requested != Method.LEXICAL:
            try:
                query_vector = self.embedder.encode([query])[0]
                state = "AVAILABLE"
            except EmbeddingUnavailable:
                state = "UNAVAILABLE"
        rows_by_id = {}
        lexical = []
        semantic = []
        with self.database.transaction(readonly=True) as session:
            repository = TicketRepository(session, actor, UserRole.AGENT)
            if ticket_id is not None:
                repository.ticket(ticket_id)
            base = evidence_rows(options, self.allow_synthetic)
            terms = lexical_terms(query)
            if terms and requested != Method.SEMANTIC:
                tsquery = func.websearch_to_tsquery(ENGLISH, terms)
                document = func.to_tsvector(ENGLISH, KnowledgeChunk.search_text)
                score = func.ts_rank_cd(document, tsquery, 32)
                lex_rows = session.execute(
                    base.add_columns(score.label("score")).where(document.op("@@")(tsquery))
                    .order_by(score.desc(), KnowledgeEntry.id, KnowledgeChunk.id).limit(100)
                ).all()
                rows_by_id.update({row.chunk_id: row for row in lex_rows})
                lexical = [Candidate(row.chunk_id, row.article_id, row.score) for row in lex_rows]
            if query_vector is not None:
                dense_rows = session.execute(
                    base.add_columns(KnowledgeEmbedding.vector).join(KnowledgeEmbedding, and_(
                        KnowledgeEmbedding.content_hash == KnowledgeChunk.embedding_hash,
                        KnowledgeEmbedding.model_key == self.embedder.identity.key,
                    )).where(KnowledgeRevision.index_model == self.embedder.identity.key,
                             KnowledgeRevision.index_state == "READY")
                    .order_by(KnowledgeChunk.id).limit(MAX_DENSE_ROWS + 1)
                ).all()
                if len(dense_rows) > MAX_DENSE_ROWS:
                    state = "CORPUS_LIMIT"
                elif not dense_rows:
                    state = "EMPTY_INDEX"
                else:
                    rows_by_id.update({row.chunk_id: row for row in dense_rows})
                    semantic = cosine_candidates(dense_rows, query_vector)[:100]
            method = Method.LEXICAL if requested == Method.HYBRID and state != "AVAILABLE" else requested
            ranked = rank_evidence(lexical, semantic, method, options.top_k)
            # Under READ COMMITTED, publication might have changed between candidate statements.
            # A final eligibility read prevents returning any superseded or unpublished candidate.
            fresh = {row.chunk_id: row for row in session.execute(
                base.where(KnowledgeChunk.id.in_([item.chunk_id for item, _, _ in ranked]))
            )} if ranked else {}
        score_type = {Method.HYBRID: "RECIPROCAL_RANK_FUSION", Method.SEMANTIC: "COSINE_SIMILARITY",
                      Method.LEXICAL: "POSTGRES_TEXT_RANK"}[method]
        items = []
        for candidate, lexical_rank, semantic_rank in ranked:
            row = fresh.get(candidate.chunk_id)
            if row is None or (semantic_rank is not None and row.index_model != rows_by_id[candidate.chunk_id].index_model):
                continue
            items.append(Evidence(
                article_id=row.article_id, revision=row.revision, revision_id=row.revision_id, chunk_id=row.chunk_id,
                content_hash=row.content_hash, title=row.title, excerpt=row.excerpt, source_name=row.source_name,
                source_type=row.source_type, source_url=row.source_url, method=method, rank=len(items) + 1,
                score=candidate.score, score_type=score_type, lexical_rank=lexical_rank, semantic_rank=semantic_rank,
                embedding_model=row.index_model if semantic_rank is not None else None,
            ))
        return RetrievalResult(ticket_id=ticket_id, requested_method=requested, method=method,
                               semantic_state=state, items=items)
