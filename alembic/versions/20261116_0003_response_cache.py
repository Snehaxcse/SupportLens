"""Add the separate response cache and atomic evidence invalidation."""
from alembic import op

revision = "20261116_0003"
down_revision = "20261114_0002"
branch_labels = None
depends_on = None

DDL = (
    """CREATE TABLE response_cache_entries (
    source_run_id UUID NOT NULL,
    approved_by UUID NOT NULL,
    approval_request_id UUID NOT NULL,
    source_draft_version INTEGER NOT NULL,
    issue_key VARCHAR(48) NOT NULL,
    canonical_issue VARCHAR(180) NOT NULL,
    intent VARCHAR(24) NOT NULL,
    risk VARCHAR(16) NOT NULL,
    scope VARCHAR(16) NOT NULL,
    policy_version VARCHAR(64) NOT NULL,
    match_version VARCHAR(40) NOT NULL,
    embedding_model VARCHAR(120) NOT NULL,
    vector REAL[] NOT NULL,
    content TEXT NOT NULL,
    duplicate_key VARCHAR(64) NOT NULL,
    state VARCHAR(16) NOT NULL,
    version INTEGER NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    invalidated_at TIMESTAMP WITH TIME ZONE,
    invalidation_reason VARCHAR(24),
    revoked_by UUID,
    revocation_request_id UUID,
    hit_count INTEGER NOT NULL,
    last_hit_at TIMESTAMP WITH TIME ZONE,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_response_cache_entries PRIMARY KEY (id),
    CONSTRAINT ck_response_cache_entries_dimension CHECK (array_ndims(vector) = 1 AND cardinality(vector) = 384),
    CONSTRAINT ck_response_cache_entries_scope_risk CHECK (scope IN ('GENERAL', 'SYNTHETIC') AND risk = 'STANDARD'),
    CONSTRAINT ck_response_cache_entries_intent CHECK (intent IN ('APP_TECHNICAL', 'CONTENT', 'FEATURE')),
    CONSTRAINT ck_response_cache_entries_bounds CHECK (length(content) BETWEEN 1 AND 1600 AND source_draft_version > 0 AND version > 0),
    CONSTRAINT ck_response_cache_entries_ttl CHECK (expires_at > created_at AND expires_at <= created_at + interval '30 days'),
    CONSTRAINT ck_response_cache_entries_usage CHECK (hit_count >= 0 AND (hit_count = 0) = (last_hit_at IS NULL)),
    CONSTRAINT ck_response_cache_entries_state CHECK ((state = 'ACTIVE' AND invalidated_at IS NULL AND invalidation_reason IS NULL) OR (state = 'INACTIVE' AND invalidated_at IS NOT NULL AND invalidation_reason IN ('EVIDENCE_CHANGED', 'REVOKED', 'EXPIRED') AND invalidation_reason IS NOT NULL)),
    CONSTRAINT ck_response_cache_entries_revocation CHECK ((revoked_by IS NOT NULL) = coalesce(invalidation_reason = 'REVOKED', false) AND (revocation_request_id IS NOT NULL) = (revoked_by IS NOT NULL)),
    CONSTRAINT uq_response_cache_entries_source_run_id UNIQUE (source_run_id),
    CONSTRAINT fk_response_cache_entries_source_run_id_ai_runs FOREIGN KEY(source_run_id) REFERENCES ai_runs (id) ON DELETE RESTRICT,
    CONSTRAINT fk_response_cache_entries_approved_by_users FOREIGN KEY(approved_by) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT uq_response_cache_entries_approval_request_id UNIQUE (approval_request_id),
    CONSTRAINT fk_response_cache_entries_revoked_by_users FOREIGN KEY(revoked_by) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT uq_response_cache_entries_revocation_request_id UNIQUE (revocation_request_id)
)""",
    """CREATE TABLE response_cache_evidence (
    cache_id UUID NOT NULL,
    chunk_id UUID NOT NULL,
    article_id UUID NOT NULL,
    revision_id UUID NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    CONSTRAINT pk_response_cache_evidence PRIMARY KEY (cache_id, chunk_id),
    CONSTRAINT fk_response_cache_evidence_cache_id_response_cache_entries FOREIGN KEY(cache_id) REFERENCES response_cache_entries (id) ON DELETE RESTRICT,
    CONSTRAINT fk_response_cache_evidence_chunk_id_knowledge_chunks FOREIGN KEY(chunk_id) REFERENCES knowledge_chunks (id) ON DELETE RESTRICT,
    CONSTRAINT fk_response_cache_evidence_article_id_knowledge_entries FOREIGN KEY(article_id) REFERENCES knowledge_entries (id) ON DELETE RESTRICT,
    CONSTRAINT fk_response_cache_evidence_revision_id_knowledge_revisions FOREIGN KEY(revision_id) REFERENCES knowledge_revisions (id) ON DELETE RESTRICT
)""",
    """CREATE INDEX ix_response_cache_candidates ON response_cache_entries (scope, intent, issue_key, expires_at) WHERE state = 'ACTIVE'""",
    """CREATE UNIQUE INDEX uq_response_cache_active_content ON response_cache_entries (duplicate_key) WHERE state = 'ACTIVE'""",
    """CREATE INDEX ix_response_cache_evidence_article ON response_cache_evidence (article_id)""",
    """CREATE FUNCTION public.supportlens_guard_response_cache() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE
  mutable_fields text[] := ARRAY['state', 'version', 'invalidated_at', 'invalidation_reason', 'revoked_by',
                                 'revocation_request_id', 'hit_count', 'last_hit_at'];
BEGIN
  IF TG_OP = 'DELETE' OR OLD.state <> 'ACTIVE' THEN
    RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Cache history is retained';
  END IF;
  IF (to_jsonb(NEW) - mutable_fields) IS DISTINCT FROM (to_jsonb(OLD) - mutable_fields)
     OR (NEW.state = 'ACTIVE' AND (NEW.version <> OLD.version OR NEW.hit_count <> OLD.hit_count + 1
         OR NEW.last_hit_at IS NULL OR NEW.last_hit_at < coalesce(OLD.last_hit_at, OLD.created_at)))
     OR (NEW.state = 'INACTIVE' AND (NEW.version <> OLD.version + 1 OR NEW.hit_count <> OLD.hit_count
         OR NEW.last_hit_at IS DISTINCT FROM OLD.last_hit_at)) THEN
    RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Invalid cache state transition';
  END IF;
  RETURN NEW;
END; $$""",
    """CREATE TRIGGER supportlens_response_cache_guard BEFORE UPDATE OR DELETE ON public.response_cache_entries
FOR EACH ROW EXECUTE FUNCTION public.supportlens_guard_response_cache()""",
    """CREATE TRIGGER supportlens_response_cache_evidence_guard BEFORE UPDATE OR DELETE ON public.response_cache_evidence
FOR EACH ROW EXECUTE FUNCTION public.supportlens_audit_append_only()""",
    """CREATE FUNCTION public.supportlens_invalidate_response_cache() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
  IF OLD.status = 'PUBLISHED' AND (NEW.status <> 'PUBLISHED' OR NEW.current_version <> OLD.current_version) THEN
    UPDATE public.response_cache_entries AS entry
      SET state='INACTIVE', version=entry.version+1, invalidated_at=clock_timestamp(), invalidation_reason='EVIDENCE_CHANGED'
      WHERE entry.state='ACTIVE' AND EXISTS (SELECT 1 FROM public.response_cache_evidence AS evidence
        WHERE evidence.cache_id=entry.id AND evidence.article_id=OLD.id);
  END IF;
  RETURN NEW;
END; $$""",
    """CREATE TRIGGER supportlens_response_cache_invalidate AFTER UPDATE ON public.knowledge_entries
FOR EACH ROW EXECUTE FUNCTION public.supportlens_invalidate_response_cache()""",
)


def upgrade():
    for statement in DDL:
        op.execute(statement)


def downgrade():
    raise RuntimeError("Response cache downgrade is disabled because it would remove retained audit history.")
