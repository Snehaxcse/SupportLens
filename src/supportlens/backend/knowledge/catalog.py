"""Revision creation and publication use the same locked catalog record."""

from sqlalchemy import func, or_, select

from ..api.errors import APIError
from ..db.models import KnowledgeArticle
from ..domain.enums import UserRole
from ..tickets.service import TicketRepository
from .models import KnowledgeEntry, KnowledgeEntryEvent, KnowledgeRevision
from .schemas import (
    ArticleDetail, ArticleFilters, ArticleInput, ArticlePage, ArticleStatus,
    ArticleSummary, Operation, RevisionPage, RevisionSummary, SourceType,
)


def active_rows():
    return (
        select(KnowledgeEntry, KnowledgeRevision, KnowledgeArticle)
        .join(KnowledgeRevision, (KnowledgeRevision.entry_id == KnowledgeEntry.id)
              & (KnowledgeRevision.version == KnowledgeEntry.current_version))
        .join(KnowledgeArticle, KnowledgeArticle.id == KnowledgeRevision.id)
    )


class Catalog:
    def __init__(self, database, *, allow_synthetic=False, wake=None):
        self.database = database
        self.allow_synthetic = allow_synthetic
        self.wake = wake

    def _summary(self, entry, revision, article):
        return ArticleSummary(
            id=entry.id, title=article.title, status=entry.status, revision=revision.version,
            revision_id=revision.id, generation=entry.generation,
            source_name=article.source_reference, source_type=revision.source_type,
            source_url=revision.source_url, index_state=revision.index_state,
            retrieval_eligible=(entry.status == ArticleStatus.PUBLISHED
                                 and revision.version == entry.current_version
                                 and revision.index_state in {"READY", "LEXICAL_ONLY"}
                                 and (self.allow_synthetic or not entry.synthetic)),
            updated_at=entry.updated_at,
        )

    def _detail(self, entry, revision, article):
        return ArticleDetail(
            **self._summary(entry, revision, article).model_dump(), body=article.content,
            rights_reference=revision.rights_reference, created_by=entry.created_by,
            editor_id=revision.editor_id, changed_by=entry.changed_by,
            created_at=entry.created_at, revision_created_at=article.created_at, index_model=revision.index_model,
        )

    @staticmethod
    def _entry(session, entry_id, *, lock=False):
        statement = select(KnowledgeEntry).where(KnowledgeEntry.id == entry_id)
        entry = session.scalar(statement.with_for_update() if lock else statement)
        if entry is None:
            raise APIError(404, "knowledge_not_found", "The knowledge article was not found.")
        return entry

    @staticmethod
    def _revision(session, entry, version=None, *, lock=False):
        statement = (
            select(KnowledgeRevision, KnowledgeArticle)
            .join(KnowledgeArticle, KnowledgeArticle.id == KnowledgeRevision.id)
            .where(KnowledgeRevision.entry_id == entry.id,
                   KnowledgeRevision.version == (version if version is not None else entry.current_version))
        )
        if lock:
            statement = statement.with_for_update(of=KnowledgeRevision)
        row = session.execute(statement).one_or_none()
        if row is None:
            raise APIError(404, "knowledge_revision_not_found", "The article revision was not found.")
        return row

    @staticmethod
    def _check_editable(entry, expected):
        if entry.generation != expected:
            raise APIError(409, "knowledge_changed", "The article changed. Reload it before trying again.")
        if entry.status == ArticleStatus.ARCHIVED:
            raise APIError(409, "knowledge_archived", "Archived articles are read-only.")

    @staticmethod
    def _record(session, entry, revision, actor_id, action):
        session.add(KnowledgeEntryEvent(entry_id=entry.id, revision_id=revision.id,
                                       actor_id=actor_id, generation=entry.generation,
                                       action=action, status=entry.status))

    @staticmethod
    def _insert_revision(session, entry, actor_id, data: ArticleInput, now):
        article = KnowledgeArticle(title=data.title, content=data.body, source_reference=data.source_name,
                                   version=entry.current_version, is_active=False, created_at=now, updated_at=now)
        session.add(article)
        session.flush()
        revision = KnowledgeRevision(id=article.id, entry_id=entry.id, version=article.version, editor_id=actor_id,
                                     source_type=data.source_type, source_url=data.source_url,
                                     rights_reference=data.rights_reference, index_state="IDLE", attempts=0)
        session.add(revision)
        session.flush()
        return revision, article

    def create(self, actor, data):
        with self.database.transaction() as session:
            repository = TicketRepository(session, actor, UserRole.AGENT, write=True)
            now = repository.now()
            entry = KnowledgeEntry(current_version=1, generation=1, status=ArticleStatus.DRAFT,
                                   synthetic=data.source_type == SourceType.SYNTHETIC_TEST,
                                   created_by=actor.id, changed_by=actor.id, created_at=now, updated_at=now)
            session.add(entry)
            session.flush()
            revision, article = self._insert_revision(session, entry, actor.id, data, now)
            self._record(session, entry, revision, actor.id, "CREATE")
            return self._detail(entry, revision, article)

    def edit(self, actor, entry_id, data):
        with self.database.transaction() as session:
            repository = TicketRepository(session, actor, UserRole.AGENT, write=True)
            entry = self._entry(session, entry_id, lock=True)
            self._check_editable(entry, data.expected_generation)
            if entry.synthetic != (data.source_type == SourceType.SYNTHETIC_TEST):
                raise APIError(422, "knowledge_source_change", "Synthetic status cannot be changed.")
            previous, _ = self._revision(session, entry, lock=True)
            if previous.index_state in {"PENDING", "RUNNING"}:
                previous.index_state = "IDLE"
                previous.lease_token = previous.lease_until = None
            now = repository.now()
            entry.generation += 1
            entry.current_version += 1
            entry.status = ArticleStatus.DRAFT
            entry.changed_by, entry.updated_at = actor.id, now
            revision, article = self._insert_revision(session, entry, actor.id, data, now)
            self._record(session, entry, revision, actor.id, "EDIT")
            return self._detail(entry, revision, article)

    def transition(self, actor, entry_id, data):
        operation = data.operation
        invoke_worker = False
        with self.database.transaction() as session:
            repository = TicketRepository(session, actor, UserRole.AGENT, write=True)
            entry = self._entry(session, entry_id, lock=True)
            self._check_editable(entry, data.expected_generation)
            revision, article = self._revision(session, entry, lock=True)
            expected = {
                Operation.PUBLISH: ArticleStatus.DRAFT, Operation.UNPUBLISH: ArticleStatus.PUBLISHED,
                Operation.REINDEX: ArticleStatus.PUBLISHED,
            }.get(operation)
            if expected is not None and entry.status != expected:
                raise APIError(409, "knowledge_transition", "This action is unavailable for the current article status.")
            if operation == Operation.PUBLISH and entry.synthetic and not self.allow_synthetic:
                raise APIError(409, "knowledge_synthetic", "Synthetic publication requires explicit local test configuration.")
            if operation == Operation.REINDEX and revision.index_state in {"PENDING", "RUNNING"}:
                raise APIError(409, "knowledge_index_active", "An index job is already active.")
            if operation in {Operation.PUBLISH, Operation.REINDEX}:
                entry.status = ArticleStatus.PUBLISHED
                revision.index_state = "PENDING"
                invoke_worker = True
            else:
                entry.status = ArticleStatus.ARCHIVED if operation == Operation.ARCHIVE else ArticleStatus.DRAFT
                if revision.index_state in {"PENDING", "RUNNING"}:
                    revision.index_state = "IDLE"
            revision.lease_token = revision.lease_until = None
            revision.attempts = 0
            entry.generation += 1
            entry.changed_by, entry.updated_at = actor.id, repository.now()
            self._record(session, entry, revision, actor.id, operation)
            result = self._detail(entry, revision, article)
        if invoke_worker and self.wake is not None:
            try:
                self.wake()
            except Exception:
                pass  # The committed job remains available to the polling worker.
        return result

    def detail(self, actor, entry_id, version=None):
        with self.database.transaction(readonly=True) as session:
            TicketRepository(session, actor, UserRole.AGENT)
            entry = self._entry(session, entry_id)
            revision, article = self._revision(session, entry, version)
            return self._detail(entry, revision, article)

    def list(self, actor, filters: ArticleFilters):
        with self.database.transaction(readonly=True) as session:
            TicketRepository(session, actor, UserRole.AGENT)
            query = active_rows()
            if filters.status:
                query = query.where(KnowledgeEntry.status == filters.status)
            if filters.source_type:
                query = query.where(KnowledgeRevision.source_type == filters.source_type)
            if filters.q:
                query = query.where(or_(*(field.icontains(filters.q, autoescape=True) for field in (
                    KnowledgeArticle.title, KnowledgeArticle.source_reference, KnowledgeArticle.content,
                ))))
            total = session.scalar(select(func.count()).select_from(
                query.with_only_columns(KnowledgeEntry.id, maintain_column_froms=True).subquery()
            ))
            rows = session.execute(query.order_by(KnowledgeEntry.updated_at.desc(), KnowledgeEntry.id)
                                   .offset((filters.page - 1) * filters.page_size).limit(filters.page_size))
            return ArticlePage(items=[self._summary(*row) for row in rows], page=filters.page,
                               page_size=filters.page_size, total=total)

    def revisions(self, actor, entry_id, page, page_size):
        with self.database.transaction(readonly=True) as session:
            TicketRepository(session, actor, UserRole.AGENT)
            self._entry(session, entry_id)
            query = (select(KnowledgeRevision, KnowledgeArticle)
                     .join(KnowledgeArticle, KnowledgeArticle.id == KnowledgeRevision.id)
                     .where(KnowledgeRevision.entry_id == entry_id))
            total = session.scalar(select(func.count()).select_from(query.subquery()))
            rows = session.execute(query.order_by(KnowledgeRevision.version.desc())
                                   .offset((page - 1) * page_size).limit(page_size))
            return RevisionPage(page=page, page_size=page_size, total=total, items=[
                RevisionSummary(revision=revision.version, revision_id=revision.id, editor_id=revision.editor_id,
                                title=article.title, source_name=article.source_reference, created_at=article.created_at)
                for revision, article in rows
            ])
