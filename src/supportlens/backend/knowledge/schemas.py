"""Bounded agent contracts and an explicit evidence projection for later consumers."""

from datetime import datetime
from enum import StrEnum
import re
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ArticleStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class SourceType(StrEnum):
    APPROVED_REFERENCE = "APPROVED_REFERENCE"
    INTERNAL_PROCEDURE = "INTERNAL_PROCEDURE"
    SYNTHETIC_TEST = "SYNTHETIC_TEST"


class Method(StrEnum):
    LEXICAL = "lexical"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"


class Operation(StrEnum):
    PUBLISH = "PUBLISH"
    UNPUBLISH = "UNPUBLISH"
    ARCHIVE = "ARCHIVE"
    REINDEX = "REINDEX"


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True)

    @field_validator("*", mode="before")
    @classmethod
    def safe_string(cls, value):
        if isinstance(value, str) and re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]", value):
            raise ValueError("Text contains unsafe characters.")
        return value


class ArticleInput(Input):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=100_000)
    source_name: str = Field(min_length=1, max_length=300)
    source_type: SourceType
    source_url: str | None = Field(default=None, max_length=2048)
    rights_reference: str = Field(min_length=1, max_length=500)

    @field_validator("source_url")
    @classmethod
    def https_source(cls, value):
        if value is None:
            return value
        url = urlsplit(value)
        if (re.search(r"[\s\\]", value) or url.scheme != "https" or not url.hostname
                or url.username is not None or url.password is not None or url.port not in (None, 443)):
            raise ValueError("Use an HTTPS source URL without credentials.")
        return value

    @model_validator(mode="after")
    def source_requirements(self):
        if self.source_type == SourceType.APPROVED_REFERENCE and not self.source_url:
            raise ValueError("Approved external references require a source URL.")
        if self.source_type == SourceType.SYNTHETIC_TEST and self.source_url:
            raise ValueError("Synthetic fixtures cannot claim an external source.")
        return self


class ArticleEdit(ArticleInput):
    expected_generation: int = Field(ge=1, strict=True)


class LifecycleInput(Input):
    expected_generation: int = Field(ge=1, strict=True)
    operation: Operation


class ArticleFilters(Input):
    q: str = Field(default="", max_length=200)
    status: ArticleStatus | None = None
    source_type: SourceType | None = None
    page: int = Field(default=1, ge=1, le=1000)
    page_size: int = Field(default=20, ge=1, le=50)


class ArticleSummary(BaseModel):
    id: UUID
    title: str
    status: ArticleStatus
    revision: int
    revision_id: UUID
    generation: int
    source_name: str
    source_type: SourceType
    source_url: str | None
    index_state: str
    retrieval_eligible: bool
    updated_at: datetime


class ArticleDetail(ArticleSummary):
    body: str
    rights_reference: str
    created_by: UUID
    editor_id: UUID
    changed_by: UUID
    created_at: datetime
    revision_created_at: datetime
    index_model: str | None


class RevisionSummary(BaseModel):
    revision: int
    revision_id: UUID
    editor_id: UUID
    title: str
    source_name: str
    created_at: datetime


class ArticlePage(BaseModel):
    items: list[ArticleSummary]
    page: int
    page_size: int
    total: int


class RevisionPage(BaseModel):
    items: list[RevisionSummary]
    page: int
    page_size: int
    total: int


class RetrievalOptions(Input):
    method: Method = Method.HYBRID
    top_k: int = Field(default=5, ge=1, le=10, strict=True)
    source_type: SourceType | None = None


class PreviewInput(RetrievalOptions):
    query: str = Field(min_length=1, max_length=2000)


class Evidence(BaseModel):
    article_id: UUID
    revision: int
    revision_id: UUID
    chunk_id: UUID
    content_hash: str
    title: str
    excerpt: str
    source_name: str
    source_type: SourceType
    source_url: str | None
    method: Method
    rank: int
    score: float = Field(allow_inf_nan=False)
    score_type: str
    lexical_rank: int | None
    semantic_rank: int | None
    embedding_model: str | None


class RetrievalResult(BaseModel):
    ticket_id: UUID | None
    requested_method: Method
    method: Method
    semantic_state: str
    items: list[Evidence]
