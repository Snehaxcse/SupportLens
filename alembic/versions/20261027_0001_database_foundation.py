"""Initial SupportLens database foundation. Frozen schema, no destructive downgrade.

Revision: 20261027_0001
Revises: none
Scope: seven tables and integrity guards, no application or ML execution.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261027_0001"
down_revision = None
branch_labels = None
depends_on = None

GUARD_DDL = (
    """CREATE FUNCTION public.supportlens_guard_ticket() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE customer_role text; agent_role text;
BEGIN
  SELECT role INTO customer_role FROM public.users WHERE id=NEW.customer_id FOR SHARE;
  IF customer_role IS NOT NULL AND customer_role <> 'CUSTOMER' THEN
    RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Ticket owner must be a customer';
  END IF;
  IF NEW.assigned_agent_id IS NOT NULL THEN
    SELECT role INTO agent_role FROM public.users WHERE id=NEW.assigned_agent_id FOR SHARE;
    IF agent_role IS NOT NULL AND agent_role <> 'AGENT' THEN
      RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Assigned user must be an agent';
    END IF;
  END IF;
  IF TG_OP='UPDATE' AND NEW.status IS DISTINCT FROM OLD.status THEN
    IF NOT ((OLD.status='OPEN' AND NEW.status IN ('IN_PROGRESS','RESOLVED'))
      OR (OLD.status='IN_PROGRESS' AND NEW.status='RESOLVED')
      OR (OLD.status='RESOLVED' AND NEW.status='REOPENED')
      OR (OLD.status='REOPENED' AND NEW.status IN ('IN_PROGRESS','RESOLVED'))) THEN
      RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Invalid ticket status transition';
    END IF;
  END IF;
  RETURN NEW;
END; $$""",
    """CREATE TRIGGER supportlens_ticket_guard BEFORE INSERT OR UPDATE ON public.tickets
FOR EACH ROW EXECUTE FUNCTION public.supportlens_guard_ticket()""",
    """CREATE FUNCTION public.supportlens_guard_internal_message() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE author_role text;
BEGIN
  SELECT role INTO author_role FROM public.users WHERE id=NEW.author_id FOR SHARE;
  IF NEW.visibility='INTERNAL' AND author_role IS NOT NULL AND author_role <> 'AGENT' THEN
    RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Internal messages require an agent author';
  END IF;
  RETURN NEW;
END; $$""",
    """CREATE TRIGGER supportlens_internal_message_guard BEFORE INSERT OR UPDATE ON public.ticket_messages
FOR EACH ROW EXECUTE FUNCTION public.supportlens_guard_internal_message()""",
    """CREATE FUNCTION public.supportlens_guard_user_role() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
  IF NEW.role IS DISTINCT FROM OLD.role THEN
    IF (NEW.role <> 'CUSTOMER' AND EXISTS(SELECT 1 FROM public.tickets WHERE customer_id=OLD.id))
      OR (NEW.role <> 'AGENT' AND (EXISTS(SELECT 1 FROM public.tickets WHERE assigned_agent_id=OLD.id)
        OR EXISTS(SELECT 1 FROM public.ticket_messages WHERE author_id=OLD.id AND visibility='INTERNAL'))) THEN
      RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Role change would invalidate support history';
    END IF;
  END IF;
  RETURN NEW;
END; $$""",
    """CREATE TRIGGER supportlens_user_role_guard BEFORE UPDATE OF role ON public.users
FOR EACH ROW EXECUTE FUNCTION public.supportlens_guard_user_role()""",
    """CREATE FUNCTION public.supportlens_guard_knowledge_revision() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
  IF NEW.title IS DISTINCT FROM OLD.title OR NEW.content IS DISTINCT FROM OLD.content
    OR NEW.source_reference IS DISTINCT FROM OLD.source_reference OR NEW.version IS DISTINCT FROM OLD.version THEN
    RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Knowledge revisions are immutable; create a new revision row';
  END IF;
  RETURN NEW;
END; $$""",
    """CREATE TRIGGER supportlens_knowledge_revision_guard BEFORE UPDATE ON public.knowledge_articles
