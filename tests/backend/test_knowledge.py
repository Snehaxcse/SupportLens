from math import isclose, sqrt
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from supportlens.backend.api.app import create_app
from supportlens.backend.api.errors import unauthorized
from supportlens.backend.knowledge.chunking import chunks, digest, normalize
from supportlens.backend.knowledge.embedding import LocalEmbedder
from supportlens.backend.knowledge.retrieval import Candidate, lexical_terms, rank_evidence
from supportlens.backend.knowledge.schemas import ArticleInput, LifecycleInput, Method
from tests.backend.test_auth_api import api_settings


def article(**fields):
    return dict(title="Synthetic article", body="Fictional verification body.",
                source_name="SupportLens tests", source_type="SYNTHETIC_TEST",
                rights_reference="Original synthetic fixture.") | fields


@pytest.mark.parametrize("fields", [
    {"title": " "}, {"body": "unsafe\x00text"}, {"author_id": "forged"},
    {"status": "PUBLISHED"}, {"source_url": "https://example.invalid"},
    {"source_type": "APPROVED_REFERENCE", "source_url": None},
    {"source_type": "APPROVED_REFERENCE", "source_url": "javascript:alert(1)"},
    {"source_type": "APPROVED_REFERENCE", "source_url": "https://me@example.invalid"},
    {"source_type": "APPROVED_REFERENCE", "source_url": "https://example.invalid\\evil"},
    {"source_type": "APPROVED_REFERENCE", "source_url": "https://example.invalid:8080"},
])
def test_article_rejects_unsafe_and_privileged_input(fields):
    with pytest.raises(ValidationError):
        ArticleInput.model_validate(article(**fields))


def test_lifecycle_generation_must_be_strict():
    with pytest.raises(ValidationError):
        LifecycleInput.model_validate({"expected_generation": "1", "operation": "PUBLISH"})


def test_chunking_is_deterministic_deduplicated_and_revision_bound():
    first = UUID(int=1)
    second = UUID(int=2)
    text = "# Setup\r\n" + "long fictional procedure " * 60 + "\n\n# Repeated\nDuplicate\n# Repeated\nDuplicate"
    a = chunks(first, "Title", text)
    b = chunks(first, "Title", normalize(text))
    c = chunks(second, "Title", text)
    assert a == b
    assert len(a) >= 3 and all(0 < len(item.text) <= 900 for item in a)
    assert sum(item.text == "# Repeated\nDuplicate" for item in a) == 1
    assert all(item.text.startswith("# Setup") for item in a if "procedure" in item.text)
    assert {row.id for row in a}.isdisjoint({row.id for row in c})
    assert [item.embedding_hash for item in a] == [item.embedding_hash for item in c]
    assert all(item.content_hash == digest(item.text) for item in a)


def test_ranking_combines_channels_and_deduplicates_by_article():
    a, b, c = Candidate(UUID(int=1), UUID(int=10), .4), Candidate(UUID(int=2), UUID(int=20), .3), Candidate(UUID(int=3), UUID(int=10), .2)
    ranks = rank_evidence([b, a, c], [a, c, b], Method.HYBRID, 3)
    assert [row.article_id for row, _, _ in ranks] == [a.article_id, b.article_id]
    assert ranks[0][1:] == (2, 1)
    assert lexical_terms('playback "" - music OR playback') == '"playback" OR "music" OR "or"'


def test_real_model_paraphrase_and_shape():
    embedder = LocalEmbedder()
    vectors = embedder.encode(["Music playback stops mid-song.", "Songs stop playing halfway.", "Change the payment method."])
    assert len(vectors) == 3 and all(len(vector) == 384 for vector in vectors)
    assert all(isclose(sqrt(sum(x * x for x in vector)), 1, abs_tol=1e-5) for vector in vectors)
    first, paraphrase, unrelated = vectors
    assert sum(a * b for a, b in zip(first, paraphrase)) > sum(a * b for a, b in zip(first, unrelated))
    assert embedder.encode(["Music playback stops mid-song."])[0] == pytest.approx(first, abs=1e-5)


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/knowledge", None), ("POST", "/knowledge", article()),
    ("GET", "/knowledge/00000000-0000-0000-0000-000000000001", None),
    ("PATCH", "/knowledge/00000000-0000-0000-0000-000000000001", article(expected_generation=1)),
    ("POST", "/knowledge/00000000-0000-0000-0000-000000000001/lifecycle", {"expected_generation": 1, "operation": "PUBLISH"}),
    ("GET", "/knowledge/00000000-0000-0000-0000-000000000001/revisions", None),
    ("GET", "/tickets/00000000-0000-0000-0000-000000000001/knowledge", None),
    ("POST", "/knowledge/retrieval-preview", {"query": "playback"}),
])
def test_agent_boundary_precedes_all_knowledge_operations(api_settings, method, path, body):
    class Verifier:
        def verify(self, token):
            if token != "fake.customer.signature":
                raise unauthorized()
            return SimpleNamespace(uid="fake")

    runtime = SimpleNamespace(database=object(), verifier=Verifier(),
                              users=SimpleNamespace(current=lambda _: SimpleNamespace(role="CUSTOMER")))
    client = TestClient(create_app(api_settings, runtime=runtime))
    path = "/api/v1/agent" + path
    payload = {"json": body} if body is not None else {}
    assert client.request(method, path, **payload).status_code == 401
    assert client.request(method, path, headers={"Authorization": "Bearer fake.customer.signature"}, **payload).status_code == 403
