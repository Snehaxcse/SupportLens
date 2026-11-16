"""Run fixed synthetic ranking fixtures through PostgreSQL and real embeddings."""

import argparse
from contextlib import contextmanager
from hashlib import sha256
import json
from math import log2
from pathlib import Path
from statistics import mean
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.orm import Session

from ..config import Environment, load_settings
from ..db.migrations import CACHE_REVISION, KNOWLEDGE_REVISION, verify_schema
from ..db.models import User
from ..db.preflight import inspect_connection, preflight
from ..db.session import build_engine
from ..db.testing import require_test_opt_in
from ..domain.enums import UserRole
from .catalog import Catalog
from .chunking import CHUNK_FORMAT
from .embedding import LocalEmbedder
from .indexing import Indexer
from .retrieval import MIN_COSINE, RRF_K, RetrievalService
from .schemas import ArticleEdit, ArticleInput, LifecycleInput, Method, PreviewInput

FIXTURES = Path(__file__).resolve().parents[4] / "evaluation/m4_b/fixtures.json"


def metrics(ranked, relevance, k):
    scores = [relevance.get(key, 0) for key in ranked[:k]]
    recall = sum(score > 0 for score in scores) / len(relevance)
    reciprocal = next((1 / rank for rank, score in enumerate(scores, 1) if score > 0), 0)
    ideal = sorted(relevance.values(), reverse=True)[:k]
    def dcg(values):
        return sum((2 ** grade - 1) / log2(rank + 1) for rank, grade in enumerate(values, 1))
    return recall, reciprocal, dcg(scores) / dcg(ideal)


class EvaluationDatabase:
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def transaction(self, *, readonly=False):
        # A single outer TEST transaction contains all fixture writes and is always rolled back.
        with Session(bind=self.connection, join_transaction_mode="create_savepoint", expire_on_commit=False) as session:
            with session.begin():
                yield session


def evaluate(database, dataset, k):
    embedder = LocalEmbedder()
    catalog = Catalog(database, allow_synthetic=True)
    indexer = Indexer(database, embedder, allow_synthetic=True)
    retrieval = RetrievalService(database, embedder, allow_synthetic=True)
    with database.transaction() as session:
        user = User(firebase_uid="synthetic_m4b_eval_" + uuid4().hex, email="synthetic@example.invalid",
                    display_name="Synthetic retrieval evaluation", role=UserRole.AGENT)
        session.add(user)
        session.flush()
        actor = SimpleNamespace(id=user.id)

    def input_for(document):
        return dict(title=document["title"], body=document["body"], source_name="SupportLens synthetic retrieval fixtures",
                    source_type="SYNTHETIC_TEST", rights_reference="Original SupportLens test fixture. Fictional content.")

    articles = {}
    for document in dataset["documents"]:
        draft = catalog.create(actor, ArticleInput(**input_for(document)))
        published = catalog.transition(actor, draft.id, LifecycleInput(expected_generation=1, operation="PUBLISH"))
        assert indexer.process_once()
        assert catalog.detail(actor, draft.id).index_state == "READY"
        articles[draft.id] = (document, published)

    def lookup(query, method, source="SYNTHETIC_TEST"):
        return retrieval.preview(actor, PreviewInput(query=query, top_k=k, method=method, source_type=source))

    report = {}
    for method in Method:
        per_query = []
        for query in dataset["queries"]:
            result = lookup(query["text"], method)
            assert result.method == method and (method == Method.LEXICAL or result.semantic_state == "AVAILABLE")
            assert all(item.article_id in articles for item in result.items), "Evaluation catalog must be isolated."
            ranked = [articles[item.article_id][0]["key"] for item in result.items]
            per_query.append({"query": query["key"], "ranked": ranked,
                              "metrics": metrics(ranked, query["relevance"], k) if query["relevance"] else None})
        measured = [row["metrics"] for row in per_query if row["metrics"] is not None]
        report[method.value] = {
            "recall_at_k": round(mean(row[0] for row in measured), 4),
            "mrr_at_k": round(mean(row[1] for row in measured), 4),
            "ndcg_at_k": round(mean(row[2] for row in measured), 4),
            "no_result_queries_returning_empty": sum(not row["ranked"] for row in per_query if row["metrics"] is None),
            "queries": per_query,
        }
    assert not lookup("music playback", Method.HYBRID, "APPROVED_REFERENCE").items
    article_id, (document, published) = next(iter(articles.items()))
    changed = input_for(document) | {"body": "A new fictional version of the music playback fixture.",
                                     "expected_generation": published.generation}
    draft = catalog.edit(actor, article_id, ArticleEdit(**changed))
    assert all(item.article_id != article_id for item in lookup("music playback", Method.HYBRID).items)
    published = catalog.transition(actor, article_id, LifecycleInput(expected_generation=draft.generation, operation="PUBLISH"))
    assert indexer.process_once()
    hit = next(item for item in lookup("music playback", Method.HYBRID).items if item.article_id == article_id)
    assert hit.revision_id == draft.revision_id and hit.revision == 2
    catalog.transition(actor, article_id, LifecycleInput(expected_generation=published.generation, operation="UNPUBLISH"))
    assert all(item.article_id != article_id for item in lookup("music playback", Method.HYBRID).items)
    return {
        "scope": "Synthetic integration and illustrative relevance only",
        "version": dataset["version"], "documents": len(articles), "queries": len(dataset["queries"]), "top_k": k,
        "embedding_model": embedder.identity.key, "embedding_dimension": embedder.identity.dimension,
        "minimum_cosine": MIN_COSINE, "rrf_constant": RRF_K, "chunk_format": CHUNK_FORMAT,
        "source_filter_passed": True, "revision_freshness_passed": True, "unpublication_passed": True,
        "methods": report,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top-k", type=int, default=3, choices=range(1, 11))
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        require_test_opt_in(settings)
        preflight(settings)
        fixture_bytes = FIXTURES.read_bytes()
        engine = build_engine(settings, Environment.TEST)
        try:
            with engine.connect() as connection:
                transaction = connection.begin()
                try:
                    inspect_connection(connection, settings.spec(Environment.TEST), require_readonly=False)
                    if verify_schema(connection)["revision"] not in {KNOWLEDGE_REVISION, CACHE_REVISION}:
                        raise RuntimeError("The verified knowledge migration is required.")
                    result = evaluate(EvaluationDatabase(connection), json.loads(fixture_bytes), args.top_k)
                finally:
                    transaction.rollback()
        finally:
            engine.dispose()
        print(json.dumps(result | {"fixture_sha256": sha256(fixture_bytes).hexdigest()}, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