FOR EACH ROW EXECUTE FUNCTION public.supportlens_guard_knowledge_revision()""",
    """CREATE FUNCTION public.supportlens_audit_append_only() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
  RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Ticket audit events are append-only';
END; $$""",
    """CREATE TRIGGER supportlens_audit_append_only_guard BEFORE UPDATE OR DELETE ON public.ticket_events
FOR EACH ROW EXECUTE FUNCTION public.supportlens_audit_append_only()""",
)

def upgrade():
# Frozen explicit table/constraint snapshot.
    op.create_table('knowledge_articles',
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('source_reference', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('length(btrim(content)) BETWEEN 1 AND 100000', name=op.f('ck_knowledge_articles_content_length')),
    sa.CheckConstraint('length(btrim(source_reference)) > 0', name=op.f('ck_knowledge_articles_source_nonblank')),
    sa.CheckConstraint('length(btrim(title)) > 0', name=op.f('ck_knowledge_articles_title_nonblank')),
    sa.CheckConstraint('updated_at >= created_at', name=op.f('ck_knowledge_articles_timestamp_order')),
    sa.CheckConstraint('version >= 1', name=op.f('ck_knowledge_articles_version_positive')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_knowledge_articles')),
    sa.UniqueConstraint('id', 'version', name='uq_knowledge_articles_id_version')
    )
    op.create_table('users',
    sa.Column('firebase_uid', sa.String(length=128), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('display_name', sa.String(length=120), nullable=True),
    sa.Column('role', sa.Enum('CUSTOMER', 'AGENT', name='user_role', native_enum=False, create_constraint=True), server_default='CUSTOMER', nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('display_name IS NULL OR length(btrim(display_name)) > 0', name=op.f('ck_users_display_name_nonblank')),
    sa.CheckConstraint('length(btrim(email)) > 0', name=op.f('ck_users_email_nonblank')),
    sa.CheckConstraint('length(btrim(firebase_uid)) > 0', name=op.f('ck_users_firebase_uid_nonblank')),
    sa.CheckConstraint('updated_at >= created_at', name=op.f('ck_users_timestamp_order')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('firebase_uid', name=op.f('uq_users_firebase_uid'))
    )
    op.create_table('tickets',
    sa.Column('customer_id', sa.Uuid(), nullable=False),
    sa.Column('assigned_agent_id', sa.Uuid(), nullable=True),
    sa.Column('subject', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('status', sa.Enum('OPEN', 'IN_PROGRESS', 'RESOLVED', 'REOPENED', name='ticket_status', native_enum=False, create_constraint=True), server_default='OPEN', nullable=False),
    sa.Column('priority', sa.Enum('LOW', 'NORMAL', 'HIGH', name='ticket_priority', native_enum=False, create_constraint=True), server_default='NORMAL', nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(status = 'RESOLVED') = (resolved_at IS NOT NULL)", name=op.f('ck_tickets_resolution_consistent')),
    sa.CheckConstraint('length(btrim(description)) BETWEEN 1 AND 20000', name=op.f('ck_tickets_description_length')),
    sa.CheckConstraint('length(btrim(subject)) > 0', name=op.f('ck_tickets_subject_nonblank')),
    sa.CheckConstraint('resolved_at IS NULL OR resolved_at >= created_at', name=op.f('ck_tickets_resolution_order')),
    sa.CheckConstraint('updated_at >= created_at', name=op.f('ck_tickets_timestamp_order')),
    sa.ForeignKeyConstraint(['assigned_agent_id'], ['users.id'], name=op.f('fk_tickets_assigned_agent_id_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['customer_id'], ['users.id'], name=op.f('fk_tickets_customer_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tickets'))
    )
    op.create_index('ix_tickets_agent_status', 'tickets', ['assigned_agent_id', 'status'], unique=False)
    op.create_index('ix_tickets_customer_created', 'tickets', ['customer_id', 'created_at'], unique=False)
    op.create_table('ai_runs',
    sa.Column('ticket_id', sa.Uuid(), nullable=False),
    sa.Column('model_version', sa.String(length=120), nullable=False),
    sa.Column('predicted_intent', sa.Enum('ACCOUNT_ACCESS', 'ACCOUNT_SECURITY', 'BILLING_PAYMENT', 'SUBSCRIPTION', 'APP_TECHNICAL', 'CONTENT', 'FEATURE', 'OTHER_SUPPORT', 'NON_SUPPORT', 'UNKNOWN', name='predicted_intent', native_enum=False, create_constraint=True), nullable=True),
    sa.Column('score_metadata', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('requires_human_review', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', name='ai_run_status', native_enum=False, create_constraint=True), server_default='PENDING', nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("jsonb_typeof(score_metadata) = 'object'", name=op.f('ck_ai_runs_score_metadata_object')),
    sa.CheckConstraint('length(btrim(model_version)) > 0', name=op.f('ck_ai_runs_model_version_nonblank')),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], name=op.f('fk_ai_runs_ticket_id_tickets'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ai_runs'))
    )
    op.create_index('ix_ai_runs_ticket_created', 'ai_runs', ['ticket_id', 'created_at'], unique=False)
    op.create_table('ticket_events',
    sa.Column('ticket_id', sa.Uuid(), nullable=False),
    sa.Column('actor_id', sa.Uuid(), nullable=True),
    sa.Column('event_type', sa.Enum('TICKET_CREATED', 'STATUS_CHANGED', 'ASSIGNMENT_CHANGED', 'REPLY_ADDED', 'AI_DECISION_RECORDED', name='ticket_event_type', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("jsonb_typeof(metadata) = 'object'", name=op.f('ck_ticket_events_metadata_object')),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], name=op.f('fk_ticket_events_actor_id_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], name=op.f('fk_ticket_events_ticket_id_tickets'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ticket_events'))
    )
    op.create_index('ix_ticket_events_ticket_created', 'ticket_events', ['ticket_id', 'created_at'], unique=False)
    op.create_table('ticket_messages',
    sa.Column('ticket_id', sa.Uuid(), nullable=False),
    sa.Column('author_id', sa.Uuid(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('visibility', sa.Enum('PUBLIC', 'INTERNAL', name='message_visibility', native_enum=False, create_constraint=True), server_default='PUBLIC', nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('length(btrim(body)) BETWEEN 1 AND 20000', name=op.f('ck_ticket_messages_body_length')),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], name=op.f('fk_ticket_messages_author_id_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], name=op.f('fk_ticket_messages_ticket_id_tickets'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ticket_messages'))
    )
    op.create_index('ix_ticket_messages_ticket_created', 'ticket_messages', ['ticket_id', 'created_at'], unique=False)
    op.create_table('knowledge_citations',
    sa.Column('ai_run_id', sa.Uuid(), nullable=False),
    sa.Column('knowledge_article_id', sa.Uuid(), nullable=False),
    sa.Column('article_version', sa.Integer(), nullable=False),
    sa.Column('citation_metadata', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.CheckConstraint("jsonb_typeof(citation_metadata) = 'object'", name=op.f('ck_knowledge_citations_citation_metadata_object')),
    sa.CheckConstraint('article_version >= 1', name=op.f('ck_knowledge_citations_article_version_positive')),
    sa.ForeignKeyConstraint(['ai_run_id'], ['ai_runs.id'], name=op.f('fk_knowledge_citations_ai_run_id_ai_runs'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['knowledge_article_id', 'article_version'], ['knowledge_articles.id', 'knowledge_articles.version'], name=op.f('fk_knowledge_citations_knowledge_article_id_knowledge_articles'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_knowledge_citations')),
    sa.UniqueConstraint('ai_run_id', 'knowledge_article_id', 'article_version', name='uq_knowledge_citations_run_article_version')
    )
    op.create_index('ix_knowledge_citations_article_version', 'knowledge_citations', ['knowledge_article_id', 'article_version'], unique=False)
    for statement in GUARD_DDL:
        op.execute(statement)


def downgrade():
    raise RuntimeError("Destructive downgrades are disabled; use a reviewed forward migration.")
