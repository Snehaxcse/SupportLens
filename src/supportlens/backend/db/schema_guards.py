"""Initial PostgreSQL cross-row integrity guards, frozen into revision 20261027_0001."""

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

EXPECTED_TRIGGERS = {
    "supportlens_ticket_guard", "supportlens_internal_message_guard",
    "supportlens_user_role_guard", "supportlens_knowledge_revision_guard",
    "supportlens_audit_append_only_guard",
}
