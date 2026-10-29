"""Live PostgreSQL checks: opt-in only, synthetic data, outer transaction always rolled back."""
import os
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from supportlens.backend.config import ConfigurationError, Environment, load_settings
from supportlens.backend.db.migrations import verify_databases, verify_schema
from supportlens.backend.db.models import (
    AIRun, KnowledgeArticle, KnowledgeCitation, Ticket, TicketEvent, TicketMessage, User,
)
from supportlens.backend.db.preflight import inspect_connection
from supportlens.backend.db.session import build_engine
from supportlens.backend.db.testing import require_test_opt_in

pytestmark = [pytest.mark.db_integration, pytest.mark.skipif(
    os.environ.get("SUPPORTLENS_RUN_DB_TESTS") != "1", reason="Explicit verified PostgreSQL test opt-in is absent.")]


@pytest.fixture
def verified_test_connection():
    try:
        settings = load_settings()
        require_test_opt_in(settings)
        verify_databases(settings)
        engine = build_engine(settings, Environment.TEST)
    except ConfigurationError as exc:
        pytest.fail(str(exc), pytrace=False)
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            inspect_connection(connection, settings.spec(Environment.TEST), require_readonly=False)
            verify_schema(connection)
            yield connection
        finally:
            # Never commit, delete, truncate, drop or reset. Only this test's writes roll back.
            transaction.rollback()
            engine.dispose()


def new_user(connection, role="CUSTOMER"):
    return connection.execute(insert(User).values(firebase_uid="synthetic_" + uuid.uuid4().hex,
        email="synthetic@example.invalid", role=role).returning(User.id)).scalar_one()


def new_ticket(connection, customer):
    return connection.execute(insert(Ticket).values(customer_id=customer, subject="Synthetic ticket",
        description="Synthetic integration test only.").returning(Ticket.id)).scalar_one()


def expect_integrity_failure(connection, statement):
    with pytest.raises(IntegrityError):
        with connection.begin_nested():
            connection.execute(statement)


def test_live_firebase_uid_uniqueness_and_timestamp_defaults(verified_test_connection):
    connection = verified_test_connection
    uid = "synthetic_" + uuid.uuid4().hex
    row = connection.execute(insert(User).values(firebase_uid=uid, email="synthetic@example.invalid")
                             .returning(User.id, User.role, User.created_at, User.updated_at)).one()
    assert row.role == "CUSTOMER" and row.created_at.tzinfo is not None
    assert row.created_at.utcoffset() == timedelta(0) and row.updated_at >= row.created_at
    expect_integrity_failure(connection, insert(User).values(firebase_uid=uid, email="synthetic@example.invalid"))


def test_live_foreign_key_and_enum_checks(verified_test_connection):
    connection = verified_test_connection
    expect_integrity_failure(connection, insert(Ticket).values(customer_id=uuid.uuid4(), subject="Synthetic", description="Synthetic"))
    customer = new_user(connection)
    with pytest.raises(IntegrityError) as failure:
        with connection.begin_nested():
            connection.exec_driver_sql(
                "INSERT INTO public.tickets(customer_id, subject, description, priority) VALUES (%s,%s,%s,%s)",
                (customer, "Synthetic", "Synthetic", "BAD"))
    assert failure.value.orig.sqlstate == "23514"  # CHECK violation, not a length/type error.


def test_live_internal_messages_are_agent_only(verified_test_connection):
    connection = verified_test_connection
    customer, agent = new_user(connection), new_user(connection, "AGENT")
    ticket = new_ticket(connection, customer)
    expect_integrity_failure(connection, insert(TicketMessage).values(ticket_id=ticket, author_id=customer,
        body="Synthetic internal note", visibility="INTERNAL"))
    connection.execute(insert(TicketMessage).values(ticket_id=ticket, author_id=agent,
        body="Synthetic internal note", visibility="INTERNAL"))
    expect_integrity_failure(connection, update(User).where(User.id==agent).values(role="CUSTOMER"))


def test_live_assignment_role_and_resolution_consistency(verified_test_connection):
    connection = verified_test_connection
    customer = new_user(connection)
    ticket = new_ticket(connection, customer)
    expect_integrity_failure(connection, update(Ticket).where(Ticket.id==ticket).values(assigned_agent_id=customer))
    expect_integrity_failure(connection, update(Ticket).where(Ticket.id==ticket).values(status="RESOLVED"))


def test_live_citation_version_and_immutable_knowledge(verified_test_connection):
    connection = verified_test_connection
    ticket = new_ticket(connection, new_user(connection))
    run = connection.execute(insert(AIRun).values(ticket_id=ticket, model_version="synthetic-m1")
                             .returning(AIRun.id)).scalar_one()
    article = connection.execute(insert(KnowledgeArticle).values(title="Synthetic article", content="Synthetic content",
        source_reference="synthetic-reference", version=1).returning(KnowledgeArticle.id)).scalar_one()
    expect_integrity_failure(connection, insert(KnowledgeCitation).values(ai_run_id=run, knowledge_article_id=article, article_version=2))
    connection.execute(insert(KnowledgeCitation).values(ai_run_id=run, knowledge_article_id=article, article_version=1))
    expect_integrity_failure(connection, update(KnowledgeArticle).where(KnowledgeArticle.id==article).values(content="Changed synthetic content"))


def test_live_audit_events_are_append_only(verified_test_connection):
    connection = verified_test_connection
    customer = new_user(connection)
    ticket = new_ticket(connection, customer)
    event = connection.execute(insert(TicketEvent).values(ticket_id=ticket, actor_id=customer, event_type="TICKET_CREATED")
                               .returning(TicketEvent.id)).scalar_one()
    expect_integrity_failure(connection, update(TicketEvent).where(TicketEvent.id==event).values(event_metadata={"changed": True}))
