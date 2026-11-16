"""Add M4-B catalog, immutable-revision metadata, jobs, chunks and embeddings."""
from alembic import op

revision = "20261114_0002"
down_revision = "20261027_0001"
branch_labels = None
depends_on = None

DDL = (
    """CREATE TABLE knowledge_embeddings (
    content_hash VARCHAR(64) NOT NULL,
    model_key VARCHAR(120) NOT NULL,
    vector REAL[] NOT NULL,
    CONSTRAINT pk_knowledge_embeddings PRIMARY KEY (content_hash, model_key),
    CONSTRAINT ck_knowledge_embeddings_dimension CHECK (array_ndims(vector) = 1 AND cardinality(vector) = 384)
)""",
    """CREATE TABLE knowledge_entries (
    current_version INTEGER NOT NULL,
    generation INTEGER NOT NULL,
    status VARCHAR(12) NOT NULL,
    synthetic BOOLEAN NOT NULL,
    created_by UUID NOT NULL,
    changed_by UUID NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_knowledge_entries PRIMARY KEY (id),
    CONSTRAINT ck_knowledge_entries_version_positive CHECK (current_version >= 1 AND generation >= current_version),
    CONSTRAINT ck_knowledge_entries_status_valid CHECK (status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')),
    CONSTRAINT fk_knowledge_entries_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT fk_knowledge_entries_changed_by_users FOREIGN KEY(changed_by) REFERENCES users (id) ON DELETE RESTRICT
)""",
    """CREATE TABLE knowledge_revisions (
    id UUID NOT NULL,
    entry_id UUID NOT NULL,
    version INTEGER NOT NULL,
    editor_id UUID NOT NULL,
    source_type VARCHAR(24) NOT NULL,
    source_url VARCHAR(2048),
    rights_reference VARCHAR(500) NOT NULL,
    index_state VARCHAR(16) NOT NULL,
    index_model VARCHAR(120),
    attempts INTEGER NOT NULL,
    lease_token UUID,
    lease_until TIMESTAMP WITH TIME ZONE,
    CONSTRAINT pk_knowledge_revisions PRIMARY KEY (id),
    CONSTRAINT fk_knowledge_revisions_id_knowledge_articles FOREIGN KEY(id, version) REFERENCES knowledge_articles (id, version) ON DELETE RESTRICT,
    CONSTRAINT uq_knowledge_revisions_entry_version UNIQUE (entry_id, version),
    CONSTRAINT ck_knowledge_revisions_bounds CHECK (version >= 1 AND attempts BETWEEN 0 AND 3),
    CONSTRAINT ck_knowledge_revisions_source_type_valid CHECK (source_type IN ('APPROVED_REFERENCE', 'INTERNAL_PROCEDURE', 'SYNTHETIC_TEST')),
    CONSTRAINT ck_knowledge_revisions_index_state_valid CHECK (index_state IN ('IDLE', 'PENDING', 'RUNNING', 'READY', 'LEXICAL_ONLY', 'FAILED')),
    CONSTRAINT ck_knowledge_revisions_lease_consistent CHECK ((index_state = 'RUNNING' AND lease_token IS NOT NULL AND lease_until IS NOT NULL) OR (index_state <> 'RUNNING' AND lease_token IS NULL AND lease_until IS NULL)),
    CONSTRAINT fk_knowledge_revisions_entry_id_knowledge_entries FOREIGN KEY(entry_id) REFERENCES knowledge_entries (id) ON DELETE RESTRICT,
    CONSTRAINT fk_knowledge_revisions_editor_id_users FOREIGN KEY(editor_id) REFERENCES users (id) ON DELETE RESTRICT
)""",
    """CREATE TABLE knowledge_chunks (
    id UUID NOT NULL,
    revision_id UUID NOT NULL,
    ordinal INTEGER NOT NULL,
    text TEXT NOT NULL,
    search_text TEXT NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    embedding_hash VARCHAR(64) NOT NULL,
    CONSTRAINT pk_knowledge_chunks PRIMARY KEY (id),
    CONSTRAINT uq_knowledge_chunks_revision_ordinal UNIQUE (revision_id, ordinal),
    CONSTRAINT uq_knowledge_chunks_revision_hash UNIQUE (revision_id, content_hash),
    CONSTRAINT ck_knowledge_chunks_bounds CHECK (ordinal >= 0 AND length(text) BETWEEN 1 AND 1000),
    CONSTRAINT fk_knowledge_chunks_revision_id_knowledge_revisions FOREIGN KEY(revision_id) REFERENCES knowledge_revisions (id) ON DELETE RESTRICT
)""",
    """CREATE TABLE knowledge_entry_events (
    entry_id UUID NOT NULL,
    revision_id UUID NOT NULL,
    actor_id UUID NOT NULL,
    generation INTEGER NOT NULL,
    action VARCHAR(16) NOT NULL,
    status VARCHAR(12) NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_knowledge_entry_events PRIMARY KEY (id),
    CONSTRAINT uq_knowledge_events_entry_generation UNIQUE (entry_id, generation),
    CONSTRAINT ck_knowledge_entry_events_action_valid CHECK (action IN ('CREATE', 'EDIT', 'PUBLISH', 'UNPUBLISH', 'ARCHIVE', 'REINDEX')),
    CONSTRAINT ck_knowledge_entry_events_status_valid CHECK (status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')),
    CONSTRAINT fk_knowledge_entry_events_entry_id_knowledge_entries FOREIGN KEY(entry_id) REFERENCES knowledge_entries (id) ON DELETE RESTRICT,
    CONSTRAINT fk_knowledge_entry_events_revision_id_knowledge_revisions FOREIGN KEY(revision_id) REFERENCES knowledge_revisions (id) ON DELETE RESTRICT,
    CONSTRAINT fk_knowledge_entry_events_actor_id_users FOREIGN KEY(actor_id) REFERENCES users (id) ON DELETE RESTRICT
)""",
    """CREATE INDEX ix_knowledge_entries_status_updated ON knowledge_entries (status, updated_at, id)""",
    """CREATE INDEX ix_knowledge_revisions_job ON knowledge_revisions (index_state, lease_until)""",
    """CREATE INDEX ix_knowledge_chunks_search ON knowledge_chunks USING gin (to_tsvector('english'::regconfig, search_text))""",
    """ALTER TABLE knowledge_entries ADD CONSTRAINT fk_knowledge_entries_current_revision FOREIGN KEY(id, current_version) REFERENCES knowledge_revisions (entry_id, version) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED""",
    """CREATE TRIGGER supportlens_knowledge_event_append_only BEFORE UPDATE OR DELETE ON public.knowledge_entry_events
FOR EACH ROW EXECUTE FUNCTION public.supportlens_audit_append_only()""",
    """CREATE TRIGGER supportlens_knowledge_chunk_append_only BEFORE UPDATE OR DELETE ON public.knowledge_chunks
FOR EACH ROW EXECUTE FUNCTION public.supportlens_audit_append_only()""",
    """CREATE TRIGGER supportlens_knowledge_embedding_append_only BEFORE UPDATE OR DELETE ON public.knowledge_embeddings
FOR EACH ROW EXECUTE FUNCTION public.supportlens_audit_append_only()""",
    """CREATE FUNCTION public.supportlens_guard_knowledge_provenance() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
  IF TG_OP = 'DELETE' OR ROW(NEW.id, NEW.entry_id, NEW.version, NEW.editor_id, NEW.source_type, NEW.source_url, NEW.rights_reference)
      IS DISTINCT FROM ROW(OLD.id, OLD.entry_id, OLD.version, OLD.editor_id, OLD.source_type, OLD.source_url, OLD.rights_reference) THEN
    RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Knowledge provenance is immutable';
  END IF;
  RETURN NEW;
END; $$""",
    """CREATE TRIGGER supportlens_knowledge_provenance_guard BEFORE UPDATE OR DELETE ON public.knowledge_revisions
FOR EACH ROW EXECUTE FUNCTION public.supportlens_guard_knowledge_provenance()""",
)


def upgrade():
    for statement in DDL:
        op.execute(statement)


def downgrade():
    raise RuntimeError("Knowledge downgrade is disabled because it would remove retained history.")
